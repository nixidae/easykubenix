"""New files of a local entry point, put where `lib.fileset.gitTracked` sees them.

`gitTracked` lists the git index, so a file that is not in it is missing
from a source path made that way (ADR-044 in solid-kubernetes: a
NixOSRollout's source). jj adds a new file to the index of a colocated
repository when it snapshots the working copy, which any jj command does,
so ekn runs `jj util snapshot` before it evaluates. Measured on jj 0.46.

A command that delivers (`kubeapply`, `deploy`) then refuses while files
are still outside the index: a plain git repository's untracked files, or a
file jj declined to snapshot, such as one over `snapshot.max-new-file-size`.
Other commands say nothing: ekn cannot tell whether a configuration uses
`gitTracked`, and a warning on every run of every repository is noise.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import anyio
import pygit2
from pygit2.enums import FileStatus


class UntrackedFilesError(RuntimeError):
    """Files a gitTracked source would leave out, for a command that delivers it."""


def untracked(repo: pygit2.Repository) -> list[str]:
    """Untracked files that are not ignored, relative to the working directory."""
    return sorted(path for path, flags in repo.status(untracked_files="all").items() if flags & FileStatus.WT_NEW)


async def prepare(entry: str | os.PathLike[str], *, refuse: bool) -> None:
    """Snapshot a colocated jj repository; with `refuse`, fail on files outside the index."""
    git_dir = pygit2.discover_repository(str(Path(entry).resolve()))
    if git_dir is None:
        return
    repo = pygit2.Repository(git_dir)
    if repo.workdir is None:
        return
    root = Path(repo.workdir)
    jj = shutil.which("jj")
    if (root / ".jj").is_dir() and jj is not None:
        # Captured: it says "Snapshot complete." on success, which is noise.
        await anyio.run_process([jj, "util", "snapshot"], cwd=root, check=True)
        repo = pygit2.Repository(git_dir)
    if refuse and (new := untracked(repo)):
        raise UntrackedFilesError(
            f"{len(new)} files outside the git index, which a lib.fileset.gitTracked source leaves out: "
            f"{', '.join(new)}. Run any jj command in a jj repository, or `git add` or ignore them."
        )
