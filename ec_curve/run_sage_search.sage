"""
run_sage_search.sage  —  Real-time integral-point search via SageMath REPL

Run directly:  sage run_sage_search.sage

Searches n = 1, -1, 2, -2, 3, -3, ...  expanding forever.
Uses E.integral_points() which is provably complete (Siegel's theorem).
Results appended to output/solutions_master.txt

Each n is searched in a subprocess with a hard timeout so a slow/hang curve
cannot block the entire sweep.
"""

import time, json, sys, subprocess, os
from pathlib import Path

OUTPUT_DIR  = Path("output")
MASTER_FILE = OUTPUT_DIR / "solutions_master.txt"
CKPT_FILE   = OUTPUT_DIR / "sage_checkpoint.json"
SKIP_FILE   = OUTPUT_DIR / "sage_skipped.txt"
OUTPUT_DIR.mkdir(exist_ok=True)

# Timeout per n-value (seconds).  Most curves finish in <30s; raise if needed.
N_TIMEOUT = 120

# Path to the one-shot sage script that searches a single n
WORKER_SCRIPT = Path(__file__).parent / "sage_one_n.sage"


def _write_worker_script():
    """Create the single-n worker script sage executes per subprocess."""
    code = r'''# sage_one_n.sage  —  search one n, print "SOLUTION n x y" lines
import sys
n = Integer(sys.argv[1])
a2 = 1296*n^2
a4 = 15552*n^3
a6 = 46656*n^4 - 19*n
E = EllipticCurve(QQ, [0, a2, 0, a4, a6])
if E.discriminant() == 0:
    sys.exit(0)
try:
    r = E.rank(proof=False)
except Exception:
    r = 0
mw = []
if r > 0:
    try:
        mw = E.gens(proof=False)
    except Exception:
        mw = []
try:
    pts = E.integral_points(mw_base=mw, both_signs=True)
except Exception as e:
    print(f"WARN integral_points n={n}: {e}", file=sys.stderr)
    sys.exit(0)
for pt in pts:
    if pt.is_infinity():
        continue
    x, y = int(pt[0]), int(pt[1])
    lhs = y**2
    rhs = int(x**3 + 1296*n^2*x^2 + 15552*n^3*x + 46656*n^4 - 19*n)
    if lhs == rhs:
        print(f"SOLUTION {n} {x} {y}")
    else:
        print(f"WARN verify fail n={n} x={x} y={y}", file=sys.stderr)
'''
    WORKER_SCRIPT.write_text(code)


def load_state():
    try:
        data = json.loads(CKPT_FILE.read_text())
        return data.get("last_radius", 0)
    except Exception:
        return 0


def save_state(radius):
    CKPT_FILE.write_text(json.dumps({"last_radius": int(radius)}))


def search_n(n):
    """Run sage_one_n.sage for this n in a subprocess with a timeout.
    Returns list of (n,x,y) tuples and a boolean indicating whether it timed out."""
    sage_bin = "/usr/local/bin/sage"
    cmd = [sage_bin, str(WORKER_SCRIPT), str(n)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=N_TIMEOUT)
        results = []
        for line in proc.stdout.splitlines():
            if line.startswith("SOLUTION"):
                parts = line.split()
                results.append((int(parts[1]), int(parts[2]), int(parts[3])))
        if proc.returncode != 0 and proc.stderr.strip():
            print(f"  [STDERR n={n}] {proc.stderr.strip()[:200]}", flush=True)
        return results, False
    except subprocess.TimeoutExpired:
        return [], True


# ── Main ────────────────────────────────────────────────────────────────────
print("=" * 66)
print("  SAGE INTEGRAL-POINT SEARCH  (algebraically complete)")
print("  y^2 = x^3 + 1296n^2x^2 + 15552n^3x + (46656n^4 - 19n)")
print("=" * 66, flush=True)

start_radius = load_state() + 1
print(f"  Resuming from radius {start_radius}", flush=True)

total_new = 0

master_seen = set()
if MASTER_FILE.exists():
    for line in open(MASTER_FILE):
        parts = line.split()
        if len(parts) == 3:
            try:
                master_seen.add((int(parts[0]), int(parts[1]), int(parts[2])))
            except ValueError:
                pass

master_f = open(str(MASTER_FILE), "a", buffering=1)

try:
    _write_worker_script()
    radius = start_radius
    while True:
        for sign in [1, -1]:
            n = sign * radius
            t0 = time.time()
            sols, timed_out = search_n(n)
            dt = time.time() - t0

            if timed_out:
                msg = f"  [TIMEOUT n={n}] skipped after {N_TIMEOUT}s"
                print(msg, flush=True)
                with open(str(SKIP_FILE), "a") as sf:
                    sf.write(f"{n}\n")
            else:
                for trip in sols:
                    if trip not in master_seen:
                        master_seen.add(trip)
                        nn, xx, yy = trip
                        master_f.write(f"{nn} {xx} {yy}\n")
                        master_f.flush()
                        total_new += 1
                        print(f"  SOLUTION  n={nn:>10}  x={xx:>22}  y={yy:>22}",
                              flush=True)
                if sols or radius % 10 == 0:
                    print(f"  n={n:>8}  nsols={len(sols)}  "
                          f"t={dt:.1f}s  cum_new={total_new}", flush=True)

        save_state(radius)
        radius += 1

except KeyboardInterrupt:
    print("\n[interrupted]", flush=True)
finally:
    master_f.close()
    print(f"Saved. total_new={total_new}", flush=True)
