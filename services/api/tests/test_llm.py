"""Structured-output schemas meet strict mode's rules, or are refused when they can't."""

from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, Field

from app.llm import output_schema


class Claim(BaseModel):
    title: str  # a field may be called "title", like the keyword strict mode drops
    passages: list[int]


class Verdict(BaseModel):
    outcome: Literal["yes", "no"]
    claims: list[Claim]
    note: str | None = None  # has a default, so Pydantic would not list it as required


def _objects(node: Any) -> list[dict[str, Any]]:
    """Every object schema in `node`, at any depth."""
    if isinstance(node, list):
        return [found for item in node for found in _objects(item)]
    if not isinstance(node, dict):
        return []
    own = [node] if node.get("type") == "object" else []
    return own + [found for value in node.values() for found in _objects(value)]


def test_every_object_is_closed_and_lists_every_property_as_required() -> None:
    schema = output_schema(Verdict).json_schema

    objects = _objects(schema)
    assert len(objects) == 2  # Verdict and Claim (under $defs)
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])
    assert "note" in schema["required"]


def test_titles_and_defaults_are_dropped_but_a_field_named_title_is_kept() -> None:
    schema = output_schema(Verdict).json_schema
    text = str(schema)

    assert "'default'" not in text
    assert "title" in schema["$defs"]["Claim"]["properties"]
    assert "title" not in schema
    assert "title" not in schema["$defs"]["Claim"]
    assert "title" not in schema["$defs"]["Claim"]["properties"]["title"]


def test_the_schema_is_named_after_the_model() -> None:
    assert output_schema(Verdict).name == "Verdict"


class Described(BaseModel):
    claim: Claim = Field(description="the main claim")


def test_a_reference_with_a_keyword_beside_it_is_inlined() -> None:
    schema = output_schema(Described).json_schema

    claim = schema["properties"]["claim"]
    assert "$ref" not in claim
    assert claim["description"] == "the main claim"
    assert claim["additionalProperties"] is False
    assert claim["required"] == ["title", "passages"]


class WithDict(BaseModel):
    counts: dict[str, int]


class A(BaseModel):
    kind: Literal["a"]


class B(BaseModel):
    kind: Literal["b"]


class WithUnion(BaseModel):
    choice: Annotated[A | B, Field(discriminator="kind")]


@pytest.mark.parametrize(("model", "says"), [(WithDict, "dict fields"), (WithUnion, "oneOf")])
def test_shapes_strict_mode_cannot_express_are_refused(model: type[BaseModel], says: str) -> None:
    with pytest.raises(ValueError, match=says):
        output_schema(model)


def test_a_name_the_api_would_reject_is_refused() -> None:
    generic = type("Box[int]", (BaseModel,), {"__annotations__": {"x": int}})

    with pytest.raises(ValueError, match="can't name a schema"):
        output_schema(generic)
