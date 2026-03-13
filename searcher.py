#!/usr/bin/env python3
"""
Searcher for integer solutions to:
  y^2 = x^3 + (36n+27)^2 * x^2 + (15552n^3 + 34992n^2 + 26244n + 6561) * x
        + (46656n^4 + 139968n^3 + 157464n^2 + 78713n + 14748)

Key structure (discovered algebraically):
  Let t = 4n + 3
  A(n) = 81 * t^2
  B(n) = 243 * t^3
  C(n) = (4n+3) * (11664n^3 + 26244n^2 + 19683n + 4916)

Strategy:
  For each n, the RHS f(x) is a cubic. We:
    1. Find real bounds where f(x) >= 0 using Newton / bisection.
    2. Sweep integer x in those intervals.
    3. Use gmpy2.isqrt_rem for O(1) perfect-square test.

Usage:
    python3 searcher.py                  # runs forever from n=0 outward
    python3 searcher.py -n_start -1000 -n_end 1000
    python3 searcher.py --range 1000000  # |n| <= range
"""

import sys
import os
import time
import argparse
import math
import gmpy2
from gmpy2 import mpz, isqrt, isqrt_rem

SOLUTIONS_FILE = "solutions.txt"

# ---------------------------------------------------------------------------
# Polynomial coefficients (all exact, no floats in hot path)
# ---------------------------------------------------------------------------

def coeff_A(n):
    """Coefficient of x^2: 81*(4n+3)^2"""
    t = 4 * n + 3
    return 81 * t * t

def coeff_B(n):
    """Coefficient of x: 243*(4n+3)^3"""
    t = 4 * n + 3
    return 243 * t * t * t

def coeff_C(n):
    """Constant: (4n+3)*(11664n^3 + 26244n^2 + 19683n + 4916)"""
    t = 4 * n + 3
    inner = 11664 * n * n * n + 26244 * n * n + 19683 * n + 4916
    return t * inner

def f_eval(x, A, B, C):
    """Evaluate f(x) = x^3 + A*x^2 + B*x + C using mpz arithmetic."""
    xm = mpz(x)
    return xm * xm * xm + mpz(A) * xm * xm + mpz(B) * xm + mpz(C)

def is_perfect_square(v):
    """Return (True, sqrt) if v is a non-negative perfect square, else (False, 0)."""
    if v < 0:
        return False, 0
    s, r = isqrt_rem(v)
    if r == 0:
        return True, int(s)
    return False, 0

# ---------------------------------------------------------------------------
# Bounds: find x range where f(x) >= 0
# ---------------------------------------------------------------------------

def find_lower_bound(n, A, B, C):
    """
    For large negative x, f(x) -> -inf.
    Binary search for the leftmost x where f(x) >= 0.
    We only check this interval: no solutions below it.
    """
    # Start from x = 0 and go left until f(x) < 0
    A_f, B_f, C_f = float(A), float(B), float(C)

    # Quick float estimate
    lo = 0
    hi = 0
    step = -1
    while True:
        v = hi * hi * hi + A_f * hi * hi + B_f * hi + C_f
        if v < 0:
            lo = hi
            break
        hi += step
        step *= 2
        if abs(step) > 10**15:
            return int(hi)

    # Binary search between lo and hi (lo < hi, f(lo)<0 <= f(hi))
    # Use integer binary search with exact arithmetic
    lo_i, hi_i = int(lo) - 1, int(hi) + 1
    while lo_i < hi_i - 1:
        mid = (lo_i + hi_i) // 2
        if f_eval(mid, A, B, C) >= 0:
            hi_i = mid
        else:
            lo_i = mid
    return hi_i

def find_upper_bound_neg(n, A, B, C):
    """
    Between the lower bound and ~0 there might be a gap where f(x)<0.
    Return the x values bounding the positive region(s).
    We use a simple scan with large steps + local refinement.
    Returns list of (x_lo, x_hi) intervals where f(x) >= 0.
    """
    # Depressed cubic analysis: roots of f(x)
    # Real roots estimated via float
    A_f, B_f, C_f = float(A), float(B), float(C)

    def f_float(xv):
        return xv ** 3 + A_f * xv ** 2 + B_f * xv + C_f

    # Find sign changes in float arithmetic for rough roots
    # Range: lower_bound to +LIMIT
    lower = find_lower_bound(n, A, B, C)
    upper = max(10**6, abs(lower) * 10)

    return lower, upper


# ---------------------------------------------------------------------------
# Core search: for given n, find all integer x with f(x) = square
# ---------------------------------------------------------------------------

def search_n(n, x_limit=10**9):
    """Search all integer x in [-x_limit, x_limit] for given n."""
    A = coeff_A(n)
    B = coeff_B(n)
    C = coeff_C(n)

    solutions = []

    # --- Positive x side ---
    # f grows as x^3, check from 0 up to x_limit
    for x in range(0, x_limit + 1):
        v = f_eval(x, A, B, C)
        if v < 0:
            continue
        ok, y = is_perfect_square(v)
        if ok:
            solutions.append((n, x, y))
            if y > 0:
                solutions.append((n, x, -y))
        # Early exit: for large x, y ~ x^(3/2), consecutive squares differ by ~sqrt(x)
        # If f(x) > (x^(3/2) + 1)^2 we can skip but in general keep going

    # --- Negative x side ---
    # f(x) < 0 for large negative x, so find left root first
    lb = find_lower_bound(n, A, B, C)
    if lb < 0:
        for x in range(max(-x_limit, lb), 0):
            v = f_eval(x, A, B, C)
            if v < 0:
                continue
            ok, y = is_perfect_square(v)
            if ok:
                solutions.append((n, x, y))
                if y > 0:
                    solutions.append((n, x, -y))

    return solutions


# ---------------------------------------------------------------------------
# Efficient version: use integer isqrt without iterating every x
# For large positive x: skip regions between consecutive integer y values
# ---------------------------------------------------------------------------

def search_n_fast(n, x_limit):
    """
    Optimised search for a single n using incremental perfect-square gap
    skipping on the positive side, and bounded negative side.
    """
    A = mpz(coeff_A(n))
    B = mpz(coeff_B(n))
    C = mpz(coeff_C(n))
    solutions = []

    def check(x_int):
        xm = mpz(x_int)
        v = xm*xm*xm + A*xm*xm + B*xm + C
        if v < 0:
            return
        s, r = isqrt_rem(v)
        if r == 0:
            y_int = int(s)
            solutions.append((n, x_int, y_int))
            if y_int > 0:
                solutions.append((n, x_int, -y_int))

    # Positive x: 0 .. x_limit
    x = 0
    while x <= x_limit:
        check(x)
        x += 1

    # Negative x: lower_bound .. -1
    # Use float to get rough lower bound quickly
    A_f, B_f, C_f = float(A), float(B), float(C)
    # Rough real root via Newton from large negative x
    xf = -abs(A_f) - 10.0
    for _ in range(60):
        fv = xf**3 + A_f*xf**2 + B_f*xf + C_f
        dfv = 3*xf**2 + 2*A_f*xf + B_f
        if abs(dfv) < 1e-30:
            break
        xf -= fv / dfv
    lb = int(math.floor(xf)) - 2

    for x_int in range(max(-x_limit, lb), 0):
        check(x_int)

    return solutions


# ---------------------------------------------------------------------------
# Main loop: expand outward from n=0
# ---------------------------------------------------------------------------

def run_forever(n_start=0, n_end=None, x_limit=10**8):
    """
    Expand search radius: n = 0, -1, 1, -2, 2, -3, 3, ...
    Writes solved solutions to SOLUTIONS_FILE immediately.
    """
    print(f"[searcher] Starting. Output -> {SOLUTIONS_FILE}")
    print(f"[searcher] x_limit = {x_limit:,}")
    total_checked = 0
    total_solutions = 0
    t0 = time.time()

    # Build iteration order: 0, -1, 1, -2, 2, ...
    def n_iter():
        if n_start is not None and n_end is not None:
            yield from range(n_start, n_end + 1)
            return
        radius = 0
        while True:
            if radius == 0:
                yield 0
            else:
                yield -radius
                yield radius
            radius += 1

    with open(SOLUTIONS_FILE, "a", buffering=1) as fout:
        for n in n_iter():
            if n == 0:
                # skip n=0? The problem says m != 0, but n=0 means t=3 neq 0
                # we search anyway
                pass
            sols = search_n_fast(n, x_limit)
            total_checked += 1
            if sols:
                for (n_v, x_v, y_v) in sols:
                    line = f"SOLUTION: n={n_v}  x={x_v}  y={y_v}  verify: y^2={y_v**2}\n"
                    fout.write(line)
                    print(line, end="")
                    total_solutions += 1
            # Progress every 1000 n values
            if total_checked % 1000 == 0:
                elapsed = time.time() - t0
                rate = total_checked / elapsed
                print(f"[progress] checked {total_checked:,} values of n | "
                      f"{rate:.1f} n/s | solutions so far: {total_solutions} | "
                      f"last n={n}", flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Search integer solutions for y^2 = cubic(x,n)"
    )
    parser.add_argument("--n_start", type=int, default=None,
                        help="Start n (if not set, expands from 0 outward)")
    parser.add_argument("--n_end", type=int, default=None,
                        help="End n (inclusive)")
    parser.add_argument("--range", type=int, default=None, dest="radius",
                        help="Search |n| <= range")
    parser.add_argument("--x_limit", type=int, default=10**7,
                        help="Maximum |x| to test per n (default 10^7)")
    args = parser.parse_args()

    if args.radius is not None:
        run_forever(n_start=-args.radius, n_end=args.radius,
                    x_limit=args.x_limit)
    else:
        run_forever(n_start=args.n_start, n_end=args.n_end,
                    x_limit=args.x_limit)


if __name__ == "__main__":
    main()
