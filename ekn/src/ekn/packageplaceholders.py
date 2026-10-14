from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ekn.manifestyaml import dump_manifest
from ekn.nixyaml import from_yaml_stream

if TYPE_CHECKING:
    from collections.abc import Iterator

    from huggorm.jsonprimop import JsonValue

_ENVIRONMENT_VARIABLE = "EKN_PACKAGE_PLACEHOLDERS"
_STORE_ROOT = re.compile(r"/nix/store/(?:[0-9abcdfghijklmnpqrsvwxyz]{32}|A{32})-[A-Za-z0-9._+?=-]+")
_PLACEHOLDER = "/nix/store/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA-placeholder"
_HASH_ANNOTATIONS = ("ekn.dev/manifest-hash", "ekn.dev/manifest-digest")


@contextmanager
def evaluation_mode(*, enabled: bool) -> Iterator[None]:
    """Scope the package-placeholder bridge to one CLI evaluation."""
    previous = os.environ.pop(_ENVIRONMENT_VARIABLE, None)
    if enabled:
        os.environ[_ENVIRONMENT_VARIABLE] = "1"
    try:
        yield
    finally:
        os.environ.pop(_ENVIRONMENT_VARIABLE, None)
        if previous is not None:
            os.environ[_ENVIRONMENT_VARIABLE] = previous


def _normalize(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return _STORE_ROOT.sub(_PLACEHOLDER, value)
    if isinstance(value, list):
        return [_normalize(child) for child in value]
    if isinstance(value, dict):
        return {key: _normalize(child) for key, child in value.items()}
    return value


def normalize_manifest_yaml(content: str) -> str:
    """Normalize package roots and derived hashes for Kubernetes comparison only."""
    objects = from_yaml_stream(content)
    if not objects or not all(isinstance(obj, dict) and "apiVersion" in obj and "kind" in obj for obj in objects):
        return content
    normalized = []
    for obj in objects:
        result = _normalize(obj)
        if isinstance(result, dict):
            metadata = result.get("metadata")
            if isinstance(metadata, dict):
                annotations = metadata.get("annotations")
                if isinstance(annotations, dict):
                    for name in _HASH_ANNOTATIONS:
                        annotations.pop(name, None)
                    if not annotations:
                        metadata.pop("annotations", None)
        normalized.append(dump_manifest(result))
    return "---\n".join(normalized)
