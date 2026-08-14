from __future__ import annotations

import json
from pathlib import Path
from typing import List

import numpy as np

from app.config import EMBEDDING_DIMENSIONS
from app.processing.embeddings import encode_chunks, simple_embedding, top_k_matches


def load_company_context(company_dir: Path) -> tuple[str, List[dict], np.ndarray]:
    profile_path = company_dir / "company_profile.md"
    chunks_path = company_dir / "chunks.json"
    embeddings_path = company_dir / "embeddings.npy"

    profile = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    chunks = []
    if chunks_path.exists():
        with chunks_path.open("r", encoding="utf-8") as fh:
            chunks = json.load(fh)

    embeddings = np.empty((0, EMBEDDING_DIMENSIONS), dtype=float)
    if embeddings_path.exists():
        try:
            loaded = np.load(embeddings_path)
            if isinstance(loaded, np.ndarray):
                if loaded.size == 0:
                    embeddings = np.empty((0, EMBEDDING_DIMENSIONS), dtype=float)
                elif loaded.ndim == 1 and loaded.shape[0] == EMBEDDING_DIMENSIONS:
                    embeddings = loaded.reshape(1, -1)
                elif loaded.ndim == 1:
                    embeddings = loaded.reshape(1, -1)
                elif loaded.shape[1] == EMBEDDING_DIMENSIONS:
                    embeddings = loaded
                else:
                    embeddings = np.empty((0, EMBEDDING_DIMENSIONS), dtype=float)
        except Exception:
            embeddings = np.empty((0, EMBEDDING_DIMENSIONS), dtype=float)

    return profile, chunks, embeddings


def retrieve_top_chunks(question: str, company_dir: Path, top_k: int = 5) -> List[dict]:
    profile, chunks, embeddings = load_company_context(company_dir)
    if not chunks or embeddings.size == 0:
        return []
    query_vector = simple_embedding(question)
    matches = top_k_matches(query_vector, embeddings, top_k=top_k)
    return [chunks[idx] for idx in matches if idx < len(chunks)]


def retrieve_top_chunks_from_data(question: str, chunks: List[dict], top_k: int = 5) -> List[dict]:
    """Executa retrieval sobre o pacote enviado pelo navegador, sem estado no servidor."""
    safe_chunks = [item for item in chunks[:200] if isinstance(item, dict) and item.get("content")]
    if not safe_chunks:
        return []
    embeddings = encode_chunks([str(item.get("content", ""))[:5000] for item in safe_chunks])
    matches = top_k_matches(simple_embedding(question), embeddings, top_k=top_k)
    return [safe_chunks[idx] for idx in matches if idx < len(safe_chunks)]
