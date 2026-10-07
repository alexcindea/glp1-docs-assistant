"""Builds the configured LLM provider and embedder. The only place that picks one."""

from app.config import Settings
from app.embeddings import Embedder, FakeEmbedder
from app.llm import FakeLLMProvider, LLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    match settings.llm_provider:
        case "fake":
            return FakeLLMProvider()


def build_embedder(settings: Settings) -> Embedder:
    match settings.embedder:
        case "fake":
            return FakeEmbedder(dimensions=settings.embedding_dimensions)
