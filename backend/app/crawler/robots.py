from __future__ import annotations

from urllib import robotparser
from urllib.parse import urljoin

from app.config import USER_AGENT
from app.crawler.fetcher import fetch_url


async def read_robots_txt(base_url: str) -> dict:
    robots_url = urljoin(base_url, "/robots.txt")
    try:
        text = await fetch_url(robots_url)
        return {"allowed": True, "rules": _parse_robots(text), "source": robots_url}
    except Exception:
        return {"allowed": True, "rules": [], "source": robots_url}


def _parse_robots(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def is_allowed_by_robots(url: str, robots_rules: list[str]) -> bool:
    if not robots_rules:
        return True
    parser = robotparser.RobotFileParser()
    parser.parse(robots_rules)
    return parser.can_fetch(USER_AGENT, url)
