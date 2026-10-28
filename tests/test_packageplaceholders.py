from __future__ import annotations

import copy
import os
from typing import TYPE_CHECKING

import pygit2
import pytest
from json_at import at, mapping_at, text_at

from ekn import cli, packageplaceholders
from ekn._cli import build_parser
from ekn.git import commit_manifests, diff_manifests
from ekn.gitops import flatten_manifests
from ekn.nixyaml import from_yaml
from ekn.packageplaceholders import evaluation_mode, normalize_manifest_yaml

if TYPE_CHECKING:
    from pathlib import Path

    from huggorm.jsonprimop import JsonValue
    from kappy.apply import Manifest

VARIABLE = "EKN_PACKAGE_PLACEHOLDERS"
REAL = "/nix/store/" + "0" * 32 + "-program-1.0"
PREVIEW = "/nix/store/" + "A" * 32 + "-program"
MANIFEST: Manifest = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {
        "name": "probe",
        "annotations": {"ekn.dev/manifest-hash": "old", "ekn.dev/manifest-digest": "old", "keep": "yes"},
    },
    "data": {"command": f"{REAL}/bin/program --file={REAL}/share/input", "other": "unchanged"},
}


@pytest.mark.parametrize("operation", ["render", "diff"])
def test_inspection_accepts_flag(operation: str) -> None:
    parsed = build_parser(cli.Ekn).parse_args([operation, "--package-placeholders", "--skip-crds"])
    assert parsed.package_placeholders is True
    assert parsed.skip_crds is True


@pytest.mark.parametrize("operation", ["commit", "deploy", "kubeapply", "clusterdiff", "eval", "validate"])
def test_other_commands_reject_flag(operation: str) -> None:
    with pytest.raises(SystemExit):
        build_parser(cli.Ekn).parse_args([operation, "--package-placeholders"])


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("previous", [None, "1", "custom"])
def test_mode_restores_environment_after_failure(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, previous: str | None
) -> None:
    if previous is None:
        monkeypatch.delenv(VARIABLE, raising=False)
    else:
        monkeypatch.setenv(VARIABLE, previous)

    def fail() -> None:
        with evaluation_mode(enabled=enabled):
            assert os.environ.get(VARIABLE) == ("1" if enabled else None)
            raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        fail()
    assert os.environ.get(VARIABLE) == previous


@pytest.mark.parametrize("enabled", [False, True])
async def test_render_flag_controls_evaluation(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setenv(VARIABLE, "inherited")

    async def evaluate(*_args: object, **_kwargs: object) -> tuple[list[object], None]:
        assert os.environ.get(VARIABLE) == ("1" if enabled else None)
        return [], None

    monkeypatch.setattr(cli, "evaluate_generated_with_spec", evaluate)
    await cli.Render(file=".", package_placeholders=enabled).run()
    assert os.environ[VARIABLE] == "inherited"


@pytest.mark.parametrize("enabled", [False, True])
async def test_diff_flag_controls_evaluation_and_comparison(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setenv(VARIABLE, "inherited")

    async def resolve(*_args: object, **kwargs: object) -> tuple[str, None, list[tuple[str, str]], list[str]]:
        assert os.environ.get(VARIABLE) == ("1" if enabled else None)
        assert kwargs["skip_crds"] is True
        return "deploy", None, [], []

    def diff(*_args: object, **kwargs: object) -> None:
        assert kwargs == {"skip_crds": True, "package_placeholders": enabled}

    async def status(*_args: object) -> None:
        pass

    monkeypatch.setattr(cli, "_resolve_gitops", resolve)
    monkeypatch.setattr(cli, "diff_manifests", diff)
    monkeypatch.setattr(cli, "try_jj_status", status)
    await cli.Diff(file=".", skip_crds=True, package_placeholders=enabled).run()
    assert os.environ[VARIABLE] == "inherited"


async def test_delivery_clears_inherited_preview_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VARIABLE, "1")

    async def run(_self: object) -> None:
        assert VARIABLE not in os.environ

    monkeypatch.setattr(cli.KubeApply, "run", run)
    await cli._run_profiled(cli.KubeApply(file="."))
    assert os.environ[VARIABLE] == "1"


def _preview_manifest() -> Manifest:
    obj = copy.deepcopy(MANIFEST)
    annotations = mapping_at(obj, "metadata", "annotations")
    annotations["ekn.dev/manifest-hash"] = "new"
    annotations.pop("ekn.dev/manifest-digest")
    mapping_at(obj, "data")["command"] = text_at(obj, "data", "command").replace(REAL, PREVIEW)
    return obj


def test_normalization_preserves_paths_after_roots_and_unrelated_annotations() -> None:
    [(_, content)] = flatten_manifests([MANIFEST])
    normalized = from_yaml(normalize_manifest_yaml(content))
    assert isinstance(normalized, dict)
    metadata: JsonValue = {"name": "probe", "annotations": {"keep": "yes"}}
    data: JsonValue = {
        "command": f"{PREVIEW.replace('-program', '-placeholder')}/bin/program "
        f"--file={PREVIEW.replace('-program', '-placeholder')}/share/input",
        "other": "unchanged",
    }
    assert normalized["metadata"] == metadata
    assert normalized["data"] == data


@pytest.mark.parametrize("reverse", [False, True])
def test_diff_normalizes_both_inputs_without_moving_refs(test_repo: Path, reverse: bool) -> None:
    old, new = (MANIFEST, _preview_manifest()) if not reverse else (_preview_manifest(), MANIFEST)
    commit_manifests(str(test_repo), "preview-test", flatten_manifests([old]), "baseline")
    repo = pygit2.Repository(str(test_repo))
    before = repo.references["refs/heads/preview-test"].target
    new_files = flatten_manifests([new])
    assert diff_manifests(str(test_repo), "preview-test", new_files) is not None
    assert diff_manifests(str(test_repo), "preview-test", new_files, package_placeholders=True) is None
    assert repo.references["refs/heads/preview-test"].target == before
    assert new_files == flatten_manifests([new])


@pytest.mark.parametrize("change", ["executable", "file", "setting", "annotation"])
def test_diff_keeps_other_changes_visible(test_repo: Path, change: str) -> None:
    commit_manifests(str(test_repo), "preview-test", flatten_manifests([MANIFEST]), "baseline")
    new = _preview_manifest()
    data = mapping_at(new, "data")
    if change == "executable":
        data["command"] = text_at(data, "command").replace("/bin/program", "/bin/other")
    elif change == "file":
        data["command"] = text_at(data, "command").replace("/share/input", "/share/other")
    elif change == "setting":
        data["other"] = "changed"
    else:
        mapping_at(new, "metadata", "annotations")["keep"] = "changed"
    diff = diff_manifests(str(test_repo), "preview-test", flatten_manifests([new]), package_placeholders=True)
    assert diff is not None
    assert ("other" if change in {"executable", "file"} else "changed") in diff


def test_diff_keeps_tofu_files_exact(test_repo: Path) -> None:
    commit_manifests(str(test_repo), "preview-test", [("config.tf.json", REAL)], "baseline")
    assert diff_manifests(str(test_repo), "preview-test", [("config.tf.json", PREVIEW)], package_placeholders=True)


@pytest.mark.parametrize("name", ["PATH", "PYTHONPATH"])
def test_assembled_package_paths_match_one_preview_reference(test_repo: Path, name: str) -> None:
    old = copy.deepcopy(MANIFEST)
    old["data"] = {}
    old["spec"] = {"containers": [{"env": [{"name": name, "value": f"{REAL}/lib:{REAL}-dependency/lib"}]}]}
    new = copy.deepcopy(old)
    mapping_at(new, "spec", "containers", 0, "env", 0)["value"] = f"{PREVIEW}/lib"
    commit_manifests(str(test_repo), "assembled-path-test", flatten_manifests([old]), "baseline")
    assert diff_manifests(str(test_repo), "assembled-path-test", flatten_manifests([new])) is not None
    assert (
        diff_manifests(str(test_repo), "assembled-path-test", flatten_manifests([new]), package_placeholders=True)
        is None
    )


@pytest.mark.parametrize(
    "value",
    [f"{REAL}/lib:{REAL}/bin", f"{REAL}/lib:/custom/lib", f"{REAL}/lib:", f":{REAL}/lib"],
)
def test_assembled_path_normalization_preserves_suffixes_literals_and_empty_entries(value: str) -> None:
    obj = copy.deepcopy(MANIFEST)
    obj["data"] = {}
    obj["spec"] = {"containers": [{"env": [{"name": "PYTHONPATH", "value": value}]}]}
    [(_, content)] = flatten_manifests([obj])
    normalized = from_yaml(normalize_manifest_yaml(content))
    assert isinstance(normalized, dict)
    assert at(normalized, "spec", "containers", 0, "env", 0, "value") == value.replace(
        REAL, packageplaceholders._PLACEHOLDER
    )


def test_non_path_environment_variables_preserve_repeated_entries() -> None:
    obj = copy.deepcopy(MANIFEST)
    obj["data"] = {}
    obj["spec"] = {"containers": [{"env": [{"name": "OTHER", "value": f"{REAL}/lib:{REAL}/lib"}]}]}
    [(_, content)] = flatten_manifests([obj])
    normalized = from_yaml(normalize_manifest_yaml(content))
    assert isinstance(normalized, dict)
    assert (
        at(normalized, "spec", "containers", 0, "env", 0, "value")
        == f"{packageplaceholders._PLACEHOLDER}/lib:{packageplaceholders._PLACEHOLDER}/lib"
    )


def test_non_manifest_yaml_stays_exact() -> None:
    content = f"resources:\n- {REAL}\n"
    assert normalize_manifest_yaml(content) == content


@pytest.mark.parametrize("digest", ["0" * 31, "0" * 33, "B" * 32])
def test_normalization_preserves_non_store_paths(digest: str) -> None:
    obj = copy.deepcopy(MANIFEST)
    mapping_at(obj, "data")["command"] = f"/nix/store/{digest}-program/bin/program"
    [(_, content)] = flatten_manifests([obj])
    normalized = from_yaml(normalize_manifest_yaml(content))
    assert isinstance(normalized, dict)
    assert normalized["data"] == obj["data"]


def test_normalization_preserves_types_and_embedded_data_keys() -> None:
    obj: Manifest = {
        "apiVersion": "example.org/v1",
        "kind": "Probe",
        "metadata": {"name": "probe", "annotations": {"ekn.dev/manifest-hash": "hash"}},
        "spec": {
            "maximum": 2**63 - 1,
            "string": "true",
            "boolean": True,
            "ekn.dev/manifest-hash": "user data",
            "command": [f"{REAL}/bin/program", "--enabled"],
        },
    }
    [(_, content)] = flatten_manifests([obj])
    normalized = from_yaml(normalize_manifest_yaml(content))
    assert isinstance(normalized, dict)
    metadata: JsonValue = {"name": "probe"}
    spec: JsonValue = {
        **mapping_at(obj, "spec"),
        "command": [f"{PREVIEW.replace('-program', '-placeholder')}/bin/program", "--enabled"],
    }
    assert normalized["metadata"] == metadata
    assert normalized["spec"] == spec


@pytest.mark.parametrize("reverse", [False, True])
def test_declared_rollout_hashes_normalize_both_sides(test_repo: Path, reverse: bool) -> None:
    old = copy.deepcopy(MANIFEST)
    new = _preview_manifest()
    old["spec"] = {"template": {"metadata": {"annotations": {"example.org/config": "old", "keep": "yes"}}}}
    new["spec"] = {"template": {"metadata": {"annotations": {"example.org/config": "new", "keep": "yes"}}}}
    mapping_at(new, "metadata", "annotations")["ekn.dev/package-placeholder-hashes"] = (
        '["/spec/template/metadata/annotations/example.org~1config"]'
    )
    before, after = (new, old) if reverse else (old, new)
    commit_manifests(str(test_repo), "preview-test", flatten_manifests([before]), "baseline")
    assert diff_manifests(str(test_repo), "preview-test", flatten_manifests([after]), package_placeholders=True) is None
    after = copy.deepcopy(after)
    mapping_at(after, "spec", "template", "metadata", "annotations")["keep"] = "changed"
    assert diff_manifests(str(test_repo), "preview-test", flatten_manifests([after]), package_placeholders=True)


def test_audit_warns_with_object_and_field_without_values(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings = []
    monkeypatch.setattr(packageplaceholders._log, "warning", lambda *args, **kwargs: warnings.append((args, kwargs)))
    packageplaceholders.warn_unwrapped_packages([MANIFEST])
    assert warnings == [
        (
            ("unwrapped package paths remain in preview manifests",),
            {"fields": ["ConfigMap/_cluster/probe/data/command"]},
        )
    ]
    assert REAL not in repr(warnings)
    warnings.clear()
    packageplaceholders.warn_unwrapped_packages([_preview_manifest()])
    assert warnings == []
