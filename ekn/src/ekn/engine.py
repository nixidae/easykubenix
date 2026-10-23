"""kappy, the apply engine, with the names `ekn` gives it.

kappy has no labels or field manager of its own: a caller names them. This
module holds the ones `ekn` chose and binds them once, so the rest of the
package calls the engine without repeating them.
"""

from __future__ import annotations

from functools import partial
from typing import Final

from kappy.apply import (
    apply_and_prune as _apply_and_prune,
    field_manager_for as _field_manager_for,
    prune_generation as _prune_generation,
    prune_selector as _prune_selector,
)
from kappy.directapply import converge_direct as _converge_direct

ENVIRONMENT_LABEL: Final = "ekn.dev/environment"
"""Stamped by `ekn` at apply time, on every object it applies. Its value is
`ekn.environment`. Deliberately not rendered into the manifests: an object a
GitOps engine synced from the committed YAML must not carry it, or `ekn` and
that engine both consider it theirs to prune."""

UNIT_LABEL: Final = "ekn.dev/deployment-unit"
"""Rendered by easykubenix onto every object in a deployment unit (see
gitops.nix), so it is on the object whoever applies it. `ekn` never writes
it, and only reads it to scope a prune."""

FIELD_MANAGER: Final = "ekn"
"""Server-side-apply field manager, unless a caller names another. A GitOps
target can (`gitOps.targets.<name>.fieldManager`), so that a bootstrap apply
can hand its objects to the controller that takes them over rather than
leaving every field it set owned by `ekn` forever -- SSA only releases a
field when its owning manager stops declaring it, and a bootstrap apply
never runs again."""

DELIVERY_MANAGERS: Final = frozenset({FIELD_MANAGER})
"""Field managers whose objects a prune may delete, before an apply adds its own."""

apply_and_prune = partial(
    _apply_and_prune,
    environment_label=ENVIRONMENT_LABEL,
    unit_label=UNIT_LABEL,
    field_manager=FIELD_MANAGER,
    delivery_managers=DELIVERY_MANAGERS,
)
converge_direct = partial(
    _converge_direct,
    environment_label=ENVIRONMENT_LABEL,
    unit_label=UNIT_LABEL,
    field_manager=FIELD_MANAGER,
)
prune_generation = partial(
    _prune_generation,
    environment_label=ENVIRONMENT_LABEL,
    unit_label=UNIT_LABEL,
    delivery_managers=DELIVERY_MANAGERS,
)
field_manager_for = partial(_field_manager_for, unit_label=UNIT_LABEL)
prune_selector = partial(_prune_selector, environment_label=ENVIRONMENT_LABEL, unit_label=UNIT_LABEL)

__all__ = [
    "DELIVERY_MANAGERS",
    "ENVIRONMENT_LABEL",
    "FIELD_MANAGER",
    "UNIT_LABEL",
    "apply_and_prune",
    "converge_direct",
    "field_manager_for",
    "prune_generation",
    "prune_selector",
]
