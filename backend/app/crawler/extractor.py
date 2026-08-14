from __future__ import annotations

import re
from typing import Dict

from bs4 import BeautifulSoup


def extract_primary_content(html: str, url: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe", "nav", "footer", "form"]):
        tag.decompose()
    noise_pattern = re.compile(
        r"cookie|newsletter|breadcrumb|social|share|popup|modal|menu|sidebar|advert|banner|consent",
        re.I,
    )
    for tag in soup.find_all(attrs={"class": noise_pattern}) + soup.find_all(attrs={"id": noise_pattern}):
        tag.decompose()
    title = (soup.title.get_text(" ", strip=True) if soup.title else "").strip() or "Página"
    meta_desc = ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        meta_desc = meta["content"].strip()
    h1 = ""
    h1_tag = soup.find("h1")
    if h1_tag:
        h1 = h1_tag.get_text(" ", strip=True)

    main = soup.find("main") or soup.find("article") or soup.body or soup
    blocks: list[str] = []
    for tag in main.find_all(["h1", "h2", "h3", "p", "li", "table"]):
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        if not text or (tag.name not in {"h1", "h2", "h3"} and len(text) < 20):
            continue
        if tag.name in {"h1", "h2", "h3"}:
            blocks.append(f"{'#' * int(tag.name[1])} {text}")
        else:
            blocks.append(text)

    content = "\n\n".join(blocks)
    return {
        "url": url,
        "title": title,
        "h1": h1,
        "meta_description": meta_desc,
        "content": content.strip(),
    }
