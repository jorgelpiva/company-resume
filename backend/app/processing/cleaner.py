from __future__ import annotations

import re
from datetime import datetime, timezone


def clean_text(raw_text: str) -> str:
    text = raw_text.replace("\xa0", " ")
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", block).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs)


def normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def build_markdown(title: str, url: str, content: str, mapped_at: str | None = None) -> str:
    safe_title = title.replace("\"", "\\\"")
    timestamp = mapped_at or datetime.now(timezone.utc).isoformat()
    return (
        f"---\nurl: {url}\ntitle: \"{safe_title}\"\nmapped_at: {timestamp}\n---\n\n"
        f"# {title}\n\n{content}\n"
    )
