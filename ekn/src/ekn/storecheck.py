"""Refuse an apply whose store paths no node can fetch.

A CSI-mounted store path can only be substituted: the volume names an
output path, so `allowSubstitutes = false` holds and a node cannot build it.
A path on no substituter is therefore a mount that fails on a node, minutes
after the apply and nowhere near it.

**Fails closed**, because the case this exists for is a combination nobody
predicted. On nixlab2 a newly-working garbage collector removed `cacheEnv`
from all four node stores while the cache push that would have replaced it
had been failing since 01:19. Either half alone was survivable. Together
they took out the only copy of the environment the store server itself
mounts, so the store server could not start, and the push that would have
fixed it goes *through* the store server. Nothing would have written a test
for that; a precondition catches it without having to.

**The substituters come from `ekn.assertCached`, and easykubenix sets
nothing there.** The right list is the one the *nodes* carry, and only the
module deploying the store server knows it. nixkube's is one cache while
the machine deploying it usually has three, so reading the deploying
machine's own Nix settings would pass a path that no node can fetch -- the
exact failure this guards. Empty means off.

The work is here, rather than in a separate program, because `ekn` already
opens stores by URI -- see `push_closure_to_store`. An exit code carries
three states and no detail. See issue #37.

The three answers a substituter can give stay three, because huggorm
keeps them apart:

    present       is_valid_path -> True
                  or query_missing leaves it out of `unknown`
    absent        query_missing names it in `unknown`
    cannot ask    NixError

Measured 2026-09-17 against cache.nixos.org and an unroutable ssh-ng host.

A store server with substituters of its own (pynixd and its R2 cold tier)
holds a path it can fetch but does not have. `query_missing` on that daemon store
asks those substituters about the whole closure. Measured 2026-11-26 on sg1's
pynixd: a path in R2 answers `will_substitute=1, unknown=0`, and a made-up
path `unknown=1`. `query_substitutable_paths` is not the question: it skips a
cache without `WantMassQuery` (`store-api.cc:526`), and it gives no references.

Only a remote store (`is_remote_store`) sends `query_missing` to its daemon.
Any other store answers it from the deploying machine's own substituters:
measured 2026-11-26, an empty `file://` cache passed hello from
cache.nixos.org.
One code path therefore covers `https`, `ssh-ng`, `s3` and `file`; a
scheme-specific fetcher could only ask an HTTP substituter (nixkube #41).

**A held path also needs a signature the nodes accept** (`ekn.assertCachedKeys`).
A node refuses a substitute that no key in its `trusted-public-keys` signed,
unless its content address gives its path (`ValidPathInfo::checkSignatures`).
Measured 2026-11-28 on sg1: `nixkube-env` was on pynixd with no signature, the
check passed, and the nodes refused it. `nix copy` skips a path the destination
holds, so no later push signed it. The signature comes from the same
`query_path_info` as the references: no second query per path. A path that a
store can only fetch from its own substituters has no info to read, so its
signature stays unchecked.

`resend_unsigned` gets such a path signed. It sends the path again from the
deploying machine's store, with `AddToStoreNar`, which skips no valid path.
pynixd with `sign_added_paths` signs an admin's add when the stored NAR hash
and references are the ones sent (`LocalDBStore.sign_added`, nixidae/nixidae#1009).
Nix's `AddSignatures` is no route: the client must compute the signature, and
the deployer does not hold the cache's key.
"""

from __future__ import annotations

import json
import re
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio
import structlog
from huggorm import AsyncSession, AsyncStore, PublicKey, StorePath, TrustedFlag
from huggorm.errors import InvalidPath, NixError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
    from typing import Any

    from huggorm import PathInfo

    #: `(uri, base_name)` -> `(Held, None)` present, `(None, None)`
    #: absent, `(None, why)` when that substituter could not answer.
    type Probe = Callable[[str, str], Awaitable[tuple[Held | None, str | None]]]
    #: Each unsigned path (`<digest>-<name>`) and the substituter that holds it.
    type Resign = Callable[[Mapping[str, str]], Awaitable[None]]

_log = structlog.get_logger()

STORE_DIR = "/nix/store"
# Nix's base-32 alphabet: a package placeholder's `e` hash is never a path.
STORE_PATH = re.compile(rf"{STORE_DIR}/([0-9abcdfghijklmnpqrsvwxyz]{{32}}-[A-Za-z0-9._+?=-]*)")

DEFAULT_JOBS = 16
# Chunks in flight between the two stores of one `resend_unsigned` copy.
_PIPE_CHUNKS = 16


class StorePathsUnavailableError(RuntimeError):
    """The apply was refused because its store paths are not all fetchable."""


class StorePathsUnsignedError(StorePathsUnavailableError):
    """The apply was refused because a held path carries no signature the nodes accept."""


@dataclass(frozen=True, slots=True)
class Held:
    """What one substituter says about a path it serves."""

    references: frozenset[str]
    #: A key the nodes trust signed it, or its content address gives its path.
    #: `None`: not checked, because no key was named or the store has no info to read.
    signed: bool | None = None


@dataclass(frozen=True)
class Verdict:
    """What every substituter together could say about a closure."""

    reachable: frozenset[str]
    missing: frozenset[str]
    #: Kept apart from `missing` deliberately. A substituter that will not
    #: answer is a broken check rather than a missing path, and the two need
    #: different fixes -- one is "push it", the other is "the cache is down
    #: or the hostname is wrong". Conflating them sends someone to push a
    #: path that is already there.
    unclear: Mapping[str, str]
    #: Held, and by no substituter with a signature the nodes accept: each
    #: path and the first substituter that holds it.
    unsigned: Mapping[str, str] = field(default_factory=dict)
    #: Held, with the signature not checked.
    unchecked: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _Answer:
    """What the substituters together say about one path."""

    held: Held | None
    #: The substituter whose copy is taken when no copy is signed.
    unsigned_at: str | None
    problems: list[str]


def store_paths_in_text(text: str) -> set[str]:
    """Every store path this text names, as `<digest>-<name>`."""
    return {match.group(1) for match in STORE_PATH.finditer(text)}


def store_paths_in(objects: Sequence[dict[str, Any]]) -> set[str]:
    """Every store path these objects name.

    A caller with the objects in hand should use this rather than the
    rendered manifest: a `--target` slice is narrower than the whole
    instance the cache push reads, and a seed rewrites an object's data
    before it goes out.
    """
    return store_paths_in_text(json.dumps(objects))


async def _ask(base_name: str, substituters: Sequence[str], probe: Probe, limiter: anyio.CapacityLimiter) -> _Answer:
    """The first copy with a signature the nodes accept, else the first unsigned copy.

    A node tries the next substituter when a copy's signature fails, so an
    unsigned copy on one beside a signed copy on another is no problem.
    """
    problems: list[str] = []
    unsigned: tuple[str, Held] | None = None
    for uri in substituters:
        async with limiter:
            held, problem = await probe(uri, base_name)
        if problem is not None:
            problems.append(problem)
            continue
        if held is None:
            continue
        if held.signed is False:
            unsigned = unsigned or (uri, held)
            continue
        return _Answer(held, None, problems)
    if unsigned is not None:
        return _Answer(unsigned[1], unsigned[0], problems)
    return _Answer(None, None, problems)


async def walk(
    roots: set[str],
    substituters: Sequence[str],
    probe: Probe,
    *,
    jobs: int = DEFAULT_JOBS,
) -> Verdict:
    """Ask every substituter about the whole closure of `roots`.

    Breadth-first, one level at a time, because a level is usually wide and
    a path's references are only known once it has been found somewhere.
    """
    seen: set[str] = set()
    missing: set[str] = set()
    unclear: dict[str, str] = {}
    unsigned: dict[str, str] = {}
    unchecked: set[str] = set()
    frontier = set(roots)
    limiter = anyio.CapacityLimiter(jobs)

    while frontier:
        batch = sorted(frontier - seen)
        seen.update(batch)
        answers: dict[str, _Answer] = {}

        async def one(base_name: str, answers: dict[str, _Answer]) -> None:
            answers[base_name] = await _ask(base_name, substituters, probe, limiter)

        async with anyio.create_task_group() as group:
            for base_name in batch:
                group.start_soon(one, base_name, answers)

        frontier = set()
        for base_name, answer in answers.items():
            if answer.held is None:
                # A path one substituter could not answer for, that another
                # served, is not unclear. Report a problem only where nothing
                # resolved it.
                if answer.problems:
                    unclear[base_name] = "; ".join(answer.problems)
                else:
                    missing.add(base_name)
                continue
            if answer.unsigned_at is not None:
                unsigned[base_name] = answer.unsigned_at
            elif answer.held.signed is None:
                unchecked.add(base_name)
            frontier.update(answer.held.references - seen)

    return Verdict(frozenset(seen), frozenset(missing), unclear, unsigned, frozenset(unchecked))


async def still_unsigned(
    names: Iterable[str], substituters: Sequence[str], probe: Probe, *, jobs: int = DEFAULT_JOBS
) -> dict[str, str]:
    """Which of `names` no substituter holds with a signature the nodes accept, and where each is held.

    The paths alone, not their closure: the walk has checked the rest.
    """
    limiter = anyio.CapacityLimiter(jobs)
    answers: dict[str, _Answer] = {}

    async def one(base_name: str) -> None:
        answers[base_name] = await _ask(base_name, substituters, probe, limiter)

    async with anyio.create_task_group() as group:
        for base_name in names:
            group.start_soon(one, base_name)
    return {name: answer.unsigned_at for name, answer in answers.items() if answer.unsigned_at is not None}


def public_keys(trusted_keys: Iterable[str]) -> dict[str, list[PublicKey]]:
    """The keys by name, as a signature names its key."""
    keys: dict[str, list[PublicKey]] = {}
    for text in trusted_keys:
        key = PublicKey(text)
        keys.setdefault(key.name(), []).append(key)
    return keys


async def accepted(store: AsyncStore, info: PathInfo, keys: Mapping[str, Sequence[PublicKey]]) -> bool | None:
    """`ValidPathInfo::checkSignatures` against `keys`, at a threshold of one; `None` with no key to check."""
    if not keys:
        return None
    if info.ca() is not None and await store.is_content_addressed(info):
        return True
    candidates = [sig for sig in info.sigs() if sig.key_name() in keys]
    if not candidates:
        return False
    fingerprint = await store.fingerprint(info)
    return any(key.verify_detached(fingerprint, sig) for sig in candidates for key in keys[sig.key_name()])


@asynccontextmanager
async def huggorm_probe(substituters: Sequence[str], trusted_keys: Sequence[str] = ()) -> AsyncIterator[Probe]:
    """A `Probe` backed by the given stores, each opened once.

    Once, and not per level: a store per level reconnects an `ssh-ng`
    substituter every time. A store opens on its first query, so a
    substituter that cannot be reached answers `NixError` there.

    `trusted_keys`: the nodes' `trusted-public-keys`. Empty checks no signature.
    """
    keys = public_keys(trusted_keys)
    async with AsyncSession() as session:
        stores = {uri: session.store(uri) for uri in substituters}

        async def fetchable(store: AsyncStore, base_name: str) -> tuple[Held | None, None]:
            """A path this store would fetch: present, its unfetchable closure members as its references.

            Those members go back into the walk, so another substituter may still supply them.
            """
            missing = await store.query_missing([await store.parse_derived_path(f"{STORE_DIR}/{base_name}")])
            unknown = {member.to_string() for member in missing.unknown()}
            if base_name in unknown:
                return None, None
            return Held(frozenset(unknown)), None

        async def probe(uri: str, base_name: str) -> tuple[Held | None, str | None]:
            store = stores[uri]
            path = StorePath(base_name)
            try:
                if not await store.is_valid_path(path):
                    if not await store.is_remote_store():
                        return None, None
                    return await fetchable(store, base_name)
                info = await store.query_path_info(path)
                signed = await accepted(store, info, keys)
            except InvalidPath:
                return None, None
            except NixError as exc:
                return None, f"{uri}: {exc}"
            return Held(frozenset(reference.to_string() for reference in info.references()), signed), None

        yield probe


class _NarPipe:
    """One store's `nar_from_path` into another's `add_to_store_nar`, a chunk at a time."""

    def __init__(self) -> None:
        self._send, self._receive = anyio.create_memory_object_stream[bytes](_PIPE_CHUNKS)
        self._rest = b""

    async def write(self, data: bytes) -> None:
        await self._send.send(data)

    def close(self) -> None:
        self._send.close()

    async def read(self, n: int) -> bytes:
        while not self._rest:
            try:
                self._rest = await self._receive.receive()
            except anyio.EndOfStream:
                return b""
        out, self._rest = self._rest[:n], self._rest[n:]
        return out


async def send_again(local: AsyncStore, dest: AsyncStore, info: PathInfo) -> None:
    """`info`'s path from `local` to `dest`, valid there or not: `AddToStoreNar` has no validity check."""
    pipe = _NarPipe()

    async def dump() -> None:
        try:
            await local.nar_from_path(info.path(), pipe)
        finally:
            pipe.close()

    async with anyio.create_task_group() as group:
        group.start_soon(dump)
        # Unsigned by definition: the destination's admin client skips the check.
        await dest.add_to_store_nar(info, pipe, check_sigs=False)


async def resend_unsigned(unsigned: Mapping[str, str]) -> None:
    """Send each unsigned path again, from this machine's store, to the substituter that holds it.

    pynixd signs what an admin sends when its stored NAR hash and references
    match. A store signs nothing for an untrusted client, so a substituter
    that does not trust this one is skipped. A path this machine does not
    hold stays unsigned: nothing here can vouch for it. The caller checks again.
    """
    by_uri: dict[str, list[str]] = {}
    for name, uri in unsigned.items():
        by_uri.setdefault(uri, []).append(name)
    async with AsyncSession() as session:
        local = session.store()
        for uri, names in sorted(by_uri.items()):
            dest = session.store(uri)
            try:
                trusted = await dest.is_remote_store() and await dest.is_trusted_client() == TrustedFlag.TRUSTED
            except NixError as exc:
                _log.warning("cannot ask the substituter to sign", substituter=uri, error=str(exc))
                continue
            if not trusted:
                _log.warning("the substituter signs nothing for this client: it is not trusted", substituter=uri)
                continue
            for name in sorted(names):
                try:
                    info = await local.query_path_info(StorePath(name))
                except InvalidPath:
                    _log.warning("this machine does not hold the path to send it again", path=f"{STORE_DIR}/{name}")
                    continue
                _log.info("sending an unsigned path again to be signed", path=f"{STORE_DIR}/{name}", substituter=uri)
                try:
                    await send_again(local, dest, info)
                except NixError as exc:
                    _log.warning("the path did not go again", path=f"{STORE_DIR}/{name}", error=str(exc))


class NoSubstitutersError(RuntimeError):
    """No substituter was named, so the check would pass everything."""


async def assert_fetchable(  # noqa: PLR0913 -- each argument is a separate input of the check
    roots: set[str],
    *,
    substituters: Sequence[str],
    trusted_keys: Sequence[str] = (),
    resign: Resign | None = None,
    jobs: int = DEFAULT_JOBS,
    probe: Probe | None = None,
) -> None:
    """Refuse unless every path in the closure of `roots` can be fetched.

    `trusted_keys`: the nodes' `trusted-public-keys`; a held path no one of
    them signed fails too. Empty checks no signature. `resign` gets those
    paths signed (`resend_unsigned`) before the check asks again.

    `probe` exists so the walk -- the part with the union and the
    three-way answer in it -- is testable without a store. An injected
    probe also answers the second check.
    """
    if not roots:
        _log.info("no store paths to assert")
        return

    if not substituters:
        # Fails closed. Asking nothing passes everything, which reads as a
        # healthy cluster and is the opposite of what this check is for.
        raise NoSubstitutersError(
            "no substituter was named, so the check would pass every path without asking "
            "anything. Name the caches the nodes carry, with --substituter."
        )

    _log.info(
        "asserting every store path is fetchable",
        paths=len(roots),
        substituters=list(substituters),
        signatures=bool(trusted_keys),
    )

    async with AsyncExitStack() as stack:
        walk_probe = probe or await stack.enter_async_context(huggorm_probe(substituters, trusted_keys))
        verdict = await walk(roots, substituters, walk_probe, jobs=jobs)

    if verdict.unclear:
        detail = "\n".join(f"  {name}: {why}" for name, why in sorted(verdict.unclear.items())[:10])
        raise StorePathsUnavailableError(
            f"the store-path check could not answer for {len(verdict.unclear)} path(s).\n{detail}\n"
            f"This is a broken check rather than a missing path -- a substituter that will not "
            f"answer needs a different fix from one that is missing a path. Treating it as a pass "
            f"would be going ahead without the guard."
        )

    if verdict.missing:
        detail = "\n".join(f"  /nix/store/{name}" for name in sorted(verdict.missing))
        raise StorePathsUnavailableError(
            f"{len(verdict.missing)} of {len(verdict.reachable)} paths in the closure are on no "
            f"substituter.\n{detail}\n"
            f"A node cannot build these -- a CSI volume names an output path, so it can only be "
            f"substituted. Push them first (see ekn.cacheTo)."
        )

    unsigned = dict(verdict.unsigned)
    if unsigned and resign is not None:
        _log.info("getting unsigned paths signed", paths=len(unsigned))
        await resign(unsigned)
        async with AsyncExitStack() as stack:
            # Stores opened anew: a store keeps the path info it answered (`pathInfoCache`).
            again = probe or await stack.enter_async_context(huggorm_probe(substituters, trusted_keys))
            unsigned = await still_unsigned(unsigned, substituters, again, jobs=jobs)

    if unsigned:
        detail = "\n".join(f"  /nix/store/{name} on {uri}" for name, uri in sorted(unsigned.items()))
        raise StorePathsUnsignedError(
            f"{len(unsigned)} of {len(verdict.reachable)} paths in the closure carry no signature "
            f"by a key the nodes trust (ekn.assertCachedKeys).\n{detail}\n"
            f"A node refuses an unsigned substitute. An admin's copy into pynixd with "
            f"sign_added_paths signs it; `nix copy` skips a path the cache already holds."
        )

    if trusted_keys and verdict.unchecked:
        _log.warning(
            "signatures not checked: the substituter fetches these paths from its own substituters",
            paths=len(verdict.unchecked),
        )
    _log.info("store paths are fetchable", paths=len(verdict.reachable))


__all__ = [
    "DEFAULT_JOBS",
    "Held",
    "NoSubstitutersError",
    "StorePathsUnavailableError",
    "StorePathsUnsignedError",
    "Verdict",
    "accepted",
    "assert_fetchable",
    "huggorm_probe",
    "public_keys",
    "resend_unsigned",
    "send_again",
    "still_unsigned",
    "store_paths_in",
    "walk",
]
