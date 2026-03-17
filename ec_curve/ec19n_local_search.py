#!/usr/bin/env python3
"""
ec19n_local_search.py  —  Local parallel search on this machine.

Spawns one worker process per (n, x-block) across all 4 valid n-values,
using all available CPU cores to search x ∈ [-X_MAX, +X_MAX].

Solutions with n ∈ {1,-1,19,-19} and k = y/(6n) ∈ ℤ are written to
output/solutions_ec19n.txt in real time.

Usage:
    python3 ec19n_local_search.py [--workers 8] [--x_max 1e15] [--x_block 1e12]
"""

import argparse, subprocess, threading, time, os, sys
from pathlib import Path
from queue import Queue

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
WORKER     = BASE_DIR / "ec19n_worker"
MASTER     = OUTPUT_DIR / "solutions_ec19n.txt"
CKPT_DIR   = OUTPUT_DIR / "checkpoints"
RESULT_DIR = OUTPUT_DIR / "results"

for d in [OUTPUT_DIR, CKPT_DIR, RESULT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

VALID_N = [1, -1, 19, -19]


def verify(n, x, y, k):
    if y != k * 6 * n: return False
    rhs = x**3 + 1296*n**2*x**2 + 15552*n**3*x + 46656*n**4 - 19*n
    return y * y == rhs


def load_master_seen() -> set:
    seen = set()
    if MASTER.exists():
        for line in MASTER.read_text().splitlines():
            parts = line.split()
            if len(parts) == 4:
                try: seen.add(tuple(int(v) for v in parts))
                except ValueError: pass
    return seen


class JobQueue:
    def __init__(self, x_max: int, x_block: int):
        self._q: Queue = Queue()
        for n in VALID_N:
            x = -x_max
            while x <= x_max:
                x_end = min(x + x_block - 1, x_max)
                self._q.put((n, x, x_end))
                x = x_end + 1

    def get(self, timeout=2):
        return self._q.get(timeout=timeout)

    def task_done(self):
        self._q.task_done()

    @property
    def qsize(self): return self._q.qsize()


def worker_thread(job_q: JobQueue, seen: set, seen_lock: threading.Lock,
                  master_f, total_found: list, stop: threading.Event):
    while not stop.is_set():
        try:
            n, x0, x1 = job_q.get(timeout=1)
        except Exception:
            continue

        wu    = RESULT_DIR / f"wu_n{n:+d}_{x0:+d}.txt"
        res   = RESULT_DIR / f"res_n{n:+d}_{x0:+d}.txt"
        ckpt  = CKPT_DIR   / f"ck_n{n:+d}_{x0:+d}.txt"
        wu.write_text(f"n {n}\nx_start {x0}\nx_end {x1}\n")
        res.unlink(missing_ok=True)

        try:
            r = subprocess.run(
                [str(WORKER), str(wu), str(res), str(ckpt)],
                capture_output=True, text=True,
                timeout=(x1 - x0 + 1) // 10**8 + 3600
            )
        except subprocess.TimeoutExpired:
            print(f"  [TIMEOUT] n={n} x=[{x0},{x1}]", flush=True)
            job_q.task_done()
            continue

        # Collect solutions
        if res.exists():
            for line in res.read_text().splitlines():
                parts = line.split()
                if len(parts) != 4: continue
                try:
                    sol = tuple(int(v) for v in parts)
                except ValueError:
                    continue
                nn, xx, yy, kk = sol
                if not verify(nn, xx, yy, kk):
                    print(f"  [WARN] verify fail: {sol}", flush=True)
                    continue
                with seen_lock:
                    if sol not in seen:
                        seen.add(sol)
                        master_f.write(f"{nn} {xx} {yy} {kk}\n")
                        master_f.flush()
                        total_found[0] += 1
                        print(
                            f"\n  ★★★ SOLUTION  n={nn}  x={xx}  y={yy}  k={kk}\n",
                            flush=True)

        wu.unlink(missing_ok=True)
        job_q.task_done()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--x_max",   type=float, default=1e15)
    ap.add_argument("--x_block", type=float, default=1e12)
    args = ap.parse_args()

    if not WORKER.exists():
        print(f"Worker binary not found: {WORKER}")
        print("Build it first:  gcc -O3 -march=native -std=c99 -o ec19n_worker ec19n_worker.c -lm")
        sys.exit(1)

    x_max   = int(args.x_max)
    x_block = int(args.x_block)

    print("=" * 66)
    print("  ec19n LOCAL PARALLEL SEARCH")
    print(f"  n ∈ {{1,-1,19,-19}}  |x| ≤ {x_max:.2e}  block={x_block:.2e}")
    print(f"  {args.workers} worker threads")
    print("=" * 66, flush=True)

    seen       = load_master_seen()
    seen_lock  = threading.Lock()
    total_found = [len(seen)]
    job_q      = JobQueue(x_max, x_block)
    stop       = threading.Event()

    print(f"  {job_q.qsize} jobs queued across 4 n-values", flush=True)

    master_f = MASTER.open("a", buffering=1)
    threads  = []
    for _ in range(args.workers):
        t = threading.Thread(target=worker_thread,
                             args=(job_q, seen, seen_lock,
                                   master_f, total_found, stop),
                             daemon=True)
        t.start()
        threads.append(t)

    t0 = time.time()
    try:
        while True:
            remaining = job_q.qsize
            elapsed   = time.time() - t0
            print(f"  [{time.strftime('%H:%M:%S')}]  jobs_remaining={remaining}"
                  f"  solutions_found={total_found[0]}  elapsed={elapsed:.0f}s",
                  flush=True)
            if remaining == 0:
                time.sleep(5)
                if job_q.qsize == 0:
                    break
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[interrupted]", flush=True)
    finally:
        stop.set()
        master_f.close()
        print(f"\nDone. Total solutions with y/(6n) ∈ ℤ: {total_found[0]}", flush=True)


if __name__ == "__main__":
    main()
