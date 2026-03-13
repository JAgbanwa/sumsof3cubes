#!/usr/bin/env python3
"""
worker_pari.py  —  Charity Engine / BOINC Python wrapper for the
                   PARI/GP algebraic-completeness worker

Equation:
    y² = x³ + 1296·n²·x² + 15552·n³·x + (46656·n⁴ − 19·n)

Strategy:
    For each n, this worker calls PARI/GP's ellintegralpoints() which
    provably finds ALL integer points on the elliptic curve E_n.
    This is fundamentally different from the brute-force C worker:
    no matter how large the solutions are, they will be found.

Work-unit format (wu.txt):
    n_start  <int>
    n_end    <int>
    batch    <int>   (how many n values per gp subprocess; default 50)

Output (result.txt):  one line per solution:  n  x  y

Checkpoint (checkpoint_pari.json):
    {"last_n": <int>}

Usage (standalone):
    python3 worker_pari.py wu.txt result.txt [checkpoint_pari.json]

BOINC/CE:
    Same invocation; compile with no changes. The script respects
    boinc_api via subprocess heartbeats if BOINC=1 env is set.

Dependencies:
    • gp (PARI/GP)  —  `apt-get install pari-gp`
    • Python 3.7+
    • Optional: cypari2  for in-process PARI (auto-detected)
"""

import sys
import os
import json
import time
import subprocess
import threading
import argparse
import tempfile
from pathlib import Path

# ── optional in-process PARI via cypari2 ──────────────────────────────
try:
    import cypari2
    _CYPARI = cypari2.Pari()
    _CYPARI.default("stacksize", 256 * 1024 * 1024)  # 256 MB
    HAS_CYPARI = True
except ImportError:
    HAS_CYPARI = False

# ── BOINC heartbeat thread ──────────────────────────────────────────────
_BOINC_MODE = os.environ.get("BOINC", "0") == "1"

def _boinc_heartbeat():
    """Writes a fraction-done signal every 10 s so the BOINC client
       doesn't think the worker has died."""
    while True:
        try:
            with open("fraction_done", "w") as f:
                f.write("0.5\n")
        except Exception:
            pass
        time.sleep(10)

if _BOINC_MODE:
    t = threading.Thread(target=_boinc_heartbeat, daemon=True)
    t.start()

# ── Path to the .gp script, co-located with this file ─────────────────
_HERE      = Path(__file__).parent
_GP_SCRIPT = _HERE / "worker_ec.gp"
_GP_BIN    = os.environ.get("GP_BIN", "gp")


# ══════════════════════════════════════════════════════════════════════
# Pure-Python fallback verifier (used to double-check every solution)
# ══════════════════════════════════════════════════════════════════════

def verify(n: int, x: int, y: int) -> bool:
    rhs = x**3 + 1296*n**2*x**2 + 15552*n**3*x + 46656*n**4 - 19*n
    return y*y == rhs


# ══════════════════════════════════════════════════════════════════════
# cypari2 path: run entirely inside Python with no subprocess overhead
# ══════════════════════════════════════════════════════════════════════

def _search_cypari(n_start: int, n_end: int):
    """Yield (n, x, y) for all solutions via cypari2 in-process PARI."""
    pari = _CYPARI

    def search_one(n):
        a2 = 1296 * n * n
        a4 = 15552 * n**3
        a6 = 46656 * n**4 - 19 * n
        try:
            E   = pari.ellinit([0, a2, 0, a4, a6])
            if pari.elldisc(E) == 0:
                return
            pts = pari.ellintegralpoints(E)
            for pt in pts:
                x, y = int(pt[0]), int(pt[1])
                if verify(n, x, y):
                    yield (n, x,  y)
                    if y != 0:
                        yield (n, x, -y)
        except Exception as exc:
            print(f"[cypari] n={n}  error: {exc}", file=sys.stderr)

    # n = 0 special case: y² = x³
    if n_start <= 0 <= n_end:
        yield (0, 0, 0)
        k = 1
        while k * k <= 10**6:
            yield (0, k*k,  k**3)
            yield (0, k*k, -k**3)
            k += 1

    for n in range(n_start, n_end + 1):
        if n == 0:
            continue
        yield from search_one(n)


# ══════════════════════════════════════════════════════════════════════
# gp subprocess path
# ══════════════════════════════════════════════════════════════════════

def _build_gp_input(n_start: int, n_end: int) -> str:
    return (
        f'\\\\r {_GP_SCRIPT}\n'
        f'ec_search({n_start},{n_end})\n'
        f'quit\n'
    )


def _search_gp_subprocess(n_start: int, n_end: int, timeout: int = 3600):
    """Yield (n, x, y) from a gp subprocess covering [n_start, n_end]."""
    gp_input = _build_gp_input(n_start, n_end)
    try:
        proc = subprocess.run(
            [_GP_BIN, "-q", f"--stacksize=256m"],
            input=gp_input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("##") or line.startswith("?"):
                continue
            parts = line.split()
            if len(parts) == 3:
                try:
                    n, x, y = int(parts[0]), int(parts[1]), int(parts[2])
                    if verify(n, x, y):
                        yield (n, x, y)
                    else:
                        print(f"[gp] VERIFY FAIL n={n} x={x} y={y}",
                              file=sys.stderr)
                except ValueError:
                    pass
        if proc.returncode != 0:
            print(f"[gp] stderr: {proc.stderr[:500]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"[gp] TIMEOUT for n=[{n_start},{n_end}]", file=sys.stderr)
    except FileNotFoundError:
        print(f"[gp] gp binary not found at '{_GP_BIN}'. "
              "Install pari-gp or set GP_BIN env.", file=sys.stderr)
        raise


# ══════════════════════════════════════════════════════════════════════
# Checkpoint helpers
# ══════════════════════════════════════════════════════════════════════

def load_checkpoint(path: str, default_n: int) -> int:
    try:
        with open(path) as f:
            return json.load(f).get("last_n", default_n)
    except Exception:
        return default_n


def save_checkpoint(path: str, last_n: int):
    with open(path, "w") as f:
        json.dump({"last_n": last_n}, f)


# ══════════════════════════════════════════════════════════════════════
# Main worker
# ══════════════════════════════════════════════════════════════════════

def run_worker(wu_path: str, result_path: str, ckpt_path: str,
               batch: int = 20):
    # ── Parse work unit ────────────────────────────────────────────
    n_start = n_end = None
    with open(wu_path) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                if parts[0] == "n_start": n_start = int(parts[1])
                if parts[0] == "n_end":   n_end   = int(parts[1])
                if parts[0] == "batch":   batch   = int(parts[1])
    if n_start is None or n_end is None:
        print("[worker_pari] wu.txt missing n_start / n_end", file=sys.stderr)
        sys.exit(1)

    # ── Resume ─────────────────────────────────────────────────────
    resume_n = load_checkpoint(ckpt_path, n_start)
    if resume_n > n_end:
        print(f"[worker_pari] Already completed [{n_start},{n_end}]")
        return

    # ── Open result file in append mode on resume ──────────────────
    mode = "a" if resume_n > n_start else "w"
    out_f = open(result_path, mode, buffering=1)

    total = 0
    t_start = time.time()

    # ── Prefer cypari2 in-process if available ─────────────────────
    use_cypari = HAS_CYPARI
    if not use_cypari:
        # probe gp binary
        try:
            subprocess.run([_GP_BIN, "--version"], capture_output=True,
                           timeout=5)
        except Exception:
            print("[worker_pari] Neither cypari2 nor gp found!",
                  file=sys.stderr)
            sys.exit(2)

    # ── Iterate n in batches ────────────────────────────────────────
    n = resume_n
    while n <= n_end:
        batch_end = min(n + batch - 1, n_end)
        print(f"[worker_pari] Processing n=[{n},{batch_end}]"
              f"  use_cypari={use_cypari}", flush=True)

        try:
            if use_cypari:
                gen = _search_cypari(n, batch_end)
            else:
                gen = _search_gp_subprocess(n, batch_end)

            for (nn, xx, yy) in gen:
                out_f.write(f"{nn} {xx} {yy}\n")
                out_f.flush()
                print(f"  [SOLUTION] n={nn:>12}  x={xx:>20}  y={yy:>20}")
                total += 1

        except Exception as exc:
            print(f"[worker_pari] Error batch n=[{n},{batch_end}]: {exc}",
                  file=sys.stderr)
            # fall back to subprocess if cypari crashed
            if use_cypari:
                use_cypari = False
                continue

        save_checkpoint(ckpt_path, batch_end + 1)
        n = batch_end + 1

        elapsed = time.time() - t_start
        print(f"  total_solutions={total}  elapsed={elapsed:.1f}s", flush=True)

    out_f.close()
    elapsed = time.time() - t_start
    print(f"[worker_pari] DONE n=[{n_start},{n_end}]  "
          f"solutions={total}  elapsed={elapsed:.1f}s")


# ══════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="PARI/GP elliptic-curve integral-points worker for "
                    "y² = x³ + 1296n²x² + 15552n³x + (46656n⁴ − 19n)")
    ap.add_argument("wu_file",     help="Work-unit file (n_start/n_end/batch)")
    ap.add_argument("result_file", help="Output file for solutions")
    ap.add_argument("checkpoint",  nargs="?",
                    default="checkpoint_pari.json",
                    help="Checkpoint JSON file (default: checkpoint_pari.json)")
    ap.add_argument("--batch", type=int, default=20,
                    help="n values per PARI subprocess call (default 20)")
    args = ap.parse_args()
    run_worker(args.wu_file, args.result_file, args.checkpoint,
               batch=args.batch)


if __name__ == "__main__":
    main()
