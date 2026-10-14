from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING

import structlog

from ekn.manifestyaml import dump_manifest
from ekn.nixyaml import from_yaml_stream

if TYPE_CHECKING:
    from collections.abc import Iterator

    from huggorm.jsonprimop import JsonValue

_ENVIRONMENT_VARIABLE = "EKN_PACKAGE_PLACEHOLDERS"
_STORE_ROOT = re.compile(r"/nix/store/(?:[0-9abcdfghijklmnpqrsvwxyz]{32}|A{32})-[A-Za-z0-9._+?=-]+")
_PLACEHOLDER = "/nix/store/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA-placeholder"
_HASH_ANNOTATIONS = ("ekn.dev/manifest-hash", "ekn.dev/manifest-digest")
_HASH_PATHS_ANNOTATION = "ekn.dev/package-placeholder-hashes"
_REAL_STORE_ROOT = re.compile(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9._+?=-]+")
_log = structlog.get_logger()


def _unwrapped_fields(value: JsonValue, path: str) -> Iterator[str]:
    if isinstance(value, str) and _REAL_STORE_ROOT.search(value):
        yield path
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _unwrapped_fields(child, f"{path}/{key.replace('~', '~0').replace('/', '~1')}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _unwrapped_fields(child, f"{path}/{index}")


def warn_unwrapped_packages(objects: list[JsonValue]) -> None:
    """Report real package paths in preview manifests without logging their values."""
    fields: list[str] = []

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        metadata = obj.get("metadata")
        if not isinstance(metadata, dict):
            continue
        label = f"{obj.get('kind', '?')}/{metadata.get('namespace', '_cluster')}/{metadata.get('name', '?')}"
        fields.extend(_unwrapped_fields(obj, label))
    if fields:
        _log.warning("unwrapped package paths remain in preview manifests", fields=sorted(fields))


def declared_hash_paths(content: str) -> tuple[str, ...]:
    """Read explicit comparison hints for hashes that include placeholder paths."""
    paths: set[str] = set()
    for obj in from_yaml_stream(content):
        if not isinstance(obj, dict):
            continue
        metadata = obj.get("metadata")
        if not isinstance(metadata, dict):
            continue
        annotations = metadata.get("annotations")
        if not isinstance(annotations, dict) or _HASH_PATHS_ANNOTATION not in annotations:
            continue
        encoded = annotations[_HASH_PATHS_ANNOTATION]
        if not isinstance(encoded, str):
            raise TypeError("package-placeholder-hashes must contain a JSON array of pointers")
        decoded = json.loads(encoded)
        if not isinstance(decoded, list) or not all(isinstance(path, str) and path.startswith("/") for path in decoded):
            raise ValueError("package-placeholder-hashes must contain a JSON array of pointers")
        paths.update(decoded)
    return tuple(sorted(paths))


def _remove_hash(value: JsonValue, pointer: str) -> None:
    keys = [key.replace("~1", "/").replace("~0", "~") for key in pointer[1:].split("/")]
    parent = value
    for key in keys[:-1]:
        if isinstance(parent, dict):
            parent = parent.get(key)
        elif isinstance(parent, list) and key.isdigit() and int(key) < len(parent):
            parent = parent[int(key)]
        else:
            return
    if isinstance(parent, dict):
        parent.pop(keys[-1], None)


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
        result = {key: _normalize(child) for key, child in value.items()}
        if result.get("name") in ("PATH", "PYTHONPATH"):
            path = result.get("value")
            if isinstance(path, str):
                parts = path.split(":")
                if all(part.startswith(_PLACEHOLDER + "/") for part in parts):
                    result["value"] = ":".join(dict.fromkeys(parts))
        return result
    return value


def normalize_manifest_yaml(content: str, *, hash_paths: tuple[str, ...] = ()) -> str:
    """Normalize package roots and derived hashes for Kubernetes comparison only."""
    objects = from_yaml_stream(content)
    if not objects or not all(isinstance(obj, dict) and "apiVersion" in obj and "kind" in obj for obj in objects):
        return content
    normalized = []
    for obj in objects:
        result = _normalize(obj)
        for pointer in hash_paths:
            _remove_hash(result, pointer)
        if isinstance(result, dict):
            metadata = result.get("metadata")
            if isinstance(metadata, dict):
                annotations = metadata.get("annotations")
                if isinstance(annotations, dict):
                    for name in (*_HASH_ANNOTATIONS, _HASH_PATHS_ANNOTATION):
                        annotations.pop(name, None)
                    if not annotations:
                        metadata.pop("annotations", None)
        normalized.append(dump_manifest(result))
    return "---\n".join(normalized)
