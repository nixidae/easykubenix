#!/usr/bin/env python3
"""Medians per engine and N, and the cost per object from N=0 to the largest N.

Usage: summary.py <results.json>
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

FIELDS = ["wall_s", "cpu_s", "max_rss_mib", "gc_total_mib", "values"]


def main() -> None:
    rows = json.loads(Path(sys.argv[1]).read_text())
    groups: dict[tuple[str, int], list[dict[str, float]]] = defaultdict(list)
    for row in rows:
        groups[row["engine"], row["n"]].append(row)
    medians = {key: {f: statistics.median(r[f] for r in runs) for f in FIELDS} for key, runs in groups.items()}

    print("| engine | N | " + " | ".join(FIELDS) + " |")
    print("| --- | --- |" + " --- |" * len(FIELDS))
    for (engine, n), m in sorted(medians.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        print(f"| {engine} | {n} | " + " | ".join(f"{m[f]:g}" for f in FIELDS) + " |")

    top = max(n for _, n in medians)
    print(f"\nPer object, N=0 to N={top}:\n")
    print("| engine | ms CPU | KiB allocated | values |")
    print("| --- | --- | --- | --- |")
    for engine in sorted({e for e, _ in medians}):
        lo, hi = medians[engine, 0], medians[engine, top]
        print(
            f"| {engine} | {(hi['cpu_s'] - lo['cpu_s']) * 1000 / top:.3f} "
            f"| {(hi['gc_total_mib'] - lo['gc_total_mib']) * 1024 / top:.1f} "
            f"| {(hi['values'] - lo['values']) / top:.0f} |"
        )


if __name__ == "__main__":
    main()
