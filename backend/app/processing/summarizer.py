from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from app.gemini_client import generate_content, get_api_key


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


def has_institutional_identity(pages: list[dict[str, Any]]) -> bool:
    """Detecta identidade explícita no RAG, sem confundir catálogo com página institucional."""
    route_markers = ("/sobre", "/quem-somos", "/institucional", "/about", "/nossa-historia")
    title_markers = ("quem somos", "sobre nós", "sobre a empresa", "institucional", "nossa história")
    identity_phrases = re.compile(
        r"\b(?:somos\s+(?:uma|um|a|o)|é\s+(?:uma|um)\s+(?:empresa|organização|plataforma)|foi fundada|nossa missão)\b",
        re.I,
    )
    for page in pages:
        url = page.get("url", "")
        path = urlparse(url).path.lower()
        title = page.get("title", "").lower()
        content = page.get("content", "")
        if len(content) >= 200 and (any(marker in path for marker in route_markers) or any(marker in title for marker in title_markers)):
            return True
        if path in {"", "/"} and identity_phrases.search(content[:5000]):
            return True
    return False


def _replace_profile_section(profile: str, heading: str, body: str) -> str:
    pattern = rf"(?ms)(^##\s+{re.escape(heading)}\s*$\n).*?(?=^##\s+|\Z)"
    return re.sub(pattern, lambda match: f"{match.group(1)}{body.strip()}\n\n", profile, count=1)


def _section_body(profile: str, heading: str) -> str:
    match = re.search(rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)", profile)
    return match.group(1).strip() if match else ""


def enrich_profile_with_external_identity(profile: str, context: dict[str, Any] | None) -> str:
    """Preenche somente a identidade ausente, preservando o restante do perfil extrativo."""
    if not context or not context.get("summary"):
        return profile
    sources = [
        item.get("url")
        for item in context.get("sources", [])[:3]
        if isinstance(item, dict) and item.get("url")
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
    rag_has_identity = has_institutional_identity(pages)
    effective_context = None if rag_has_identity else research_context
    enriched_fallback = enrich_profile_with_external_identity(fallback, effective_context)
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
"""
    try:
        profile = (await generate_content(
            prompt,
            generation_config={"temperature": 0.1, "maxOutputTokens": 6000},
            timeout=60,
        )).strip()
        profile = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", profile, flags=re.I)
        if profile.startswith("# ") and "## Fontes analisadas" in profile:
            return profile + "\n", "gemini"
    except Exception:
        pass
    return enriched_fallback, "extractive+external" if effective_context else "extractive"
