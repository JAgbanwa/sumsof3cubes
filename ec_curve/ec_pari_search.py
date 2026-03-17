#!/usr/bin/env python3
"""
ec_pari_search.py  —  Provably-complete integer-point search via PARI/GP

For each n in [n_lo, n_hi], writes a temp .gp file and runs gp on it.
ellintegralpoints() guarantees ALL integer points are found (Baker bound
+ MW saturation). No x upper bound — provably complete.

Usage:
    python3 ec_pari_search.py --n-lo -2000 --n-hi 2000 --workers 4
"""
import argparse, subprocess, sys, os, time, threading, tempfile
from pathlib import Path
from queue import Queue, Empty

REPO = Path(__file__).parent
OUT  = REPO / "output" / "solutions_pari_complete.txt"
GP   = os.environ.get("GP_BIN", "gp")

KNOWN = [
    (-1216, 3648,    159695000),
    (-361,   -19,    28396260),
    (-304, -1824,    39923636),
    (-304,  1824,       77900),
    ( 128, -2295,     7035557),
    ( 361,    19,    28396260),
    (1117,-109117, 4118154242),
]

def f(n, x):
    return x**3 + 1296*n**2*x**2 + 15552*n**3*x + 46656*n**4 - 19*n

def verify(n, x, y):
    return y*y == f(n, x)

# --------------------------------------------------------------------------
# Build a .gp script file for a single n value
# --------------------------------------------------------------------------
def make_gp_file(n: int, path: str):
    script = f"""\
default(parisize, 384*1024*1024);
{{
  my(n={n});
  my(a2=1296*n^2, a4=15552*n^3, a6=46656*n^4-19*n);
  my(E=ellinit([0,a2,0,a4,a6]));
  my(d=elldisc(E));
  if(d==0, print("SINGULAR_",{n}); quit);
  my(mw=ellgenerators(E));
  my(pts=ellintegralpoints(E,mw,1));
  if(#pts==0,
    print("NONE_",{n}),
    for(i=1,#pts,
      print("PT_",{n},"_",pts[i][1],"_",pts[i][2])
    )
  );
}}
quit
"""
    with open(path, "w") as fh:
        fh.write(script)

# --------------------------------------------------------------------------
# Run gp on one n, return list of (n, x, y)
# --------------------------------------------------------------------------
def search_one(n: int, timeout: int) -> list | None:
    with tempfile.NamedTemporaryFile(suffix=".gp", delete=False, mode="w") as tf:
        tf_path = tf.name
    try:
        make_gp_file(n, tf_path)
        r = subprocess.run(
            [GP, "-q", "--stacksize", "384m", tf_path],
            capture_output=True, text=True, timeout=timeout
        )
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        print(f"ERROR: gp not found at '{GP}'", file=sys.stderr); sys.exit(1)
    finally:
        try: os.unlink(tf_path)
        except: pass

    results = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("PT_"):
            # format: PT_<n>_<x>_<y>
            parts = line[3:].split("_")
            if len(parts) == 3:
                try:
                    rn, rx, ry = int(parts[0]), int(parts[1]), int(parts[2])
                    if verify(rn, rx, ry):
                        results.append((rn, rx, ry))
                    else:
                        print(f"  [VERIFY-FAIL] n={rn} x={rx} y={ry}", file=sys.stderr)
                except ValueError:
                    pass
    return results

# --------------------------------------------------------------------------
# Parallel pool
# --------------------------------------------------------------------------
def run_parallel(n_values, workers, timeout, out_path):
    q: Queue = Queue()
    for n in n_values:
        q.put(n)

    results_all  = []
    timed_out    = []
    done_count   = [0]
    lock         = threading.Lock()
    total        = len(n_values)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a") as fh:
        fh.write(f"# PARI/GP complete search — started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write("# n  x  y\n")

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
                    for (rn, rx, ry) in pts:
                        results_all.append((rn, rx, ry))
                        line = f"{rn}  {rx}  {ry}\n"
                        with open(out_path, "a") as fh:
                            fh.write(line)
                        print(f"  *** SOLUTION  n={rn:<8}  x={rx:<16}  y={ry}", flush=True)
            q.task_done()

    threads = [threading.Thread(target=worker_fn, daemon=True) for _ in range(workers)]
    for t in threads: t.start()

    t0 = time.time()
    last_report = t0
    while any(t.is_alive() for t in threads):
        time.sleep(5)
        now = time.time()
        if now - last_report >= 30:
            with lock:
                done = done_count[0]
                found = len(results_all)
            elapsed = now - t0
            rate = done / elapsed if elapsed else 0
            eta  = (total - done) / rate / 60 if rate else 999
            print(f"  [progress] {done}/{total}  |  solutions={found}"
                  f"  |  {rate:.2f} n/s  |  ETA {eta:.0f} min", flush=True)
            last_report = now

    q.join()

    with open(out_path, "a") as fh:
        elapsed = time.time() - t0
        fh.write(f"# elapsed {elapsed/60:.1f} min  |  timed_out={timed_out}\n")
        fh.write(f"# TOTAL SOLUTIONS: {len(results_all)}\n")

    return results_all, timed_out

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-lo",    type=int, default=-2000)
    ap.add_argument("--n-hi",    type=int, default=2000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600,
                    help="seconds per n before giving up")
    ap.add_argument("--out",     type=Path, default=OUT)
    args = ap.parse_args()

    # verify known solutions
    print("=== Verifying known solutions ===")
    for (n, x, y) in KNOWN:
        ok = verify(n, x, y)
        print(f"  n={n:<7}  x={x:<9}  y={y:<13}  {'OK' if ok else '*** FAIL ***'}")
    print()

    n_values = [n for n in range(args.n_lo, args.n_hi + 1) if n != 0]
    print(f"=== PARI/GP complete search ===")
    print(f"  n ∈ [{args.n_lo}, {args.n_hi}]  ({len(n_values)} curves)")
    print(f"  workers={args.workers}  timeout={args.timeout}s/curve")
    print(f"  output → {args.out}")
    print()

    t0 = time.time()
    solutions, timed_out = run_parallel(n_values, args.workers, args.timeout, args.out)
    elapsed = time.time() - t0

    print()
    print("=== COMPLETE ===")
    print(f"  Elapsed : {elapsed/60:.1f} min")
    print(f"  Timed-out ({len(timed_out)}): {timed_out[:20]}")
    print(f"  Solutions: {len(solutions)}")
    if solutions:
        print()
        print("  ALL SOLUTIONS FOUND:")
        hdr = f"  {'n':>8}  {'x':>16}  {'y':>18}"
        print(hdr)
        print("  " + "-"*46)
        for (n, x, y) in sorted(solutions, key=lambda t:(abs(t[0]), t[0], t[1])):
            print(f"  {n:>8}  {x:>16}  {y:>18}")

if __name__ == "__main__":
    main()
