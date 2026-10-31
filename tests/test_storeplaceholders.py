"""Downstream placeholders in an evaluated manifest, and their real paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ekn.storeplaceholders import PlaceholderMismatchError, has_placeholders, placeholder_map, rewrite

if TYPE_CHECKING:
    from huggorm.jsonprimop import JsonValue

SERVE = "/" + "1" * 52
WORKER = "/" + "2" * 52
SERVE_PATH = "/nix/store/" + "a" * 32 + "-dynatron-serve"
WORKER_PATH = "/nix/store/" + "b" * 32 + "-textarion-pandoc-worker"


def deployment(serve: str, worker: str) -> JsonValue:
    """The shapes a real one carried: a command, an annotation listing two
    closures, and a JSON list inside an environment value."""
    return {
        "kind": "Deployment",
        "metadata": {"annotations": {"nixkube/pod": f"{serve} {worker}"}},
        "spec": {
            "containers": [
                {
                    "command": [f"{serve}/bin/dynatron-serve"],
                    "env": [{"name": "PANDOC_COMMAND", "value": f'["{worker}/bin/worker"]'}],
                    "replicas": 1,
                }
            ]
        },
    }


class TestTheMap:
    def test_each_placeholder_finds_its_path(self) -> None:
        mapping = placeholder_map(
            [deployment(SERVE, WORKER)],
            [deployment(SERVE_PATH, WORKER_PATH)],
        )

        assert mapping == {SERVE: SERVE_PATH, WORKER: WORKER_PATH}

    def test_a_manifest_with_no_placeholder_maps_nothing(self) -> None:
        assert placeholder_map([deployment(SERVE_PATH, WORKER_PATH)], [deployment(SERVE_PATH, WORKER_PATH)]) == {}

    @pytest.mark.parametrize(
        "resolved",
        [
            # Another string changed, where no placeholder stood.
            [{**deployment(SERVE_PATH, WORKER_PATH), "kind": "StatefulSet"}],  # type: ignore[dict-item]
            # A list grew.
            [deployment(SERVE_PATH, WORKER_PATH), {}],
            # The placeholder became something that is not a store path.
            [deployment("/tmp/serve", WORKER_PATH)],
        ],
    )
    def test_two_manifests_that_do_not_line_up_are_refused(self, resolved: JsonValue) -> None:
        with pytest.raises(PlaceholderMismatchError):
            placeholder_map([deployment(SERVE, WORKER)], resolved)

    def test_one_placeholder_for_two_paths_is_refused(self) -> None:
        with pytest.raises(PlaceholderMismatchError, match="two store paths"):
            placeholder_map(
                [deployment(SERVE, WORKER), deployment(SERVE, WORKER)],
                [deployment(SERVE_PATH, WORKER_PATH), deployment(WORKER_PATH, WORKER_PATH)],
            )


class TestTheRewrite:
    def test_every_placeholder_becomes_its_path(self) -> None:
        rewritten = rewrite([deployment(SERVE, WORKER)], {SERVE: SERVE_PATH, WORKER: WORKER_PATH})

        assert rewritten == [deployment(SERVE_PATH, WORKER_PATH)]
        assert not has_placeholders(rewritten)

    def test_a_placeholder_the_map_lacks_is_left_for_the_caller_to_see(self) -> None:
        rewritten = rewrite([deployment(SERVE, WORKER)], {SERVE: SERVE_PATH})

        assert has_placeholders(rewritten)

    def test_a_store_path_hash_is_never_taken_for_a_placeholder(self) -> None:
        """32 characters, not 52, and the rest of the name follows."""
        assert not has_placeholders(deployment(SERVE_PATH, WORKER_PATH))
