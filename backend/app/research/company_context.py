from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from typing import Any
from urllib.parse import urlparse

from ddgs import DDGS

from app.gemini_client import generate_grounded_content, get_api_key


logger = logging.getLogger(__name__)


SITE_TYPES = {
    "empresa", "portal_de_conteudo", "marketplace", "ecommerce",
    "instituicao_educacional", "organizacao_sem_fins_lucrativos",
    "produto_ou_servico", "mecanismo_de_busca", "governo", "outro",
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
    "mecanismo_de_busca": [
        "Como funciona o mecanismo de busca?",
        "Quais recursos de pesquisa são apresentados?",
        "Que tipos de conteúdo podem ser pesquisados?",
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


def _page_evidence_items(page_evidence: Any, domain: str) -> list[dict[str, str]]:
    if not page_evidence:
        return []
    raw_items = page_evidence if isinstance(page_evidence, list) else [page_evidence]
    items: list[dict[str, str]] = []
    for raw in raw_items[:8]:
        if isinstance(raw, dict):
            content = " ".join(
                str(raw.get(key, ""))
                for key in ("meta_description", "summary", "content")
                if raw.get(key)
            )
            items.append({
                "url": _short_text(raw.get("url") or f"https://{domain}/", 1000),
                "title": _short_text(raw.get("title"), 200),
                "snippet": _short_text(content, 1800),
            })
        elif str(raw).strip():
            items.append({
                "url": f"https://{domain}/",
                "title": "",
                "snippet": _short_text(raw, 1800),
            })
    return items


def _format_page_evidence(page_evidence: Any, domain: str) -> str:
    return "\n\n".join(
        "\n".join((
            f"URL: {item['url']}",
            f"TÍTULO: {item['title']}",
            f"CONTEÚDO: {item['snippet']}",
        ))
        for item in _page_evidence_items(page_evidence, domain)
    )[:8000]


def _normalize(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", ascii_value)


def _normalize_text(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_value)).strip()


def _type_signals(text: str) -> dict[str, int]:
    """Extrai sinais explícitos; palavras de categoria isoladas recebem pouco peso."""
    normalized = _normalize_text(text)
    signals: dict[str, int] = {}

    search_terms = (
        "mecanismo de busca", "motor de busca", "ferramenta de busca na internet",
        "servico de busca na internet", "buscador da internet", "buscador", "web search engine",
        "internet search engine", "search engine", "google search", "pesquisa google",
    )
    if any(term in normalized for term in search_terms):
        signals["mecanismo_de_busca"] = 6
    elif (
        re.search(
            r"\b(?:pesquisar|buscar|search)\b.{0,220}\b(?:informacoes|information|respostas|results|web|internet|imagens|images|videos)\b",
            normalized,
        )
        or (
            "maneiras de pesquisar" in normalized
            and "encontrar respostas" in normalized
        )
    ):
        signals["mecanismo_de_busca"] = 5

    strong_marketplace_patterns = (
        r"\bconecta\w*\s+(?:os\s+)?compradores?\s+(?:a|e|com)\s+(?:os\s+)?vendedores?\b",
        r"\bconecta\w*\s+(?:os\s+)?vendedores?\s+(?:a|e|com)\s+(?:os\s+)?compradores?\b",
        r"\bplataforma\b.{0,90}\bcomprar\s+e\s+vender\b",
        r"\bcomprar\s+e\s+vender\b",
        r"\bmarketplace\s+de\s+comercio\s+eletronico\b",
        r"\b(?:atua|opera|funciona|classificad[oa]|descrit[oa])\s+como\s+(?:um\s+|uma\s+)?marketplace\b",
        r"\b(?:e|eh|is)\s+(?:um\s+|uma\s+)?(?:empresa\b.{0,80}\b)?marketplace\b",
        r"\bvendedores?\s+(?:terceiros|parceiros)\b",
        r"\blojistas?\s+(?:terceiros|parceiros)\b",
    )
    if any(re.search(pattern, normalized) for pattern in strong_marketplace_patterns):
        signals["marketplace"] = 6
    elif re.search(r"\bmarketplace\b", normalized):
        # Um nome de produto como "Cloud Marketplace" não basta para classificar
        # a entidade raiz como um marketplace.
        signals["marketplace"] = 1

    if any(term in normalized for term in (
        "portal de noticias", "portal de conteudo", "site de noticias", "jornalismo",
        "noticias esportes", "noticias e entretenimento",
    )):
        signals["portal_de_conteudo"] = 5
    if any(term in normalized for term in (
        "comercio eletronico", "e commerce", "loja online", "loja virtual",
    )):
        signals["ecommerce"] = 4
    if any(term in normalized for term in ("universidade", "faculdade", "instituicao de ensino")):
        signals["instituicao_educacional"] = 5
    if any(term in normalized for term in ("governo", "prefeitura", "ministerio", "autarquia")):
        signals["governo"] = 5
    return signals


def _entity_from_title(title: str, domain: str) -> str:
    cleaned = re.split(r"\s+[|–—-]\s+", title, maxsplit=1)[0].strip()
    cleaned = re.sub(
        r"^(?:tudo(?:\s+o\s+que\s+você\s+precisa\s+saber)?\s+sobre|conheça|conheca)\s+(?:o|a)?\s*",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\s+(?:brasil|brazil|oficial|official)\s*$", "", cleaned, flags=re.I)
    generic_titles = {
        "home", "home page", "homepage", "inicio", "pagina inicial", "site oficial",
        "official site", "welcome",
    }
    domain_name = re.sub(r"[-_]+", " ", domain.removeprefix("www.").split(".", 1)[0]).title()
    if not cleaned or _normalize_text(cleaned) in generic_titles:
        return domain_name[:120]
    return cleaned[:120]


def _questions_for(site_type: str, entity_name: str) -> list[str]:
    if site_type in QUESTION_TEMPLATES:
        return QUESTION_TEMPLATES[site_type]
    return [
        f"Quem é {entity_name}?",
        "O que essa organização faz?",
        "Quais produtos ou serviços são apresentados?",
    ]


def _search_web(query: str, backend: str | None = None) -> list[dict[str, str]]:
    # Backends isolados: a indisponibilidade de um não invalida os demais.
    backends = (backend,) if backend else ("wikipedia", "bing", "brave", "duckduckgo")
    for selected_backend in backends:
        try:
            raw_results = DDGS(timeout=10).text(
                query,
                region="br-pt",
                safesearch="moderate",
                max_results=8,
                backend=selected_backend,
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


def _search_public_context(
    domain: str,
    query: str,
    *,
    deadline: float | None = None,
) -> dict[str, Any] | None:
    best_low_confidence_context: dict[str, Any] | None = None
    for backend in ("wikipedia", "bing", "brave", "duckduckgo"):
        if deadline is not None and time.monotonic() >= deadline:
            break
        results = _search_web(query, backend)
        context = _build_public_context(domain, query, results)
        if context and context.get("confidence") in {"alta", "media"}:
            return context
        if context and best_low_confidence_context is None:
            best_low_confidence_context = context
    return best_low_confidence_context


def _public_context_quality(context: dict[str, Any], domain: str) -> int:
    confidence_score = 20 if context.get("confidence") in {"alta", "media"} else 0
    type_score = 10 if context.get("site_type") not in {None, "", "empresa", "outro"} else 0
    source_hosts = [
        _canonical_host(item.get("url", ""))
        for item in context.get("sources", [])
        if isinstance(item, dict)
    ]
    canonical_domain = domain.lower().removeprefix("www.").rstrip(".")
    if canonical_domain in source_hosts:
        source_score = 15
    elif any(host.endswith(f".{canonical_domain}") for host in source_hosts):
        source_score = 5
    else:
        source_score = 0
    summary_score = 5 if len(context.get("summary", "")) >= 120 else 0
    return confidence_score + type_score + source_score + summary_score


def _search_public_context_queries(domain: str, queries: list[str]) -> dict[str, Any] | None:
    best_context: dict[str, Any] | None = None
    best_score = -1
    deadline = time.monotonic() + 30
    for query in queries:
        if time.monotonic() >= deadline:
            break
        context = _search_public_context(domain, query, deadline=deadline)
        if not context:
            continue
        score = _public_context_quality(context, domain)
        if score > best_score:
            best_context, best_score = context, score
        if score >= 50:
            break
    return best_context


async def _public_search_context(domain: str, page_evidence: Any = None) -> dict[str, Any] | None:
    canonical_domain = domain.lower().removeprefix("www.").rstrip(".")
    # A consulta identifica primeiro a entidade exata. Incluir possíveis classes
    # aqui enviesava os resultados para produtos homônimos, como Cloud Marketplace.
    local_items = _page_evidence_items(page_evidence, canonical_domain)
    title_hint = next(
        (
            item["title"] for item in local_items
            if item.get("title") and item["title"].lower() != "página"
        ),
        "",
    )
    query = f'"{canonical_domain}"'
    if title_hint and _normalize(title_hint) != _domain_name_key(canonical_domain):
        query += f' "{title_hint}"'
    query += " como funciona o site"
    identity_query = f'"{canonical_domain}" site oficial o que é'
    offering_query = f'"{canonical_domain}" o que o site oferece'
    return await asyncio.to_thread(
        _search_public_context_queries,
        canonical_domain,
        [query, identity_query, offering_query],
    )


def _canonical_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".").removeprefix("www.")


def _domain_name_key(domain: str) -> str:
    return _normalize(domain.lower().removeprefix("www.").split(".", 1)[0])


def _title_subject(title: str) -> str:
    subject = re.split(r"\s+[|–—-]\s+", title, maxsplit=1)[0].strip()
    subject = re.sub(r"\s*\([^)]*\)\s*$", "", subject).strip()
    subject = re.sub(
        r"^(?:tudo(?:\s+o\s+que\s+você\s+precisa\s+saber)?\s+sobre|conheça|conheca|sobre)\s+(?:o|a)?\s*",
        "",
        subject,
        flags=re.I,
    )
    return subject


def _title_matches_root(title: str, domain: str, entity_name: str = "") -> bool:
    subject_key = _normalize(_title_subject(title))
    if not subject_key:
        return False
    name_key = _domain_name_key(domain)
    entity_key = _normalize(entity_name)
    candidates = {candidate for candidate in (name_key, entity_key) if candidate}
    harmless_suffixes = ("brasil", "brazil", "oficial", "official", "wikipedia")
    service_suffixes = ("search", "busca", "pesquisa")
    return any(
        subject_key == candidate
        or subject_key in {f"{candidate}{suffix}" for suffix in (*harmless_suffixes, *service_suffixes)}
        for candidate in candidates
    )


def _result_score(item: dict[str, str], domain: str) -> int:
    host = _canonical_host(item.get("url", ""))
    canonical_domain = domain.lower().removeprefix("www.").rstrip(".")
    exact_host = host == canonical_domain
    subdomain = host.endswith(f".{canonical_domain}")
    title_matches = _title_matches_root(item.get("title", ""), canonical_domain)
    text = f"{urlparse(item.get('url', '')).path} {item.get('title', '')}".lower()
    institutional = any(term in text for term in ("institucional", "quem-somos", "sobre", "about"))
    name_in_snippet = _domain_name_key(canonical_domain) in _normalize(item.get("snippet", ""))
    return (
        (7 if "wikipedia.org" in host and title_matches else 0)
        + (-3 if "wikipedia.org" in host and not title_matches else 0)
        + (10 if exact_host else 0)
        + (1 if subdomain and not exact_host else 0)
        + (3 if title_matches else 0)
        + (1 if name_in_snippet else 0)
        + (3 if exact_host and institutional else 0)
    )


def _result_is_entity_aligned(item: dict[str, str], domain: str, entity_name: str) -> bool:
    host = _canonical_host(item.get("url", ""))
    canonical_domain = domain.lower().removeprefix("www.").rstrip(".")
    if host == canonical_domain:
        path_segments = [
            segment for segment in urlparse(item.get("url", "")).path.lower().split("/")
            if segment
        ]
        if not path_segments or _title_matches_root(item.get("title", ""), canonical_domain, entity_name):
            return True
        if path_segments[0] in {"about", "about-us", "company", "institucional", "quem-somos", "sobre"}:
            return True
        marketplace_strength = _type_signals(
            f"{item.get('title', '')}. {item.get('snippet', '')}"
        ).get("marketplace", 0)
        return path_segments[0] == "marketplace" and marketplace_strength >= 5
    return _title_matches_root(item.get("title", ""), canonical_domain, entity_name)


def _evidence_weight(item: dict[str, str], domain: str, entity_name: str) -> int:
    if not _result_is_entity_aligned(item, domain, entity_name):
        return 0
    host = _canonical_host(item.get("url", ""))
    canonical_domain = domain.lower().removeprefix("www.").rstrip(".")
    if host == canonical_domain:
        return 5
    if "wikipedia.org" in host:
        return 4
    if host.endswith(f".{canonical_domain}"):
        return 2
    return 3


def _has_compatible_search_text(item: dict[str, str]) -> bool:
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    return len(re.findall(r"[А-Яа-я]", text)) < 5


def _classify_search_results(
    results: list[dict[str, str]],
    domain: str,
    entity_name: str,
) -> str:
    """Combina evidências por resultado, sem misturar entidades ou subprodutos."""
    scores: dict[str, int] = {}
    strong_marketplace_support = False
    for item in results[:5]:
        weight = _evidence_weight(item, domain, entity_name)
        if not weight:
            continue
        signals = _type_signals(f"{item.get('title', '')}. {item.get('snippet', '')}")
        for site_type, strength in signals.items():
            if site_type == "marketplace":
                # Marketplace exige descrição explícita da entidade raiz. Uma
                # ocorrência nominal isolada nunca decide a classificação.
                if strength < 5:
                    continue
                strong_marketplace_support = True
            scores[site_type] = scores.get(site_type, 0) + (strength * weight)

    if not scores:
        return "empresa"
    if not strong_marketplace_support:
        scores.pop("marketplace", None)
    return max(scores, key=scores.get) if scores else "empresa"


def _localized_public_summary(entity_name: str, site_type: str, raw_summary: str) -> str:
    if site_type != "mecanismo_de_busca":
        return raw_summary
    normalized = _normalize_text(raw_summary)
    searchable_items = []
    for terms, label in (
        (("webpage", "pagina", "site"), "páginas da Web"),
        (("image", "imagem"), "imagens"),
        (("video",), "vídeos"),
        (("map", "mapa"), "mapas"),
    ):
        if any(term in normalized for term in terms):
            searchable_items.append(label)
    scope = ", ".join(searchable_items)
    if scope:
        return (
            f"{entity_name} é apresentado pelas fontes como um mecanismo de busca para encontrar "
            f"informações, incluindo {scope}."
        )
    return (
        f"{entity_name} é apresentado pelas fontes como um mecanismo de busca para pesquisar "
        "informações e encontrar respostas."
    )


def _build_public_context(
    domain: str,
    query: str,
    results: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not results:
        return None

    canonical_domain = domain.lower().removeprefix("www.").rstrip(".")
    ranked_results = sorted(results, key=lambda item: _result_score(item, canonical_domain), reverse=True)
    root_results = [
        item for item in ranked_results
        if _result_is_entity_aligned(item, canonical_domain, "")
    ]
    if not root_results:
        return None
    primary = root_results[0]
    entity_name = _entity_from_title(primary["title"], domain)
    aligned_results = [
        item
        for item in ranked_results
        if _result_is_entity_aligned(item, canonical_domain, entity_name)
        and _has_compatible_search_text(item)
    ] or [primary]
    site_type = _classify_search_results(aligned_results, canonical_domain, entity_name)
    representative = aligned_results[0]
    summary_items = aligned_results[:1] if len(representative["snippet"]) >= 300 else aligned_results[:2]
    raw_summary = _short_text(" ".join(item["snippet"] for item in summary_items), 1200)
    summary = _localized_public_summary(entity_name, site_type, raw_summary)
    primary_is_substantial = (
        _result_score(representative, canonical_domain) >= 5
        and len(representative.get("snippet", "")) >= 120
    )
    return {
        "entity_name": entity_name,
        "site_type": site_type,
        "summary": summary,
        "organization_name": entity_name,
        "relationship": (
            f"O domínio {canonical_domain} está associado a {entity_name}, "
            "segundo os resultados da pesquisa pública."
        ),
        "content_focus": [],
        "suggested_questions": _questions_for(site_type, entity_name),
        "confidence": "media" if len(aligned_results) >= 2 or primary_is_substantial else "baixa",
        "sources": aligned_results[:5],
        "search_queries": [query],
        "model": "",
        "provider": "ddgs",
    }


def _normalized_classification_evidence(
    value: Any,
    grounded_sources: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    source_urls = {item.get("url", "") for item in grounded_sources}
    source_hosts = {_canonical_host(url) for url in source_urls if url}
    evidence: list[dict[str, str]] = []
    for item in value[:6]:
        if not isinstance(item, dict):
            continue
        source_url = _short_text(item.get("source_url") or item.get("url"), 1000)
        statement = _short_text(
            item.get("evidence") or item.get("trecho") or item.get("statement") or item.get("claim"),
            500,
        )
        if not source_url.startswith(("http://", "https://")) or not statement:
            continue
        if source_url not in source_urls and _canonical_host(source_url) not in source_hosts:
            continue
        evidence.append({"source_url": source_url, "evidence": statement})
    return evidence


def _verified_grounded_marketplace(
    evidence: list[dict[str, str]],
    sources: list[dict[str, str]],
    local_items: list[dict[str, str]],
    domain: str,
    entity_name: str,
) -> bool:
    source_titles = {item.get("url", ""): item.get("title", "") for item in sources}
    evidence_items = [
        {
            "url": item["source_url"],
            "title": source_titles.get(item["source_url"], ""),
            "snippet": item["evidence"],
        }
        for item in evidence
    ]
    # Títulos de grounding só servem como prova quando eles próprios descrevem a
    # relação bilateral; o simples nome "Marketplace" continua sendo sinal fraco.
    source_title_items = [
        {"url": item.get("url", ""), "title": item.get("title", ""), "snippet": ""}
        for item in sources
    ]
    candidates = [*local_items, *evidence_items, *source_title_items]
    return _classify_search_results(candidates, domain, entity_name) == "marketplace"


def _strong_non_market_type(text: str) -> str | None:
    signals = {
        site_type: strength
        for site_type, strength in _type_signals(text).items()
        if site_type != "marketplace" and strength >= 4
    }
    return max(signals, key=signals.get) if signals else None


def _local_type_summary(
    local_items: list[dict[str, str]],
    entity_name: str,
    site_type: str,
    domain: str,
) -> str:
    descriptions = {
        "mecanismo_de_busca": "um mecanismo de busca",
        "portal_de_conteudo": "um portal de conteúdo",
        "ecommerce": "uma loja on-line",
        "instituicao_educacional": "uma instituição educacional",
        "governo": "um site governamental",
    }
    description = descriptions.get(site_type, "um serviço digital")
    observed = _short_text(
        " ".join(item.get("snippet", "") for item in local_items if item.get("snippet")),
        500,
    )
    base = f"{entity_name or domain} é apresentado no conteúdo da URL solicitada como {description}."
    return f"{base} Conteúdo observado no site: {observed}" if observed else base


def _normalize_grounded_context(
    data: dict[str, Any],
    grounded: dict[str, Any],
    *,
    domain: str = "",
    page_evidence: Any = None,
) -> dict[str, Any]:
    declared_site_type = _short_text(data.get("site_type"), 60)
    if declared_site_type not in SITE_TYPES:
        declared_site_type = "outro"
    confidence = _short_text(data.get("confidence"), 10).lower()
    if confidence not in {"alta", "media", "baixa"}:
        confidence = "baixa"
    sources = [
        {"url": _short_text(item.get("url"), 1000), "title": _short_text(item.get("title"), 200), "snippet": ""}
        for item in grounded.get("sources", [])[:8]
        if isinstance(item, dict) and str(item.get("url", "")).startswith(("http://", "https://"))
    ]
    entity_name = _short_text(data.get("entity_name"), 120)
    if domain and not any(
        _result_is_entity_aligned(source, domain, entity_name)
        for source in sources
    ):
        confidence = "baixa"
    summary = _short_text(data.get("summary"), 1200)
    relationship = _short_text(data.get("relationship"), 600)
    content_focus = _string_list(data.get("content_focus"), limit=6)
    local_items = _page_evidence_items(page_evidence, domain) if domain else []
    local_site_type = (
        _classify_search_results(local_items, domain, entity_name)
        if local_items and domain
        else "empresa"
    )
    claim_text = " ".join((summary, relationship, " ".join(content_focus)))
    claim_non_market_type = _strong_non_market_type(claim_text)
    classification_evidence = _normalized_classification_evidence(
        data.get("classification_evidence"),
        sources,
    )
    marketplace_is_verified = bool(domain) and _verified_grounded_marketplace(
        classification_evidence,
        sources,
        local_items,
        domain,
        entity_name,
    )

    site_type = declared_site_type
    if declared_site_type == "marketplace":
        if claim_non_market_type:
            site_type = (
                local_site_type
                if local_site_type not in {"empresa", "ecommerce"}
                else claim_non_market_type
            )
        elif marketplace_is_verified:
            site_type = "marketplace"
        elif local_site_type != "empresa":
            # O rótulo externo foi rejeitado, mas a própria URL pode sustentar um
            # tipo seguro. Nesse caso descartamos também o resumo contaminado.
            site_type = local_site_type
            summary = _local_type_summary(local_items, entity_name, site_type, domain)
            relationship = f"A classificação usa apenas o conteúdo observado em {domain}."
            confidence = "media" if confidence != "baixa" and sources else "baixa"
        else:
            site_type = "outro"
            confidence = "baixa"
    elif local_site_type not in {"empresa", "ecommerce"}:
        # Evidência extraída da URL solicitada vence uma classificação externa
        # incompatível. E-commerce fica separado porque um marketplace também
        # pode apresentar linguagem de loja/catálogo na home.
        site_type = local_site_type
    elif claim_non_market_type and claim_non_market_type != declared_site_type:
        # Corrige contradições explícitas, como JSON marcado como marketplace ou
        # empresa cuja própria evidência descreve um mecanismo de busca.
        site_type = claim_non_market_type

    template_questions = _questions_for(site_type, entity_name)
    proposed_questions = _string_list(data.get("suggested_questions"), limit=5)
    safe_proposed_questions = [
        question for question in proposed_questions if question in template_questions
    ]
    return {
        "entity_name": entity_name,
        "site_type": site_type,
        "summary": summary,
        "organization_name": _short_text(data.get("organization_name"), 160),
        "relationship": relationship,
        "content_focus": content_focus,
        "classification_evidence": classification_evidence,
        # O modelo nunca cria perguntas para um tipo não validado. Mantemos apenas
        # sugestões que já pertencem ao template seguro da classificação final.
        "suggested_questions": safe_proposed_questions or template_questions,
        "confidence": confidence,
        "sources": sources,
        "search_queries": _string_list(grounded.get("queries"), limit=6),
        "model": grounded.get("model", ""),
        "provider": "gemini_google_search",
    }


async def research_company_context(
    url: str,
    domain: str,
    page_evidence: Any = None,
) -> dict[str, Any] | None:
    """Identifica a entidade; usa Gemini grounding e busca pública como fallback."""
    if get_api_key():
        local_evidence = _format_page_evidence(page_evidence, domain)
        prompt = f"""Identifique a entidade e a finalidade exatas da URL {url} (domínio {domain}).
Priorize fontes oficiais e primárias. Distinga o site, a marca, a empresa operadora e eventual grupo controlador.
site_type descreve exclusivamente o site/serviço acessado pela URL solicitada, não qualquer outro produto,
subdomínio ou serviço da mesma organização. Por exemplo, a existência de um produto chamado Marketplace
não torna o domínio raiz inteiro um marketplace.
Só use marketplace quando uma fonte verificável disser que a entidade raiz intermedeia compradores e
vendedores/lojistas terceiros. Catálogo de produtos, falta de página institucional ou a palavra marketplace
isolada não bastam. Um serviço cuja função principal é pesquisar a Web deve ser mecanismo_de_busca.
Use primeiro a EVIDÊNCIA EXTRAÍDA DO SITE, quando fornecida, e complemente-a com pesquisa apenas para
resolver identidade ou finalidade que permaneçam incertas.
Retorne SOMENTE JSON válido com: entity_name, site_type, summary, organization_name, relationship,
content_focus (lista), confidence (alta, media ou baixa) e classification_evidence (lista de objetos com
source_url e evidence). Cada classification_evidence deve apontar para uma fonte realmente consultada e
registrar a evidência específica que sustenta site_type. Não retorne perguntas; elas serão geradas pelo servidor.
site_type deve ser: empresa, portal_de_conteudo, marketplace, ecommerce, instituicao_educacional,
organizacao_sem_fins_lucrativos, produto_ou_servico, mecanismo_de_busca, governo ou outro.

EVIDÊNCIA EXTRAÍDA DO SITE:
{local_evidence or "Não fornecida."}
"""
        try:
            grounded = await generate_grounded_content(
                prompt,
                generation_config={"temperature": 0.1, "maxOutputTokens": 1200},
                timeout=60,
            )
            return _normalize_grounded_context(
                _clean_json_response(grounded["text"]),
                grounded,
                domain=domain,
                page_evidence=page_evidence,
            )
        except Exception as exc:
            logger.warning(
                "Pesquisa grounded falhou para o domínio %s; usando fallback público (%s)",
                domain,
                type(exc).__name__,
                exc_info=True,
            )
    return await _public_search_context(domain, page_evidence)
