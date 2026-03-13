#!/usr/bin/env python3
"""
local_parallel_search.py
─────────────────────────────────────────────────────────────────────
Standalone multi-process local search — no BOINC installation needed.

Runs N worker processes in parallel.  Each process handles a different
block of m values starting at ±10^20, expanding outward forever.

Usage:
    python3 local_parallel_search.py [--workers N] [--floor M] [--block B]
    python3 local_parallel_search.py --workers 8 --floor 1e20 --block 10

Output:
    solutions_large.txt   — master solutions file (atomic appends)
    logs/worker_*.log     — per-worker logs
    ce_checkpoint.json    — global checkpoint / frontier state

All solutions are automatically committed + pushed to GitHub if
$GITHUB_TOKEN and $GITHUB_REMOTE are set in the environment.
─────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import os
import sys
import json
import time
import argparse
import subprocess
import threading
import multiprocessing as mp
from pathlib import Path
from typing import Optional

HERE         = Path(__file__).parent
REPO_DIR     = HERE.parent
MASTER_FILE  = REPO_DIR / "solutions_large.txt"
CKPT_FILE    = HERE / "ce_checkpoint.json"
LOG_DIR      = HERE / "logs"
WORKER_SCRIPT = HERE / "worker_pari_large.py"
WU_TMPDIR    = HERE / "_local_wu_tmp"

LOG_DIR.mkdir(exist_ok=True)
WU_TMPDIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────
# Global frontier (shared across workers via a managed dict)
# ─────────────────────────────────────────────────────────────────────

_manager: Optional[mp.Manager] = None
_frontiers: Optional[dict]     = None
_frontiers_lock: Optional[mp.Lock] = None


def load_checkpoint() -> dict:
    try:
        return json.loads(CKPT_FILE.read_text())
    except Exception:
        return {}


def save_checkpoint(data: dict):
    tmp = CKPT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(CKPT_FILE)

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
# Append to master solutions file (thread-safe via file lock)
# ─────────────────────────────────────────────────────────────────────

_file_lock = threading.Lock()


def append_solutions(new_solutions: list[tuple[int, int, int]]):
    if not new_solutions:
        return
    lines = []
    for m, X, Y in new_solutions:
        # also compute user (x,y) if available
        user = ""
        if m != 0:
            dx, dy = 6 * m, 6 * m * m
            if X % dx == 0 and Y % dy == 0:
                x_, y_ = X // dx, Y // dy
                user = f"  # user: x={x_} y={y_}"
        lines.append(f"{m} {X} {Y}{user}\n")
    with _file_lock:
        with open(MASTER_FILE, "a") as fh:
            fh.writelines(lines)

# ─────────────────────────────────────────────────────────────────────
# Worker process function
# ─────────────────────────────────────────────────────────────────────

def worker_process(
    wid: int,
    frontiers: dict,      # shared: {"pos": int, "neg": int}
    lock: mp.Lock,        # protects frontiers
    block_size: int,
    timeout_per_m: int,
    gp_stack_mb: int,
    result_queue: mp.Queue,
):
    """
    Worker loop:
      1. Claim the next unused block of m values (pos or neg side).
      2. Run worker_pari_large.py on that block in a subprocess.
      3. Parse solutions and put them on result_queue.
      4. Repeat indefinitely.
    """
    logfile = LOG_DIR / f"worker_{wid:02d}.log"
    side    = "pos" if wid % 2 == 0 else "neg"

    def log(msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [w{wid}] {msg}"
        print(line, flush=True)
        with open(logfile, "a") as fh:
            fh.write(line + "\n")

    log(f"started — side={side} block_size={block_size}")

    while True:
        # ── Claim a block ────────────────────────────────────────────
        with lock:
            if side == "pos":
                m_start = frontiers["pos"] + 1
                m_end   = m_start + block_size - 1
                frontiers["pos"] = m_end
            else:
                m_end   = frontiers["neg"] - 1
                m_start = m_end - block_size + 1
                frontiers["neg"] = m_start

        log(f"claimed m=[{m_start}, {m_end}]")

        # ── Write wu.txt ──────────────────────────────────────────────
        wu_file   = WU_TMPDIR / f"wu_w{wid}.txt"
        res_file  = WU_TMPDIR / f"res_w{wid}.txt"
        ckpt_file = WU_TMPDIR / f"ckpt_w{wid}.json"

        wu_file.write_text(
            f"m_start {m_start}\n"
            f"m_end   {m_end}\n"
            f"timeout_per_m {timeout_per_m}\n"
            f"gp_stack_mb   {gp_stack_mb}\n"
        )
        res_file.unlink(missing_ok=True)

        # ── Run worker subprocess ─────────────────────────────────────
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, str(WORKER_SCRIPT),
                 str(wu_file), str(res_file), str(ckpt_file)],
                capture_output=False,
                timeout=(block_size * timeout_per_m * 2),
            )
        except subprocess.TimeoutExpired:
            log(f"TIMEOUT on m=[{m_start},{m_end}]")

        elapsed = time.monotonic() - t0

        # ── Parse results ─────────────────────────────────────────────
        new_sols: list[tuple[int, int, int]] = []
        if res_file.exists():
            for line in res_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("SOL "):
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            m_, X, Y = int(parts[1]), int(parts[2]), int(parts[3])
                            if verify_weierstrass(m_, X, Y):
                                new_sols.append((m_, X, Y))
                        except ValueError:
                            pass

        log(f"m=[{m_start},{m_end}]  {len(new_sols)} solution(s)  {elapsed:.0f}s")

        if new_sols:
            result_queue.put(new_sols)

        # ── Save frontier checkpoint ──────────────────────────────────
        with lock:
            ckpt_data = {
                "pos_frontier": frontiers["pos"],
                "neg_frontier": frontiers["neg"],
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        save_checkpoint(ckpt_data)

# ─────────────────────────────────────────────────────────────────────
# Result collector + GitHub push
# ─────────────────────────────────────────────────────────────────────

def collector_thread(result_queue: mp.Queue, push_interval: int):
    """Collect solutions from workers, write to master file, push to GitHub."""
    repo_dir    = REPO_DIR
    token       = os.environ.get("GITHUB_TOKEN", "")
    last_push   = time.monotonic()
    total_found = 0

    while True:
        try:
            sols = result_queue.get(timeout=2)
            append_solutions(sols)
            total_found += len(sols)
            for s in sols:
                print(f"  ★ NEW SOLUTION m={s[0]} X={s[1]} Y={s[2]}", flush=True)

            # Push to GitHub if interval elapsed
            if time.monotonic() - last_push >= push_interval and total_found > 0:
                _push_to_github(repo_dir, total_found, token)
                last_push = time.monotonic()
        except Exception:
            pass

def _push_to_github(repo_dir: Path, n: int, token: str):
    env = os.environ.copy()
    if token:
        try:
            r = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=repo_dir, capture_output=True, text=True
            )
            origin = r.stdout.strip()
            if origin.startswith("https://") and "@" not in origin:
                new_origin = origin.replace(
                    "https://", f"https://x-token:{token}@")
                subprocess.run(
                    ["git", "remote", "set-url", "origin", new_origin],
                    cwd=repo_dir, env=env, capture_output=True
                )
        except Exception:
            pass

    msg = (f"[CE auto] +{n} solution(s) |m|≥10^20  "
           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
    for cmd in [
        ["git", "add", "-A"],
        ["git", "commit", "--allow-empty", "-m", msg],
        ["git", "push", "origin", "HEAD"],
    ]:
        r = subprocess.run(cmd, cwd=repo_dir, capture_output=True,
                           text=True, env=env)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            print(f"[git] {r.stderr.strip()}", file=sys.stderr)
            break
    else:
        print(f"[git] pushed {n} solutions to GitHub", flush=True)

# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Parallel local search for integral points |m|≥10^20")
    ap.add_argument("--workers",   type=int,   default=4)
    ap.add_argument("--floor",     type=float, default=1e20,
        help="Minimum |m| to search (default 1e20)")
    ap.add_argument("--block",     type=int,   default=10,
        help="m values per worker block (default 10)")
    ap.add_argument("--timeout_m", type=int,   default=300,
        help="Seconds allowed per m value (default 300)")
    ap.add_argument("--stack_mb",  type=int,   default=512)
    ap.add_argument("--push_interval", type=int, default=300,
        help="Seconds between GitHub pushes (default 300)")
    args = ap.parse_args()

    floor  = int(args.floor)
    ckpt   = load_checkpoint()

    # ── Shared frontier ───────────────────────────────────────────────
    manager = mp.Manager()
    frontiers = manager.dict({
        "pos": ckpt.get("pos_frontier", floor - 1),
        "neg": ckpt.get("neg_frontier", -(floor - 1)),
    })
    lock = manager.Lock()

    print("═══════════════════════════════════════════════════════════")
    print(f" Local parallel search  |m| ≥ {floor:.2e}")
    print(f" Workers: {args.workers}   Block: {args.block}   "
          f"TimeoutPerM: {args.timeout_m}s")
    print(f" Solutions → {MASTER_FILE}")
    print("═══════════════════════════════════════════════════════════")

    # ── Result queue + collector ─────────────────────────────────────
    result_q = mp.Queue()
    ct = threading.Thread(
        target=collector_thread,
        args=(result_q, args.push_interval),
        daemon=True,
    )
    ct.start()

    # ── Worker processes ──────────────────────────────────────────────
    procs = []
    for wid in range(args.workers):
        p = mp.Process(
            target=worker_process,
            args=(wid, frontiers, lock,
                  args.block, args.timeout_m, args.stack_mb, result_q),
            daemon=True,
        )
        p.start()
        procs.append(p)
        print(f"  worker {wid} PID={p.pid}")
        time.sleep(0.3)

    print("\n[main] All workers running. Ctrl+C to stop.\n")

    try:
        while True:
            alive = sum(p.is_alive() for p in procs)
            n_sol = sum(1 for l in MASTER_FILE.read_text().splitlines()
                        if l.strip() and not l.startswith("#"))
            print(f"[{time.strftime('%H:%M:%S')}]  "
                  f"alive={alive}/{args.workers}  "
                  f"solutions={n_sol}  "
                  f"pos_frontier={frontiers['pos']}  "
                  f"neg_frontier={frontiers['neg']}",
                  flush=True)
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[main] Stopping…")
        for p in procs:
            p.terminate()
        save_checkpoint({
            "pos_frontier": frontiers["pos"],
            "neg_frontier": frontiers["neg"],
        })
        print("[main] Checkpoint saved.")


if __name__ == "__main__":
    main()
