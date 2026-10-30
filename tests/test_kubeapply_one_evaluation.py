# SPDX-License-Identifier: Apache-2.0
"""`ekn kubeapply` pushes the store paths of the evaluation it applies (#1130).

A second evaluation reads the working tree again. An edit between the two
pushed one manifest and applied another, and the fetchability check refused
the apply. Nothing here reaches Nix or a cluster: the run stops at the API,
after the push.
"""

from __future__ import annotations

from typing import Any

import kr8s.asyncio
import pytest

from ekn import cli
from ekn.eval import CacheConfigResult, KubeApplyConfigResult

CACHE = CacheConfigResult(cache_to=["ssh-ng://cache"], cache_paths=["/nix/store/" + "a" * 32 + "-source"])


class Stop(Exception):
    pass


@pytest.mark.parametrize("cache_push", [True, False])
async def test_the_push_is_the_applied_evaluations(monkeypatch: pytest.MonkeyPatch, cache_push: bool) -> None:
    evaluated: list[dict[str, Any]] = []
    pushed: list[CacheConfigResult] = []

    async def evaluate(*_args: Any, **kwargs: Any) -> KubeApplyConfigResult:
        evaluated.append(kwargs)
        return KubeApplyConfigResult.model_validate(
            {
                "groups": [{"unit": None, "field_manager": "ekn", "objects": []}],
                "environment": "test",
                "resource_priority": {},
                "sops_age_identities": [],
                "handAppliedUnits": [],
                "cache": CACHE if kwargs.get("cache") else None,
            }
        )

    async def evaluate_again(*_args: Any, **_kwargs: Any) -> CacheConfigResult:
        raise AssertionError("the push evaluated the configuration a second time")

    async def push(cfg: CacheConfigResult, **_kwargs: Any) -> None:
        pushed.append(cfg)

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def api(*_args: Any, **_kwargs: Any) -> object:
        raise Stop

    monkeypatch.setattr(cli, "evaluate_kubeapply_config", evaluate)
    monkeypatch.setattr(cli, "evaluate_cache_config", evaluate_again)
    monkeypatch.setattr(cli, "_push_cache_config", push)
    monkeypatch.setattr(cli, "_run_pre_apply", nothing)
    monkeypatch.setattr(kr8s.asyncio, "api", api)

    with pytest.raises(Stop):
        await cli.KubeApply(file=".", cache_push=cache_push, skip_schema_check=True).run()

    assert [kwargs.get("cache") for kwargs in evaluated] == [cache_push]
    assert pushed == ([CACHE] if cache_push else [])
