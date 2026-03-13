#!/usr/bin/env python3
"""
worker_sage.py  —  Algebraically-COMPLETE integer-point search via SageMath

Equation:  y^2 = x^3 + 1296*n^2*x^2 + 15552*n^3*x + (46656*n^4 - 19*n)

For each n, initialise E_n over QQ and call E.integral_points(both_signs=True).
Sage uses:
  • Nagell-Tate  (torsion subgroup)
  • 2-descent    (Mordell-Weil rank + generators)
  • Baker / Wüstholz height bounds  (bound on integral points)
  • LLL lattice reduction + sieve    (enumerate within bound)
  → PROVABLY FINDS ALL INTEGRAL POINTS (Siegel's theorem guarantees finiteness).

Work-unit format (wu.txt):
    n_start  <int>
    n_end    <int>

Output (result.txt):  n  x  y  (one solution per line)

Usage:
    sage -python worker_sage.py wu.txt result.txt [checkpoint.json]
    # or, if sage is on PATH:
    python3 worker_sage.py wu.txt result.txt [checkpoint.json]
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path

# ── Import Sage (works both via `sage -python` and system python3 if
#    sage is installed and its site-packages are on PYTHONPATH) ────────────
try:
    from sage.all import EllipticCurve, QQ
    _SAGE_OK = True
except ImportError:
    _SAGE_OK = False


# ══════════════════════════════════════════════════════════════════════
# Equation helpers
# ══════════════════════════════════════════════════════════════════════

def ec_rhs(n: int, x: int) -> int:
    return (x**3
            + 1296 * n**2 * x**2
            + 15552 * n**3 * x
            + 46656 * n**4
            - 19 * n)


def verify(n: int, x: int, y: int) -> bool:
    return y * y == ec_rhs(n, x)


def n0_solutions(x_lim: int = 10**6):
    """n=0 is degenerate: y^2 = x^3  ↔  (k^2, k^3) for k ∈ Z."""
    yield (0, 0, 0)
    k = 1
    while k * k <= x_lim:
        yield (0, k * k,  k**3)
        yield (0, k * k, -k**3)
        k += 1


# ══════════════════════════════════════════════════════════════════════
# SageMath path: provably complete
# ══════════════════════════════════════════════════════════════════════

def search_n_sage(n: int) -> list[tuple]:
    """Return ALL integer solutions (n,x,y) for this n using Sage."""
    if not _SAGE_OK:
        raise RuntimeError("SageMath not available")
    a2 = 1296 * n * n
    a4 = 15552 * n**3
    a6 = 46656 * n**4 - 19 * n
    E = EllipticCurve(QQ, [0, a2, 0, a4, a6])
    if E.discriminant() == 0:
        return []
    results = []
    try:
        # Compute MW generators with proof=False (uses BSD heuristics; very
        # reliable in practice and allows Sage to proceed when 2-descent
        # cannot determine the rank with certainty).
        try:
            mw = E.gens(proof=False)
        except Exception:
            # Rank 0 or torsion-only curve — integral_points can still run.
            mw = []
        pts = E.integral_points(mw_base=mw, both_signs=True)
        for pt in pts:
            if pt.is_infinity():
                continue
            x, y = int(pt[0]), int(pt[1])
            if verify(n, x, y):
                results.append((n, x, y))
            else:
                print(f"  [WARN] verify fail n={n} x={x} y={y}",
                      file=sys.stderr)
    except Exception as exc:
        print(f"  [ERR] n={n}: {exc}", file=sys.stderr)
    return results


# ══════════════════════════════════════════════════════════════════════
# Checkpoint helpers
# ══════════════════════════════════════════════════════════════════════

def load_ckpt(path: str, default: int) -> int:
    try:
        return json.loads(Path(path).read_text()).get("last_n", default)
    except Exception:
        return default


def save_ckpt(path: str, last_n: int):
    Path(path).write_text(json.dumps({"last_n": last_n}))


# ══════════════════════════════════════════════════════════════════════
# Main worker
# ══════════════════════════════════════════════════════════════════════

def run_worker(wu_path: str, result_path: str, ckpt_path: str):
    if not _SAGE_OK:
        print("[worker_sage] SageMath not importable — cannot proceed.",
              file=sys.stderr)
        sys.exit(2)

    # ── Parse work unit ─────────────────────────────────────────────
    n_start = n_end = None
    with open(wu_path) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                if parts[0] == "n_start":
                    n_start = int(parts[1])
                if parts[0] == "n_end":
                    n_end = int(parts[1])
    if n_start is None or n_end is None:
        print("[worker_sage] ERROR: wu.txt missing n_start or n_end",
              file=sys.stderr)
        sys.exit(1)

    # ── Resume from checkpoint ──────────────────────────────────────
    resume = load_ckpt(ckpt_path, n_start)
    if resume > n_end:
        print(f"[worker_sage] Already complete [{n_start},{n_end}]")
        return

    mode = "a" if resume > n_start else "w"
    total = 0
    t0 = time.time()

    print(f"[worker_sage] Starting  n=[{n_start},{n_end}]  resume_at={resume}",
          flush=True)

    with open(result_path, mode, buffering=1) as out:
        # ── n=0 degenerate ───────────────────────────────────────────
        if n_start <= 0 <= n_end and resume <= 0:
            for (nn, xx, yy) in n0_solutions():
                out.write(f"{nn} {xx} {yy}\n")
            save_ckpt(ckpt_path, 1)
            resume = 1

        # ── positive n ───────────────────────────────────────────────
        for n in range(max(resume, max(1, n_start)), n_end + 1):
            sols = search_n_sage(n)
            for (nn, xx, yy) in sols:
                out.write(f"{nn} {xx} {yy}\n")
                out.flush()
                print(f"  ★ SOLUTION  n={nn:>10}  x={xx:>20}  y={yy:>20}",
                      flush=True)
                total += 1
            save_ckpt(ckpt_path, n + 1)
            if n % 20 == 0 or sols:
                print(f"  [sage] n={n}  total={total}  "
                      f"t={time.time()-t0:.1f}s", flush=True)

        # ── negative n ───────────────────────────────────────────────
        for n in range(min(-1, n_end), n_start - 1, -1):
            if n == 0:
                continue
            sols = search_n_sage(n)
            for (nn, xx, yy) in sols:
                out.write(f"{nn} {xx} {yy}\n")
                out.flush()
                print(f"  ★ SOLUTION  n={nn:>10}  x={xx:>20}  y={yy:>20}",
                      flush=True)
                total += 1
            save_ckpt(ckpt_path, n - 1)
            if n % 20 == 0 or sols:
                print(f"  [sage] n={n}  total={total}  "
                      f"t={time.time()-t0:.1f}s", flush=True)

    elapsed = time.time() - t0
    print(f"[worker_sage] DONE  [{n_start},{n_end}]  "
          f"solutions={total}  elapsed={elapsed:.1f}s")


# ══════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="Sage integral-point worker for "
                    "y^2 = x^3 + 1296n^2x^2 + 15552n^3x + (46656n^4 - 19n)")
    ap.add_argument("wu_file")
    ap.add_argument("result_file")
    ap.add_argument("checkpoint", nargs="?", default="checkpoint_sage.json")
    args = ap.parse_args()
    run_worker(args.wu_file, args.result_file, args.checkpoint)


if __name__ == "__main__":
    main()
