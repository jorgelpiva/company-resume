from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from ddgs import DDGS

from app.gemini_client import generate_grounded_content, get_api_key


SITE_TYPES = {
    "empresa", "portal_de_conteudo", "marketplace", "ecommerce",
    "instituicao_educacional", "organizacao_sem_fins_lucrativos",
    "produto_ou_servico", "governo", "outro",
}

QUESTION_TEMPLATES = {
    "portal_de_conteudo": [
        "Quais notícias estão em destaque?",
        "Quais editorias e temas o portal cobre?",
        "Quais marcas ou canais fazem parte do portal?",
    ],
    "marketplace": [
        "Como funciona o marketplace?",
        "Quais produtos e serviços a plataforma oferece?",
        "Quais diferenciais de compra, venda e entrega são apresentados?",
    ],
    "ecommerce": [
        "O que a loja vende?",
        "Quais categorias de produtos estão disponíveis?",
        "Quais condições de compra e entrega são apresentadas?",
    ],
}


def _clean_json_response(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("A pesquisa não retornou um objeto JSON")
    return data


def _short_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _string_list(value: Any, *, limit: int, item_limit: int = 180) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_short_text(item, item_limit) for item in value[:limit] if _short_text(item, item_limit)]


def _normalize(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", ascii_value)


def _classify_site(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    if any(term in normalized for term in ("marketplace", "conecta compradores e vendedores", "comprar e vender")):
        return "marketplace"
    if any(term in normalized for term in ("portal de noticias", "jornalismo", "noticias, esportes", "noticias e entretenimento")):
        return "portal_de_conteudo"
    if any(term in normalized for term in ("comercio eletronico", "e-commerce", "loja online")):
        return "ecommerce"
    if any(term in normalized for term in ("universidade", "faculdade", "instituicao de ensino")):
        return "instituicao_educacional"
    if any(term in normalized for term in ("governo", "prefeitura", "ministerio", "autarquia")):
        return "governo"
    return "empresa"


def _entity_from_title(title: str, domain: str) -> str:
    cleaned = re.split(r"\s+[|–—-]\s+", title, maxsplit=1)[0].strip()
    cleaned = re.sub(
        r"^(?:tudo(?:\s+o\s+que\s+você\s+precisa\s+saber)?\s+sobre|conheça|conheca)\s+(?:o|a)?\s*",
        "",
        cleaned,
        flags=re.I,
    )
    return cleaned[:120] or domain.split(".", 1)[0]


def _questions_for(site_type: str, entity_name: str) -> list[str]:
    if site_type in QUESTION_TEMPLATES:
        return QUESTION_TEMPLATES[site_type]
    return [
        f"Quem é {entity_name}?",
        "O que essa organização faz?",
        "Quais produtos ou serviços são apresentados?",
    ]


def _search_web(query: str) -> list[dict[str, str]]:
    # Backends isolados: a indisponibilidade de um não invalida os demais.
    for backend in ("wikipedia", "bing", "brave", "duckduckgo"):
        try:
            raw_results = DDGS(timeout=10).text(
                query,
                region="br-pt",
                safesearch="moderate",
                max_results=8,
                backend=backend,
            )
        except Exception:
            continue
        results = [
            {
                "url": _short_text(item.get("href"), 1000),
                "title": _short_text(item.get("title"), 200),
                "snippet": _short_text(item.get("body"), 700),
            }
            for item in raw_results
            if isinstance(item, dict)
            and str(item.get("href", "")).startswith(("http://", "https://"))
            and item.get("title")
            and item.get("body")
        ]
        if results:
            return results
    return []


async def _public_search_context(domain: str) -> dict[str, Any] | None:
    name_hint = re.sub(r"[-_]", " ", domain.split(".", 1)[0]).strip()
    query = f'"{name_hint}" quem é empresa marketplace portal o que faz'
    results = await asyncio.to_thread(_search_web, query)
    return _build_public_context(domain, query, results)


def _build_public_context(
    domain: str,
    query: str,
    results: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not results:
        return None

    name_hint = re.sub(r"[-_]", " ", domain.split(".", 1)[0]).strip()
    name_key = _normalize(name_hint)
    def result_score(item: dict[str, str]) -> int:
        parsed = urlparse(item["url"])
        host = parsed.hostname or ""
        institutional = any(term in f"{parsed.path} {item['title']}".lower() for term in ("institucional", "quem-somos", "sobre"))
        return (
            (10 if domain in host and institutional else 0)
            + (5 if "wikipedia.org" in host else 0)
            + (3 if domain in host else 0)
            + (2 if name_key in _normalize(item["title"]) else 0)
        )
    results.sort(
        key=result_score,
        reverse=True,
    )
    evidence = " ".join(f"{item['title']} {item['snippet']}" for item in results[:5])
    site_type = _classify_site(evidence)
    primary = results[0]
    entity_name = _entity_from_title(primary["title"], domain)
    summary_items = results[:1] if len(primary["snippet"]) >= 300 else results[:2]
    summary = _short_text(" ".join(item["snippet"] for item in summary_items), 1200)
    return {
        "entity_name": entity_name,
        "site_type": site_type,
        "summary": summary,
        "organization_name": entity_name,
        "relationship": f"O domínio {domain} está associado a {entity_name}, segundo os resultados da pesquisa pública.",
        "content_focus": [],
        "suggested_questions": _questions_for(site_type, entity_name),
        "confidence": "media" if len(results) >= 2 else "baixa",
        "sources": results[:5],
        "search_queries": [query],
        "model": "",
        "provider": "ddgs",
    }


def _normalize_grounded_context(data: dict[str, Any], grounded: dict[str, Any]) -> dict[str, Any]:
    site_type = _short_text(data.get("site_type"), 60)
    if site_type not in SITE_TYPES:
        site_type = "outro"
    confidence = _short_text(data.get("confidence"), 10).lower()
    if confidence not in {"alta", "media", "baixa"}:
        confidence = "baixa"
    sources = [
        {"url": _short_text(item.get("url"), 1000), "title": _short_text(item.get("title"), 200), "snippet": ""}
        for item in grounded.get("sources", [])[:8]
        if isinstance(item, dict) and str(item.get("url", "")).startswith(("http://", "https://"))
    ]
    entity_name = _short_text(data.get("entity_name"), 120)
    return {
        "entity_name": entity_name,
        "site_type": site_type,
        "summary": _short_text(data.get("summary"), 1200),
        "organization_name": _short_text(data.get("organization_name"), 160),
        "relationship": _short_text(data.get("relationship"), 600),
        "content_focus": _string_list(data.get("content_focus"), limit=6),
        "suggested_questions": _string_list(data.get("suggested_questions"), limit=5) or _questions_for(site_type, entity_name),
        "confidence": confidence,
        "sources": sources,
        "search_queries": _string_list(grounded.get("queries"), limit=6),
        "model": grounded.get("model", ""),
        "provider": "gemini_google_search",
    }


async def research_company_context(url: str, domain: str) -> dict[str, Any] | None:
    """Identifica a entidade; usa Gemini grounding e busca pública como fallback."""
    if get_api_key():
        prompt = f"""Pesquise rapidamente a identidade do site {url} (domínio {domain}) antes de rastreá-lo.
Priorize fontes oficiais e primárias. Distinga o site, a marca, a empresa operadora e eventual grupo controlador.
Não presuma que todo domínio representa uma empresa: ele pode ser portal, marketplace, produto, governo ou instituição.
Retorne SOMENTE JSON válido com: entity_name, site_type, summary, organization_name, relationship,
content_focus (lista), suggested_questions (lista) e confidence (alta, media ou baixa).
site_type deve ser: empresa, portal_de_conteudo, marketplace, ecommerce, instituicao_educacional,
organizacao_sem_fins_lucrativos, produto_ou_servico, governo ou outro.
"""
        try:
            grounded = await generate_grounded_content(
                prompt,
                generation_config={"temperature": 0.1, "maxOutputTokens": 1200},
                timeout=60,
            )
            return _normalize_grounded_context(_clean_json_response(grounded["text"]), grounded)
        except Exception:
            pass
    return await _public_search_context(domain)
