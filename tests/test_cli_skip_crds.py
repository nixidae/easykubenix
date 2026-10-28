from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ekn import cli
from ekn._cli import build_parser
from ekn.eval import KubeApplyConfigResult

if TYPE_CHECKING:
    from huggorm.jsonprimop import JsonValue


@pytest.mark.parametrize("operation", ["render", "diff", "clusterdiff"])
def test_inspection_commands_accept_skip_crds(operation: str) -> None:
    parsed = build_parser(cli.Ekn).parse_args([operation, "--skip-crds"])
    assert parsed.skip_crds is True


@pytest.mark.parametrize("operation", ["commit", "deploy", "kubeapply"])
def test_delivery_commands_reject_skip_crds(operation: str) -> None:
    with pytest.raises(SystemExit):
        build_parser(cli.Ekn).parse_args([operation, "--skip-crds"])


@pytest.mark.parametrize("skip_crds", [False, True])
@pytest.mark.parametrize("validate", [False, True])
async def test_render_validates_crds_before_filtering_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], skip_crds: bool, validate: bool
) -> None:
    objects: list[JsonValue] = [
        {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "probes.example.org"},
        },
        {"apiVersion": "example.org/v1", "kind": "Probe", "metadata": {"name": "probe"}},
    ]
    checked: list[object] = []

    async def evaluate(*_args: object, **kwargs: object) -> tuple[list[JsonValue], str | None]:
        assert kwargs["with_spec"] is validate
        return objects, "spec" if validate else None

    async def check(manifests: object, *_args: object, **_kwargs: object) -> None:
        checked.append(manifests)

    monkeypatch.setattr(cli, "evaluate_generated_with_spec", evaluate)
    monkeypatch.setattr(cli, "_check_schemas", check)
    command = object.__new__(cli.Render)
    command.file = None
    command.flake = None
    command.attr = None
    command.validate = validate
    command.package_placeholders = False
    command.skip_crds = skip_crds
    command.target = []
    await command.run()
    output = capsys.readouterr().out
    assert "kind: Probe" in output
    assert ("kind: CustomResourceDefinition" in output) is (not skip_crds)
    assert checked == ([objects] if validate else [])


async def test_diff_passes_skip_flag_to_render_and_comparison(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    flags: list[object] = []

    async def resolve(*_args: object, **kwargs: object) -> tuple[str, None, list[tuple[str, str]], list[str]]:
        flags.append(kwargs["skip_crds"])
        return "deploy", None, [], []

    def diff(*_args: object, **kwargs: object) -> str:
        flags.append(kwargs["skip_crds"])
        return "visible workload diff\n"

    async def status(*_args: object) -> None:
        pass

    monkeypatch.setattr(cli, "_resolve_gitops", resolve)
    monkeypatch.setattr(cli, "diff_manifests", diff)
    monkeypatch.setattr(cli, "try_jj_status", status)
    command = object.__new__(cli.Diff)
    command.file = None
    command.flake = None
    command.attr = None
    command.skip_crds = True
    command.package_placeholders = False
    await command.run()
    assert flags == [True, True]
    assert capsys.readouterr().out == "visible workload diff\n"


@pytest.mark.parametrize("skip_crds", [False, True])
async def test_clusterdiff_filters_before_decryption_and_keeps_cluster_fence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], skip_crds: bool
) -> None:
    objects = [
        {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "probes.example.org"},
        },
        {"apiVersion": "example.org/v1", "kind": "Probe", "metadata": {"name": "probe"}},
    ]
    cfg = KubeApplyConfigResult.model_validate(
        {
            "groups": [{"unit": None, "field_manager": "ekn", "objects": objects}],
            "environment": "test",
            "resource_priority": {},
            "sops_age_identities": [],
        }
    )
    selected = objects[1:] if skip_crds else objects
    decrypted: list[object] = []
    calls: list[str] = []
    api = object()

    async def evaluate(*_args: object) -> KubeApplyConfigResult:
        return cfg

    async def decrypt(manifest: object) -> object:
        decrypted.append(manifest)
        return manifest

    async def connect() -> object:
        return api

    async def fence(_self: object, actual_api: object, actual_cfg: object) -> None:
        assert actual_api is api
        assert actual_cfg is cfg
        calls.append("fence")

    async def diff(manifests: object, **kwargs: object) -> str:
        assert manifests == selected
        assert kwargs == {"api": api, "skip_crds": skip_crds}
        calls.append("diff")
        return "visible custom resource diff\n"

    monkeypatch.setattr(cli, "evaluate_kubeapply_config", evaluate)
    monkeypatch.setattr(cli, "maybe_decrypt", decrypt)
    monkeypatch.setattr("kr8s.asyncio.api", connect)
    monkeypatch.setattr(cli.ClusterDiff, "_fence", fence)
    monkeypatch.setattr(cli, "cluster_diff", diff)
    await cli.ClusterDiff(file=".", skip_crds=skip_crds).run()
    assert decrypted == selected
    assert calls == ["fence", "diff"]
    assert capsys.readouterr().out == "visible custom resource diff\n"
