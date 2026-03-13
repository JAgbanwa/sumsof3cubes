#!/usr/bin/env python3
"""
BOINC Validator for sum-of-cubes searcher.

Validates results by re-checking each reported solution algebraically.
Two results agree if they report the same set of (n, x, |y|) solutions.

The validator is called by BOINC with:
    python3 validator.py <result1_file> <result2_file>

Exit codes:
    0 = valid (results agree)
    1 = invalid / mismatch
    2 = error / can't check
"""

import sys
import os

def parse_solutions(path):
    """Parse result file; each line: 'n x y' (integers)."""
    solutions = set()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) != 3:
                    continue
                n, x, y = int(parts[0]), int(parts[1]), int(parts[2])
                solutions.add((n, x, abs(y)))
    except Exception as e:
        print(f"[validator] Error reading {path}: {e}", file=sys.stderr)
        return None
    return solutions

def verify_solution(n, x, y):
    """Re-compute RHS and verify y^2 == RHS."""
    t = 4 * n + 3
    A = 81 * t * t
    B = 243 * t * t * t
    C = t * (11664 * n**3 + 26244 * n**2 + 19683 * n + 4916)
    rhs = x**3 + A * x**2 + B * x + C
    return rhs == y * y

def main():
    if len(sys.argv) < 3:
        print("Usage: validator.py <result1> <result2>", file=sys.stderr)
        sys.exit(2)

    r1 = parse_solutions(sys.argv[1])
    r2 = parse_solutions(sys.argv[2])

    if r1 is None or r2 is None:
        sys.exit(2)

    # Verify every claimed solution from both results
    all_claimed = r1 | r2
    for (n, x, y) in all_claimed:
        if not verify_solution(n, x, y):
            print(f"[validator] INVALID SOLUTION: n={n} x={x} y={y}", file=sys.stderr)
            sys.exit(1)

    # Check agreement
    if r1 == r2:
        print(f"[validator] VALID: both results agree ({len(r1)} solutions)")
        sys.exit(0)
    else:
        only_r1 = r1 - r2
        only_r2 = r2 - r1
        print(f"[validator] MISMATCH: only_in_r1={only_r1} only_in_r2={only_r2}",
              file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
