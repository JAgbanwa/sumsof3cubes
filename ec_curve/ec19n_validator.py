#!/usr/bin/env python3
"""
ec19n_validator.py  —  BOINC result validator for ec19n project.

Verifies that:
  1. Two result files from redundant hosts agree (canonical result check).
  2. Every reported solution (n, x, y, k) satisfies:
       y  == k * 6 * n
       y² == x³ + 1296n²x² + 15552n³x + (46656n⁴ - 19n)
       n ∈ {1, -1, 19, -19}

Exit codes:
  0  — valid / canonical match
  1  — mismatch between two result files
  2  — internal error or format problem
"""

import sys
from pathlib import Path


def parse_results(path: str) -> list:
    """Return list of (n, x, y, k) int tuples, or raise ValueError."""
    solutions = []
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 4:
            raise ValueError(f"Line {i}: expected 4 fields, got {len(parts)}: {line!r}")
        n, x, y, k = (int(p) for p in parts)
        solutions.append((n, x, y, k))
    return solutions


def verify_solution(n: int, x: int, y: int, k: int) -> str | None:
    """Return None if valid, else an error string."""
    if n not in (1, -1, 19, -19):
        return f"n={n} not in {{1,-1,19,-19}}"
    if y != k * 6 * n:
        return f"y={y} ≠ k*6*n = {k*6*n}"
    rhs = x**3 + 1296*n**2*x**2 + 15552*n**3*x + 46656*n**4 - 19*n
    if y * y != rhs:
        return f"y²={y*y} ≠ f(x,n)={rhs}"
    return None


def main():
    if len(sys.argv) < 3:
        print("Usage: ec19n_validator.py <result1> <result2>", file=sys.stderr)
        sys.exit(2)

    try:
        res1 = parse_results(sys.argv[1])
        res2 = parse_results(sys.argv[2])
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

    # Verify each solution in file 1
    for sol in res1:
        err = verify_solution(*sol)
        if err:
            print(f"[INVALID] {sol}: {err}", file=sys.stderr)
            sys.exit(2)

    # Verify each solution in file 2
    for sol in res2:
        err = verify_solution(*sol)
        if err:
            print(f"[INVALID] {sol}: {err}", file=sys.stderr)
            sys.exit(2)

    # Canonical match: both files must report the same set of solutions
    if set(res1) != set(res2):
        print(f"[MISMATCH] file1 has {len(res1)} sols, file2 has {len(res2)} sols",
              file=sys.stderr)
        sys.exit(1)

    print(f"[OK] {len(res1)} solutions, both results agree.", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
