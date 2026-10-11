#!/usr/bin/env python3
"""Time each engine at each N, three runs, from a warm store.

Usage: run.py <kubenix checkout> <output.json>

Records wall time, user time and peak RSS (GNU time) and Nix's own
statistics (cpuTime, GC heap, values allocated) per run.
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
ENGINES = ["kubenix", "ekn-objects", "ekn-generated"]
SIZES = [0, 100, 500, 2000]
RUNS = 3


def run(kubenix: str, engine: str, n: int) -> dict[str, object]:
    with tempfile.NamedTemporaryFile(suffix=".json") as stats:
        env = os.environ | {"NIX_SHOW_STATS": "1", "NIX_SHOW_STATS_PATH": stats.name}
        expr = f'import {HERE} {{ engine = "{engine}"; n = {n}; kubenix = {kubenix}; }}'
        proc = subprocess.run(
            [TIME, "--format", "TIME %e %U %M", "nix", "eval", "--impure", "--expr", expr],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(f"{engine} n={n} failed:\n{proc.stderr}")
        wall, user, rss_kib = next(
            line.split()[1:] for line in reversed(proc.stderr.splitlines()) if line.startswith("TIME ")
        )
        nix = json.loads(Path(stats.name).read_text())
    return {
        "engine": engine,
        "n": n,
        "wall_s": float(wall),
        "user_s": float(user),
        "cpu_s": round(nix["cpuTime"], 3),
        "max_rss_mib": round(int(rss_kib) / 1024, 1),
        "gc_heap_mib": round(nix["gc"]["heapSize"] / 2**20, 1),
        "gc_total_mib": round(nix["gc"]["totalBytes"] / 2**20, 1),
        "values": nix["values"]["number"],
        "thunks": nix["nrThunks"],
        "output_bytes": int(proc.stdout.strip()),
    }


def main() -> None:
    kubenix, out = sys.argv[1], Path(sys.argv[2])
    # Warm the store and every import-from-derivation before measuring.
    for engine in ENGINES:
        run(kubenix, engine, 1)
    results = []
    for n in SIZES:
        for engine in ENGINES:
            for _ in range(RUNS):
                row = run(kubenix, engine, n)
                results.append(row)
                print(json.dumps(row), flush=True)
    out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
