from __future__ import annotations

from pathlib import Path

import pytest

from ekn.git import commit_manifests, diff_manifests
from ekn.gitops import flatten_manifests
from ekn.nixyaml import from_go_like_yaml

SAMPLE_MANIFESTS = [
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "my-config", "namespace": "default"},
        "data": {"key": "value"},
    },
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "cluster-config", "namespace": "kube-system"},
        "data": {"setting": "true"},
    },
]


class TestFlattenManifests:
    @pytest.mark.parametrize("text", ["y", "Y", "n", "N", "08", "1e+06", "+_803.e+048", "-_9.e-515"])
    def test_strings_keep_their_type_in_kubernetes(self, text: str) -> None:
        manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "scalar-probe"},
            "data": {text: text},
        }
        [(_, content)] = flatten_manifests([manifest])
        assert from_go_like_yaml(content) == manifest

    def test_large_integer_schema_bounds_keep_their_value(self) -> None:
        manifest = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "probes.example.org"},
            "spec": {"minimum": -(2**63), "maximum": 2**63 - 1},
        }
        [(_, content)] = flatten_manifests([manifest])
        assert from_go_like_yaml(content) == manifest

    def test_list(self) -> None:
        files = flatten_manifests(SAMPLE_MANIFESTS)
        paths = [p for p, _ in files]
        assert "default/ConfigMap/my-config.yaml" in paths
        assert "kube-system/ConfigMap/cluster-config.yaml" in paths

    def test_content_yaml(self) -> None:
        files = flatten_manifests(SAMPLE_MANIFESTS)
        _, content = files[0]
        assert "apiVersion: v1" in content
        assert "kind: ConfigMap" in content

    def test_subdir(self) -> None:
        files = flatten_manifests(SAMPLE_MANIFESTS, subdir="./clusters/prod")
        paths = [p for p, _ in files]
        assert "clusters/prod/default/ConfigMap/my-config.yaml" in paths

    def test_not_list(self) -> None:
        with pytest.raises(TypeError, match="expected list"):
            flatten_manifests({"default": {}})


SOPS_MANIFESTS = [
    *SAMPLE_MANIFESTS,
    {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "eso-creds", "namespace": "default"},
        "sops": {"age": [], "version": "3.9.0"},
        "data": {"token": "ENC[...]"},
    },
]

CRD_MANIFEST = {
    "apiVersion": "apiextensions.k8s.io/v1",
    "kind": "CustomResourceDefinition",
    "metadata": {"name": "probes.example.org"},
    "spec": {"description": "first schema"},
}


def test_skip_crds_omits_files_and_kustomization_references() -> None:
    files = dict(flatten_manifests([*SAMPLE_MANIFESTS, CRD_MANIFEST], kustomize=True, skip_crds=True))
    assert files == dict(flatten_manifests(SAMPLE_MANIFESTS, kustomize=True))


@pytest.mark.parametrize("change", ["add", "delete", "modify", "unchanged"])
def test_skip_crds_ignores_both_sides_of_diff(test_repo: Path, change: str) -> None:
    old = SAMPLE_MANIFESTS if change == "add" else [*SAMPLE_MANIFESTS, CRD_MANIFEST]
    new = SAMPLE_MANIFESTS if change == "delete" else [*SAMPLE_MANIFESTS, CRD_MANIFEST]
    if change == "modify":
        new = [*SAMPLE_MANIFESTS, {**CRD_MANIFEST, "spec": {"description": "second schema"}}]
    commit_manifests(str(test_repo), "test-render", flatten_manifests(old, kustomize=True), "baseline")
    files = flatten_manifests(new, kustomize=True, skip_crds=True)
    assert diff_manifests(str(test_repo), "test-render", files, skip_crds=True) is None
    if change != "unchanged":
        assert diff_manifests(str(test_repo), "test-render", flatten_manifests(new, kustomize=True)) is not None


def test_skip_crds_keeps_workload_changes_visible(test_repo: Path) -> None:
    old = [*SAMPLE_MANIFESTS, CRD_MANIFEST]
    new = [{**SAMPLE_MANIFESTS[0], "data": {"key": "updated"}}, SAMPLE_MANIFESTS[1], CRD_MANIFEST]
    commit_manifests(str(test_repo), "test-render", flatten_manifests(old, kustomize=True), "baseline")
    diff = diff_manifests(
        str(test_repo), "test-render", flatten_manifests(new, kustomize=True, skip_crds=True), skip_crds=True
    )
    assert diff is not None
    assert "+  key: updated" in diff
    assert "CustomResourceDefinition" not in diff


def test_skip_crds_ignores_new_crd_only_target(test_repo: Path) -> None:
    files = flatten_manifests([CRD_MANIFEST], subdir="clusters/crds", kustomize=True)
    assert diff_manifests(str(test_repo), "missing-branch", files, skip_crds=True) is None


def test_skip_crds_ignores_encrypted_crd_generator(test_repo: Path) -> None:
    crd = {**CRD_MANIFEST, "sops": {"age": [], "version": "3.9.0"}}
    old = flatten_manifests([*SAMPLE_MANIFESTS, crd], kustomize=True)
    commit_manifests(str(test_repo), "test-render", old, "baseline")
    new = flatten_manifests(SAMPLE_MANIFESTS, kustomize=True)
    assert diff_manifests(str(test_repo), "test-render", new, skip_crds=True) is None


class TestFlattenManifestsKustomize:
    def test_plain_only_kustomization(self) -> None:
        files = flatten_manifests(SAMPLE_MANIFESTS, kustomize=True)
        paths = dict(files)
        assert "kustomization.yaml" in paths
        kustomization = paths["kustomization.yaml"]
        assert "default/ConfigMap/my-config.yaml" in kustomization
        assert "kube-system/ConfigMap/cluster-config.yaml" in kustomization
        assert "generators" not in kustomization

    def test_sops_object_gets_ksops_generator(self) -> None:
        files = flatten_manifests(SOPS_MANIFESTS, kustomize=True)
        paths = dict(files)
        assert "default/Secret/eso-creds.ksops-generator.yaml" in paths
        generator = paths["default/Secret/eso-creds.ksops-generator.yaml"]
        assert "kind: ksops" in generator
        # Regression: the generator's `files:` entry is relative to the
        # kustomization root (where kustomize invokes the ksops KRM
        # function from), not to the generator file's own directory -- a
        # bare "eso-creds.yaml" (which this substring check alone can't
        # distinguish from the correct full path) fails at kustomize-build
        # time with "no such file or directory" once the generator lives
        # in a namespace/kind/ subdirectory, which is always.
        assert "files:\n- default/Secret/eso-creds.yaml\n" in generator

        kustomization = paths["kustomization.yaml"]
        assert "default/Secret/eso-creds.yaml" not in kustomization
        assert "default/Secret/eso-creds.ksops-generator.yaml" in kustomization

    def test_subdir_relative_paths(self) -> None:
        files = flatten_manifests(SOPS_MANIFESTS, subdir="./bootstrap", kustomize=True)
        paths = dict(files)
        kustomization = paths["bootstrap/kustomization.yaml"]
        assert "default/ConfigMap/my-config.yaml" in kustomization
        assert "bootstrap/" not in kustomization


class TestCommit:
    def test_first_commit(self, test_repo: Path) -> None:
        files = flatten_manifests(SAMPLE_MANIFESTS)
        sha = commit_manifests(".", "test-render", files, "first commit")
        assert sha
        parents = (test_repo / ".git" / "refs" / "heads" / "test-render").read_text().strip()
        assert len(parents) == 40

    def test_second_commit(self, test_repo: Path) -> None:
        files = flatten_manifests(SAMPLE_MANIFESTS)
        commit_manifests(".", "test-render", files, "first")
        sha2 = commit_manifests(".", "test-render", files, "second")
        assert sha2

    def test_diff_none(self, test_repo: Path) -> None:
        files = flatten_manifests(SAMPLE_MANIFESTS)
        commit_manifests(".", "test-render", files, "first")
        result = diff_manifests(".", "test-render", files)
        assert result is None

    def test_diff_change(self, test_repo: Path) -> None:
        files = flatten_manifests(SAMPLE_MANIFESTS)
        commit_manifests(".", "test-render", files, "first")

        changed = [
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "my-config", "namespace": "default"},
                "data": {"key": "updated"},
            },
        ]
        new_files = flatten_manifests(changed)
        result = diff_manifests(".", "test-render", new_files)
        assert result is not None
        assert "key: updated" in result
