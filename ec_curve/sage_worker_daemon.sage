"""
sage_worker_daemon.sage  —  Long-running Sage daemon that reads n values from
stdin, one per line, and writes results to stdout.

Protocol:
  input:  "SEARCH <n>"  → compute integral points for E_n, write results
  output: "RESULT <n> <x> <y>"  for each integral point (one per line)
          "DONE <n>"            when finished with n
          "ERROR <n> <msg>"     on errors
          "TIMEOUT"             should never happen here (handled by caller)

Run via:
  sage sage_worker_daemon.sage

The orchestrator (sage_orchestrator.py) spawns this and talks to it via pipes.
"""
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

def process_n(n):
    n = Integer(n)
    a2 = 1296 * n^2
    a4 = 15552 * n^3
    a6 = 46656 * n^4 - 19*n
    E = EllipticCurve(QQ, [0, a2, 0, a4, a6])
    if E.discriminant() == 0:
        print(f"DONE {n}", flush=True)
        return
    try:
        r = E.rank(proof=False)
    except Exception as e:
        print(f"ERROR {n} rank_failed:{e}", flush=True)
        r = 0
    mw = []
    if r > 0:
        try:
            mw = E.gens(proof=False)
        except Exception as e:
            print(f"ERROR {n} gens_failed:{e}", flush=True)
            mw = []
    try:
        pts = E.integral_points(mw_base=mw, both_signs=True)
    except Exception as e:
        print(f"ERROR {n} integral_pts:{e}", flush=True)
        print(f"DONE {n}", flush=True)
        return
    for pt in pts:
        if pt.is_infinity():
            continue
        x, y = int(pt[0]), int(pt[1])
        lhs = y*y
        rhs = int(x**3 + 1296*n^2*x^2 + 15552*n^3*x + 46656*n^4 - 19*n)
        if lhs == rhs:
            print(f"RESULT {int(n)} {x} {y}", flush=True)
        else:
            print(f"ERROR {n} verify_fail x={x} y={y}", flush=True)
    print(f"DONE {n}", flush=True)

print("READY", flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if line.startswith("SEARCH "):
        n_val = int(line.split()[1])
        try:
            process_n(n_val)
        except Exception as e:
            print(f"ERROR {n_val} exception:{e}", flush=True)
            print(f"DONE {n_val}", flush=True)
    elif line == "QUIT":
        break

print("BYE", flush=True)
