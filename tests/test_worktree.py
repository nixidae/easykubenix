from __future__ import annotations

from typing import TYPE_CHECKING

import pygit2
import pytest

from ekn import worktree

if TYPE_CHECKING:
    from pathlib import Path


def repository(root: Path) -> pygit2.Repository:
    repo = pygit2.init_repository(str(root))
    (root / ".gitignore").write_text("scratch/\n")
    (root / "tracked.nix").write_text("{ }\n")
    repo.index.add(".gitignore")
    repo.index.add("tracked.nix")
    repo.index.write()
    return repo


def fake_jj(bin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `jj` that records its arguments and working directory."""
    bin_dir.mkdir()
    record = bin_dir / "called"
    jj = bin_dir / "jj"
    jj.write_text(f'#!/bin/sh\necho "$PWD $*" > {record}\n')
    jj.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    return record


async def test_a_plain_repository_reports_new_files_but_not_ignored_ones(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    repository(root)
    (root / "new.nix").write_text("{ }\n")
    (root / "scratch").mkdir()
    (root / "scratch" / "kubeconfig").write_text("secret\n")
    with pytest.raises(worktree.UntrackedFilesError, match=r"new\.nix") as raised:
        await worktree.prepare(root, refuse=True)
    assert "scratch" not in str(raised.value)
    # A command that only reads does not care.
    await worktree.prepare(root / "tracked.nix", refuse=False)


async def test_a_clean_repository_passes(tmp_path: Path) -> None:
    repository(tmp_path)
    await worktree.prepare(tmp_path, refuse=True)


async def test_outside_a_repository_nothing_happens(tmp_path: Path) -> None:
    await worktree.prepare(tmp_path, refuse=True)


async def test_a_colocated_repository_is_snapshotted_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    repository(root)
    (root / ".jj").mkdir()
    record = fake_jj(tmp_path / "bin", monkeypatch)
    await worktree.prepare(root / "tracked.nix", refuse=True)
    assert record.read_text().split() == [str(root), "util", "snapshot"]


async def test_a_file_the_snapshot_left_out_still_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    repository(root)
    (root / ".jj").mkdir()
    (root / "huge.bin").write_bytes(b"\0")
    fake_jj(tmp_path / "bin", monkeypatch)
    with pytest.raises(worktree.UntrackedFilesError, match=r"huge\.bin"):
        await worktree.prepare(root, refuse=True)
