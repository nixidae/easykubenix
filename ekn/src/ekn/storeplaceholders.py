"""Nix's downstream placeholders in an evaluated manifest, and their real paths.

A store path whose derivation is content-addressed, or depends on a dynamic
derivation (`builtins.outputOf`), has no known output path at evaluation
time. Evaluation renders it as a downstream placeholder: `/` and 52 Nix32
characters, such as `/1szfgkx2...wns/bin/serve`. Nix rewrites a placeholder to
the real path when it builds a derivation that names it, so
`internal.manifestJSONFile` holds real paths, while `kubernetes.generated`
read with `to_python` still holds placeholders. An object applied from the
second runs a command that does not exist.

`placeholder_map` pairs the two renderings of one manifest and reads off each
placeholder's path; `rewrite` applies the map to any objects.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from huggorm.jsonprimop import JsonValue

_NIX32 = "0123456789abcdfghijklmnpqrsvwxyz"

#: A downstream placeholder: `/` and a SHA-256 in Nix32. A store path's hash
#: is 32 characters, so it never matches.
PLACEHOLDER = re.compile(rf"/[{_NIX32}]{{52}}(?![{_NIX32}])")

_STORE_PATH = rf"/nix/store/[{_NIX32}]{{32}}-[A-Za-z0-9._+?=-]+"


class PlaceholderMismatchError(ValueError):
    """The two renderings of a manifest do not line up."""


def has_placeholders(value: JsonValue) -> bool:
    """Whether any string in `value` holds a downstream placeholder.

    >>> has_placeholders({"command": ["/" + "1" * 52 + "/bin/x"]})
    True
    >>> has_placeholders({"command": ["/nix/store/" + "1" * 32 + "-x/bin/x"]})
    False
    """
    return any(PLACEHOLDER.search(text) for text in _strings(value))


def _strings(value: JsonValue) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _pairs(unresolved: JsonValue, resolved: JsonValue, where: str) -> Iterator[tuple[str, str, str]]:
    """Every string of `unresolved` with its counterpart in `resolved`."""
    if isinstance(unresolved, str) and isinstance(resolved, str):
        yield where, unresolved, resolved
    elif isinstance(unresolved, dict) and isinstance(resolved, dict):
        if unresolved.keys() != resolved.keys():
            raise PlaceholderMismatchError(f"{where}: the two manifests have different keys")
        for key, child in unresolved.items():
            yield from _pairs(child, resolved[key], f"{where}/{key}")
    elif isinstance(unresolved, list) and isinstance(resolved, list):
        if len(unresolved) != len(resolved):
            raise PlaceholderMismatchError(f"{where}: the two manifests have lists of different lengths")
        for index, (child, other) in enumerate(zip(unresolved, resolved, strict=True)):
            yield from _pairs(child, other, f"{where}/{index}")
    elif unresolved != resolved:
        raise PlaceholderMismatchError(f"{where}: the two manifests differ outside a string")


def placeholder_map(unresolved: JsonValue, resolved: JsonValue) -> dict[str, str]:
    """Each placeholder in `unresolved` and the store path `resolved` has there.

    The two are one manifest rendered twice: by evaluation, and by the build
    of `internal.manifestJSONFile`. They agree everywhere but on the
    placeholders, so the literal text around each one finds its path.

    >>> p = "/" + "1" * 52
    >>> path = "/nix/store/" + "a" * 32 + "-serve"
    >>> placeholder_map({"c": [p + "/bin/serve"]}, {"c": [path + "/bin/serve"]}) == {p: path}
    True
    """
    found: dict[str, str] = {}
    for where, before, after in _pairs(unresolved, resolved, ""):
        names = PLACEHOLDER.findall(before)
        if not names:
            if before != after:
                raise PlaceholderMismatchError(f"{where}: a string with no placeholder differs")
            continue
        literals = PLACEHOLDER.split(before)
        pattern = f"({_STORE_PATH})".join(re.escape(literal) for literal in literals)
        match = re.fullmatch(pattern, after)
        if match is None:
            raise PlaceholderMismatchError(f"{where}: no store path where the placeholder stood")
        for name, path in zip(names, match.groups(), strict=True):
            if found.setdefault(name, path) != path:
                raise PlaceholderMismatchError(f"{where}: {name} stands for two store paths")
    return found


def rewrite(value: JsonValue, mapping: Mapping[str, str]) -> JsonValue:
    """`value` with every placeholder in `mapping` replaced by its path.

    A placeholder the map does not name is left as it is; `has_placeholders`
    tells the caller whether any remain.
    """
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda match: mapping.get(match.group(0), match.group(0)), value)
    if isinstance(value, dict):
        return {key: rewrite(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [rewrite(child, mapping) for child in value]
    return value
