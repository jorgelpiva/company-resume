from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv


load_dotenv()


# Modelos gratuitos, na mesma ordem de fallback usada pelo projeto Catequista IA.
MODELS_TO_TRY = (
    "gemini-1.5-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-pro-latest",
    "gemini-2.0-flash",
)

# Modelos da lista acima que aceitam a ferramenta google_search atual.
SEARCH_MODELS_TO_TRY = (
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-2.0-flash",
)


class GeminiGenerationError(RuntimeError):
    pass


def get_api_key() -> str | None:
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")


def get_models_to_try() -> tuple[str, ...]:
    preferred_model = os.getenv("GEMINI_MODEL", "").strip()
    if not preferred_model:
        return MODELS_TO_TRY
    return (preferred_model, *(model for model in MODELS_TO_TRY if model != preferred_model))


def get_search_models_to_try() -> tuple[str, ...]:
    preferred_model = os.getenv("GEMINI_SEARCH_MODEL", "").strip()
    if not preferred_model:
        configured_model = os.getenv("GEMINI_MODEL", "").strip()
        preferred_model = configured_model if configured_model in SEARCH_MODELS_TO_TRY else ""
    if not preferred_model:
        return SEARCH_MODELS_TO_TRY
    return (preferred_model, *(model for model in SEARCH_MODELS_TO_TRY if model != preferred_model))


def _error_summary(model: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"[{model}] HTTP {exc.response.status_code}"
    return f"[{model}] {type(exc).__name__}"


async def _generate_response(
    prompt: str,
    *,
    generation_config: dict[str, Any] | None,
    timeout: float,
    models: tuple[str, ...],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    api_key = get_api_key()
    if not api_key:
        raise GeminiGenerationError("GOOGLE_API_KEY ou GEMINI_API_KEY não configurada")

    payload: dict[str, Any] = {"contents": [{"parts": [{"text": prompt}]}]}
    if generation_config:
        payload["generationConfig"] = generation_config
    if tools:
        payload["tools"] = tools

    last_error = ""
    async with httpx.AsyncClient(timeout=timeout) as client:
        for model in models:
            try:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": api_key},
                    json=payload,
                )
                response.raise_for_status()
                return response.json(), model
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = _error_summary(model, exc)

    raise GeminiGenerationError(
        "Todos os modelos da cota gratuita falharam ou estão indisponíveis. "
        f"Último erro: {last_error}"
    )


async def generate_content(
    prompt: str,
    *,
    generation_config: dict[str, Any] | None = None,
    timeout: float = 60,
) -> str:
    """Gera conteúdo com fallback automático entre os modelos gratuitos."""
    data, _model = await _generate_response(
        prompt,
        generation_config=generation_config,
        timeout=timeout,
        models=get_models_to_try(),
    )
    return data["candidates"][0]["content"]["parts"][0]["text"]


async def generate_grounded_content(
    prompt: str,
    *,
    generation_config: dict[str, Any] | None = None,
    timeout: float = 60,
) -> dict[str, Any]:
    """Pesquisa com Google Search grounding e devolve texto, fontes e consultas."""
    data, model = await _generate_response(
        prompt,
        generation_config=generation_config,
        timeout=timeout,
        models=get_search_models_to_try(),
        tools=[{"google_search": {}}],
    )
    candidate = data["candidates"][0]
    text = "".join(
        part.get("text", "")
        for part in candidate.get("content", {}).get("parts", [])
        if isinstance(part, dict)
    )
    grounding = candidate.get("groundingMetadata", {})
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for chunk in grounding.get("groundingChunks", []):
        web = chunk.get("web", {}) if isinstance(chunk, dict) else {}
        uri = web.get("uri", "")
        if uri and uri not in seen_urls:
            seen_urls.add(uri)
            sources.append({"url": uri, "title": web.get("title", "")})
    return {
        "text": text,
        "sources": sources,
        "queries": grounding.get("webSearchQueries", []),
        "model": model,
    }
