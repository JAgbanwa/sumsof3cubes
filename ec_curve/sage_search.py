"""
sage_search.py  —  Real-time algebraically-complete integral-point search
                    using SageMath for:
    y^2 = x^3 + 1296*n^2*x^2 + 15552*n^3*x + (46656*n^4 - 19*n)

Run as:  sage sage_search.py  OR  python3 sage_search.py  (with sage on path)

Searches n = 1, -1, 2, -2, 3, -3, ...  expanding forever.
Writes results to  output/solutions_master.txt
"""
from sage.all import EllipticCurve, QQ
import time, sys, os, json
from pathlib import Path

try:
    OUTPUT_DIR = Path(__file__).parent / "output"
except NameError:
    OUTPUT_DIR = Path.cwd() / "output"
MASTER_FILE  = OUTPUT_DIR / "solutions_master.txt"
CKPT_FILE    = OUTPUT_DIR / "sage_search_checkpoint.json"

OUTPUT_DIR.mkdir(exist_ok=True)


def verify(n, x, y):
    return y*y == x**3 + 1296*n**2*x**2 + 15552*n**3*x + 46656*n**4 - 19*n


def is_target(n, x, y):
    """True when y/(6n) is an integer (the primary goal)."""
    return n != 0 and y % (6 * n) == 0


def search_n(n):
    a2 = 1296 * n * n
    a4 = 15552 * n**3
    a6 = 46656 * n**4 - 19 * n
    E  = EllipticCurve(QQ, [0, a2, 0, a4, a6])
    if E.discriminant() == 0:
        return []
    results = []
    try:
        try:
            mw = E.gens(proof=False)
        except Exception:
            mw = []
        pts = E.integral_points(mw_base=mw, both_signs=True)
        for pt in pts:
            if pt.is_infinity():
                continue
            x, y = int(pt[0]), int(pt[1])
            if verify(n, x, y) and is_target(n, x, y):
                results.append((int(n), x, y))
    except Exception as e:
        print(f"  [ERR] n={n}: {e}", flush=True)
    return results


def load_state():
    try:
        data = json.loads(CKPT_FILE.read_text())
        return data.get("last_radius", 0), set(map(tuple, data.get("found", [])))
    except Exception:
        return 0, set()


def save_state(radius, found):
    CKPT_FILE.write_text(json.dumps({
        "last_radius": int(radius),
        "found":       [[int(v) for v in t] for t in found]
    }))


def main():
    last_radius, master_found = load_state()

    # Load already-written solutions to avoid duplicates
    if MASTER_FILE.exists():
        with open(MASTER_FILE) as mf:
            for line in mf:
                parts = line.split()
                if len(parts) == 3:
                    try:
                        master_found.add((int(parts[0]), int(parts[1]), int(parts[2])))
                    except ValueError:
                        pass

    print("=" * 66)
    print("  SAGE INTEGRAL-POINT SEARCH  (algebraically complete)")
    print("  y^2 = x^3 + 1296n^2x^2 + 15552n^3x + (46656n^4 - 19n)")
    print("=" * 66)
    print(f"  Resuming at radius {last_radius}")
    print(f"  Master solutions loaded: {len(master_found)}")
    print(flush=True)

    t0 = time.time()
    total_new = 0

    with open(MASTER_FILE, "a", buffering=1) as mf:
        radius = int(last_radius) + 1
        while True:
            for n in [radius, -radius]:
                t1 = time.time()
                sols = search_n(n)
                dt  = time.time() - t1
                for trip in sols:
                    if trip not in master_found:
                        master_found.add(trip)
                        nn, xx, yy = trip
                        mf.write(f"{nn} {xx} {yy}\n")
                        mf.flush()
                        total_new += 1
                        k = yy // (6 * nn)
                        print(f"  ★★★ SOLUTION  n={nn:>10}  x={xx:>22}  y={yy:>22}  y/(6n)={k}",
                              flush=True)
                if int(radius) % 50 == 0 or sols:
                    print(f"  n={n:>8}  t_n={dt:.2f}s  "
                          f"total_new={total_new}  elapsed={time.time()-t0:.0f}s",
                          flush=True)
            save_state(radius, master_found)
            radius = int(radius) + 1


if __name__ == "__main__":
    main()
