#!/usr/bin/env python3
"""
validator.py  —  Charity Engine / BOINC Validator

Checks that two result files for the same work unit agree on the set
of integer solutions to:

    y² = x³ + 1296·n²·x² + 15552·n³·x + (46656·n⁴ − 19·n)

Exit codes (BOINC convention):
    0  — valid (results agree)
    1  — invalid / disagreement
    2  — error  (can't read or parse files)

Called by BOINC as:
    python3 validator.py <result1_file> <result2_file>
"""

import sys


def ec_rhs(n: int, x: int) -> int:
    """Right-hand side of the elliptic curve equation."""
    return (x**3
            + 1296 * n**2 * x**2
            + 15552 * n**3 * x
            + 46656 * n**4
            - 19 * n)


def verify(n: int, x: int, y: int) -> bool:
    return y * y == ec_rhs(n, x)


def parse_file(path: str) -> set | None:
    """Parse result file; returns a set of (n, x, |y|) tuples or None on error."""
    sols = set()
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                parts = ln.split()
                if len(parts) != 3:
                    continue
                try:
                    n, x, y = int(parts[0]), int(parts[1]), int(parts[2])
                    sols.add((n, x, abs(y)))
                except ValueError:
                    pass
    except OSError as e:
        print(f"[validator] cannot read {path}: {e}", file=sys.stderr)
        return None
    return sols


def main():
    if len(sys.argv) < 3:
        print("Usage: validator.py <result1> <result2>", file=sys.stderr)
        sys.exit(2)

    r1 = parse_file(sys.argv[1])
    r2 = parse_file(sys.argv[2])

    if r1 is None or r2 is None:
        sys.exit(2)

    # ── Verify every claimed solution algebraically ──────────────────
    for (n, x, y) in r1 | r2:
        if not verify(n, x, y):
            print(f"[validator] ALGEBRAIC FAIL: n={n} x={x} y={y}",
                  file=sys.stderr)
            sys.exit(1)

    # ── Check both results agree ─────────────────────────────────────
    if r1 == r2:
        print(f"[validator] VALID — both results agree ({len(r1)} solutions)")
        sys.exit(0)
    else:
        only1 = r1 - r2
        only2 = r2 - r1
        print(f"[validator] MISMATCH: only_in_r1={only1}  only_in_r2={only2}",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
