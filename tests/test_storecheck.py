"""Refusing an apply whose store paths no node can fetch.

The case this guards is a combination nobody would write a test for. On
nixlab2 a newly-working garbage collector removed `cacheEnv` from all four
node stores while the cache push that would have replaced it had been
failing for hours. Either half alone was survivable; together they took out
the only copy of the environment the store server mounts, and the push that
fixes it goes through that same store server.

Issue nixidae/nixidae#371, and #37 for why the walk is in `ekn`.

The probe is injected, so these exercise the walk itself -- the union across
substituters, the closure, and the three-way answer -- rather than a store.
The probe's own contract is checked against real stores; see
`storecheck.huggorm_probe`'s docstring for the measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import anyio
import pytest
from huggorm import AsyncStore, generate_secret_key

from ekn.storecheck import (
    Held,
    NoSubstitutersError,
    StorePathsUnavailableError,
    StorePathsUnsignedError,
    accepted,
    assert_fetchable,
    public_keys,
    send_again,
    store_paths_in,
    walk,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Set

    from huggorm import PathInfo, Signature

    from ekn.storecheck import Probe

CACHE_ENV = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-cacheEnv"
HTTPX = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-python3.14-httpx"
ABSENT = "cccccccccccccccccccccccccccccccc-never-pushed"

#: A made-up world: substituter -> path -> references.
type World = dict[str, dict[str, set[str]]]

OBJECTS = [
    {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "pynixd-0"},
        "spec": {
            "volumes": [
                {
                    "name": "init-store",
                    "csi": {"volumeAttributes": {"storePath": f"/nix/store/{CACHE_ENV}"}},
                }
            ]
        },
    }
]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


def probe_from(world: World, *, broken: Set[str] = frozenset()) -> Probe:
    """A `Probe` over a made-up world: substituter -> path -> references."""

    async def probe(uri: str, base_name: str) -> tuple[Held | None, str | None]:
        if uri in broken:
            return None, f"{uri}: pretend this cache is down"
        references = world.get(uri, {}).get(base_name)
        return (Held(frozenset(references)), None) if references is not None else (None, None)

    return probe


class TestWhatItReads:
    def test_it_finds_a_store_path_inside_a_csi_volume(self) -> None:
        assert store_paths_in(OBJECTS) == {CACHE_ENV}

    def test_a_path_without_the_store_prefix_is_not_one(self) -> None:
        assert store_paths_in([{"data": {"note": f"{CACHE_ENV} on its own is not a path"}}]) == set()


class TestTheUnionAcrossSubstituters:
    async def test_a_path_on_the_second_substituter_counts_as_present(self) -> None:
        """`cacheEnv` is on nixkube.cachix.org and its member
        `python3.14-httpx` is only on cache.nixos.org. Either alone reports
        a false failure; this is the case that made the union necessary."""
        world: World = {
            "https://nixkube.cachix.org": {CACHE_ENV: {HTTPX}},
            "https://cache.nixos.org": {HTTPX: set()},
        }

        verdict = await walk({CACHE_ENV}, list(world), probe_from(world))

        assert verdict.missing == frozenset()
        assert verdict.reachable == frozenset({CACHE_ENV, HTTPX})

    async def test_one_substituter_alone_reports_the_member_missing(self) -> None:
        world: World = {"https://nixkube.cachix.org": {CACHE_ENV: {HTTPX}}}

        verdict = await walk({CACHE_ENV}, list(world), probe_from(world))

        assert verdict.missing == frozenset({HTTPX})


class TestTheClosure:
    async def test_a_present_top_path_with_an_absent_member_still_fails(self) -> None:
        """Issue #8. Checking only the named paths passes this."""
        world: World = {"https://cache.nixos.org": {CACHE_ENV: {ABSENT}}}

        with pytest.raises(StorePathsUnavailableError, match="on no substituter"):
            await assert_fetchable(store_paths_in(OBJECTS), substituters=list(world), probe=probe_from(world))

    async def test_a_cycle_in_the_references_terminates(self) -> None:
        """A store path's references include itself, routinely."""
        world: World = {"https://cache.nixos.org": {CACHE_ENV: {CACHE_ENV, HTTPX}, HTTPX: {CACHE_ENV}}}

        verdict = await walk({CACHE_ENV}, list(world), probe_from(world))

        assert verdict.missing == frozenset()
        assert verdict.reachable == frozenset({CACHE_ENV, HTTPX})


class TestTheThreeAnswersStayThree:
    async def test_a_cache_that_cannot_answer_is_not_a_missing_path(self) -> None:
        """Reporting it as missing sends someone to push a path that is
        already there. The two need different fixes."""
        world: World = {"https://cache.nixos.org": {}}

        with pytest.raises(StorePathsUnavailableError, match="could not answer") as caught:
            await assert_fetchable(
                store_paths_in(OBJECTS),
                substituters=list(world),
                probe=probe_from(world, broken={"https://cache.nixos.org"}),
            )

        assert "on no substituter" not in str(caught.value)

    async def test_a_broken_cache_beside_a_working_one_is_not_a_problem(self) -> None:
        """The path was served. One substituter being down says nothing
        about it."""
        world: World = {
            "https://down.example": {},
            "https://cache.nixos.org": {CACHE_ENV: set()},
        }

        await assert_fetchable(
            store_paths_in(OBJECTS),
            substituters=list(world),
            probe=probe_from(world, broken={"https://down.example"}),
        )

    async def test_everything_fetchable_passes_quietly(self) -> None:
        world: World = {"https://cache.nixos.org": {CACHE_ENV: set()}}

        await assert_fetchable(store_paths_in(OBJECTS), substituters=list(world), probe=probe_from(world))


class TestItFailsClosed:
    async def test_paths_but_no_substituters_refuses_rather_than_passing(self) -> None:
        """`ekn.assertCached = [ ]` turns the check off at the call site, so
        reaching here with paths and no substituter is a caller's mistake.
        Asking nothing passes everything, which reads as a healthy cluster
        and is the opposite of what this guard is for."""
        with pytest.raises(NoSubstitutersError, match="no substituter was named"):
            await assert_fetchable(store_paths_in(OBJECTS), substituters=[], probe=probe_from({}))

    async def test_objects_naming_no_store_path_need_no_substituter(self) -> None:
        """Nothing to assert. An instance whose objects name no store path
        does not have to configure a cache to have the check on."""
        await assert_fetchable(store_paths_in([{"kind": "ConfigMap"}]), substituters=[], probe=probe_from({}))


#: A made-up world with signatures: substituter -> path -> (references, signed).
type SignedWorld = dict[str, dict[str, tuple[set[str], bool | None]]]


def signed_probe_from(world: SignedWorld) -> Probe:
    """A `Probe` over a made-up world whose copies say whether a trusted key signed them."""

    async def probe(uri: str, base_name: str) -> tuple[Held | None, str | None]:
        entry = world.get(uri, {}).get(base_name)
        if entry is None:
            return None, None
        references, signed = entry
        return Held(frozenset(references), signed), None

    return probe


class TestSignatures:
    """sg1, 2026-11-28: `nixkube-env` was held unsigned, the check passed, and no node took it."""

    async def test_a_held_path_without_a_trusted_signature_fails_and_is_named(self) -> None:
        world: SignedWorld = {"ssh-ng://pynixd": {CACHE_ENV: ({HTTPX}, True), HTTPX: (set(), False)}}

        with pytest.raises(StorePathsUnsignedError, match="carry no signature") as caught:
            await assert_fetchable(
                store_paths_in(OBJECTS),
                substituters=list(world),
                trusted_keys=["k-1:x"],
                probe=signed_probe_from(world),
            )

        assert f"/nix/store/{HTTPX} on ssh-ng://pynixd" in str(caught.value)
        assert CACHE_ENV not in str(caught.value)

    async def test_an_unsigned_path_is_still_on_no_substituter_when_absent(self) -> None:
        """Absent is the first answer: a missing path cannot be signed."""
        world: SignedWorld = {"ssh-ng://pynixd": {CACHE_ENV: ({ABSENT}, False)}}

        with pytest.raises(StorePathsUnavailableError, match="on no substituter"):
            await assert_fetchable(store_paths_in(OBJECTS), substituters=list(world), probe=signed_probe_from(world))

    async def test_a_signed_copy_on_another_substituter_is_enough(self) -> None:
        """A node tries the next substituter when a signature fails."""
        world: SignedWorld = {
            "ssh-ng://pynixd": {CACHE_ENV: (set(), False)},
            "https://nixkube.cachix.org": {CACHE_ENV: (set(), True)},
        }

        verdict = await walk({CACHE_ENV}, list(world), signed_probe_from(world))

        assert verdict.unsigned == {}

    async def test_the_walk_names_the_first_substituter_holding_it_unsigned(self) -> None:
        world: SignedWorld = {
            "ssh-ng://pynixd": {CACHE_ENV: (set(), False)},
            "https://nixkube.cachix.org": {CACHE_ENV: (set(), False)},
        }

        verdict = await walk({CACHE_ENV}, list(world), signed_probe_from(world))

        assert verdict.unsigned == {CACHE_ENV: "ssh-ng://pynixd"}
        assert verdict.missing == frozenset()

    async def test_an_unchecked_signature_passes(self) -> None:
        """No key named, or a path the store only fetches: nothing to check it against."""
        world: SignedWorld = {"ssh-ng://pynixd": {CACHE_ENV: (set(), None)}}

        await assert_fetchable(
            store_paths_in(OBJECTS), substituters=list(world), trusted_keys=["k-1:x"], probe=signed_probe_from(world)
        )

    async def test_a_resigned_path_passes_the_second_check(self) -> None:
        world: SignedWorld = {"ssh-ng://pynixd": {CACHE_ENV: ({HTTPX}, True), HTTPX: (set(), False)}}
        asked: list[dict[str, str]] = []

        async def resign(unsigned: Mapping[str, str]) -> None:
            asked.append(dict(unsigned))
            for name, uri in unsigned.items():
                references, _signed = world[uri][name]
                world[uri][name] = (references, True)

        await assert_fetchable(
            store_paths_in(OBJECTS),
            substituters=list(world),
            trusted_keys=["k-1:x"],
            resign=resign,
            probe=signed_probe_from(world),
        )

        assert asked == [{HTTPX: "ssh-ng://pynixd"}]

    async def test_a_path_the_resign_left_unsigned_still_fails(self) -> None:
        """An untrusted deployer, or a cache whose content differs: pynixd signs nothing."""
        world: SignedWorld = {"ssh-ng://pynixd": {CACHE_ENV: (set(), False)}}
        asked: list[dict[str, str]] = []

        async def resign(unsigned: Mapping[str, str]) -> None:
            asked.append(dict(unsigned))

        with pytest.raises(StorePathsUnsignedError, match=CACHE_ENV):
            await assert_fetchable(
                store_paths_in(OBJECTS),
                substituters=list(world),
                trusted_keys=["k-1:x"],
                resign=resign,
                probe=signed_probe_from(world),
            )

        assert asked == [{CACHE_ENV: "ssh-ng://pynixd"}]

    async def test_a_signed_closure_asks_no_resign(self) -> None:
        world: SignedWorld = {"ssh-ng://pynixd": {CACHE_ENV: (set(), True)}}

        async def resign(unsigned: Mapping[str, str]) -> None:
            raise AssertionError(f"asked to sign {unsigned}")

        await assert_fetchable(
            store_paths_in(OBJECTS),
            substituters=list(world),
            trusted_keys=["k-1:x"],
            resign=resign,
            probe=signed_probe_from(world),
        )


@dataclass
class _Info:
    """The two fields `accepted` reads before it asks the store."""

    sigs_: list[Signature]
    ca_: object = None

    def sigs(self) -> list[Signature]:
        return self.sigs_

    def ca(self) -> object:
        return self.ca_


class _Store:
    """`fingerprint` and `is_content_addressed`, as a store answers them."""

    def __init__(self, *, content_addressed: bool = False) -> None:
        self.content_addressed = content_addressed

    async def fingerprint(self, info: object) -> str:
        return FINGERPRINT

    async def is_content_addressed(self, info: object) -> bool:
        return self.content_addressed


FINGERPRINT = f"1;/nix/store/{CACHE_ENV};sha256:{'0' * 52};1;"


class TestAccepted:
    """`ValidPathInfo::checkSignatures` at a threshold of one, with real ed25519 keys."""

    @staticmethod
    async def _accepted(store: _Store, info: _Info, trusted: list[str]) -> bool | None:
        return await accepted(cast("AsyncStore", store), cast("PathInfo", info), public_keys(trusted))

    async def test_a_signature_by_a_trusted_key_is_accepted(self) -> None:
        key = generate_secret_key("nix-cache-1")
        info = _Info([key.sign_detached(FINGERPRINT)])

        assert await self._accepted(_Store(), info, [key.to_public_key().to_string()]) is True

    async def test_a_signature_by_another_key_of_the_same_name_is_refused(self) -> None:
        """The name only picks the key: the bytes must verify."""
        key, impostor = generate_secret_key("nix-cache-1"), generate_secret_key("nix-cache-1")
        info = _Info([impostor.sign_detached(FINGERPRINT)])

        assert await self._accepted(_Store(), info, [key.to_public_key().to_string()]) is False

    async def test_a_path_with_no_signature_is_refused(self) -> None:
        key = generate_secret_key("nix-cache-1")

        assert await self._accepted(_Store(), _Info([]), [key.to_public_key().to_string()]) is False

    async def test_a_content_addressed_path_needs_no_signature(self) -> None:
        key = generate_secret_key("nix-cache-1")
        info = _Info([], ca_="fixed:r:sha256:x")

        assert await self._accepted(_Store(content_addressed=True), info, [key.to_public_key().to_string()]) is True

    async def test_no_key_checks_nothing(self) -> None:
        assert await self._accepted(_Store(), _Info([]), []) is None


class TestSendAgain:
    async def test_a_nar_of_many_chunks_reaches_the_other_store_whole(self) -> None:
        """`nar_from_path` into `add_to_store_nar` through the pipe, in a store held in memory."""
        local, dest = AsyncStore("dummy://?read-only=false"), AsyncStore("dummy://?read-only=false")
        path = await local.add_to_store("held", bytes(range(256)) * 8192)
        info = await local.query_path_info(path)

        with anyio.fail_after(30):
            await send_again(local, dest, info)

        assert await dest.is_valid_path(path)
        assert (await dest.query_path_info(path)).nar_hash() == info.nar_hash()
