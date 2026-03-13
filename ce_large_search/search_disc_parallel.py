#!/usr/bin/env python3
"""
search_disc_parallel.py
========================
Parallel search for integer (m, x, y) solutions to
    m·y² = 36x³ + 36m·x² + 12m²·x + m³ − 19
in the range m ∈ [M_LO, M_HI].

Method: for each x, compute discriminant D = 288x³+144x²+24x−151
and check whether it is a perfect square.  If sqrt(D) = s:
    m = (s − 12x − 1) / 4   (must be a positive integer)
Also checks t = 2..T_MAX via m·t·(2m+12x+t) = 36x³−19.

Runs worker_disc (C/GMP binary) in parallel across NUM_WORKERS subranges.
Solutions are written to solutions_disc.txt and pushed to GitHub.

Usage:
    python3 search_disc_parallel.py [--workers N] [--m_lo M] [--m_hi M]
                                    [--x_start X] [--t_max T]
"""

from __future__ import annotations
import os, sys, math, time, json, subprocess, threading, argparse
from pathlib import Path

HERE        = Path(__file__).parent
REPO        = HERE.parent
BINARY      = HERE / "worker_disc"
MASTER_FILE = REPO / "solutions_disc.txt"
CKPT_DIR    = HERE / "_disc_checkpoints"
LOG_DIR     = HERE / "_disc_logs"
WU_DIR      = HERE / "_disc_wu"

for d in (CKPT_DIR, LOG_DIR, WU_DIR, REPO):
    d.mkdir(parents=True, exist_ok=True)

MASTER_FILE.touch(exist_ok=True)

# ── defaults ─────────────────────────────────────────────────────────
DEFAULT_M_LO   = 10**20
DEFAULT_M_HI   = 10**27       # x stays in int64 range for m < ~1.18e29
DEFAULT_T_MAX  = 200
DEFAULT_WORKERS = os.cpu_count() or 4

# ── x bounds ─────────────────────────────────────────────────────────

def m_to_x(m: int) -> int:
    """Approximate x lower bound for given m (t=1 envelope)."""
    return max(1, int(math.ceil((m**2 / 18)**(1/3))) - 1)

# ── verifier (Python, independent check) ─────────────────────────────

def verify(m: int, x: int, y: int) -> bool:
    lhs = m * y * y
    rhs = 36*x**3 + 36*m*x**2 + 12*m**2*x + m**3 - 19
    return lhs == rhs

# ── result file lock ──────────────────────────────────────────────────

_master_lock = threading.Lock()

def append_solution(m: int, x: int, y: int):
    line = f"m={m}  x={x}  y={y}\n"
    with _master_lock:
        with open(MASTER_FILE, "a") as f:
            f.write(line)
    print(f"\n{'='*60}\n  *** SOLUTION FOUND ***\n  {line.strip()}\n{'='*60}\n",
          flush=True)

# ── GitHub push ───────────────────────────────────────────────────────

def git_push(n_new: int):
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        r = subprocess.run(["git","remote","get-url","origin"],
                           cwd=REPO, capture_output=True, text=True)
        url = r.stdout.strip()
        if url.startswith("https://") and "@" not in url:
            url = url.replace("https://", f"https://x-token:{token}@")
            subprocess.run(["git","remote","set-url","origin",url],
                           cwd=REPO, capture_output=True)
    msg = (f"[disc] +{n_new} solution(s) m in [1e20,1e27]  "
           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
    for cmd in [["git","add","-A"],
                ["git","commit","--allow-empty","-m",msg],
                ["git","push","origin","HEAD"]]:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout+r.stderr):
            print(f"[git] {r.stderr.strip()}", file=sys.stderr)
            return
    print(f"[git] pushed {n_new} new solution(s) to GitHub", flush=True)

# ── worker thread ─────────────────────────────────────────────────────

def worker_thread(wid: int, x_start: int, x_end: int,
                  m_lo: int, m_hi: int, t_max: int,
                  result_counts: list):
    wu_file   = WU_DIR   / f"wu_{wid:02d}.txt"
    res_file  = WU_DIR   / f"res_{wid:02d}.txt"
    ckpt_file = CKPT_DIR / f"ckpt_{wid:02d}.txt"
    log_file  = LOG_DIR  / f"log_{wid:02d}.txt"

    # Resume from checkpoint
    x_resume = x_start
    if ckpt_file.exists():
        try:
            x_resume = int(ckpt_file.read_text().strip()) + 1
            if x_resume > x_end:
                print(f"[w{wid}] already complete (ckpt={x_resume-1})", flush=True)
                return
            print(f"[w{wid}] resuming from x={x_resume:.4e}", flush=True)
        except Exception:
            pass

    wu_file.write_text(
        f"x_start {x_resume}\n"
        f"x_end   {x_end}\n"
        f"t_max   {t_max}\n"
        f"m_lo    {m_lo}\n"
        f"m_hi    {m_hi}\n"
    )
    res_file.unlink(missing_ok=True)

    cmd = [str(BINARY), str(wu_file), str(res_file), str(ckpt_file)]

    def log(msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [w{wid}] {msg}"
        print(line, flush=True)
        with open(log_file, "a") as f:
            f.write(line + "\n")

    log(f"start x=[{x_resume:.4e},{x_end:.4e}]  m=[{m_lo:.2e},{m_hi:.2e}]")

    with subprocess.Popen(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True) as proc:
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            log(line)
            if line.startswith("SOLUTION") or line.startswith("SOL"):
                # Parse "SOLUTION m=... x=... t=... y=..."
                # or   "SOL <m> <x> <y>"
                try:
                    parts = line.split()
                    if parts[0] == "SOL":
                        m, x, y = int(parts[1]), int(parts[2]), int(parts[3])
                    else:
                        vals = {}
                        for p in parts[1:]:
                            k, v = p.split("=")
                            vals[k] = int(v)
                        m, x, y = vals["m"], vals["x"], vals["y"]
                    if verify(m, x, y):
                        append_solution(m, x, y)
                        result_counts[wid] += 1
                    else:
                        log(f"VERIFY_FAIL m={m} x={x} y={y}")
                except Exception as e:
                    log(f"parse error: {e}  line={line!r}")

    log(f"done  solutions_this_worker={result_counts[wid]}")

# ── main ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--m_lo",    type=float, default=float(DEFAULT_M_LO))
    ap.add_argument("--m_hi",    type=float, default=float(DEFAULT_M_HI))
    ap.add_argument("--x_start", type=float, default=0.0,
                    help="Override x_start (default: derived from m_lo)")
    ap.add_argument("--t_max",   type=int, default=DEFAULT_T_MAX)
    args = ap.parse_args()

    if not BINARY.exists():
        print(f"ERROR: {BINARY} not found. Build first:")
        print(f"  gcc -O3 -march=native -std=c11 -o {BINARY} "
              f"{HERE/'worker_disc.c'} -lgmp -lm")
        sys.exit(1)

    m_lo = int(args.m_lo)
    m_hi = int(args.m_hi)
    t_max = args.t_max
    nw = args.workers

    x_start = int(args.x_start) if args.x_start > 0 else m_to_x(m_lo)
    x_end   = 9_200_000_000_000_000_000   # int64 max safe value (~9.2e18)
    # Cap to what actually yields m <= m_hi
    x_end_m = int((m_hi**2 / 18)**(1/3)) + 10
    x_end   = min(x_end, x_end_m)

    # Split x range among workers
    slab = (x_end - x_start + nw - 1) // nw
    slabs = []
    for i in range(nw):
        xs = x_start + i * slab
        xe = min(x_start + (i+1) * slab - 1, x_end)
        slabs.append((xs, xe))

    print("=" * 65)
    print(f" Discriminant scanner  m ∈ [{m_lo:.2e}, {m_hi:.2e}]")
    print(f" x ∈ [{x_start:.4e}, {x_end:.4e}]  t_max={t_max}")
    print(f" workers={nw}   slab_size={slab:.3e}")
    print(f" binary: {BINARY}")
    print(f" solutions → {MASTER_FILE}")
    print("=" * 65)
    print()

    result_counts = [0] * nw
    threads = []
    for i, (xs, xe) in enumerate(slabs):
        if xs > xe:
            continue
        t = threading.Thread(
            target=worker_thread,
            args=(i, xs, xe, m_lo, m_hi, t_max, result_counts),
            daemon=True,
        )
        t.start()
        threads.append(t)
        time.sleep(0.1)

    last_push  = time.monotonic()
    last_nsols = 0

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(60)
            nsols = sum(result_counts)
            elapsed_h = (time.monotonic() - last_push) / 3600
            # Progress summary
            print(f"[{time.strftime('%H:%M:%S')}] "
                  f"alive={sum(t.is_alive() for t in threads)}/{len(threads)}  "
                  f"solutions={nsols}",
                  flush=True)
            # Auto-push every 30min or when new solutions found
            if nsols > last_nsols or elapsed_h >= 0.5:
                if nsols > last_nsols:
                    git_push(nsols - last_nsols)
                elif nsols > 0:
                    git_push(0)
                last_nsols = nsols
                last_push  = time.monotonic()

    except KeyboardInterrupt:
        print("\n[main] interrupted — checkpoints saved by workers")

    total = sum(result_counts)
    print(f"\n[main] DONE  total solutions found: {total}")
    if total > 0:
        git_push(total)


if __name__ == "__main__":
    main()
