from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable, List

import numpy as np

from app.config import EMBEDDING_DIMENSIONS


def simple_embedding(text: str) -> np.ndarray:
    normalized = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    tokens = re.findall(r"[a-z0-9]{2,}", normalized)
    features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    vector = np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        index = value % EMBEDDING_DIMENSIONS
        vector[index] += 1.0 if value & 1 else -1.0
    norm = np.linalg.norm(vector)
    if norm:
        vector /= norm
    return vector


def encode_chunks(chunks: Iterable[str]) -> np.ndarray:
    vectors = [simple_embedding(chunk) for chunk in chunks]
    if not vectors:
        return np.empty((0, EMBEDDING_DIMENSIONS), dtype=np.float32)
    return np.vstack(vectors)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def top_k_matches(question_vector: np.ndarray, embeddings: np.ndarray, top_k: int = 5) -> List[int]:
    if embeddings.size == 0:
        return []
    sims = []
    for idx, row in enumerate(embeddings):
        sims.append((idx, cosine_similarity(question_vector, row)))
    sims.sort(key=lambda item: item[1], reverse=True)
    return [idx for idx, _ in sims[:top_k]]
