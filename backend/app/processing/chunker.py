from __future__ import annotations

import re
import unicodedata
from typing import List


def chunk_text(text: str, chunk_size: int = 1500) -> List[str]:
    if not text:
        return []
    chunks: List[str] = []
    current = []
    current_length = 0
    for paragraph in text.split("\n\n"):
        cleaned = paragraph.strip()
        if not cleaned:
            continue
        if len(cleaned) > chunk_size:
            if current:
                chunks.append("\n\n".join(current))
                current, current_length = [], 0
            chunks.extend(_split_long_block(cleaned, chunk_size))
            continue
        if current_length + len(cleaned) > chunk_size and current:
            chunks.append("\n\n".join(current))
            current = [cleaned]
            current_length = len(cleaned)
        else:
            current.append(cleaned)
            current_length += len(cleaned)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_long_block(text: str, chunk_size: int) -> List[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    result: List[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > chunk_size:
            if current:
                result.append(current)
                current = ""
            result.extend(sentence[i:i + chunk_size] for i in range(0, len(sentence), chunk_size))
        elif len(current) + len(sentence) + 1 > chunk_size:
            result.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        result.append(current)
    return result


def build_chunk_documents(company_slug: str, title: str, url: str, chunks: List[str]) -> List[dict]:
    docs = []
    for index, chunk in enumerate(chunks, start=1):
        docs.append({
            "company": company_slug,
            "url": url,
            "title": title,
            "section": title,
            "chunk_id": f"{slugify(title)}-{index:03d}",
            "content": chunk,
        })
    return docs


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "pagina"
