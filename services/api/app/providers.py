"""Builds the configured LLM provider and embedder. The only place that picks one."""

from app.config import Settings
from app.embedding_models import LOCAL_MODELS
from app.embeddings import Embedder, FakeEmbedder
from app.llm import FakeLLMProvider, LLMProvider
from app.local_embedder import TransformersEmbedder
from app.models import EMBEDDING_DIMENSIONS


def build_llm_provider(settings: Settings) -> LLMProvider:
    match settings.llm_provider:
        case "fake":
            return FakeLLMProvider()


def build_embedder(settings: Settings) -> Embedder:
    embedder: Embedder
    match settings.embedder:
        case "fake":
            embedder = FakeEmbedder(dimensions=EMBEDDING_DIMENSIONS)
        case "qwen3-0.6b":
            embedder = TransformersEmbedder(LOCAL_MODELS[settings.embedder])
    check_dimensions(embedder)
    return embedder


def check_dimensions(embedder: Embedder) -> None:
    """Fail at startup, not at the first insert, when vectors won't fit the column."""
    if embedder.dimensions != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"embedder {embedder.name} makes {embedder.dimensions}-dimensional vectors, but "
            f"the database stores {EMBEDDING_DIMENSIONS}; switching models needs a migration"
        )
