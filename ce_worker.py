#!/usr/bin/env python3
"""Charity Engine worker for searching integer solutions to
    y^2 = (36/m) x^3 + 36 x^2 + 12 m x + (m^3-19)/m, m != 0

Strategy (x-driven exhaustive search):
- For fixed integer x, integrality requires m | (36*x^3 - 19).
- So candidate m are exactly divisors of D = 36*x^3 - 19.
- For each divisor m, test whether resulting y^2 is a nonnegative square.

This script processes a chunk [x_start, x_end] and emits all solutions found.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple


# ---------- fast arithmetic helpers ----------

def is_square(n: int) -> bool:
    if n < 0:
        return False
    r = math.isqrt(n)
    return r * r == n


def _mr_is_probable_prime(n: int) -> bool:
    """Deterministic MR for n < 2^128 with conservative base set; works well in practice for CE scans."""
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

    # Good practical base set for 64-bit and many larger values.
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
    if n % 2 == 0:
        return 2
    if n % 3 == 0:
        return 3

    while True:
        c = random.randrange(1, n - 1)
        f = lambda x: (pow(x, 2, n) + c) % n
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
    """Prime factorization of |n| into out list (with multiplicity)."""
    n = abs(n)
    if n <= 1:
        return
    if _mr_is_probable_prime(n):
        out.append(n)
    else:
        d = _pollard_rho(n)
        factorize(d, out)
        factorize(n // d, out)


def divisors_from_factors(factors: Iterable[int]) -> List[int]:
    ctr = Counter(factors)
    divisors = [1]
    for p, e in ctr.items():
        current = list(divisors)
        mul = 1
        for _ in range(e):
            mul *= p
            divisors.extend(v * mul for v in current)
    return divisors


@dataclass(frozen=True)
class Solution:
    m: int
    n: int
    x: int
    y: int


def scan_x_range(x_start: int, x_end: int) -> List[Solution]:
    """Inclusive scan over x in [x_start, x_end]."""
    sols: List[Solution] = []

    for x in range(x_start, x_end + 1):
        D = 36 * x * x * x - 19
        if D == 0:
            continue  # no nonzero divisor m

        primes: List[int] = []
        factorize(D, primes)
        divs = divisors_from_factors(primes)

        for d in divs:
            for m in (d, -d):
                if m == 0:
                    continue

                # y^2 = (36x^3 + 36m x^2 + 12m^2 x + m^3 - 19)/m
                num = 36 * x * x * x + 36 * m * x * x + 12 * m * m * x + m * m * m - 19
                if num % m != 0:
                    continue
                y2 = num // m
                if not is_square(y2):
                    continue
                y = math.isqrt(y2)
                n = m * m * m - 19
                sols.append(Solution(m=m, n=n, x=x, y=y))
                if y != 0:
                    sols.append(Solution(m=m, n=n, x=x, y=-y))

    # Remove accidental duplicates (can occur from repeated factoring routes)
    uniq = {(s.m, s.n, s.x, s.y): s for s in sols}
    return list(uniq.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--x-start", type=int, required=True)
    ap.add_argument("--x-end", type=int, required=True)
    ap.add_argument("--out", type=str, default="solutions.jsonl")
    args = ap.parse_args()

    if args.x_end < args.x_start:
        raise SystemExit("x_end must be >= x_start")

    sols = scan_x_range(args.x_start, args.x_end)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in sorted(sols, key=lambda t: (t.x, t.m, t.y)):
            f.write(json.dumps(s.__dict__) + "\n")

    print(f"scanned_x=[{args.x_start},{args.x_end}] solutions={len(sols)} out={args.out}")


if __name__ == "__main__":
    main()
