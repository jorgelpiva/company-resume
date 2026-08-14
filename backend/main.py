"""Entrypoint estável para autodetecção do FastAPI pela Vercel."""

from app.main import app

__all__ = ["app"]
