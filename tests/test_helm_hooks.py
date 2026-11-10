from __future__ import annotations

from pathlib import Path

import pytest

from ekn.eval import evaluate_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NIX_TEST_FILE = PROJECT_ROOT / "tests/test_helm_hooks.nix"

CHECKS = [
    "changedSpecIsANewJob",
    "sameSpecIsTheSameJob",
    "plainJobKeepsItsName",
    "hookOtherThanJobKeepsItsName",
    "deleteHookIsDropped",
    "testHookIsDropped",
    "nameFitsALabel",
    "disabledLeavesTheName",
    "transformerKeepsTheHash",
]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("check", CHECKS)
async def test_helm_hooks(check: str) -> None:
    assert await evaluate_file(NIX_TEST_FILE, check) is True
