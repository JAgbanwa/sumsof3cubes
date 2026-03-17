"""
sage_4curves.sage  —  Run integral_points() on the ONLY 4 curves
                       that can yield y/(6n) ∈ ℤ:
  n ∈ {1, -1, 19, -19}
  y² = x³ + 1296n²x² + 15552n³x + (46656n⁴ - 19n)

These are FIXED elliptic curves (not a family), so Sage can handle them.
Results are written to output/solutions_sage_4curves.txt.

Run:   sage sage_4curves.sage
"""
import sys, time
sys.stdout.reconfigure(line_buffering=True)

OUTPUT = "output/solutions_sage_4curves.txt"

def search_fixed_n(n):
    n = Integer(n)
    a2 = Integer(1296) * n^2
    a4 = Integer(15552) * n^3
    a6 = Integer(46656) * n^4 - Integer(19) * n
    E = EllipticCurve(QQ, [0, a2, 0, a4, a6])
    
    print(f"\n{'='*60}")
    print(f"  n = {n}")
    print(f"  E: y² = x³ + {a2}x² + {a4}x + {a6}")
    print(f"  Δ = {E.discriminant()}")
    
    t0 = walltime()
    
    # Rank
    print(f"  Computing rank (proof=False)...", flush=True)
    try:
        r = E.rank(proof=False)
        print(f"  rank = {r}  ({walltime()-t0:.1f}s)", flush=True)
    except Exception as e:
        print(f"  rank FAILED: {e}")
        r = -1

    if r == 0:
        print(f"  rank=0 → only torsion points; checking torsion...")
        tors = E.torsion_points()
        print(f"  torsion: {tors}")
        pts = [p for p in tors if not p.is_zero()]
    elif r > 0:
        print(f"  Computing generators...", flush=True)
        try:
            mw = E.gens(proof=False)
            print(f"  generators: {mw}  ({walltime()-t0:.1f}s)", flush=True)
        except Exception as e:
            print(f"  gens FAILED: {e}")
            mw = []
        print(f"  Computing integral_points...", flush=True)
        try:
            pts_raw = E.integral_points(mw_base=mw, both_signs=True)
            pts = [p for p in pts_raw if not p.is_zero()]
            print(f"  integral_points done: {len(pts)} points  ({walltime()-t0:.1f}s)")
        except Exception as e:
            print(f"  integral_points FAILED: {e}")
            pts = []
    else:
        print(f"  rank computation failed, skipping.")
        return []

    results = []
    six_n = 6 * n
    for pt in pts:
        x, y = int(pt[0]), int(pt[1])
        # Verify
        rhs = x^3 + 1296*n^2*x^2 + 15552*n^3*x + 46656*n^4 - 19*n
        if y^2 != rhs:
            print(f"  [!!] verify fail: n={n} x={x} y={y}")
            continue
        # Check divisibility
        if y % six_n != 0:
            continue
        k = y // six_n
        results.append((int(n), x, y, int(k)))
        print(f"\n  ★★★ SOLUTION  n={n}  x={x}  y={y}  k=y/(6n)={k}\n", flush=True)

    print(f"  → {len(results)} solutions with y/(6n) ∈ ℤ for n={n}")
    return results


# ── Main ──────────────────────────────────────────────────────────────────
print("="*60)
print("  Sage integral_points search on 4 fixed curves")
print("  (The ONLY n-values that can yield y/(6n) ∈ ℤ)")
print("="*60, flush=True)

all_solutions = []
# n=1 already computed: rank=0, torsion={(0:1:0)}, NO solutions.
print("n= 1: rank=0, torsion trivial → NO solutions (already computed)")

for n_val in [-1, 19, -19]:
    sols = search_fixed_n(n_val)
    all_solutions.extend(sols)

print(f"\n{'='*60}")
print(f"  TOTAL solutions found: {len(all_solutions)}")
for s in all_solutions:
    print(f"    n={s[0]}  x={s[1]}  y={s[2]}  k={s[3]}")

# Write output
import os
os.makedirs("output", exist_ok=True)
with open(OUTPUT, "w") as f:
    for s in all_solutions:
        f.write(f"{s[0]} {s[1]} {s[2]} {s[3]}\n")
print(f"\n  Written to {OUTPUT}")
