from __future__ import annotations

import asyncio
import os
import shutil
import time
from urllib.parse import urljoin
from urllib.parse import urlparse

import httpx

from app.config import (
    CRAWL_DELAY,
    MAX_PAGE_SIZE_MB,
    MIN_PRIMARY_CONTENT_CHARS,
    RENDER_TIMEOUT,
    RENDER_VIRTUAL_TIME_BUDGET_MS,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from app.crawler.security import ensure_public_url

_rate_lock = asyncio.Lock()
_last_request_at = 0.0


async def _respect_rate_limit() -> None:
    global _last_request_at
    async with _rate_lock:
        remaining = CRAWL_DELAY - (time.monotonic() - _last_request_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
        _last_request_at = time.monotonic()


async def fetch_url(url: str) -> str:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}
    current_url = url
    max_bytes = MAX_PAGE_SIZE_MB * 1024 * 1024
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False, headers=headers) as client:
        for _ in range(6):
            await ensure_public_url(current_url)
            await _respect_rate_limit()
            response = await client.get(current_url)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    response.raise_for_status()
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(kind in content_type for kind in ("text/html", "text/plain", "xml")):
                raise ValueError(f"Tipo de conteúdo não suportado: {content_type}")
            if len(response.content) > max_bytes:
                raise ValueError("A página excede o limite de tamanho configurado")
            return response.text
    raise ValueError("A URL excedeu o limite de redirecionamentos")


def has_meaningful_primary_content(html: str, url: str) -> bool:
    # Import local para manter o extrator independente do transporte HTTP.
    from app.crawler.extractor import extract_primary_content

    extracted = extract_primary_content(html, url)
    return len(extracted["content"].strip()) >= MIN_PRIMARY_CONTENT_CHARS


def _find_chrome() -> str | None:
    configured = os.getenv("CHROME_BINARY", "").strip()
    if configured:
        return configured
    return next(
        (
            executable
            for name in ("google-chrome", "chromium", "chromium-browser", "chrome")
            if (executable := shutil.which(name))
        ),
        None,
    )


async def fetch_rendered_html(url: str) -> str:
    """Renderiza uma SPA com Chrome, limitando a navegação ao host público solicitado."""
    await ensure_public_url(url)
    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError("Chrome/Chromium não encontrado para renderizar conteúdo JavaScript")

    hostname = urlparse(url).hostname or ""
    alternate_hostname = hostname.removeprefix("www.") if hostname.startswith("www.") else f"www.{hostname}"
    resolver_rules = f"MAP * ~NOTFOUND, EXCLUDE {hostname}, EXCLUDE {alternate_hostname}"
    process = await asyncio.create_subprocess_exec(
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-sync",
        "--no-first-run",
        "--incognito",
        f"--host-resolver-rules={resolver_rules}",
        f"--virtual-time-budget={RENDER_VIRTUAL_TIME_BUDGET_MS}",
        "--dump-dom",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=RENDER_TIMEOUT)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise ValueError("A renderização JavaScript excedeu o tempo limite") from None

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise ValueError(f"Falha ao renderizar a página com JavaScript: {detail}")
    if len(stdout) > MAX_PAGE_SIZE_MB * 1024 * 1024:
        raise ValueError("A página renderizada excede o limite de tamanho configurado")
    return stdout.decode("utf-8", errors="replace")


async def fetch_html(url: str, *, render_javascript: bool = True) -> str:
    html = await fetch_url(url)
    if not render_javascript or has_meaningful_primary_content(html, url):
        return html
    try:
        rendered_html = await fetch_rendered_html(url)
    except Exception:
        return html
    return rendered_html if has_meaningful_primary_content(rendered_html, url) else html


def absolute_url(base_url: str, maybe_relative: str) -> str:
    return urljoin(base_url, maybe_relative)
