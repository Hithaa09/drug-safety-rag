"""Text embeddings through LangChain's Hugging Face wrapper, with an on-disk cache.

The cache is keyed by model and by the exact text (SHA-256), so a chunk is embedded once per
model, and changing a chunk's text re-embeds only that chunk.

BGE models expect a short instruction in front of search queries (not in front of passages);
`embed_query` adds it when `query_instruction` is set.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def _device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class Embedder:
    def __init__(self, model_name: str, cache_dir: Path, query_instruction: str = ""):
        from langchain_huggingface import HuggingFaceEmbeddings

        self.model_name = model_name
        self.query_instruction = query_instruction
        self._model = HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={"device": _device()},
            encode_kwargs={"normalize_embeddings": True, "batch_size": 32},
        )
        slug = re.sub(r"[^A-Za-z0-9]+", "_", model_name)
        self._cache_path = cache_dir / "embeddings" / f"{slug}.npz"
        self._cache: dict[str, np.ndarray] = {}
        if self._cache_path.exists():
            data = np.load(self._cache_path)
            self._cache = dict(zip(data["keys"].tolist(), data["vectors"], strict=True))

    @property
    def dimension(self) -> int:
        return len(self.embed_query("dimension check"))

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        missing = [t for t in dict.fromkeys(texts) if self._key(t) not in self._cache]
        if missing:
            log.info("embedding %d new texts with %s", len(missing), self.model_name)
            vectors = self._model.embed_documents(missing)
            for t, v in zip(missing, vectors, strict=True):
                self._cache[self._key(t)] = np.asarray(v, dtype=np.float32)
            self._save()
        return np.stack([self._cache[self._key(t)] for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return np.asarray(self._model.embed_query(self.query_instruction + text), dtype=np.float32)

    def _save(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        keys = np.array(list(self._cache))
        vectors = np.stack(list(self._cache.values()))
        tmp = self._cache_path.with_name(self._cache_path.stem + ".tmp.npz")
        np.savez(tmp, keys=keys, vectors=vectors)
        tmp.replace(self._cache_path)
