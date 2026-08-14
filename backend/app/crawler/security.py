from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from app.config import ALLOWED_SCHEMES


def validate_url_syntax(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError("A URL deve usar http ou https")
    if not parsed.hostname:
        raise ValueError("A URL deve conter um domínio válido")
    if parsed.username or parsed.password:
        raise ValueError("Credenciais na URL não são permitidas")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("Apenas as portas HTTP e HTTPS são permitidas")
    return url


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(address.is_global)


async def ensure_public_url(url: str) -> str:
    validate_url_syntax(url)
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    try:
        address = ipaddress.ip_address(hostname)
        if not _is_public_ip(str(address)):
            raise ValueError("Endereços de rede privada ou local não são permitidos")
        return url
    except ValueError as exc:
        if "não são permitidos" in str(exc):
            raise

    loop = asyncio.get_running_loop()
    try:
        records = await loop.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Não foi possível resolver o domínio informado") from exc

    addresses = {record[4][0] for record in records}
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise ValueError("O domínio resolve para uma rede privada ou não pública")
    return url
