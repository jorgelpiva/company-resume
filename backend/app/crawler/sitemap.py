from __future__ import annotations

from urllib.parse import urljoin
from xml.etree import ElementTree

from app.crawler.fetcher import fetch_url


async def discover_sitemap_urls(base_url: str) -> list[str]:
    candidate_paths = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"]
    page_urls: set[str] = set()
    pending = [urljoin(base_url, candidate) for candidate in candidate_paths]
    visited: set[str] = set()
    while pending and len(visited) < 20:
        target = pending.pop(0)
        if target in visited:
            continue
        visited.add(target)
        try:
            text = await fetch_url(target)
            xml_urls = extract_urls_from_sitemap(text, target)
            for item in xml_urls:
                if item.lower().split("?", 1)[0].endswith(".xml"):
                    if item not in visited:
                        pending.append(item)
                else:
                    page_urls.add(item)
        except Exception:
            continue
    return sorted(page_urls)


def extract_urls_from_sitemap(xml_text: str, source_url: str) -> list[str]:
    results: list[str] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return results
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != "loc" or not element.text:
            continue
        value = element.text.strip()
        if value:
            results.append(value if value.startswith(("http://", "https://")) else urljoin(source_url, value))
    return sorted(set(results))
