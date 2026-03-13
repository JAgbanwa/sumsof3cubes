#!/usr/bin/env python3
"""
local_search.py  —  Run the ec_curve search RIGHT NOW on this machine.

Launches N parallel workers (C brute-force + optional PARI exact),
covering all integers n from 0 outward in both directions.

Usage:
    python3 local_search.py                 # default: auto cpu-count
    python3 local_search.py --workers 8     # explicit parallelism
    python3 local_search.py --x_limit 1e8   # wider brute-force bound
    python3 local_search.py --pari          # also run PARI exact workers

Solutions are written in real time to:
    output/solutions_master.txt

Requires:
    ./worker_ec  (already built via `make`)
    gp           (for --pari mode)    apt-get install pari-gp
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Queue, Empty

# ══════════════════════════════════════════════════════════════════════
# Equation verifier (pure Python, no deps)
# ══════════════════════════════════════════════════════════════════════

def verify(n: int, x: int, y: int) -> bool:
    rhs = (x**3
           + 1296 * n**2 * x**2
           + 15552 * n**3 * x
           + 46656 * n**4
           - 19 * n)
    return y * y == rhs


# ══════════════════════════════════════════════════════════════════════
# Shared state
# ══════════════════════════════════════════════════════════════════════

_lock           = threading.Lock()
_solutions      = set()           # (n, x, |y|)
_total_new      = 0
_master_path    = Path("output/solutions_master.txt")
_ckpt_state_path = Path("output/local_search_state.json")


def _record_solutions_from_file(result_path: str):
    """Read a result file and append new, verified solutions to master."""
    global _total_new
    new_lines = []
    try:
        with open(result_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) != 3:
                    continue
                try:
                    n, x, y = int(parts[0]), int(parts[1]), int(parts[2])
                except ValueError:
                    continue
                key = (n, x, abs(y))
                with _lock:
                    if key in _solutions:
                        continue
                    if not verify(n, x, y):
                        print(f"  [WARN] VERIFY FAIL  n={n} x={x} y={y}",
                              flush=True)
                        continue
                    _solutions.add(key)
                    _total_new += 1
                    new_lines.append(f"{n} {x} {y}")
                    print(f"  ★★★ SOLUTION  n={n:>14}  x={x:>22}  y={y:>22}",
                          flush=True)
    except OSError:
        pass

    if new_lines:
        _master_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_master_path, "a") as mf:
            for ln in new_lines:
                mf.write(ln + "\n")


# ══════════════════════════════════════════════════════════════════════
# C brute-force worker task
# ══════════════════════════════════════════════════════════════════════

_HERE = Path(__file__).parent
_C_WORKER = _HERE / "worker_ec"


def _run_c_worker(n_start: int, n_end: int, x_limit: int,
                  wu_id: str, out_dir: Path):
    wu_path  = out_dir / f"wu_{wu_id}.txt"
    res_path = out_dir / f"result_{wu_id}.txt"
    ckp_path = out_dir / f"ckpt_{wu_id}.txt"

    wu_path.write_text(
        f"n_start  {n_start}\n"
        f"n_end    {n_end}\n"
        f"x_limit  {x_limit}\n"
    )

    if not _C_WORKER.exists():
        print(f"  [worker_c] binary not found at {_C_WORKER}! Run `make`.",
              file=sys.stderr)
        return

    cmd = [str(_C_WORKER), str(wu_path), str(res_path), str(ckp_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  [worker_c {wu_id}] exit {proc.returncode}  "
              f"{proc.stderr[:200]}", file=sys.stderr)

    _record_solutions_from_file(str(res_path))


# ══════════════════════════════════════════════════════════════════════
# PARI exact worker task
# ══════════════════════════════════════════════════════════════════════

_PARI_WORKER = _HERE / "worker_pari.py"
_GP_SCRIPT   = _HERE / "worker_ec.gp"
_GP_BIN      = os.environ.get("GP_BIN", "gp")


def _run_pari_worker(n_start: int, n_end: int, wu_id: str, out_dir: Path):
    wu_path  = out_dir / f"wu_pari_{wu_id}.txt"
    res_path = out_dir / f"result_pari_{wu_id}.txt"
    ckp_path = out_dir / f"ckpt_pari_{wu_id}.json"

    wu_path.write_text(
        f"n_start  {n_start}\n"
        f"n_end    {n_end}\n"
        f"batch    5\n"
    )

    cmd = [sys.executable, str(_PARI_WORKER),
           str(wu_path), str(res_path), str(ckp_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0:
        print(f"  [worker_pari {wu_id}] exit {proc.returncode}  "
              f"{proc.stderr[:200]}", file=sys.stderr)

    _record_solutions_from_file(str(res_path))


# ══════════════════════════════════════════════════════════════════════
# Range dispatcher
# ══════════════════════════════════════════════════════════════════════

def _wu_dispatcher(n_queue: Queue, x_limit: int, use_pari: bool,
                   out_dir: Path, n_threads: int):
    """Drain n_queue and spawn work items."""
    threads = []
    wu_counter = [0]

    def reap_dead():
        nonlocal threads
        threads = [t for t in threads if t.is_alive()]

    while True:
        reap_dead()
        if len(threads) >= n_threads:
            time.sleep(0.5)
            continue

        try:
            direction, n_start, n_end = n_queue.get(timeout=1)
        except Empty:
            continue

        wu_id = f"{direction}_{n_start}_{n_end}"
        wu_counter[0] += 1
        print(f"  [dispatch] WU #{wu_counter[0]:>5}  "
              f"n=[{n_start:>12},{n_end:>12}]  dir={direction}",
              flush=True)

        # C brute-force
        t = threading.Thread(
            target=_run_c_worker,
            args=(n_start, n_end, x_limit, wu_id, out_dir),
            daemon=True,
        )
        t.start()
        threads.append(t)

        # PARI exact (optional, separate thread)
        if use_pari:
            t2 = threading.Thread(
                target=_run_pari_worker,
                args=(n_start, n_end, wu_id, out_dir),
                daemon=True,
            )
            t2.start()
            threads.append(t2)


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="Real-time local search for integer solutions of "
                    "y² = x³ + 1296n²x² + 15552n³x + (46656n⁴ − 19n)")
    ap.add_argument("--workers",  type=int, default=os.cpu_count() or 4,
                    help="Number of parallel C workers (default: cpu_count)")
    ap.add_argument("--x_limit",  type=float, default=50_000_000,
                    help="C-worker |x| search bound (default 5e7)")
    ap.add_argument("--wu_size",  type=int, default=200,
                    help="Number of n values per work unit (default 200)")
    ap.add_argument("--pari",     action="store_true",
                    help="Also run PARI/GP exact workers in parallel")
    ap.add_argument("--out_dir",  default="output/local_wu",
                    help="Directory for WU/result files")
    args = ap.parse_args()

    x_limit = int(args.x_limit)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _master_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"╔══════════════════════════════════════════════════════════════╗")
    print(f"║   ec_curve LOCAL REAL-TIME SEARCH                           ║")
    print(f"║   y² = x³ + 1296n²x² + 15552n³x + (46656n⁴ − 19n)         ║")
    print(f"╠══════════════════════════════════════════════════════════════╣")
    print(f"║  workers={args.workers:<3}  x_limit={x_limit:<12,}  wu_size={args.wu_size:<6}   ║")
    print(f"║  pari_exact={args.pari}   master → {_master_path}  ║")
    print(f"╚══════════════════════════════════════════════════════════════╝")
    print(flush=True)

    # ── Load existing solutions from previous runs ─────────────────
    if _master_path.exists():
        with open(_master_path) as mf:
            for line in mf:
                parts = line.split()
                if len(parts) == 3:
                    try:
                        n, x, y = int(parts[0]), int(parts[1]), int(parts[2])
                        _solutions.add((n, x, abs(y)))
                    except ValueError:
                        pass
        print(f"  Loaded {len(_solutions)} existing solutions from master.")

    # ── Endless range generator ────────────────────────────────────
    n_queue: Queue = Queue(maxsize=500)

    def _fill_queue():
        pos, neg = 0, -1
        while True:
            if not n_queue.full():
                # positive
                n_queue.put(('pos', pos, pos + args.wu_size - 1))
                pos += args.wu_size
                # negative
                n_queue.put(('neg', neg - args.wu_size + 1, neg))
                neg -= args.wu_size
            else:
                time.sleep(0.5)

    filler = threading.Thread(target=_fill_queue, daemon=True)
    filler.start()

    # ── Status printer ─────────────────────────────────────────────
    def _status():
        t0 = time.time()
        while True:
            time.sleep(30)
            elapsed = time.time() - t0
            with _lock:
                total = _total_new
            print(f"  [status] elapsed={elapsed:.0f}s  "
                  f"solutions_found_this_run={total}", flush=True)

    threading.Thread(target=_status, daemon=True).start()

    # ── Dispatcher (blocks forever) ────────────────────────────────
    _wu_dispatcher(n_queue, x_limit, args.pari, out_dir, args.workers)


if __name__ == "__main__":
    main()
