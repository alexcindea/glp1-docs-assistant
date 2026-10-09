"""Local embedding models: pooling, batching and the vectors' basic properties."""

import math

import pytest

from app.config import Settings
from app.embedding_models import LOCAL_MODELS
from app.local_embedder import TransformersEmbedder, pool
from app.providers import build_embedder

torch = pytest.importorskip("torch")  # the `embed` extra


def test_cls_pooling_reads_the_first_token() -> None:
    hidden = torch.arange(12, dtype=torch.float32).reshape(2, 3, 2)
    mask = torch.tensor([[1, 1, 0], [1, 1, 1]])
    assert pool(hidden, mask, "cls").tolist() == [[0, 1], [6, 7]]


def test_last_token_pooling_reads_the_final_position_under_left_padding() -> None:
    hidden = torch.arange(12, dtype=torch.float32).reshape(2, 3, 2)
    mask = torch.tensor([[0, 1, 1], [1, 1, 1]])
    assert pool(hidden, mask, "last").tolist() == [[4, 5], [10, 11]]


def test_last_token_pooling_refuses_right_padding() -> None:
    hidden = torch.zeros(2, 3, 2)
    with pytest.raises(ValueError, match="left padding"):
        pool(hidden, torch.tensor([[1, 1, 0], [1, 1, 1]]), "last")


def test_settings_pick_the_local_model_without_loading_it() -> None:
    embedder = build_embedder(Settings(embedder="qwen3-0.6b"))
    assert isinstance(embedder, TransformersEmbedder)
    model = LOCAL_MODELS["qwen3-0.6b"]
    assert embedder.name == f"Qwen/Qwen3-Embedding-0.6B@{model.revision}"  # revision included
    assert embedder.dimensions == 1024


@pytest.mark.anyio
@pytest.mark.embed
async def test_bge_small_finds_the_related_passage() -> None:
    embedder = TransformersEmbedder(LOCAL_MODELS["bge-small"], batch_size=2)
    texts = [
        "Store the pen in a refrigerator between 2 and 8 degrees C.",
        "Thyroid C-cell tumors were seen in rats and mice.",
        "Nausea was the most common adverse reaction.",
    ]

    documents = await embedder.embed_documents(texts)
    query = await embedder.embed_query("At what temperature should pens be kept?")
    one_by_one = [(await embedder.embed_documents([text]))[0] for text in texts]

    assert all(
        len(v) == 384 and math.isclose(sum(x * x for x in v), 1, rel_tol=1e-4) for v in documents
    )
    scores = [sum(q * d for q, d in zip(query, doc, strict=True)) for doc in documents]
    assert max(range(3), key=scores.__getitem__) == 0
    # Batching (sorted by length, padded) gives the same vectors, in the input order.
    for batched, single in zip(documents, one_by_one, strict=True):
        assert max(abs(a - b) for a, b in zip(batched, single, strict=True)) < 1e-4
