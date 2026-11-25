"""Embedding models (D10): SPECTER2 for paper-level title+abstract, bge for full-text chunks. CPU by default.

Models load lazily and are cached per process. Tests swap them via `set_embedders`.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _local_adapter(repo_or_path: str) -> str:
    """Download an adapter repo to a plain directory (no symlinks: the HF cache needs them, and Windows
    without Developer Mode refuses them), returning the local path."""
    from pathlib import Path
    if Path(repo_or_path).exists():
        return repo_or_path
    from huggingface_hub import snapshot_download
    target = Path.home() / ".cache" / "litkb" / "adapters" / repo_or_path.replace("/", "__")
    if not (target / "adapter_config.json").exists():
        snapshot_download(repo_id=repo_or_path, local_dir=target)
    return str(target)


class Specter2Embedder:
    """allenai/specter2_base + the proximity adapter; CLS pooling on `title [SEP] abstract`."""

    def __init__(self, base: str, adapter: str, device: str = "cpu", batch_size: int = 16):
        import torch
        from adapters import AutoAdapterModel
        from transformers import AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(base)
        self.model = AutoAdapterModel.from_pretrained(base)
        if adapter:
            self.model.load_adapter(_local_adapter(adapter), load_as="proximity", set_active=True)
        self.model.to(device).eval()
        self.device, self.batch_size = device, batch_size
        self.dim = self.model.config.hidden_size

    def format(self, title: str, abstract: str | None) -> str:
        return (title or "") + self.tok.sep_token + (abstract or "")

    def embed(self, texts: list[str]) -> np.ndarray:
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), self.batch_size):
                enc = self.tok(texts[i:i + self.batch_size], padding=True, truncation=True, max_length=512,
                               return_tensors="pt", return_token_type_ids=False).to(self.device)
                out.append(self.model(**enc).last_hidden_state[:, 0, :].cpu().numpy())
        return np.vstack(out).astype("float32") if out else np.zeros((0, self.dim), "float32")


class BgeEmbedder:
    def __init__(self, model: str, device: str = "cpu", batch_size: int = 16):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model, device=device)
        self.batch_size = batch_size
        get_dim = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dim = get_dim()
        self.query_prefix = BGE_QUERY_PREFIX if "bge" in model.lower() and "m3" not in model.lower() else ""

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                 show_progress_bar=len(texts) > 200, convert_to_numpy=True).astype("float32")

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([self.query_prefix + text])[0]


_cache: dict[str, object] = {}


def set_embedders(paper: object | None = None, chunk: object | None = None) -> None:
    """Override the models (tests, or alternative backends). Pass None to reset to configured models."""
    _cache.clear()
    if paper is not None:
        _cache["paper"] = paper
    if chunk is not None:
        _cache["chunk"] = chunk


def paper_embedder(cfg: dict):
    if "paper" not in _cache:
        e = cfg["embed"]
        _cache["paper"] = Specter2Embedder(e["paper_model"], e.get("paper_adapter"), e["device"], e["batch_size"])
    return _cache["paper"]


def chunk_embedder(cfg: dict):
    if "chunk" not in _cache:
        e = cfg["embed"]
        _cache["chunk"] = BgeEmbedder(e["chunk_model"], e["device"], e["batch_size"])
    return _cache["chunk"]


def paper_text(embedder, title: str, abstract: str | None) -> str:
    fmt = getattr(embedder, "format", None)
    return fmt(title, abstract) if fmt else f"{title}. {abstract or ''}"


def query_vector(embedder, text: str) -> np.ndarray:
    fn = getattr(embedder, "embed_query", None)
    return fn(text) if fn else embedder.embed([text])[0]
