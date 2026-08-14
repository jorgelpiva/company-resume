from __future__ import annotations

from collections import defaultdict, deque
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.config import BLOCKED_FILE_EXTENSIONS, MAX_BLOG_ARTICLES, MAX_DEPTH, MAX_PAGES
from app.crawler.fetcher import absolute_url, fetch_html, has_meaningful_primary_content
from app.crawler.relevance import is_external_url, is_low_relevance_url
from app.crawler.robots import is_allowed_by_robots
from app.crawler.url_normalizer import normalize_url, strip_fragment


async def discover_routes(
    base_url: str,
    domain: str,
    seed_urls: list[str] | None = None,
    robots_rules: list[str] | None = None,
    html_cache: dict[str, str] | None = None,
) -> tuple[list[str], dict]:
    queue = deque([(base_url, 0)])
    visited = set()
    discovered = []
    seen_by_depth = defaultdict(set)

    robots_rules = robots_rules or []
    html_cache = html_cache if html_cache is not None else {}
    sitemap_candidates = []
    for seed in seed_urls or []:
        candidate = strip_fragment(normalize_url(seed))
        if _can_crawl(candidate, domain, robots_rules):
            sitemap_candidates.append(candidate)

    discovery_limit = max(MAX_PAGES * 4, MAX_PAGES)
    while queue and len(discovered) < discovery_limit:
        current_url, depth = queue.popleft()
        current_url = strip_fragment(normalize_url(current_url))
        if current_url in visited:
            continue
        visited.add(current_url)
        if not _can_crawl(current_url, domain, robots_rules):
            continue
        discovered.append(current_url)
        if depth >= MAX_DEPTH:
            continue
        try:
            html = html_cache.get(current_url)
            if html is None:
                # A home pode precisar de JavaScript para expor a navegação. Nas
                # demais rotas, a renderização fica para a etapa de seleção, evitando
                # abrir um navegador para cada URL descoberta.
                html = await fetch_html(current_url, render_javascript=depth == 0)
                if depth == 0 or has_meaningful_primary_content(html, current_url):
                    html_cache[current_url] = html
        except Exception:
            continue
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href or href.startswith("mailto:") or href.startswith("tel:"):
                continue
            absolute = absolute_url(current_url, href)
            candidate = strip_fragment(normalize_url(absolute))
            if not candidate.startswith(("http://", "https://")):
                continue
            if not _can_crawl(candidate, domain, robots_rules):
                continue
            if candidate in visited or candidate in seen_by_depth[depth + 1]:
                continue
            seen_by_depth[depth + 1].add(candidate)
            queue.append((candidate, depth + 1))

    merged = list(dict.fromkeys([base_url, *sitemap_candidates, *discovered]))
    merged = _limit_blog_articles(merged)
    route_tree = build_route_tree(merged, base_url)
    return merged, route_tree


def _can_crawl(url: str, domain: str, robots_rules: list[str]) -> bool:
    path = urlparse(url).path.lower()
    extension = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    return (
        url.startswith(("http://", "https://"))
        and not is_external_url(url, domain)
        and not is_low_relevance_url(url)
        and extension not in BLOCKED_FILE_EXTENSIONS
        and is_allowed_by_robots(url, robots_rules)
    )


def _limit_blog_articles(urls: list[str]) -> list[str]:
    result: list[str] = []
    blog_articles = 0
    for url in urls:
        segments = [part for part in urlparse(url).path.split("/") if part]
        is_article = len(segments) >= 2 and segments[0].lower() in {"blog", "artigos", "insights", "news", "noticias"}
        if is_article:
            if blog_articles >= MAX_BLOG_ARTICLES:
                continue
            blog_articles += 1
        result.append(url)
    return result


def build_route_tree(urls: list[str], base_url: str) -> dict:
    root = {"path": "/", "children": {}}
    for url in urls:
        parsed = urlparse(url)
        if parsed.hostname != urlparse(base_url).hostname:
            continue
        path = parsed.path or "/"
        segments = [seg for seg in path.split("/") if seg]
        current = root
        for idx, segment in enumerate(segments):
            if segment == "":
                continue
            if segment not in current["children"]:
                current["children"][segment] = {"path": "/" + "/".join(segments[:idx + 1]), "children": {}}
            current = current["children"][segment]
    return root
