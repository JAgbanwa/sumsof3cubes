#!/usr/bin/env python3
"""
validator_large.py  —  BOINC redundant-result validator
══════════════════════════════════════════════════════════════════════

Called by the BOINC server's Python validator framework to:
1. Parse two (or more) result files for the same work unit.
2. Verify every claimed solution against the exact equation.
3. declare a canonical result only when ≥ 2 results agree.

Matches the BOINC Python validator interface:
    init_result(result)      — called once per result
    compare_results(r1, r2)  — returns True if results are equivalent
    cleanup_result(result)   — optional cleanup

Also usable standalone:
    python3 validator_large.py result1.txt result2.txt
══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────
# Verifier
# ─────────────────────────────────────────────────────────────────────

def verify_weierstrass(m: int, X: int, Y: int) -> bool:
    rhs = (X**3
           + 1296 * m**2 * X**2
           + 15552 * m**3 * X
           + 46656 * m**4
           - 19 * m)
    return Y * Y == rhs

# ─────────────────────────────────────────────────────────────────────
# Parse result file → frozenset of verified (m, X, Y)
# ─────────────────────────────────────────────────────────────────────

def parse_and_verify(path: str) -> frozenset[tuple[int, int, int]]:
    sols: set[tuple[int, int, int]] = set()
    try:
        text = Path(path).read_text()
    except Exception:
        return frozenset()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("SOL "):
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                m, X, Y = int(parts[1]), int(parts[2]), int(parts[3])
            except ValueError:
                continue
            if verify_weierstrass(m, X, Y):
                sols.add((m, X, Y))
    return frozenset(sols)

# ─────────────────────────────────────────────────────────────────────
# BOINC validator interface
# ─────────────────────────────────────────────────────────────────────

def init_result(result) -> None:
    """Cache the parsed solution set on the result object."""
    try:
        path = result.output_files[0].path
    except (AttributeError, IndexError):
        path = str(result)
    result._ec_solutions = parse_and_verify(path)


def compare_results(r1, r2) -> bool:
    """
    Two results are equivalent iff their verified solution sets match.
    Both missing and extra solutions cause a mismatch.
    """
    s1 = getattr(r1, "_ec_solutions", frozenset())
    s2 = getattr(r2, "_ec_solutions", frozenset())
    return s1 == s2


def cleanup_result(result) -> None:
    try:
        del result._ec_solutions
    except AttributeError:
        pass

# ─────────────────────────────────────────────────────────────────────
# Standalone comparison
# ─────────────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, path):
        self.output_files = [type("F", (), {"path": path})()]

def main():
    if len(sys.argv) < 3:
        print("Usage: validator_large.py result1.txt result2.txt [...]")
        sys.exit(1)

    paths = sys.argv[1:]
    results = [_FakeResult(p) for p in paths]
    for r in results:
        init_result(r)

    base = results[0]
    all_match = True
    for other in results[1:]:
        ok = compare_results(base, other)
        print(f"{paths[0]}  vs  {other.output_files[0].path}: "
              f"{'MATCH ✓' if ok else 'MISMATCH ✗'}")
        if not ok:
            all_match = False
            d1 = base._ec_solutions - other._ec_solutions
            d2 = other._ec_solutions - base._ec_solutions
            if d1:
                print(f"  only in {paths[0]}: {d1}")
            if d2:
                print(f"  only in other:     {d2}")

    print(f"\nTotal solutions in {paths[0]}: {len(base._ec_solutions)}")
    sys.exit(0 if all_match else 1)


if __name__ == "__main__":
    main()
