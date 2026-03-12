#!/usr/bin/env python3
"""Charity Engine worker for searching integer solutions to

    y^2 = (36/m) x^3 + 36 x^2 + 12 m x + (m^3-19)/m,   m != 0

Strategy (x-driven exhaustive search)
--------------------------------------
For integer solutions the integrality of y^2 requires m | (36*x^3 - 19).

Proof: writing ``num = 36x^3 + 36m x^2 + 12m^2 x + m^3 - 19`` we have

    num = m*(6x+m)^2 + (36x^3 - 19)

so m | num  iff  m | (36x^3 - 19).

Therefore the candidate m values for any given x are exactly the (signed)
divisors of ``D = 36*x^3 - 19``.  All other m values are guaranteed to
produce a non-integer y^2 and can be skipped entirely.

For each divisor m the worker verifies that y^2 = num/m is a non-negative
perfect square and, if so, records the solution.

This script processes a chunk [x_start, x_end] (inclusive) and emits all
solutions as JSONL.

Output fields per solution
--------------------------
m : the parameter m from the equation (non-zero integer)
n : m^3 - 19   (the numerator constant, for bookkeeping)
x : the integer x value
y : the non-negative integer y (solutions with y and -y are both emitted)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import List


# ---------- fast arithmetic helpers ----------

def is_square(n: int) -> bool:
    if n < 0:
        return False
    r = math.isqrt(n)
    return r * r == n


def _mr_is_probable_prime(n: int) -> bool:
    """Deterministic Miller-Rabin for n < 3.3 × 10²⁴; good practical coverage beyond."""
    if n < 2:
        return False
    small_primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
    for p in small_primes:
        if n % p == 0:
            return n == p

    d = n - 1
    s = 0
    while d % 2 == 0:
        s += 1
        d //= 2

    # Base set that is deterministic for all n < 3.3 * 10^24.
    bases = [2, 325, 9375, 28178, 450775, 9780504, 1795265022]
    for a in bases:
        if a % n == 0:
            continue
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = (x * x) % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _pollard_rho(n: int) -> int:
    """Return a non-trivial factor of n (n must be composite)."""
    if n % 2 == 0:
        return 2
    if n % 3 == 0:
        return 3

    while True:
        c = random.randrange(1, n - 1)
        f = lambda x: (pow(x, 2, n) + c) % n  # noqa: E731
        x = random.randrange(0, n - 1)
        y = x
        d = 1
        while d == 1:
            x = f(x)
            y = f(f(y))
            d = math.gcd(abs(x - y), n)
        if d != n:
            return d


def factorize(n: int, out: List[int]) -> None:
    """Append all prime factors of |n| to *out* (with multiplicity)."""
    n = abs(n)
    if n <= 1:
        return
    if _mr_is_probable_prime(n):
        out.append(n)
    else:
        d = _pollard_rho(n)
        factorize(d, out)
        factorize(n // d, out)


def divisors_from_factors(factors: List[int]) -> List[int]:
    """Return all positive divisors given a prime-factor list (with multiplicity)."""
    ctr = Counter(factors)
    divisors = [1]
    for p, e in ctr.items():
        current = list(divisors)
        mul = 1
        for _ in range(e):
            mul *= p
            divisors.extend(v * mul for v in current)
    return divisors


# ---------- solution dataclass ----------

@dataclass(frozen=True)
class Solution:
    m: int
    n: int
    x: int
    y: int


# ---------- core search ----------

def scan_x_range(
    x_start: int,
    x_end: int,
    verbose: bool = False,
) -> List[Solution]:
    """Scan every integer x in [x_start, x_end] and return all solutions."""
    sols: List[Solution] = []
    total = x_end - x_start + 1
    t0 = time.monotonic()

    for idx, x in enumerate(range(x_start, x_end + 1)):
        if verbose and idx % max(1, total // 20) == 0:
            elapsed = time.monotonic() - t0
            pct = 100.0 * idx / total
            print(
                f"  progress: {idx}/{total} ({pct:.1f}%)  "
                f"elapsed={elapsed:.1f}s  solutions_so_far={len(sols)}",
                file=sys.stderr,
                flush=True,
            )

        D = 36 * x * x * x - 19
        if D == 0:
            # 36x^3 = 19 has no integer solution, but guard defensively.
            continue

        primes: List[int] = []
        factorize(D, primes)
        divs = divisors_from_factors(primes)

        for d in divs:
            for m in (d, -d):
                # y^2 = num / m,  where num = m*(6x+m)^2 + D
                num = m * (6 * x + m) ** 2 + D
                # m | num is guaranteed by construction (D = 36x^3-19, d|D).
                # We verify defensively in case of floating-point / edge issues.
                if num % m != 0:
                    continue
                y2 = num // m
                if not is_square(y2):
                    continue
                y = math.isqrt(y2)
                n_val = m * m * m - 19
                sols.append(Solution(m=m, n=n_val, x=x, y=y))
                if y != 0:
                    sols.append(Solution(m=m, n=n_val, x=x, y=-y))

    # Deduplicate (multiple factorization routes may yield the same divisor).
    uniq = {(s.m, s.n, s.x, s.y): s for s in sols}
    return list(uniq.values())


# ---------- CLI ----------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Charity Engine worker: scan x in [x_start, x_end] for integer solutions."
    )
    ap.add_argument("--x-start", type=int, required=True, help="first x value (inclusive)")
    ap.add_argument("--x-end", type=int, required=True, help="last  x value (inclusive)")
    ap.add_argument("--out", type=str, default="solutions.jsonl", help="JSONL output file")
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="print scan progress to stderr",
    )
    args = ap.parse_args()

    if args.x_end < args.x_start:
        raise SystemExit("x_end must be >= x_start")

    sols = scan_x_range(args.x_start, args.x_end, verbose=args.verbose)
    out_dir = os.path.dirname(args.out) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in sorted(sols, key=lambda t: (t.x, t.m, t.y)):
            f.write(json.dumps(s.__dict__) + "\n")

    print(
        f"scanned_x=[{args.x_start},{args.x_end}]  "
        f"solutions={len(sols)}  out={args.out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
