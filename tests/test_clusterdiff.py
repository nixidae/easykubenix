from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import httpx
import kr8s
import pytest
from json_at import at

from ekn import clusterdiff
from ekn.clusterdiff import _normalize, cluster_diff

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from huggorm.jsonprimop import JsonValue
    from kappy.apply import Manifest
    from kr8s.asyncio import Api


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


class _FakeResponse:
    # kr8s reads `Warning` off the headers of every apply response and logs
    # each one, so a fake with no `headers` raises rather than applying.
    def __init__(self, data: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self._data = data
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._data


class FakeApi:
    """Stands in for kr8s's real Api: answers GET (live lookup) and PATCH
    (dry-run apply) calls from a canned script instead of a live cluster.
    """

    namespace = "default"

    def as_api(self) -> Api:
        """This fake, typed as the client it stands in for."""
        return cast("Api", self)

    def __init__(self, script: dict[tuple[str, str], Any]) -> None:
        self._script = script
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    @asynccontextmanager
    async def call_api(
        self,
        method: str,
        *,
        version: str | None = None,
        url: str | None = None,
        namespace: str | None = None,
        content: str | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> AsyncIterator[_FakeResponse]:
        self.calls.append((method, url or "", params))
        result = self._script[(method, url or "")]
        if isinstance(result, Exception):
            raise result
        yield _FakeResponse(result)

    # An API server that serves nothing but the built-in core kinds. That is
    # what `kappy.apply.discover` reads, and a kind absent from it is exactly the
    # case this file is about: a CRD nobody has applied to the cluster yet.
    # `discover` then raises ValueError, which `cluster_diff` reports.
    async def async_api_resources(self) -> list[dict[str, Any]]:
        return []

    async def async_api_resources_uncached(self) -> list[dict[str, Any]]:
        return []


def _not_found() -> kr8s.ServerError:
    response = httpx.Response(404, request=httpx.Request("GET", "http://x/"))
    return kr8s.ServerError("not found", response=response)


def _missing_namespace(namespace: str) -> kr8s.ServerError:
    response = httpx.Response(
        404,
        request=httpx.Request("PATCH", "http://x/"),
        json={
            "kind": "Status",
            "status": "Failure",
            "message": f'namespaces "{namespace}" not found',
            "reason": "NotFound",
            "details": {"name": namespace, "kind": "namespaces"},
            "code": 404,
        },
    )
    return kr8s.ServerError(f'namespaces "{namespace}" not found', response=response)


def _config_map(name: str, data: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": "default"},
        "data": data,
    }


class _CrdApi(FakeApi):
    async def async_api_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "kind": "CustomResourceDefinition",
                "version": "apiextensions.k8s.io/v1",
                "name": "customresourcedefinitions",
                "namespaced": False,
            }
        ]


class TestNormalize:
    def test_strips_noisy_metadata_and_status(self) -> None:
        obj: Manifest = {
            "kind": "ConfigMap",
            "metadata": {
                "name": "cfg",
                "resourceVersion": "123",
                "managedFields": [{"manager": "kubectl"}],
                "uid": "abc-def",
                "generation": 3,
                "creationTimestamp": "2026-01-01T00:00:00Z",
                "annotations": {
                    "kubectl.kubernetes.io/last-applied-configuration": "{}",
                    "keep-me": "yes",
                },
            },
            "data": {"key": "value"},
            "status": {"phase": "Active"},
        }

        result = _normalize(obj)

        assert "status" not in result
        metadata: JsonValue = {"name": "cfg", "annotations": {"keep-me": "yes"}}
        assert result["metadata"] == metadata
        assert result["data"] == {"key": "value"}


class TestClusterDiff:
    @pytest.mark.parametrize("skip_crds", [False, True])
    async def test_skip_crds_avoids_live_reads_and_dry_runs(self, skip_crds: bool) -> None:
        crd: Manifest = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "probes.example.org"},
            "spec": {"description": "new schema"},
        }
        cfg = _config_map("cfg", {"key": "updated"})
        crd_url = "customresourcedefinitions/probes.example.org"
        api = _CrdApi(
            {
                ("GET", crd_url): {**crd, "spec": {"description": "old schema"}},
                ("PATCH", crd_url): crd,
                ("GET", "configmaps/cfg"): _config_map("cfg", {"key": "old"}),
                ("PATCH", "configmaps/cfg"): cfg,
            }
        )
        result = await cluster_diff([crd, cfg], api=api.as_api(), skip_crds=skip_crds)
        assert "+  key: updated" in result
        assert ("CustomResourceDefinition" in result) is (not skip_crds)
        crd_calls = [(method, url) for method, url, _ in api.calls if url == crd_url]
        assert crd_calls == ([] if skip_crds else [("GET", crd_url), ("PATCH", crd_url)])
        assert all(
            params is not None and params.get("dryRun") == "All" for method, _, params in api.calls if method == "PATCH"
        )

    async def test_skip_crd_only_diff_needs_no_object_api_calls(self) -> None:
        crd: Manifest = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "probes.example.org"},
        }
        api = FakeApi({})
        assert await cluster_diff([crd], api=api.as_api(), skip_crds=True) == ""
        assert api.calls == []

    async def test_no_diff_when_live_matches_predicted(self) -> None:
        spec = _config_map("cfg", {"key": "value"})
        api = FakeApi(
            {
                ("GET", "configmaps/cfg"): spec,
                ("PATCH", "configmaps/cfg"): spec,
            }
        )

        result = await cluster_diff([spec], api=api.as_api())

        assert result == ""

    async def test_diff_for_new_object(self) -> None:
        spec = _config_map("brand-new", {"key": "value"})
        api = FakeApi(
            {
                ("GET", "configmaps/brand-new"): _not_found(),
                ("PATCH", "configmaps/brand-new"): spec,
            }
        )

        result = await cluster_diff([spec], api=api.as_api())

        assert "live/default/ConfigMap/brand-new" in result
        assert "predicted/default/ConfigMap/brand-new" in result
        assert "+data:" in result
        assert "+  key: value" in result

    async def test_diff_shows_changed_field(self) -> None:
        spec = _config_map("cfg", {"key": "updated"})
        live = _config_map("cfg", {"key": "old"})
        api = FakeApi(
            {
                ("GET", "configmaps/cfg"): live,
                ("PATCH", "configmaps/cfg"): spec,
            }
        )

        result = await cluster_diff([spec], api=api.as_api())

        assert "-  key: old" in result
        assert "+  key: updated" in result

    async def test_missing_namespace_shows_as_full_addition(self) -> None:
        spec = _config_map("argocd-cm", {"key": "value"})
        spec["metadata"]["namespace"] = "argocd"
        api = FakeApi(
            {
                ("GET", "configmaps/argocd-cm"): _not_found(),
                ("PATCH", "configmaps/argocd-cm"): _missing_namespace("argocd"),
                # kr8s reads any 404 from the apply PATCH as "the object is
                # not there yet" and retries as a create. A real API server
                # answers that POST with the same missing namespace, so the
                # fake does too.
                ("POST", "configmaps"): _missing_namespace("argocd"),
            }
        )

        result = await cluster_diff([spec], api=api.as_api())

        assert "argocd/ConfigMap/argocd-cm" in result
        assert "+data:" in result
        assert "+  key: value" in result

    async def test_unregistered_kind_is_reported_not_crashed(self) -> None:
        application: Manifest = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {"name": "bootstrap", "namespace": "argocd"},
            "spec": {},
        }
        cfg = _config_map("cfg", {"key": "value"})
        api = FakeApi(
            {
                ("GET", "configmaps/cfg"): cfg,
                ("PATCH", "configmaps/cfg"): cfg,
            }
        )

        result = await cluster_diff([application, cfg], api=api.as_api())

        assert "argocd/Application/bootstrap" in result
        assert "not yet registered" in result
        # The one resolvable object still gets a real (empty, matching) diff --
        # one bad kind doesn't abort the whole run.
        assert result.count("#") == 1


class TestSeededObjects:
    """`ekn clusterdiff` follows the same rule the apply does, keyed on the
    variable: a seed whose variable is unset is not compared, because an
    apply would neither update nor remove it."""

    def test_a_seeded_field_is_redacted(self) -> None:
        # It is the predicted side that leaks: a live Secret comes back
        # base64 in `data`, but the predicted object holds the substituted
        # plaintext and would be rendered straight into the diff.
        redacted = clusterdiff._redact(
            {
                "kind": "Secret",
                "stringData": {"password": "s3cret", "username": "ci"},
                "data": {"password": "czNjcmV0"},
            },
            {"password"},
        )
        assert at(redacted, "stringData", "password") == clusterdiff._REDACTED
        assert at(redacted, "stringData", "username") == "ci"
        assert at(redacted, "data", "password") == clusterdiff._REDACTED

    def test_nothing_is_redacted_without_seeded_fields(self) -> None:
        obj: Manifest = {"kind": "Secret", "stringData": {"password": "s3cret"}}
        assert clusterdiff._redact(obj, set()) == obj
