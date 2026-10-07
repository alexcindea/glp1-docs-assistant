"""The interface every embedding model sits behind.

Documents and queries are embedded separately because retrieval models often
prefix queries with an instruction (bge, Qwen3 both do) but not passages. The
vector size is a property of the embedder, never a constant elsewhere.
"""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

Vector = list[float]


class Embedder(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...

    async def embed_query(self, text: str) -> Vector: ...


_TOKEN = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic hashed bag-of-words vectors, L2-normalised.

    Not semantic, but texts that share words land close together, which is
    enough for plumbing and retrieval tests without downloading a model.
    Uses blake2b rather than hash() so vectors are stable across processes.
    """

    name = "fake"

    def __init__(self, dimensions: int = 384) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> Vector:
        return self._embed(text)

    def _embed(self, text: str) -> Vector:
        vector = [0.0] * self._dimensions
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self._dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector
