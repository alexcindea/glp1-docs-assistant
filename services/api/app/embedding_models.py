"""The local embedding models we can run, each pinned to an exact revision.

Pinned so the vectors (and the eval scores built on them) don't change when a
model's repository does. Standard library only, and no app imports: the Docker
build loads this one file to download exactly these revisions before the app code
is copied in (services/api/docker/fetch_embedding_model.py).

Qwen3-Embedding-0.6B is the one the app uses (evals/results/embedder-bakeoff.md);
bge-small stays for the comparison.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LocalModel:
    repo: str
    revision: str
    dimensions: int
    # bge reads the [CLS] token; Qwen3, a decoder, reads the last token.
    pooling: Literal["cls", "last"]
    query_prefix: str  # documents get no prefix
    max_tokens: int  # longer texts are truncated (and counted by the bake-off)


LOCAL_MODELS = {
    # MIT. 33M parameters, 384 dimensions, reads up to 512 tokens.
    "bge-small": LocalModel(
        repo="BAAI/bge-small-en-v1.5",
        revision="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        dimensions=384,
        pooling="cls",
        query_prefix="Represent this sentence for searching relevant passages: ",
        max_tokens=512,
    ),
    # Apache-2.0. 0.6B parameters, 1024 dimensions; the prompt is the model card's default.
    "qwen3-0.6b": LocalModel(
        repo="Qwen/Qwen3-Embedding-0.6B",
        revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        dimensions=1024,
        pooling="last",
        query_prefix=(
            "Instruct: Given a web search query, retrieve relevant passages that answer "
            "the query\nQuery:"
        ),
        max_tokens=1024,
    ),
}

# The model the app runs (EMBEDDER=qwen3-0.6b), and so the one baked into the images.
APP_MODEL = "qwen3-0.6b"
