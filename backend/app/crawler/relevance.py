from __future__ import annotations

import re
from urllib.parse import urlparse

from app.config import LOW_RELEVANCE_TOKENS, RELEVANCE_KEYWORDS


def score_url_and_content(
    url: str,
    title: str = "",
    h1: str = "",
    meta: str = "",
    content: str = "",
) -> float:
    score = 0.0
    text = " ".join([url, title, h1, meta, content]).lower()
    parsed = urlparse(url)
    path = parsed.path.lower()
    for keyword in RELEVANCE_KEYWORDS:
        if keyword in path or keyword in text:
            score += 2.5
    for token in LOW_RELEVANCE_TOKENS:
        if token in path:
            score -= 6.0
    if "/blog/" in path:
        score += 1.0
    if "/contato" in path or "contact" in text:
        score += 1.5
    if "/servico" in path or "/servicos" in path:
        score += 2.0
    if "/sobre" in path or "quem somos" in text:
        score += 2.5
    if "empresa" in text or "about" in text:
        score += 1.5
    if title and re.search(r"(sobre|serviços|servicos|produtos|contato|carreiras|cases|empresa)", title, re.I):
        score += 2.0
    if h1 and re.search(r"(sobre|serviços|servicos|produtos|contato|carreiras|cases|empresa)", h1, re.I):
        score += 2.0
    return score


def is_low_relevance_url(url: str) -> bool:
    normalized = url.lower()
    return any(token in normalized for token in LOW_RELEVANCE_TOKENS) or "/wp-json" in normalized


def is_external_url(url: str, domain: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    expected_domain = domain.lower().rstrip(".")
    if not host:
        return True
    return host != expected_domain and not host.endswith(f".{expected_domain}")
