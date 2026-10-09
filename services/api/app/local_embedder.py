"""Local embedding models, run with Hugging Face transformers on the CPU.

The models and their pinned revisions are in app.embedding_models. torch and
transformers are imported on first use: the module can be imported where they
aren't installed, and nothing loads until the first text is embedded.

Texts are embedded longest first in batches, so a batch is padded to similar
lengths, then put back in their original order. Vectors are L2-normalised, so
cosine similarity is a dot product.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from app.embedding_models import LocalModel
from app.embeddings import Vector

if TYPE_CHECKING:
    import torch


class TransformersEmbedder:
    def __init__(self, model: LocalModel, batch_size: int = 16) -> None:
        self.model = model
        self._batch_size = batch_size
        self._lock = threading.Lock()
        self._loaded: tuple[Any, Any] | None = None  # (tokenizer, model)

    @property
    def name(self) -> str:
        """Stored with every vector: vectors from another revision are another model."""
        return f"{self.model.repo}@{self.model.revision}"

    @property
    def dimensions(self) -> int:
        return self.model.dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return await asyncio.to_thread(self.embed_sync, list(texts))

    async def embed_query(self, text: str) -> Vector:
        vectors = await asyncio.to_thread(self.embed_sync, [self.model.query_prefix + text])
        return vectors[0]

    def load(self) -> None:
        """Load the model now rather than on the first text (blocking: seconds)."""
        self._load()

    def count_tokens(self, texts: Sequence[str]) -> list[int]:
        tokenizer, _ = self._load()
        return [len(ids) for ids in tokenizer(list(texts), truncation=False)["input_ids"]]

    def embed_sync(self, texts: list[str]) -> list[Vector]:
        """Blocking: callers on the event loop use embed_documents/embed_query."""
        import torch

        tokenizer, model = self._load()
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]), reverse=True)
        vectors: list[Vector] = [[] for _ in texts]
        with torch.inference_mode():
            for start in range(0, len(order), self._batch_size):
                batch = order[start : start + self._batch_size]
                encoded = tokenizer(
                    [texts[i] for i in batch],
                    padding=True,
                    truncation=True,
                    max_length=self.model.max_tokens,
                    return_tensors="pt",
                )
                hidden = model(**encoded).last_hidden_state
                pooled = pool(hidden, encoded["attention_mask"], self.model.pooling)
                normalised = torch.nn.functional.normalize(pooled, p=2, dim=1)
                for i, vector in zip(batch, normalised.tolist(), strict=True):
                    vectors[i] = vector
        return vectors

    def _load(self) -> tuple[Any, Any]:
        with self._lock:
            if self._loaded is None:
                import torch
                from transformers import AutoModel, AutoTokenizer

                tokenizer = AutoTokenizer.from_pretrained(
                    self.model.repo,
                    revision=self.model.revision,
                    # Last-token pooling needs every text to end at the same position.
                    padding_side="left" if self.model.pooling == "last" else "right",
                )
                model = AutoModel.from_pretrained(
                    self.model.repo, revision=self.model.revision, dtype=torch.float32
                )
                self._loaded = (tokenizer, model.eval())
            return self._loaded


def pool(
    hidden: torch.Tensor, attention_mask: torch.Tensor, pooling: Literal["cls", "last"]
) -> torch.Tensor:
    """One vector per text from the model's per-token outputs."""
    if pooling == "cls":
        return hidden[:, 0]
    # Left padding puts every text's last real token in the final position. Check it
    # rather than trust it: right padding would silently pool a padding token.
    if not bool(attention_mask[:, -1].all()):
        raise ValueError("last-token pooling needs left padding")
    return hidden[:, -1]
