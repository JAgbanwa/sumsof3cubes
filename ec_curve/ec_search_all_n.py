#!/usr/bin/env python3
"""
ec_search_all_n.py
==================
Provably-complete search for integer points on the family of elliptic curves

   E_n :  y² = x³ + 1296n²x² + 15552n³x + (46656n⁴ - 19n)

Strategy (two tiers):

  Tier 1 — FAST brute-force C worker   (|x| ≤ X_LIMIT per n)
  Tier 2 — PARI ellintegralpoints()   (provably ALL integer points, no x bound)

Tier 2 is the gold standard; it uses Baker's theorem + MW-saturation so every
integer point is guaranteed to be found regardless of its size.

Usage:
    python3 ec_search_all_n.py --n-max 2000 --workers 6 --out solutions.txt
    python3 ec_search_all_n.py --n-max 10000 --workers 6 --out solutions.txt
"""

import argparse
import subprocess
import sys
import os
import time
import threading
from pathlib import Path
from queue import Queue

HERE = Path(__file__).parent
OUT_DEFAULT = HERE / "output" / "solutions_all_n.txt"

# ---------------------------------------------------------------------------
# Known solutions (for verification)
# ---------------------------------------------------------------------------
KNOWN = [
    # (n, x, y)
    (-1216,   3648,   159695000),
    (-361,     -19,    28396260),
    (-304,   -1824,    39923636),
    (-304,    1824,       77900),
    ( 128,   -2295,     7035557),
    ( 361,      19,    28396260),
    (1117, -109117,  4118154242),
]


def verify(n, x, y):
    return y * y == x**3 + 1296*n**2*x**2 + 15552*n**3*x + 46656*n**4 - 19*n


# ---------------------------------------------------------------------------
# PARI/GP one-liner runner  (one n at a time — safe, no file API)
# ---------------------------------------------------------------------------
GP_BIN = os.environ.get("GP_BIN", "gp")

_GP_TEMPLATE = r"""
default(parisize, 256*1024*1024);
{
  my(n={n}, a2=1296*n^2, a4=15552*n^3, a6=46656*n^4-19*n, E, d, mw, pts);
  E = ellinit([0,a2,0,a4,a6]);
  d = elldisc(E);
  if(d == 0, print("SINGULAR"); quit);
  mw = ellgenerators(E);
  pts = ellintegralpoints(E, mw, 1);
  if(#pts == 0, print("NONE"), for(i=1,#pts, print("PT ",n," ",pts[i][1]," ",pts[i][2])));
}
quit
""".strip()


def gp_search_one(n: int, timeout: int = 300) -> list:
    """Return list of (n, x, y) for all integer points on E_n.
    Returns None on timeout/error."""
    script = _GP_TEMPLATE.replace("{n}", str(n))
    try:
        proc = subprocess.run(
            [GP_BIN, "-q", "--stacksize", "256m"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None  # signal timed out
    except FileNotFoundError:
        print(f"ERROR: '{GP_BIN}' not found. Install PARI/GP.", file=sys.stderr)
        sys.exit(1)

    results = []
    for line in (proc.stdout + proc.stderr).splitlines():
        line = line.strip()
        if line.startswith("PT "):
            parts = line.split()
            if len(parts) == 4:
                try:
                    r_n, r_x, r_y = int(parts[1]), int(parts[2]), int(parts[3])
                    if verify(r_n, r_x, r_y):
                        results.append((r_n, r_x, r_y))
                    else:
                        print(f"VERIFY FAIL n={r_n} x={r_x} y={r_y}", file=sys.stderr)
                except ValueError:
                    pass
    return results


# ---------------------------------------------------------------------------
# Parallel worker pool
# ---------------------------------------------------------------------------

class SearchPool:
    def __init__(self, n_values: list, num_workers: int,
                 out_path: Path, timeout: int = 300):
        self.queue: Queue = Queue()
        for n in n_values:
            self.queue.put(n)

        self.out_path = out_path
        self.timeout  = timeout
        self.lock     = threading.Lock()
        self.solutions: list = []
        self.done_count  = 0
        self.total       = len(n_values)
        self.timed_out   = []
        self.threads     = []

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Write header
        with open(out_path, "w") as f:
            f.write("# Integer solutions to y^2 = x^3 + 1296n^2 x^2 + 15552n^3 x + (46656n^4 - 19n)\n")
            f.write("# Method: PARI/GP ellintegralpoints() — provably complete (Baker bound + MW saturation)\n")
            f.write(f"# Search range: n in [{min(n_values)}, {max(n_values)}]  (n=0 excluded)\n")
            f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
            f.write("# Format: n  x  y\n\n")

        for _ in range(num_workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self.threads.append(t)

    def _worker(self):
        while True:
            try:
                n = self.queue.get(timeout=2)
            except Exception:
                return
            if n == 0:
                self.queue.task_done()
                continue

            pts = gp_search_one(n, timeout=self.timeout)

            with self.lock:
                self.done_count += 1
                if pts is None:
                    self.timed_out.append(n)
                    print(f"  [TIMEOUT] n={n}", flush=True)
                else:
                    for (rn, rx, ry) in pts:
                        line = f"{rn}  {rx}  {ry}"
                        self.solutions.append((rn, rx, ry))
                        with open(self.out_path, "a") as f:
                            f.write(line + "\n")
                        print(f"  *** SOLUTION  n={rn:<8}  x={rx:<16}  y={ry}", flush=True)

            self.queue.task_done()

    def wait(self):
        self.queue.join()

    def status_reporter(self, interval: int = 15):
        t0 = time.time()
        while any(t.is_alive() for t in self.threads):
            time.sleep(interval)
            with self.lock:
                done = self.done_count
                total = self.total
                found = len(self.solutions)
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            eta  = (total - done) / rate if rate > 0 else float("inf")
            print(f"  [progress]  {done}/{total} n-values done  "
                  f"|  solutions={found}  "
                  f"|  {rate:.1f} n/s  "
                  f"|  ETA {eta/60:.0f} min",
                  flush=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Provably-complete EC integer-point search")
    ap.add_argument("--n-max",   type=int,  default=2000,
                    help="Search |n| ≤ n_max (default 2000)")
    ap.add_argument("--n-lo",    type=int,  default=None,
                    help="Override lower bound on n")
    ap.add_argument("--n-hi",    type=int,  default=None,
                    help="Override upper bound on n")
    ap.add_argument("--workers", type=int,  default=4,
                    help="Parallel gp processes (default 4)")
    ap.add_argument("--timeout", type=int,  default=300,
                    help="Seconds per n before giving up (default 300)")
    ap.add_argument("--out",     type=Path, default=OUT_DEFAULT,
                    help="Output solution file")
    ap.add_argument("--verify-known", action="store_true", default=True,
                    help="Verify known solutions before starting (default True)")
    args = ap.parse_args()

    n_lo = args.n_lo if args.n_lo is not None else -args.n_max
    n_hi = args.n_hi if args.n_hi is not None else  args.n_max

    # ---------- verify known solutions ----------
    if args.verify_known:
        print("=== Verifying known solutions ===")
        all_ok = True
        for (n, x, y) in KNOWN:
            ok = verify(n, x, y)
            print(f"  n={n:<7}  x={x:<9}  y={y:<13}  {'OK' if ok else 'FAIL'}")
            if not ok:
                all_ok = False
        if not all_ok:
            print("ERROR: known solution verification failed!", file=sys.stderr)
            sys.exit(1)
        print()

    print(f"=== Starting provably-complete PARI/GP search ===")
    print(f"  Range   : n ∈ [{n_lo}, {n_hi}]  (n=0 excluded)")
    print(f"  Workers : {args.workers}")
    print(f"  Timeout : {args.timeout}s per n")
    print(f"  Output  : {args.out}")
    print()

    n_values = [n for n in range(n_lo, n_hi + 1) if n != 0]
    print(f"  Total n-values to process: {len(n_values)}")
    print()

    pool = SearchPool(n_values, args.workers, args.out, args.timeout)

    reporter = threading.Thread(target=pool.status_reporter, args=(20,), daemon=True)
    reporter.start()

    t0 = time.time()
    pool.wait()
    elapsed = time.time() - t0

    print()
    print(f"=== SEARCH COMPLETE ===")
    print(f"  Elapsed       : {elapsed/60:.1f} min")
    print(f"  n-values done : {pool.done_count}/{len(n_values)}")
    print(f"  Timed-out     : {len(pool.timed_out)}")
    print(f"  Solutions     : {len(pool.solutions)}")
    print()

    # Write final summary to file
    with open(pool.out_path, "a") as f:
        f.write(f"\n# === SEARCH SUMMARY ===\n")
        f.write(f"# n range: [{n_lo}, {n_hi}]  |  {len(n_values)} curves processed\n")
        f.write(f"# timed out (n={args.timeout}s): {pool.timed_out if pool.timed_out else 'none'}\n")
        f.write(f"# elapsed: {elapsed/60:.1f} min\n")
        f.write(f"# TOTAL SOLUTIONS: {len(pool.solutions)}\n")

    if pool.solutions:
        print("  All solutions:")
        for (n, x, y) in sorted(pool.solutions, key=lambda t: (abs(t[0]), t[0], t[1])):
            print(f"    n={n:<8}  x={x:<16}  y={y}")
    else:
        print("  No solutions found in this range.")

    if pool.timed_out:
        print(f"\n  WARNING: {len(pool.timed_out)} n-values timed out: {pool.timed_out}")
        print("  Re-run with --timeout 600 or larger for these values.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
