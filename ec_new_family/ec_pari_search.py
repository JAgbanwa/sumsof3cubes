#!/usr/bin/env python3
"""
ec_pari_search.py  —  Provably-complete integral-point search via PARI/GP

For each n in [n_lo, n_hi], spawns a `gp` process that runs
ellintegralpoints() on E_n — certifiably finding ALL integral points.

Equation:
    y² = X³ + a4(n)·X + a6(n)
    a4(n) = -45349632·n⁴ + 419904·n³
    a6(n) = 3·(39182082048·n⁶ − 544195584·n⁵ + 1259712·n⁴ − 19·n)
    excluding  X = −3888·n²

Usage:
    python3 ec_pari_search.py --n-lo 1 --n-hi 500 --workers 4
    python3 ec_pari_search.py --n-lo -500 --n-hi -1 --workers 4
"""
import argparse, subprocess, sys, os, time, threading, tempfile
from pathlib import Path
from queue import Queue, Empty

REPO = Path(__file__).parent
OUT  = REPO / "output" / "solutions_pari.txt"
GP   = os.environ.get("GP_BIN", "gp")


def a4(n: int) -> int:
    return -45349632 * n**4 + 419904 * n**3

def a6(n: int) -> int:
    return 3 * (39182082048 * n**6 - 544195584 * n**5
                + 1259712 * n**4 - 19 * n)

def verify(n: int, X: int, y: int) -> bool:
    rhs = X**3 + a4(n) * X + a6(n)
    return y * y == rhs

# --------------------------------------------------------------------------
# Build a one-shot .gp file for a single n
# --------------------------------------------------------------------------
def make_gp_file(n: int, path: str):
    excl = -3888 * n * n
    script = f"""\
default(parisize, 384*1024*1024);
{{
  my(n={n});
  my(a4={a4(n)}, a6={a6(n)});
  my(E=ellinit([0,0,0,a4,a6]));
  if(E.disc==0, print("SINGULAR_{n}"); quit);
  my(pts=ellintegralpoints(E));
  my(excl={excl});
  if(#pts==0,
    print("NONE_{n}"),
    for(i=1,#pts,
      my(X=pts[i][1], y=pts[i][2]);
      if(X!=excl,
        print("PT_{n}_",X,"_",y)
      )
    )
  );
}}
quit
"""
    with open(path, "w") as fh:
        fh.write(script)

# --------------------------------------------------------------------------
# Run gp on one n; return list of (n, X, y) or None on timeout
# --------------------------------------------------------------------------
def search_one(n: int, timeout: int) -> list | None:
    with tempfile.NamedTemporaryFile(suffix=".gp", delete=False, mode="w") as tf:
        tf_path = tf.name
    try:
        make_gp_file(n, tf_path)
        r = subprocess.run(
            [GP, "-q", "--stacksize", "384m", tf_path],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout
        )
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        print(f"ERROR: gp not found at '{GP}'", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            os.unlink(tf_path)
        except Exception:
            pass

    results = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("PT_"):
            continue
        parts = line[3:].split("_")
        if len(parts) == 3:
            try:
                rn, rX, ry = int(parts[0]), int(parts[1]), int(parts[2])
                if verify(rn, rX, ry):
                    results.append((rn, rX, ry))
                else:
                    print(f"  [VERIFY-FAIL] n={rn} X={rX} y={ry}", file=sys.stderr)
            except ValueError:
                pass
    return results

# --------------------------------------------------------------------------
# Threaded parallel pool
# --------------------------------------------------------------------------
def run_parallel(n_values: list, workers: int, timeout: int, out_path: Path):
    q: Queue = Queue()
    for n in n_values:
        q.put(n)

    results_all = []
    timed_out   = []
    done_count  = [0]
    lock        = threading.Lock()
    total       = len(n_values)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a") as fh:
        fh.write(f"# PARI/GP complete search — started "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write("# n  X  y\n")

    def worker_fn():
        while True:
            try:
                n = q.get(timeout=2)
            except Empty:
                return
            pts = search_one(n, timeout)
            with lock:
                done_count[0] += 1
                if pts is None:
                    timed_out.append(n)
                    print(f"  [TIMEOUT n={n}]", flush=True)
                else:
                    for (rn, rX, ry) in pts:
                        results_all.append((rn, rX, ry))
                        with open(out_path, "a") as fh:
                            fh.write(f"{rn}  {rX}  {ry}\n")
                        print(f"  *** SOLUTION  n={rn:<8}  X={rX:<18}  y={ry}",
                              flush=True)
            q.task_done()

    threads = [threading.Thread(target=worker_fn, daemon=True)
               for _ in range(workers)]
    for t in threads:
        t.start()

    t0 = time.time()
    last_report = t0
    while any(t.is_alive() for t in threads):
        time.sleep(5)
        now = time.time()
        if now - last_report >= 30:
            with lock:
                done  = done_count[0]
                found = len(results_all)
            elapsed = now - t0
            rate    = done / elapsed if elapsed else 0
            eta     = (total - done) / rate / 60 if rate else 999
            print(f"  [progress] {done}/{total}  solutions={found}"
                  f"  {rate:.2f} n/s  ETA {eta:.0f} min", flush=True)
            last_report = now

    q.join()

    with open(out_path, "a") as fh:
        elapsed = time.time() - t0
        fh.write(f"# elapsed {elapsed/60:.1f} min  timed_out={timed_out}\n")
        fh.write(f"# TOTAL SOLUTIONS: {len(results_all)}\n")

    return results_all, timed_out

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Provably-complete search on E_n")
    ap.add_argument("--n-lo",    type=int, default=1)
    ap.add_argument("--n-hi",    type=int, default=200)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600,
                    help="seconds per n before giving up (default 600)")
    ap.add_argument("--out",     type=Path, default=OUT)
    args = ap.parse_args()

    n_values = [n for n in range(args.n_lo, args.n_hi + 1) if n != 0]
    print("=== ec_new_family — PARI/GP provably-complete search ===")
    print(f"  Equation : y² = X³ + a4(n)X + a6(n),  X ≠ -3888·n²")
    print(f"  n range  : [{args.n_lo}, {args.n_hi}]  ({len(n_values)} curves)")
    print(f"  workers  : {args.workers}")
    print(f"  timeout  : {args.timeout}s per curve")
    print(f"  output   : {args.out}")
    print()

    t0 = time.time()
    solutions, timed_out = run_parallel(n_values, args.workers, args.timeout, args.out)
    elapsed = time.time() - t0

    print()
    print("=== COMPLETE ===")
    print(f"  Elapsed    : {elapsed/60:.1f} min")
    print(f"  Timed-out  : {len(timed_out)}  {timed_out[:20]}")
    print(f"  Solutions  : {len(solutions)}")
    if solutions:
        print()
        print(f"  {'n':>8}  {'X':>20}  {'y':>22}")
        print("  " + "-" * 54)
        for (n, X, y) in sorted(solutions, key=lambda t: (abs(t[0]), t[0], t[1])):
            print(f"  {n:>8}  {X:>20}  {y:>22}")

if __name__ == "__main__":
    main()
