"""Text embeddings with a small open-source model that runs locally on the CPU.

BAAI/bge-small-en-v1.5: 384 dimensions, ~130 MB, MIT license. Runs through ONNX (fastembed),
so there's no PyTorch, no GPU and no API: embedding is free and works offline after the
one-time download.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL = os.environ.get("RUNBOOKS_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
DIM = 384
CACHE = Path(os.environ.get("RUNBOOKS_MODEL_CACHE", Path.home() / ".cache" / "kube-sentinel" / "models"))


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding  # imported lazily: loading the model takes a second
    return TextEmbedding(MODEL, cache_dir=str(CACHE))


def embed_passages(texts: list[str]) -> list[np.ndarray]:
    return list(_model().passage_embed(texts))


def embed_query(text: str) -> np.ndarray:
    # bge models expect queries and passages to be embedded slightly differently
    return next(iter(_model().query_embed(text)))
