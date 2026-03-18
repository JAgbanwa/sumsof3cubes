#!/usr/bin/env python3
"""
validator.py  —  BOINC cross-validator for ec_new_family

BOINC requires at least 2 independent result copies per WU (fanout=2).
This validator compares two result files for the same WU and
marks the WU as valid if both outputs are byte-identical or
contain algebraically equivalent solutions (order-independent match).

Usage (run by BOINC server daemon, or manually):
    python3 validator.py result1.txt result2.txt
    echo $?    # 0 = valid, 1 = mismatch, 2 = error
"""

import sys
from pathlib import Path


def ec_rhs(n: int, X: int) -> int:
    a4 = -45349632 * n**4 + 419904 * n**3
    a6 = 3 * (39182082048 * n**6
              - 544195584 * n**5
              + 1259712   * n**4
              - 19        * n)
    return X**3 + a4*X + a6


def parse_solutions(path: str) -> set:
    """Return frozenset of (n, X, y) tuples from a result file."""
    solutions = set()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                try:
                    solutions.add((int(parts[0]), int(parts[1]), int(parts[2])))
                except ValueError:
                    pass
    except OSError as e:
        print(f"[validator] Cannot read {path}: {e}", file=sys.stderr)
        return None
    return frozenset(solutions)


def verify_all(solutions: frozenset, path: str) -> bool:
    """Check every claimed solution actually satisfies the curve equation."""
    for (n, X, y) in solutions:
        if X == -3888 * n * n:
            print(f"[validator] EXCLUDED point X={X} in {path}", file=sys.stderr)
            return False
        if y * y != ec_rhs(n, X):
            print(f"[validator] INVALID: n={n} X={X} y={y} in {path}",
                  file=sys.stderr)
            return False
    return True


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} result1.txt result2.txt", file=sys.stderr)
        sys.exit(2)

    path1, path2 = sys.argv[1], sys.argv[2]

    s1 = parse_solutions(path1)
    s2 = parse_solutions(path2)

    if s1 is None or s2 is None:
        sys.exit(2)   # I/O error → BOINC marks result as inconclusive

    # Verify each file independently
    if not verify_all(s1, path1):
        print(f"[validator] {path1}: contains invalid solution(s) — REJECT",
              file=sys.stderr)
        sys.exit(1)
    if not verify_all(s2, path2):
        print(f"[validator] {path2}: contains invalid solution(s) — REJECT",
              file=sys.stderr)
        sys.exit(1)

    # Cross-compare
    if s1 != s2:
        extra1 = s1 - s2
        extra2 = s2 - s1
        print("[validator] MISMATCH between the two result copies:",
              file=sys.stderr)
        if extra1:
            print(f"  Only in {path1}: {sorted(extra1)}", file=sys.stderr)
        if extra2:
            print(f"  Only in {path2}: {sorted(extra2)}", file=sys.stderr)
        sys.exit(1)

    print(f"[validator] OK — {len(s1)} solution(s) match between both copies.",
          file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
