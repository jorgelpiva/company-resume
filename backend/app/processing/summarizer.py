from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from app.gemini_client import generate_content, get_api_key


IDENTITY_ROUTE_MARKERS = {
    "about", "about-us", "company", "empresa", "institucional", "quem-somos",
    "sobre", "sobre-nos", "nossa-historia", "nossa_historia",
}

IDENTITY_SUBDOMAIN_MARKERS = {
    "about", "careers", "carreiras", "company", "corporate", "institucional",
    "investor", "investors", "ri",
}


def is_institutional_identity_path(path: str) -> bool:
    """Aceita rotas da entidade raiz, não `/produto/about` de um subproduto."""
    segments = [segment.lower() for segment in (path or "").split("/") if segment]
    if not segments:
        return False
    if segments[0] in IDENTITY_ROUTE_MARKERS:
        return True
    locale_pattern = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.I)
    if len(segments) >= 2 and locale_pattern.fullmatch(segments[0]):
        return segments[1] in IDENTITY_ROUTE_MARKERS
    if (
        len(segments) >= 3
        and segments[0] == "intl"
        and locale_pattern.fullmatch(segments[1])
    ):
        return segments[2] in IDENTITY_ROUTE_MARKERS
    return False


def is_institutional_identity_host(host: str, root_domain: str) -> bool:
    """Aceita o domínio raiz e subdomínios dedicados à identidade institucional."""
    canonical_host = (host or "").lower().strip(".").removeprefix("www.")
    canonical_root = (root_domain or "").lower().strip(".").removeprefix("www.")
    if not canonical_host or not canonical_root:
        return False
    if canonical_host == canonical_root:
        return True
    suffix = f".{canonical_root}"
    if not canonical_host.endswith(suffix):
        return False
    subdomain = canonical_host.removesuffix(suffix)
    labels = [label for label in subdomain.split(".") if label]
    return bool(labels) and all(label in IDENTITY_SUBDOMAIN_MARKERS for label in labels)


def _infer_root_domain(pages: list[dict[str, Any]]) -> str:
    hosts = [
        (urlparse(str(page.get("url") or "")).hostname or "").lower().removeprefix("www.")
        for page in pages
    ]
    hosts = [host for host in hosts if host]
    return min(hosts, key=lambda host: (len(host.split(".")), len(host))) if hosts else ""


def _page_entity_markers(page: dict[str, Any], root_domain: str) -> set[str]:
    title = re.split(r"\s+[|–—-]\s+", str(page.get("title") or ""), maxsplit=1)[0]
    candidates = [title, (root_domain or "").split(".", 1)[0]]
    generic = {"home", "inicio", "loja", "pagina", "site", "store", "welcome"}
    markers: set[str] = set()
    for candidate in candidates:
        normalized = re.sub(r"[^a-z0-9]+", " ", candidate.lower()).strip()
        if len(normalized) >= 3 and normalized not in generic:
            markers.add(normalized)
    return markers


def _homepage_signal_refers_to_entity(
    page: dict[str, Any],
    content: str,
    root_domain: str,
    signal_pattern: re.Pattern[str],
) -> bool:
    markers = _page_entity_markers(page, root_domain)
    if not markers:
        return False
    normalized = re.sub(r"\s+", " ", content.lower())
    for match in signal_pattern.finditer(normalized):
        window = normalized[max(0, match.start() - 140):match.end() + 140]
        if any(re.search(rf"\b{re.escape(marker)}\b", window) for marker in markers):
            return True
    return False


def summarize_text(text: str, max_sentences: int = 5, max_chars: int = 1800) -> str:
    plain = re.sub(r"^#{1,6}\s+", "", text or "", flags=re.M)
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n\n+", plain) if len(item.strip()) >= 30]
    if not sentences:
        return plain[:max_chars].strip()
    words = re.findall(r"[\wÀ-ÿ]{4,}", plain.lower())
    frequencies = Counter(words)
    ranked = sorted(
        enumerate(sentences),
        key=lambda item: sum(frequencies[word] for word in re.findall(r"[\wÀ-ÿ]{4,}", item[1].lower())) / max(len(item[1]), 1),
        reverse=True,
    )[:max_sentences]
    selected = [sentence for _, sentence in sorted(ranked)]
    summary = " ".join(selected)
    return summary[:max_chars].rsplit(" ", 1)[0].strip() if len(summary) > max_chars else summary


def summarize_page(chunks: list[str]) -> str:
    chunk_summaries = [summarize_text(chunk, max_sentences=2, max_chars=600) for chunk in chunks]
    return summarize_text(" ".join(chunk_summaries), max_sentences=6, max_chars=2400)


def has_institutional_identity(
    pages: list[dict[str, Any]],
    root_domain: str | None = None,
) -> bool:
    """Detecta identidade explícita no RAG, sem confundir catálogo com página institucional."""
    canonical_root = (root_domain or _infer_root_domain(pages)).lower().removeprefix("www.")
    title_markers = ("quem somos", "sobre nós", "sobre a empresa", "institucional", "nossa história")
    direct_identity_phrases = re.compile(
        r"\b(?:somos\s+(?:uma|um|a|o)|we\s+are\s+(?:an?|the)|"
        r"é\s+(?:uma|um)\s+(?:empresa|organização|plataforma)|"
        r"quem\s+somos|who\s+we\s+are|"
        r"nossa\s+(?:missão|história|visão)|our\s+(?:mission|history|vision|values))\b",
        re.I,
    )
    institutional_identity_phrases = re.compile(
        rf"(?:{direct_identity_phrases.pattern}|\b(?:foi\s+fundad[ao]|fundad[ao]\s+em|was\s+founded|founded\s+in)\b)",
        re.I,
    )
    founding_phrases = re.compile(
        r"\b(?:foi\s+fundad[ao]|fundad[ao]\s+em|was\s+founded|founded\s+in)\b",
        re.I,
    )
    for page in pages:
        url = page.get("url", "")
        parsed_url = urlparse(url)
        path = parsed_url.path.lower()
        host = (parsed_url.hostname or "").lower().removeprefix("www.")
        if not is_institutional_identity_host(host, canonical_root):
            continue
        path_segments = [segment for segment in path.split("/") if segment]
        title = page.get("title", "").lower()
        content = page.get("content", "")
        content_sample = content[:5000]
        has_direct_identity_signal = bool(direct_identity_phrases.search(content_sample))
        has_institutional_identity_signal = bool(institutional_identity_phrases.search(content_sample))
        title_identifies_entity = (
            any(marker in title for marker in title_markers)
            and len(path_segments) <= 1
        )
        if (
            len(content) >= 200
            and has_institutional_identity_signal
            and (is_institutional_identity_path(path) or title_identifies_entity)
        ):
            return True
        if path in {"", "/"}:
            if has_direct_identity_signal and _homepage_signal_refers_to_entity(
                page,
                content_sample,
                canonical_root,
                direct_identity_phrases,
            ):
                return True
            if _homepage_signal_refers_to_entity(
                page,
                content_sample,
                canonical_root,
                founding_phrases,
            ):
                return True
    return False


def assess_site_content(
    pages: list[dict[str, Any]],
    root_domain: str | None = None,
) -> dict[str, Any]:
    """Separa volume textual, identidade institucional e estrutura observada."""
    texts = [re.sub(r"\s+", " ", str(page.get("content") or "")).strip() for page in pages]
    combined = " ".join(text for text in texts if text)
    homepage_texts = [
        text
        for page, text in zip(pages, texts)
        if urlparse(str(page.get("url") or "")).path in {"", "/"}
    ]
    homepage_text = " ".join(homepage_texts)
    unique_words = {
        word.lower()
        for word in re.findall(r"[\wÀ-ÿ]{3,}", combined)
    }
    sparse = len(combined) < 400 or (len(combined) < 1200 and len(unique_words) < 35)
    has_identity = has_institutional_identity(pages, root_domain=root_domain)
    status = "escasso" if sparse else ("sem_identidade_institucional" if not has_identity else "suficiente")
    return {
        "status": status,
        "pages": len(pages),
        "characters": len(combined),
        "unique_words": len(unique_words),
        "homepage_characters": len(homepage_text),
        "homepage_sparse": bool(homepage_texts) and len(homepage_text) < 400,
        "has_institutional_identity": has_identity,
    }


def build_site_content_observation(
    pages: list[dict[str, Any]],
    *,
    external_identity_verified: bool,
    root_domain: str | None = None,
) -> str:
    assessment = assess_site_content(pages, root_domain=root_domain)
    if assessment["status"] == "suficiente":
        return ""

    page_descriptions: list[str] = []
    ordered_pages = sorted(
        pages,
        key=lambda page: urlparse(str(page.get("url") or "")).path not in {"", "/"},
    )
    for page in ordered_pages[:5]:
        title = re.sub(r"\s+", " ", str(page.get("title") or "Página")).strip()
        path = urlparse(str(page.get("url") or "")).path or "/"
        label = "página inicial" if path == "/" else path
        description = f"{title} ({label})"
        if description not in page_descriptions:
            page_descriptions.append(description)
    structure = ", ".join(page_descriptions) or "nenhuma página com texto aproveitável"

    if assessment["homepage_sparse"] and assessment["status"] != "escasso":
        opening = (
            "A página inicial forneceu pouco conteúdo textual relevante. As demais páginas rastreadas "
            "trouxeram conteúdo de produtos ou serviços, mas não identidade institucional explícita."
        )
    elif assessment["status"] == "escasso":
        opening = "O próprio site forneceu pouco conteúdo textual relevante para compor um perfil institucional."
    else:
        opening = "O site trouxe conteúdo público, mas não apresentou identidade institucional explícita."
    if assessment["has_institutional_identity"]:
        verification = "A identidade foi encontrada no próprio conteúdo, mas as demais informações institucionais são limitadas."
    elif external_identity_verified:
        verification = "Por isso, a identidade foi complementada por pesquisa pública com fonte verificável."
    else:
        verification = "A pesquisa complementar não encontrou evidência verificável suficiente para identificar a entidade com segurança."
    return f"{opening} Estrutura útil observada: {structure}. {verification}"


def add_site_content_observation(
    profile: str,
    observation: str,
    *,
    has_external_identity: bool,
    has_local_identity: bool = False,
) -> str:
    if not observation:
        return profile
    executive = _section_body(profile, "Resumo executivo")
    if observation not in executive:
        executive_body = "\n\n".join(part for part in (executive, observation) if part)
        profile = _replace_profile_section(profile, "Resumo executivo", executive_body)
    who_is = _section_body(profile, "Quem é")
    if not has_external_identity and (
        not has_local_identity
        or not who_is
        or "Não identificado no conteúdo público analisado" in who_is
    ):
        profile = _replace_profile_section(profile, "Quem é", observation)
    return profile


def _replace_profile_section(profile: str, heading: str, body: str) -> str:
    pattern = rf"(?ms)(^##\s+{re.escape(heading)}\s*$\n).*?(?=^##\s+|\Z)"
    return re.sub(pattern, lambda match: f"{match.group(1)}{body.strip()}\n\n", profile, count=1)


def _section_body(profile: str, heading: str) -> str:
    match = re.search(rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)", profile)
    return match.group(1).strip() if match else ""


def is_usable_external_identity_context(context: dict[str, Any] | None) -> bool:
    """Aceita identidade externa apenas quando há evidência mínima verificável."""
    if not isinstance(context, dict):
        return False

    summary = context.get("summary")
    entity_name = context.get("entity_name")
    confidence = context.get("confidence")
    sources = context.get("sources")
    has_http_source = isinstance(sources, list) and any(
        isinstance(item, dict)
        and isinstance(item.get("url"), str)
        and item["url"].strip().lower().startswith(("http://", "https://"))
        for item in sources
    )
    return (
        isinstance(summary, str)
        and bool(summary.strip())
        and isinstance(entity_name, str)
        and bool(entity_name.strip())
        and isinstance(confidence, str)
        and confidence.strip().lower() in {"alta", "media"}
        and has_http_source
    )


def enrich_profile_with_external_identity(profile: str, context: dict[str, Any] | None) -> str:
    """Preenche somente a identidade ausente, preservando o restante do perfil extrativo."""
    if not is_usable_external_identity_context(context):
        return profile
    sources = [
        item["url"].strip()
        for item in context.get("sources", [])[:3]
        if isinstance(item, dict)
        and isinstance(item.get("url"), str)
        and item["url"].strip().lower().startswith(("http://", "https://"))
    ]
    source_lines = "\n".join(f"Fonte externa: {url}" for url in dict.fromkeys(sources))
    identity = "\n\n".join(
        part for part in (context.get("summary", ""), context.get("relationship", ""), source_lines) if part
    )
    executive = _section_body(profile, "Resumo executivo")
    if executive and "Não identificado no conteúdo público analisado" not in executive:
        identity = f"{identity}\n\nNo site mapeado, foram observados: {executive}"
    profile = _replace_profile_section(profile, "Resumo executivo", identity)
    return _replace_profile_section(profile, "Quem é", identity)


async def summarize_company_profile(
    company: dict[str, Any],
    pages: list[dict[str, Any]],
    fallback: str,
    research_context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    root_domain = str(company.get("domain") or "") or None
    rag_has_identity = has_institutional_identity(pages, root_domain=root_domain)
    effective_context = (
        research_context
        if not rag_has_identity and is_usable_external_identity_context(research_context)
        else None
    )
    site_observation = build_site_content_observation(
        pages,
        external_identity_verified=bool(effective_context),
        root_domain=root_domain,
    )
    enriched_fallback = enrich_profile_with_external_identity(fallback, effective_context)
    enriched_fallback = add_site_content_observation(
        enriched_fallback,
        site_observation,
        has_external_identity=bool(effective_context),
        has_local_identity=rag_has_identity,
    )
    if not get_api_key():
        return enriched_fallback, "extractive+external" if effective_context else "extractive"

    source_material = "\n\n".join(
        f"URL: {page.get('url', '')}\nTÍTULO: {page.get('title', '')}\nRESUMO: {page.get('summary', '')}"
        for page in pages
        if page.get("summary")
    )
    external_context = json.dumps(effective_context or {}, ensure_ascii=False, indent=2)
    prompt = f"""Crie um perfil corporativo em português, estritamente grounded nos resumos de páginas fornecidos.
IDENTIDADE INSTITUCIONAL ENCONTRADA NO RAG: {"sim" if rag_has_identity else "não"}.
Se a identidade foi encontrada no RAG, dê prioridade total a ela e ignore o contexto externo na narrativa.
Se não foi encontrada, use o CONTEXTO EXTERNO VERIFICADO apenas para preencher Resumo executivo e Quem é.
Não misture fatos externos com fatos encontrados no site. Para toda afirmação vinda desse contexto, inclua uma linha 'Fonte externa: URL'.
Não invente, não faça inferências genéricas e não use outro conhecimento externo. Quando não houver evidência, escreva exatamente: Não identificado no conteúdo público analisado.
Mantenha as URLs como linhas 'Fonte: URL' próximas das afirmações relevantes.
Construa Resumo executivo e Quem é como uma narrativa curta e coesa: primeiro explique a natureza da organização, depois conecte isso ao que foi observado no site.
Não comece dizendo que faltam informações quando o contexto externo resolveu a identidade. Não transforme catálogos em listas extensas de itens; agrupe-os em categorias e capacidades.
Quando AVALIAÇÃO DO CONTEÚDO LOCAL não estiver vazia, inclua essa limitação depois da identidade no Resumo executivo. Separe claramente o que veio do site do que foi verificado externamente.
Nunca use ausência ou escassez de conteúdo como evidência do tipo de negócio.
Seja conciso, específico e elimine repetições.

Use exatamente esta estrutura Markdown:
# {company.get('name', 'Empresa')}
## Resumo executivo
## Quem é
## História
## Missão
## Visão
## Valores
## Posicionamento
## Principais serviços
## Principais produtos
## Tecnologias mencionadas
## Segmentos atendidos
## Clientes mencionados
## Cases mencionados
## Diferenciais declarados
## Cultura
## Carreiras
## Conteúdos e temas recorrentes
## Informações relevantes para candidatos
## Pontos não esclarecidos pelo site
## Fontes analisadas
## Data do mapeamento

Empresa: {company.get('name', 'Empresa')}
Data: {company.get('mapped_at', '')}

RESUMOS DE PÁGINAS:
{source_material[:50000]}

CONTEXTO EXTERNO VERIFICADO:
{external_context[:12000]}

AVALIAÇÃO DO CONTEÚDO LOCAL:
{site_observation or "Conteúdo local suficiente para a identidade institucional."}
"""
    try:
        profile = (await generate_content(
            prompt,
            generation_config={"temperature": 0.1, "maxOutputTokens": 6000},
            timeout=60,
        )).strip()
        profile = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", profile, flags=re.I)
        if profile.startswith("# ") and "## Fontes analisadas" in profile:
            profile = add_site_content_observation(
                profile + "\n",
                site_observation,
                has_external_identity=bool(effective_context),
                has_local_identity=rag_has_identity,
            )
            return profile, "gemini"
    except Exception:
        pass
    return enriched_fallback, "extractive+external" if effective_context else "extractive"
