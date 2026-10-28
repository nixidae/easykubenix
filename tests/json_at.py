"""Typed steps into the JSON values that ekn returns.

A step into the wrong kind of value fails the test with the value it found.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from huggorm.jsonprimop import JsonValue


def at(value: object, *path: str | int) -> JsonValue:
    """`value[path[0]][path[1]]...`: a key steps into a mapping, an index into a list."""
    for step in path:
        if isinstance(step, str):
            assert isinstance(value, dict), f"{step!r} of a non-mapping: {value!r}"
            value = value[step]
        else:
            assert isinstance(value, list), f"[{step}] of a non-list: {value!r}"
            value = value[step]
    # Every container a test steps through here holds JSON.
    return cast("JsonValue", value)


def mapping_at(value: object, *path: str | int) -> dict[str, JsonValue]:
    found = at(value, *path)
    assert isinstance(found, dict), f"not a mapping: {found!r}"
    return found


def list_at(value: object, *path: str | int) -> list[JsonValue]:
    found = at(value, *path)
    assert isinstance(found, list), f"not a list: {found!r}"
    return found


def text_at(value: object, *path: str | int) -> str:
    found = at(value, *path)
    assert isinstance(found, str), f"not a string: {found!r}"
    return found
