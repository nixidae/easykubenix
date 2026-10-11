#!/usr/bin/env python3
"""Each workload and N through two easykubenix checkouts, three runs each.

Usage: run.py <baseline checkout> <spike checkout> <output.json>

Records wall time, user time and peak RSS (GNU time), and Nix's own
statistics, per run. Both arms evaluate design/kinds/spikebench from this
directory, so they share `sources` and `pkgs`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIME = "/run/current-system/sw/bin/time"
WORKLOADS = ["objects", "crds"]
SIZES = [0, 100, 500, 2000]
RUNS = 3


def run(arm_name: str, arm: str, workload: str, n: int) -> dict[str, object]:
    with tempfile.NamedTemporaryFile(suffix=".json") as stats:
        env = os.environ | {"NIX_SHOW_STATS": "1", "NIX_SHOW_STATS_PATH": stats.name}
        expr = f'import {HERE} {{ arm = {arm}; workload = "{workload}"; n = {n}; }}'
        proc = subprocess.run(
            [TIME, "--format", "TIME %e %U %M", "nix", "eval", "--impure", "--expr", expr],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(f"{arm_name} {workload} n={n} failed:\n{proc.stderr}")
        wall, user, rss_kib = next(
            line.split()[1:] for line in reversed(proc.stderr.splitlines()) if line.startswith("TIME ")
        )
        nix = json.loads(Path(stats.name).read_text())
    return {
        "arm": arm_name,
        "workload": workload,
        "n": n,
        "wall_s": float(wall),
        "user_s": float(user),
        "cpu_s": round(nix["cpuTime"], 3),
        "max_rss_mib": round(int(rss_kib) / 1024, 1),
        "gc_total_mib": round(nix["gc"]["totalBytes"] / 2**20, 1),
        "values": nix["values"]["number"],
        "thunks": nix["nrThunks"],
        "calls": nix["nrFunctionCalls"],
        "output_bytes": int(proc.stdout.strip()),
    }


def main() -> None:
    arms = {"baseline": sys.argv[1], "spike": sys.argv[2]}
    out = Path(sys.argv[3])
    # Warm the store and the YAML import-from-derivation for every size.
    for workload in WORKLOADS:
        for n in SIZES:
            for name, arm in arms.items():
                run(name, arm, workload, n)
    results = []
    for workload in WORKLOADS:
        for n in SIZES:
            for _ in range(RUNS):
                # Interleaved, so load changes hit both arms alike.
                for name, arm in arms.items():
                    row = run(name, arm, workload, n)
                    results.append(row)
                    print(json.dumps(row), flush=True)
    out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
