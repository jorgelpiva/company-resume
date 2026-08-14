from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


BLACKLIST_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    base = parsed.scheme.lower()
    if base not in {"http", "https"}:
        return url
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() not in BLACKLIST_PARAMS:
            query_items.append((key, value))
    path = parsed.path or "/"
    if path.lower().endswith("/index.html"):
        path = path[:-10] or "/"
    if path != "/":
        path = path.rstrip("/")
    cleaned = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        query=urlencode(sorted(query_items)),
        fragment="",
    )
    normalized = urlunparse(cleaned)
    return normalized


def strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))
