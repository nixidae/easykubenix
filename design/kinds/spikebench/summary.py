#!/usr/bin/env python3
"""Medians per arm, workload and N, and the spike's cost against the baseline.

Usage: summary.py <results.json>
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

FIELDS = ["wall_s", "cpu_s", "max_rss_mib", "gc_total_mib", "values", "calls"]


def main() -> None:
    rows = json.loads(Path(sys.argv[1]).read_text())
    groups: dict[tuple[str, str, int], list[dict[str, float]]] = defaultdict(list)
    for row in rows:
        groups[row["workload"], row["arm"], row["n"]].append(row)
    med = {key: {f: statistics.median(r[f] for r in runs) for f in FIELDS} for key, runs in groups.items()}

    for workload in sorted({w for w, _, _ in med}):
        print(f"\n## {workload}\n")
        print("| N | arm | " + " | ".join(FIELDS) + " |")
        print("| --- | --- |" + " --- |" * len(FIELDS))
        for n in sorted({n for w, _, n in med if w == workload}):
            for arm in ("baseline", "spike"):
                m = med[workload, arm, n]
                print(f"| {n} | {arm} | " + " | ".join(f"{m[f]:g}" for f in FIELDS) + " |")
        print("\nSpike minus baseline:\n")
        print("| N | cpu_s | values | calls | gc_total_mib |")
        print("| --- | --- | --- | --- | --- |")
        for n in sorted({n for w, _, n in med if w == workload}):
            b, s = med[workload, "baseline", n], med[workload, "spike", n]
            print(
                f"| {n} | {s['cpu_s'] - b['cpu_s']:+.3f} | {s['values'] - b['values']:+,.0f} "
                f"| {s['calls'] - b['calls']:+,.0f} | {s['gc_total_mib'] - b['gc_total_mib']:+.1f} |"
            )


if __name__ == "__main__":
    main()
