#!/usr/bin/env python3
"""solid-kubernetes' nixlab3 against two easykubenix revisions.

Usage: solid.py <easykubenix checkout> <solid.nix> <out.json> <baseline rev> <baseline solid checkout> <spike rev> <spike solid checkout>

solid-kubernetes reaches easykubenix through the umbrella, and with
SOLID_DEV_CHECKOUTS and UMBRELLA_DEV=easykubenix that is the easykubenix
working copy. The umbrella has no other way to point at a checkout, so the
arms differ by which revision that working copy is on: this moves it with
`jj new` before every run, interleaving the arms. Each arm has its own
solid-kubernetes checkout (a jj workspace), because the spike changes solid
too. Every other source comes from the umbrella's lock in both. `solid.nix`
is passed from outside the working copy, because the baseline revision does
not have it.

Leaves the working copy on a new change on top of the spike revision.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TIME = "/run/current-system/sw/bin/time"
RUNS = 3


def switch(ekn: str, rev: str) -> None:
    subprocess.run(["jj", "--no-pager", "new", rev], cwd=ekn, check=True, capture_output=True)


def run(arm: str, solid: str, harness: str, output: str) -> dict[str, object]:
    with tempfile.NamedTemporaryFile(suffix=".json") as stats:
        env = os.environ | {
            "SOLID_DEV_CHECKOUTS": str(Path.home() / "Code"),
            "UMBRELLA_DEV": "easykubenix",
            "NIX_SHOW_STATS": "1",
            "NIX_SHOW_STATS_PATH": stats.name,
        }
        expr = f'import {harness} {{ solid = {solid}; output = "{output}"; }}'
        proc = subprocess.run(
            [TIME, "--format", "TIME %e %U %M", "nix", "eval", "--impure", "--expr", expr],
            cwd=solid,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(f"{arm} failed:\n{proc.stderr[-4000:]}")
        wall, user, rss_kib = next(
            line.split()[1:] for line in reversed(proc.stderr.splitlines()) if line.startswith("TIME ")
        )
        nix = json.loads(Path(stats.name).read_text())
    return {
        "arm": arm,
        "output": proc.stdout.strip(),
        "wall_s": float(wall),
        "user_s": float(user),
        "cpu_s": round(nix["cpuTime"], 3),
        "max_rss_mib": round(int(rss_kib) / 1024, 1),
        "gc_total_mib": round(nix["gc"]["totalBytes"] / 2**20, 1),
        "values": nix["values"]["number"],
        "thunks": nix["nrThunks"],
        "calls": nix["nrFunctionCalls"],
    }


def main() -> None:
    ekn, harness, out, baseline, baseline_solid, spike, spike_solid = sys.argv[1:8]
    arms = {"baseline": (baseline, baseline_solid), "spike": (spike, spike_solid)}
    results = []
    try:
        # One hash run each: warms the store and checks the arms render alike.
        for name, (rev, solid) in arms.items():
            switch(ekn, rev)
            row = run(name, solid, harness, "hash")
            row["kind"] = "hash"
            results.append(row)
            print(json.dumps(row), flush=True)
        for _ in range(RUNS):
            for name, (rev, solid) in arms.items():
                switch(ekn, rev)
                row = run(name, solid, harness, "length")
                row["kind"] = "timing"
                results.append(row)
                print(json.dumps(row), flush=True)
    finally:
        switch(ekn, spike)
        Path(out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
