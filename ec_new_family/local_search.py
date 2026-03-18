#!/usr/bin/env python3
"""
local_search.py  —  Fast multi-core brute-force search for integral points on E_n.

Equation:
    y² = X³ + a4(n)·X + a6(n)
    a4(n) = -45349632·n⁴ + 419904·n³
    a6(n) = 3·(39182082048·n⁶ − 544195584·n⁵ + 1259712·n⁴ − 19·n)
    excluding  X = −3888·n²

This is a finite-range brute-force (not provably complete). For certified
completeness use ec_pari_search.py with gp.

Usage:
    python3 local_search.py                  # n = 1..500, |X| ≤ 10^7
    python3 local_search.py --n-hi 1000      # larger n range
    python3 local_search.py --x-bound 5e7    # wider X window
    python3 local_search.py --workers 8
    python3 local_search.py --negative       # also scan n < 0
"""
import argparse
import multiprocessing as mp
import os
import time
from math import isqrt, cbrt
from pathlib import Path

REPO = Path(__file__).parent
OUT  = REPO / "output" / "solutions_bruteforce.txt"


# ──────────────────────────────────────────────────────────────────────────────
# Curve arithmetic
# ──────────────────────────────────────────────────────────────────────────────

def a4(n: int) -> int:
    return -45349632 * n**4 + 419904 * n**3

def a6(n: int) -> int:
    return 3 * (39182082048 * n**6 - 544195584 * n**5
                + 1259712 * n**4 - 19 * n)

def verify(n: int, X: int, y: int) -> bool:
    rhs = X**3 + a4(n) * X + a6(n)
    return y * y == rhs

def x_min(n: int) -> int:
    """Left bound: where y²(X) first becomes non-negative."""
    b = a6(n)
    a = a4(n)
    if b > 0:
        lo = -int(cbrt(b)) - 500
    elif a < 0:
        lo = -int((-a / 3.0) ** 0.5) - 500
    else:
        lo = -500
    return lo


# ──────────────────────────────────────────────────────────────────────────────
# Per-chunk worker (runs in a subprocess via multiprocessing)
# ──────────────────────────────────────────────────────────────────────────────

def _search_chunk(args):
    """Return list of (n, X, y≥0) found in X ∈ [X_lo, X_hi]."""
    n, A4, A6, excl, X_lo, X_hi = args
    found = []
    for X in range(X_lo, X_hi + 1):
        rhs = X**3 + A4 * X + A6
        if rhs < 0:
            continue
        if X == excl:
            continue
        y = isqrt(rhs)
        if y * y == rhs:
            found.append((n, X, y))
    return found


# ──────────────────────────────────────────────────────────────────────────────
# Per-n search (splits into chunks for the pool)
# ──────────────────────────────────────────────────────────────────────────────

CHUNK = 2_000_000

def search_n(n: int, x_bound: int, pool: mp.Pool) -> list:
    A4   = a4(n)
    A6   = a6(n)
    excl = -3888 * n * n
    lo   = max(x_min(n), -x_bound)

    chunks = []
    X = lo
    while X <= x_bound:
        X_end = min(X + CHUNK - 1, x_bound)
        chunks.append((n, A4, A6, excl, X, X_end))
        X = X_end + 1

    pts = []
    for result in pool.map(_search_chunk, chunks):
        pts.extend(result)
    return pts


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-lo",    type=int,   default=1)
    ap.add_argument("--n-hi",    type=int,   default=500)
    ap.add_argument("--negative", action="store_true",
                    help="Also search n = -n_lo .. -1")
    ap.add_argument("--x-bound", type=float, default=1e7,
                    help="Search |X| ≤ this value (default 1e7)")
    ap.add_argument("--workers", type=int,   default=max(1, os.cpu_count() - 1))
    ap.add_argument("--out",     type=Path,  default=OUT)
    args = ap.parse_args()

    x_bound = int(args.x_bound)
    n_values = list(range(args.n_lo, args.n_hi + 1))
    if args.negative:
        n_values = (list(range(-args.n_hi, -args.n_lo + 1)) + n_values)
    n_values = [n for n in n_values if n != 0]

    print("=== ec_new_family — brute-force local search ===")
    print(f"  y² = X³ + a4(n)X + a6(n),   X ≠ -3888·n²")
    print(f"  n range  : [{min(n_values)}, {max(n_values)}]  ({len(n_values)} values)")
    print(f"  |X| ≤    : {x_bound:,}")
    print(f"  workers  : {args.workers}")
    print()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    solutions = []
    t0 = time.time()

    with mp.Pool(processes=args.workers) as pool:
        for i, n in enumerate(n_values, 1):
            lo = max(x_min(n), -x_bound)
            pts = search_n(n, x_bound, pool)
            if pts:
                for (rn, rX, ry) in pts:
                    # record both signs
                    for sign in (+1, -1) if ry > 0 else (0,):
                        y_out = sign * ry
                        solutions.append((rn, rX, y_out))
                        print(f"  *** n={rn:<6}  X={rX:<18}  y={y_out}",
                              flush=True)
                        with open(args.out, "a") as fh:
                            fh.write(f"{rn}  {rX}  {y_out}\n")
            else:
                if i % 50 == 0:
                    elapsed = time.time() - t0
                    rate    = i / elapsed if elapsed else 0
                    print(f"  [progress] n={n}  ({i}/{len(n_values)})  "
                          f"{rate:.1f} n/s", flush=True)

    elapsed = time.time() - t0
    print()
    print("=== DONE ===")
    print(f"  Elapsed   : {elapsed:.1f}s")
    print(f"  Solutions : {len(solutions)}")
    if solutions:
        print()
        print(f"  {'n':>8}  {'X':>20}  {'y':>22}")
        print("  " + "-" * 54)
        for (n, X, y) in sorted(solutions, key=lambda t:(abs(t[0]),t[0],t[1])):
            print(f"  {n:>8}  {X:>20}  {y:>22}")
    print(f"  Output    : {args.out}")


if __name__ == "__main__":
    main()
