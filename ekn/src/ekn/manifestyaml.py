from __future__ import annotations

import math
from typing import TYPE_CHECKING

import yaml
from yaml.events import (
    DocumentEndEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
    StreamEndEvent,
    StreamStartEvent,
)

from ekn.nixyaml import KubernetesDumper, resolve_plain_scalar

if TYPE_CHECKING:
    from collections.abc import Iterator

    from yaml.events import Event

_TAG = "tag:yaml.org,2002:"


class _UseRepresenterError(Exception):
    pass


def _nodes(value: object, seen: set[int]) -> Iterator[Event]:
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in seen:
            raise _UseRepresenterError
        seen.add(identity)
    if isinstance(value, dict):
        yield MappingStartEvent(None, _TAG + "map", True, flow_style=False)
        for key, child in value.items():
            if type(key) is not str:
                raise _UseRepresenterError
            yield from _nodes(key, seen)
            yield from _nodes(child, seen)
        yield MappingEndEvent()
    elif isinstance(value, list):
        yield SequenceStartEvent(None, _TAG + "seq", True, flow_style=False)
        for child in value:
            yield from _nodes(child, seen)
        yield SequenceEndEvent()
    else:
        yield _scalar(value)


def _scalar(value: object) -> ScalarEvent:
    if isinstance(value, str):
        implicit = resolve_plain_scalar(value) == _TAG + "str"
        return ScalarEvent(None, _TAG + "str", (implicit, True), value)
    if isinstance(value, bool):
        return ScalarEvent(None, _TAG + "bool", (True, False), "true" if value else "false")
    if value is None:
        return ScalarEvent(None, _TAG + "null", (True, False), "null")
    if isinstance(value, int):
        return ScalarEvent(None, _TAG + "int", (True, False), str(value))
    if isinstance(value, float):
        if math.isnan(value):
            text = ".nan"
        elif math.isinf(value):
            text = ".inf" if value > 0 else "-.inf"
        else:
            text = repr(value).lower()
            if "." not in text and "e" in text:
                text = text.replace("e", ".0e", 1)
        return ScalarEvent(None, _TAG + "float", (True, False), text)
    raise _UseRepresenterError


def _events(value: object) -> Iterator[Event]:
    yield StreamStartEvent()
    yield DocumentStartEvent()
    yield from _nodes(value, set())
    yield DocumentEndEvent()
    yield StreamEndEvent()


def dump_manifest(value: object) -> str:
    """Emit one Kubernetes YAML document, preserving mapping order and scalar types."""
    try:
        return yaml.emit(_events(value), Dumper=yaml.CSafeDumper)
    except _UseRepresenterError:
        # Raw YAML can contain aliases, cycles, or types outside JSON.
        return yaml.dump(value, Dumper=KubernetesDumper, sort_keys=False, default_flow_style=False)
