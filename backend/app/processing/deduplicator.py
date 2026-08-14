from __future__ import annotations

import hashlib
import re
from typing import Iterable, List


def deduplicate_paragraphs(texts: Iterable[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for raw in texts:
        for paragraph in re.split(r"\n\s*\n", raw or ""):
            normalized = normalize_for_hash(paragraph)
            if not normalized:
                continue
            key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(paragraph.strip())
    return deduped


def normalize_for_hash(value: str) -> str:
    text = value or ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()
