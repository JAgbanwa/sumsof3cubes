#!/usr/bin/env python3
"""Charity Engine worker: m-driven search for integer solutions to

    y^2 = (36/m) x^3 + 36 x^2 + 12 m x + (m^3-19)/m,   m != 0

This is a *complementary* worker to ``ce_worker.py``.  Instead of fixing x
and iterating over divisors of D=36x^3-19, here we fix m and iterate x over
a given range.  For each (m, x) pair we compute y^2 and test whether it is a
perfect square.

When to prefer this worker
--------------------------
* As a cross-validation tool: run both workers on the same (m, x) region and
  confirm the solution sets agree.
* When targeting specific m ranges believed to be mathematically interesting
  (e.g. m near a cube root of a known value).

The x-driven ``ce_worker.py`` is complete for any scanned x-range.  This
script is redundant in the sense that it cannot produce solutions that
``ce_worker.py`` would miss for the same x range.  Its value lies in
providing an independent verification path and in being more cache-friendly
when m is the outer loop (e.g. on SIMD hardware).

Output fields per JSONL record
-------------------------------
m : parameter m  (non-zero integer)
n : m^3 - 19
x : the integer x value
y : integer y  (both +y and -y are emitted for y != 0)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from typing import List


# ---------- helpers ----------

def is_square(n: int) -> bool:
    if n < 0:
        return False
    r = math.isqrt(n)
    return r * r == n


@dataclass(frozen=True)
class Solution:
    m: int
    n: int
    x: int
    y: int


# ---------- core search ----------

def scan_m_range(
    m_start: int,
    m_end: int,
    x_start: int,
    x_end: int,
    verbose: bool = False,
) -> List[Solution]:
    """For every m in [m_start, m_end] \\ {0} scan x in [x_start, x_end].

    Returns all (m, n, x, y) solutions found.
    """
    sols: List[Solution] = []
    m_range = [m for m in range(m_start, m_end + 1) if m != 0]
    total_m = len(m_range)
    t0 = time.monotonic()

    for idx, m in enumerate(m_range):
        if verbose and idx % max(1, total_m // 20) == 0:
            elapsed = time.monotonic() - t0
            pct = 100.0 * idx / total_m
            print(
                f"  progress: m={m}  ({pct:.1f}%)  "
                f"elapsed={elapsed:.1f}s  solutions_so_far={len(sols)}",
                file=sys.stderr,
                flush=True,
            )

        n_val = m * m * m - 19

        for x in range(x_start, x_end + 1):
            D = 36 * x * x * x - 19
            if D % m != 0:
                continue
            # y^2 = num / m,  num = m*(6x+m)^2 + D
            num = m * (6 * x + m) ** 2 + D
            if num % m != 0:
                continue
            y2 = num // m
            if not is_square(y2):
                continue
            y = math.isqrt(y2)
            sols.append(Solution(m=m, n=n_val, x=x, y=y))
            if y != 0:
                sols.append(Solution(m=m, n=n_val, x=x, y=-y))

    uniq = {(s.m, s.n, s.x, s.y): s for s in sols}
    return list(uniq.values())


# ---------- CLI ----------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Charity Engine m-driven worker: scan m in [m_start, m_end] "
            "and x in [x_start, x_end] for integer solutions."
        )
    )
    ap.add_argument("--m-start", type=int, required=True, help="first m value (inclusive)")
    ap.add_argument("--m-end", type=int, required=True, help="last  m value (inclusive)")
    ap.add_argument("--x-start", type=int, required=True, help="first x value (inclusive)")
    ap.add_argument("--x-end", type=int, required=True, help="last  x value (inclusive)")
    ap.add_argument("--out", type=str, default="solutions_m.jsonl", help="JSONL output file")
    ap.add_argument("--verbose", action="store_true", help="print progress to stderr")
    args = ap.parse_args()

    if args.m_end < args.m_start:
        raise SystemExit("m_end must be >= m_start")
    if args.x_end < args.x_start:
        raise SystemExit("x_end must be >= x_start")

    sols = scan_m_range(
        args.m_start, args.m_end,
        args.x_start, args.x_end,
        verbose=args.verbose,
    )
    out_dir = os.path.dirname(args.out) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in sorted(sols, key=lambda t: (t.m, t.x, t.y)):
            f.write(json.dumps(s.__dict__) + "\n")

    print(
        f"scanned_m=[{args.m_start},{args.m_end}]  "
        f"scanned_x=[{args.x_start},{args.x_end}]  "
        f"solutions={len(sols)}  out={args.out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
