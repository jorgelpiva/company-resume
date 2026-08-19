from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import CORS_ALLOW_ORIGINS, MAX_PAGES, MIN_PRIMARY_CONTENT_CHARS, WORK_DIR
from app.chat.service import answer_question_from_context
from app.crawler.fetcher import fetch_html
from app.crawler.relevance import score_url_and_content
from app.crawler.route_discovery import build_route_tree, discover_routes
from app.crawler.sitemap import discover_sitemap_urls
from app.crawler.robots import is_allowed_by_robots, read_robots_txt
from app.crawler.security import ensure_public_url, validate_url_syntax
from app.crawler.url_normalizer import normalize_url
from app.jobs.mapper import create_job, remove_job, update_job
from app.models import BrowserChatRequest
from app.processing.chunker import build_chunk_documents, chunk_text
from app.processing.cleaner import build_markdown, clean_text
from app.processing.deduplicator import deduplicate_paragraphs, normalize_for_hash
from app.crawler.extractor import extract_primary_content
from app.processing.profile_builder import build_company_profile
from app.processing.summarizer import (
    assess_site_content,
    has_institutional_identity,
    is_institutional_identity_host,
    is_institutional_identity_path,
    is_usable_external_identity_context,
    summarize_company_profile,
    summarize_page,
)
from app.research.company_context import research_company_context

app = FastAPI(title="Company Resume")
logger = logging.getLogger(__name__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"
if FRONTEND_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="frontend-assets")

class MapCompanyRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


def slugify_company_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "empresa"


def safe_slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")
    return re.sub(r"[^a-z0-9]+", "-", host).strip("-")


def validate_url(url: str) -> str:
    candidate = normalize_url(url)
    validate_url_syntax(candidate)
    return candidate


def get_research_seed_urls(research_context: dict[str, Any] | None, domain: str) -> list[str]:
    seeds: list[str] = []
    for source in (research_context or {}).get("sources", []):
        source_url = source.get("url", "") if isinstance(source, dict) else ""
        parsed_source = urlparse(source_url)
        source_host = (parsed_source.hostname or "").removeprefix("www.").lower()
        source_path = parsed_source.path.lower().rstrip("/") or "/"
        if (
            source_url.startswith(("http://", "https://"))
            and is_institutional_identity_host(source_host, domain)
            and is_institutional_identity_path(source_path)
        ):
            seeds.append(source_url)
    return list(dict.fromkeys(seeds))[:8]


async def extract_page_entry(
    route: str,
    validated_url: str,
    html_cache: dict[str, str],
) -> dict[str, Any] | None:
    html = html_cache.get(route)
    if html is None:
        html = await fetch_html(route)
        html_cache[route] = html
    extracted = extract_primary_content(html, route)
    title = extracted["title"] or "Página"
    h1 = extracted["h1"] or ""
    meta = extracted["meta_description"] or ""
    content = extracted["content"].strip()
    score = score_url_and_content(route, title, h1, meta, content)
    if len(content) < MIN_PRIMARY_CONTENT_CHARS and route != validated_url:
        return None
    if score <= 0 and route != validated_url:
        return None
    return {
        "url": route,
        "title": title,
        "h1": h1,
        "meta_description": meta,
        "score": score,
        "content": content,
    }


async def map_company_process(url: str, job_id: str) -> Dict[str, Any]:
    update_job(job_id, status="running", progress=5, message="Validando URL")
    validated_url = validate_url(url)
    parsed = urlparse(validated_url)
    domain = (parsed.hostname or "").removeprefix("www.").lower()
    slug = safe_slug(validated_url)

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    work_dir = WORK_DIR / f"{slug}-{job_id}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "name": domain.replace(".com.br", "").replace(".com", "").title(),
        "slug": slug,
        "domain": domain,
        "source_url": validated_url,
        "status": "processing",
        "mapped_at": datetime.now(timezone.utc).isoformat(),
        "pages_discovered": 0,
        "pages_selected": 0,
        "pages_processed": 0,
        "chunks": 0,
    }

    research_context: dict[str, Any] | None = None
    metadata["site_type"] = None
    metadata["research_confidence"] = None
    metadata["suggested_questions"] = []
    metadata["research_seed_urls"] = 0

    update_job(job_id, progress=8, message="Analisando robots.txt e sitemap")
    robots = await read_robots_txt(validated_url)
    sitemap_urls = await discover_sitemap_urls(validated_url)

    update_job(job_id, progress=15, message="Descobrindo rotas")
    html_cache: dict[str, str] = {}
    discovered_routes, route_tree = await discover_routes(
        validated_url,
        domain,
        seed_urls=sitemap_urls,
        robots_rules=robots.get("rules", []),
        html_cache=html_cache,
    )

    ranked_routes = sorted(
        discovered_routes,
        key=lambda route: (
            route == validated_url,
            (urlparse(route).hostname or "").removeprefix("www.").lower() == domain,
            score_url_and_content(route),
        ),
        reverse=True,
    )[:MAX_PAGES]

    page_entries: List[Dict[str, Any]] = []
    processed_pages: List[Dict[str, Any]] = []
    for idx, route in enumerate(ranked_routes, start=1):
        try:
            update_job(
                job_id,
                progress=20 + int((idx / max(len(ranked_routes), 1)) * 25),
                message=f"Analisando relevância ({idx}/{len(ranked_routes)})",
            )
            entry = await extract_page_entry(route, validated_url, html_cache)
            if entry:
                page_entries.append(entry)
        except Exception:
            continue

    local_identity_pages = [
        {
            "url": item["url"],
            "title": item["title"],
            "content": "\n\n".join(
                part for part in (
                    item.get("meta_description", ""),
                    item.get("h1", ""),
                    item.get("content", ""),
                )
                if part
            ),
        }
        for item in page_entries
    ]
    if not has_institutional_identity(local_identity_pages, root_domain=domain):
        update_job(job_id, progress=48, message="Complementando a identidade com pesquisa pública")
        research_context = await research_company_context(
            validated_url,
            domain,
            page_evidence=page_entries,
        )
        if research_context:
            (work_dir / "research_context.json").write_text(
                json.dumps(research_context, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            metadata["research_confidence"] = research_context.get("confidence")
            if is_usable_external_identity_context(research_context):
                metadata["site_type"] = research_context.get("site_type")
                metadata["suggested_questions"] = research_context.get("suggested_questions", [])

            # A confiança limita o que entra na narrativa, não a verificação de
            # uma página institucional oficial do próprio domínio.
            research_seed_urls = get_research_seed_urls(research_context, domain)
            metadata["research_seed_urls"] = len(research_seed_urls)
            if research_seed_urls:
                known_routes = {item["url"] for item in page_entries}
                validated_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
                robots_by_origin = {validated_origin: robots.get("rules", [])}
                seed_candidates: list[str] = []
                for route in sorted(
                    research_seed_urls,
                    key=score_url_and_content,
                    reverse=True,
                ):
                    if route in known_routes:
                        continue
                    parsed_seed = urlparse(route)
                    seed_origin = f"{parsed_seed.scheme.lower()}://{parsed_seed.netloc.lower()}"
                    if seed_origin not in robots_by_origin:
                        seed_robots = await read_robots_txt(f"{seed_origin}/")
                        robots_by_origin[seed_origin] = seed_robots.get("rules", [])
                    if is_allowed_by_robots(route, robots_by_origin[seed_origin]):
                        seed_candidates.append(route)
                    if len(seed_candidates) >= 8:
                        break
                for route in seed_candidates:
                    try:
                        entry = await extract_page_entry(route, validated_url, html_cache)
                    except Exception:
                        continue
                    if entry:
                        entry["research_identity_seed"] = True
                        page_entries.append(entry)
                        known_routes.add(route)
                discovered_routes = list(dict.fromkeys([*discovered_routes, *seed_candidates]))
                route_tree = build_route_tree(discovered_routes, validated_url)

    page_entries = sorted(
        page_entries,
        key=lambda item: (
            item["url"] == validated_url,
            bool(item.get("research_identity_seed")),
            item["score"],
        ),
        reverse=True,
    )[:MAX_PAGES]
    metadata["pages_discovered"] = len(discovered_routes)
    metadata["pages_selected"] = len(page_entries)
    metadata["robots_txt"] = robots.get("source")
    metadata["sitemap_urls_found"] = len(sitemap_urls)

    update_job(job_id, progress=52, message="Extraindo conteúdo")
    seen_paragraphs: set[str] = set()
    pages_path = work_dir / "pages"
    pages_path.mkdir(parents=True, exist_ok=True)
    for item in page_entries:
        raw_page_content = "\n\n".join(dict.fromkeys(
            part for part in (
                item.get("meta_description", ""),
                item.get("h1", ""),
                item.get("content", ""),
            )
            if part and part not in item.get("content", "")
        ))
        if item.get("content"):
            raw_page_content = "\n\n".join(
                part for part in (raw_page_content, item["content"]) if part
            )
        if not raw_page_content and item.get("title") not in {None, "", "Página"}:
            raw_page_content = f"Título da página: {item['title']}."
        if not raw_page_content and item["url"] == validated_url:
            raw_page_content = (
                "Nenhum conteúdo textual relevante pôde ser extraído da página inicial."
            )
        paragraphs = []
        for paragraph in deduplicate_paragraphs([clean_text(raw_page_content)]):
            normalized = normalize_for_hash(paragraph)
            if normalized in seen_paragraphs:
                continue
            seen_paragraphs.add(normalized)
            paragraphs.append(paragraph)
        page_content = "\n\n".join(paragraphs)
        if not page_content:
            continue
        url_hash = hashlib.sha1(item["url"].encode("utf-8")).hexdigest()[:8]
        page_filename = f"{slugify_company_name(item['title'])}-{url_hash}.md"
        page_markdown = build_markdown(item["title"], item["url"], page_content, metadata["mapped_at"])
        (pages_path / page_filename).write_text(page_markdown, encoding="utf-8")
        processed_pages.append({"url": item["url"], "title": item["title"], "content": page_content})

    metadata["pages_processed"] = len(processed_pages)
    if not processed_pages:
        raise ValueError("Nenhuma página com conteúdo institucional útil pôde ser processada")

    metadata["content_assessment"] = assess_site_content(processed_pages, root_domain=domain)
    rag_has_identity = has_institutional_identity(processed_pages, root_domain=domain)
    researched_name_available = is_usable_external_identity_context(research_context)
    metadata["identity_source"] = "rag" if rag_has_identity else ("external_research" if researched_name_available else "inferred")
    if not rag_has_identity and researched_name_available:
        metadata["name"] = research_context["entity_name"]

    homepage = next((page for page in processed_pages if page["url"] == validated_url), processed_pages[0])
    inferred_name = re.split(r"\s+[|–—-]\s+", homepage["title"])[0].strip()
    if inferred_name and inferred_name.lower() != "página" and not (not rag_has_identity and researched_name_available):
        metadata["name"] = inferred_name[:100]
        uppercase_matches = re.findall(r"\b[A-Z][A-Z0-9]{1,20}\b", homepage["content"])
        uppercase_brand = next((item for item in uppercase_matches if item.lower() == inferred_name.lower()), None)
        if uppercase_brand:
            metadata["name"] = uppercase_brand

    update_job(job_id, progress=65, message="Resumindo páginas")
    chunk_docs = []
    for page in processed_pages:
        chunks = chunk_text(page["content"], 1500)
        page["summary"] = summarize_page(chunks)
        chunk_docs.extend(build_chunk_documents(slug, page["title"], page["url"], chunks))

    chunks_path = work_dir / "chunks.json"
    chunks_path.write_text(json.dumps(chunk_docs, ensure_ascii=False, indent=2), encoding="utf-8")
    page_summaries = [
        {"url": page["url"], "title": page["title"], "summary": page["summary"]}
        for page in processed_pages
    ]
    (work_dir / "page_summaries.json").write_text(
        json.dumps(page_summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    update_job(job_id, progress=80, message="Preparando base de conhecimento")

    extractive_profile = build_company_profile(metadata, processed_pages)
    profile_text, profile_generation = await summarize_company_profile(
        metadata,
        processed_pages,
        extractive_profile,
        research_context=research_context,
    )
    metadata["profile_generation"] = profile_generation
    (work_dir / "company_profile.md").write_text(profile_text, encoding="utf-8")

    route_tree_path = work_dir / "route_tree.json"
    route_tree_path.write_text(json.dumps(route_tree, ensure_ascii=False, indent=2), encoding="utf-8")

    metadata["chunks"] = len(chunk_docs)
    metadata["name"] = metadata["name"] or "Empresa"
    metadata["status"] = "ready"
    (work_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    bundle = {
        "metadata": metadata,
        "company_profile": profile_text,
        "chunks": chunk_docs,
        "research_context": research_context or {},
        "page_summaries": page_summaries,
        "route_tree": route_tree,
    }
    shutil.rmtree(work_dir, ignore_errors=True)

    update_job(job_id, progress=100, message="Empresa pronta", status="completed")
    return bundle


@app.get("/api/health")
async def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/companies")
async def list_companies() -> List[Dict[str, Any]]:
    # A lista pertence exclusivamente ao IndexedDB de cada navegador.
    return []


@app.post("/api/companies/map")
async def map_company(request: MapCompanyRequest) -> Dict[str, Any]:
    try:
        validated = validate_url(request.url)
        await ensure_public_url(validated)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = create_job(url=request.url)
    try:
        return await map_company_process(validated, job_id)
    except Exception:
        logger.exception("Falha ao mapear o site informado")
        update_job(job_id, status="failed", message="Falha no mapeamento", error="Falha interna")
        raise HTTPException(
            status_code=422,
            detail="Não foi possível processar o site informado.",
        ) from None
    finally:
        for candidate in WORK_DIR.glob(f"*-{job_id}"):
            shutil.rmtree(candidate, ignore_errors=True)
        remove_job(job_id)


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str) -> Dict[str, Any]:
    raise HTTPException(status_code=410, detail="O mapeamento agora responde diretamente ao navegador")


@app.get("/api/companies/{slug}")
async def get_company(slug: str) -> Dict[str, Any]:
    raise HTTPException(status_code=410, detail="Os dados das empresas ficam somente neste navegador")


@app.post("/api/chat")
async def browser_chat(request: BrowserChatRequest) -> Dict[str, Any]:
    question = request.question.strip()
    return await answer_question_from_context(
        request.company.profile,
        request.company.chunks,
        request.company.research_context,
        question,
        history=request.history[-6:],
    )


@app.post("/api/companies/{slug}/chat")
async def legacy_chat_with_company(slug: str) -> Dict[str, Any]:
    raise HTTPException(status_code=410, detail="Use o contexto local do navegador em /api/chat")


@app.delete("/api/companies/{slug}")
async def delete_company(slug: str) -> Dict[str, bool | str]:
    raise HTTPException(status_code=410, detail="Exclua a empresa diretamente no navegador")


@app.post("/api/companies/{slug}/refresh")
async def refresh_company(slug: str) -> Dict[str, Any]:
    raise HTTPException(status_code=410, detail="Refaça o mapeamento a partir do navegador")


@app.get("/", response_class=HTMLResponse)
async def frontend_root():
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(
        "<h1>Frontend não compilado</h1>"
        "<p>Execute <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>.</p>",
        status_code=503,
    )

    # Interface legada mantida abaixo apenas como referência histórica e
    # deliberadamente inacessível. O fluxo atual é Vue + RxDB.
    return """
    <!doctype html>
    <html lang="pt-BR">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>Company Resume</title>
      <style>
        :root { --bg: #020817; --panel: #0f172a; --card: #111827; --text: #e5e7eb; --muted: #94a3b8; --accent: #38bdf8; --danger: #ef4444; --ok: #22c55e; }
        * { box-sizing: border-box; }
        body { margin: 0; font-family: Arial, sans-serif; background: linear-gradient(180deg,#020817,#0f172a); color: var(--text); }
        .container { max-width: 1200px; margin: 0 auto; padding: 32px 18px 64px; }
        h1 { margin: 0 0 12px; }
        .section { background: rgba(15,23,42,0.8); border: 1px solid rgba(148,163,184,0.2); border-radius: 18px; padding: 18px; margin-bottom: 22px; }
        .input-row { display: flex; gap: 12px; flex-wrap: wrap; }
        input { flex: 1; min-width: 220px; padding: 12px 14px; border-radius: 10px; border: 1px solid rgba(148,163,184,0.3); background: rgba(2,6,23,0.8); color: var(--text); }
        button { padding: 12px 18px; border: 0; border-radius: 10px; background: var(--accent); color: #03131d; font-weight: 700; cursor: pointer; }
        button.secondary { background: rgba(148,163,184,0.2); color: var(--text); }
        button.danger { background: var(--danger); color: white; }
        .company-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 18px; }
        .company-card { background: rgba(17,24,39,0.97); border: 1px solid rgba(56,189,248,0.2); border-radius: 18px; padding: 18px; }
        .company-card h3 { margin: 0 0 8px; }
        .meta { color: var(--muted); font-size: 0.96rem; margin-bottom: 10px; }
        .status { display: inline-block; padding: 4px 10px; border-radius: 999px; background: rgba(34,197,94,0.15); color: var(--ok); font-size: 0.8rem; }
        .actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
        .empty { color: var(--muted); padding: 16px 0; }
      </style>
    </head>
    <body>
      <div class="container">
        <h1>Company Resume</h1>

        <div class="section">
          <h2>Mapear nova empresa</h2>
          <div class="input-row">
            <input id="companyUrl" type="url" placeholder="https://empresa.com.br" />
            <button id="mapButton">MAPEAR EMPRESA</button>
          </div>
          <div id="statusMessage" class="meta" style="margin-top:12px;"></div>
        </div>

        <div class="section">
          <h2>Empresas mapeadas</h2>
          <div id="companies" class="company-grid"></div>
        </div>
      </div>

      <script>
        const apiBase = '/api';

        function escapeHtml(value) {
          return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
        }

        async function fetchJson(url, options = {}) {
          const response = await fetch(url, options);
          if (!response.ok) {
            const text = await response.text();
            throw new Error(text || 'Erro da API');
          }
          return response.json();
        }

        function openCompany(slug) {
          window.location.href = `/${slug}`;
        }

        function renderCompanies(companies) {
          const container = document.getElementById('companies');
          container.innerHTML = '';

          if (!companies.length) {
            container.innerHTML = '<div class="empty">Nenhuma empresa mapeada ainda. Cole a URL de uma empresa para transformar seu site público em inteligência conversacional.</div>';
            return;
          }

          companies.forEach(company => {
            const cleanSlug = company.slug || 'empresa';
            const card = document.createElement('div');
            card.className = 'company-card';
            card.innerHTML = `
              <h3>${escapeHtml(company.name)}</h3>
              <div class="meta">${escapeHtml(company.domain)}</div>
              <div class="meta">${company.pages_processed || 0} páginas processadas</div>
              <div class="meta">Atualizado em ${new Date(company.mapped_at || Date.now()).toLocaleDateString('pt-BR')}</div>
              <div class="meta"><span class="status">${company.status || 'ready'}</span></div>
              <div class="actions">
                <button class="open-button" data-slug="${cleanSlug}">ABRIR</button>
                <button class="secondary refresh-company" data-slug="${cleanSlug}">Atualizar</button>
                <button class="secondary delete-company" data-slug="${cleanSlug}">🗑</button>
              </div>
            `;
            container.appendChild(card);
          });

          document.querySelectorAll('.open-button').forEach(button => {
            button.addEventListener('click', () => openCompany(button.dataset.slug));
          });

          document.querySelectorAll('.delete-company').forEach(button => {
            button.addEventListener('click', async () => {
              const slug = button.dataset.slug;
              const company = (await fetchJson(`${apiBase}/companies`)).find(item => item.slug === slug);
              if (!company) return;
              const confirmed = window.confirm(`Tem certeza que deseja excluir ${company.name}?\n\nTodo o conteúdo processado, perfil, chunks e embeddings serão removidos.`);
              if (!confirmed) return;
              await fetchJson(`${apiBase}/companies/${slug}`, { method: 'DELETE' });
              loadCompanies();
            });
          });

          document.querySelectorAll('.refresh-company').forEach(button => {
            button.addEventListener('click', async () => {
              const job = await fetchJson(`${apiBase}/companies/${button.dataset.slug}/refresh`, { method: 'POST' });
              const status = document.getElementById('statusMessage');
              status.textContent = 'Atualizando mapeamento...';
              pollJob(job.job_id, status);
            });
          });
        }

        function pollJob(jobId, status) {
          const interval = setInterval(async () => {
            try {
              const jobStatus = await fetchJson(`${apiBase}/jobs/${jobId}`);
              status.textContent = `${jobStatus.message} (${jobStatus.progress}%)`;
              if (jobStatus.status === 'completed' || jobStatus.status === 'failed') {
                clearInterval(interval);
                status.textContent = jobStatus.status === 'completed' ? '✓ Empresa pronta' : (jobStatus.error || 'Falha no processamento');
                if (jobStatus.status === 'completed') loadCompanies();
              }
            } catch (error) {
              clearInterval(interval);
              status.textContent = 'Erro ao consultar job.';
            }
          }, 2000);
        }

        async function mapCompany() {
          const url = document.getElementById('companyUrl').value.trim();
          if (!url) return;

          const status = document.getElementById('statusMessage');
          status.textContent = 'Mapeando empresa...';
          try {
            const job = await fetchJson(`${apiBase}/companies/map`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ url })
            });

            pollJob(job.job_id, status);
          } catch (error) {
            status.textContent = error.message;
          }
        }

        async function loadCompanies() {
          const companies = await fetchJson(`${apiBase}/companies`);
          renderCompanies(companies);
        }

        document.getElementById('mapButton').addEventListener('click', mapCompany);
        loadCompanies();
      </script>
    </body>
    </html>
    """


@app.get("/{slug}", response_class=HTMLResponse)
async def company_detail_page(slug: str):
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(
        "<h1>Frontend não compilado</h1>"
        "<p>Execute <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>.</p>",
        status_code=503,
    )

    # Fallback legado inacessível; a rota SPA é resolvida por App.vue.
    return """
    <!doctype html>
    <html lang="pt-BR">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>Company Resume</title>
      <style>
        body { margin: 0; font-family: Arial, sans-serif; background: #020817; color: #e5e7eb; }
        .shell { max-width: 980px; margin: 0 auto; padding: 32px 18px 60px; }
        .header { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 22px; }
        .title { font-size: 2rem; margin: 0; }
        .subtitle { color: #94a3b8; margin-top: 6px; }
        .card { background: rgba(17,24,39,0.95); border: 1px solid rgba(56,189,248,0.2); border-radius: 16px; padding: 18px; }
        .meta { color: #cbd5e1; margin: 8px 0; }
        .actions { display: flex; gap: 10px; margin: 14px 0 18px; }
        button { padding: 12px 16px; border: 0; border-radius: 10px; cursor: pointer; font-weight: 700; }
        .primary { background: #38bdf8; color: #03131d; }
        .secondary { background: rgba(148,163,184,0.2); color: #e5e7eb; }
        .danger { background: #ef4444; color: white; }
        textarea { width: 100%; min-height: 90px; border-radius: 12px; background: rgba(2,6,23,0.9); color: #e5e7eb; border: 1px solid rgba(148,163,184,0.2); padding: 12px; }
        .chat-output { margin-top: 14px; background: rgba(2,6,23,0.8); border-radius: 12px; padding: 14px; min-height: 150px; white-space: pre-wrap; line-height: 1.6; }
        .source-list { margin-top: 12px; color: #cbd5e1; }
        .suggestions { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }
        .suggestions button { background:rgba(56,189,248,.12); color:#bae6fd; border:1px solid rgba(56,189,248,.25); }
      </style>
    </head>
    <body>
      <div class="shell">
        <div class="header">
          <div>
            <h1 class="title" id="companyName">Empresa</h1>
            <div class="subtitle" id="companyDomain">domínio</div>
          </div>
          <button class="secondary" onclick="window.location.href='/'">← Voltar</button>
        </div>
        <div class="card">
          <div class="meta" id="companyMeta">Carregando...</div>
          <div class="actions">
            <button class="primary" id="refreshBtn">Atualizar</button>
            <button class="danger" id="deleteBtn">Excluir</button>
          </div>
          <div class="card" style="margin-top: 16px;">
            <div class="suggestions" id="suggestions"></div>
            <textarea id="questionInput" placeholder="Pergunte algo sobre a empresa..."></textarea>
            <div class="actions" style="margin-bottom:0;">
              <button class="primary" id="askBtn">Perguntar</button>
            </div>
            <div id="chatOutput" class="chat-output">Consultando o perfil corporativo...</div>
            <div id="sourceList" class="source-list"></div>
          </div>
        </div>
      </div>
      <script>
        const slug = window.location.pathname.replace(/^\\/+|\\/+$/g, '');
        if (!slug || slug.startsWith('api') || slug.includes('.')) {
          window.location.href = '/';
        }
        const apiBase = '/api';
        const history = [];
        const defaultSuggestedQuestions = [
          'Quem é esta empresa?', 'O que ela faz?', 'Quais serviços oferece?',
          'Quais diferenciais ela declara?', 'O que devo saber antes de uma entrevista?'
        ];

        function renderSuggestedQuestions(questions) {
          const suggestions = document.getElementById('suggestions');
          suggestions.innerHTML = '';
          (questions && questions.length ? questions : defaultSuggestedQuestions).forEach(question => {
            const button = document.createElement('button');
            button.textContent = question;
            button.addEventListener('click', () => {
              document.getElementById('questionInput').value = question;
              askQuestion();
            });
            suggestions.appendChild(button);
          });
        }

        async function fetchJson(url, options = {}) {
          const response = await fetch(url, options);
          if (!response.ok) {
            const text = await response.text();
            throw new Error(text || 'Erro da API');
          }
          return response.json();
        }

        async function loadCompany() {
          try {
            const company = await fetchJson(`${apiBase}/companies/${slug}`);
            document.getElementById('companyName').textContent = company.name;
            document.getElementById('companyDomain').textContent = company.domain;
            document.getElementById('companyMeta').textContent = `${company.pages_processed || 0} páginas processadas • atualizado em ${new Date(company.mapped_at || Date.now()).toLocaleDateString('pt-BR')}`;
            document.getElementById('chatOutput').textContent = 'Pode me perguntar coisas como: Quem é a empresa? O que ela faz? Quais serviços oferece?';
            renderSuggestedQuestions(company.suggested_questions);

            document.getElementById('refreshBtn').onclick = async () => {
              const job = await fetchJson(`${apiBase}/companies/${company.slug}/refresh`, { method: 'POST' });
              document.getElementById('chatOutput').textContent = 'Atualizando mapeamento...';
              const interval = setInterval(async () => {
                const jobStatus = await fetchJson(`${apiBase}/jobs/${job.job_id}`);
                if (jobStatus.status === 'completed') {
                  clearInterval(interval);
                  document.getElementById('chatOutput').textContent = 'Empresa atualizada com sucesso.';
                  loadCompany();
                } else if (jobStatus.status === 'failed') {
                  clearInterval(interval);
                  document.getElementById('chatOutput').textContent = jobStatus.error || 'Falha ao atualizar.';
                }
              }, 2000);
            };

            document.getElementById('deleteBtn').onclick = async () => {
              const confirmed = window.confirm(`Tem certeza que deseja excluir ${company.name}?\n\nTodo o conteúdo processado, perfil, chunks e embeddings serão removidos.`);
              if (!confirmed) return;
              await fetchJson(`${apiBase}/companies/${company.slug}`, { method: 'DELETE' });
              window.location.href = '/';
            };
          } catch (error) {
            document.getElementById('companyName').textContent = 'Empresa não encontrada';
            document.getElementById('chatOutput').textContent = error.message;
          }
        }

        async function askQuestion() {
          const question = document.getElementById('questionInput').value.trim();
          if (!question) return;
          const output = document.getElementById('chatOutput');
          output.textContent = 'Consultando o perfil corporativo...';
          const result = await fetchJson(`${apiBase}/companies/${slug}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, history: history.slice(-6) })
          });
          output.textContent = result.answer;
          history.push({ role: 'user', content: question }, { role: 'assistant', content: result.answer });
          if (result.sources && result.sources.length) {
            const sourceList = document.getElementById('sourceList');
            sourceList.textContent = 'Fontes:';
            result.sources.forEach(url => {
              const item = document.createElement('div');
              const link = document.createElement('a');
              link.href = url;
              link.textContent = url;
              link.target = '_blank';
              link.rel = 'noopener noreferrer';
              item.appendChild(link);
              sourceList.appendChild(item);
            });
          } else {
            document.getElementById('sourceList').innerHTML = '';
          }
        }

        document.getElementById('askBtn').addEventListener('click', askQuestion);
        renderSuggestedQuestions(defaultSuggestedQuestions);
        loadCompany();
      </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=61080, reload=True)
