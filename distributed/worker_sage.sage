#!/usr/bin/env sage
"""
distributed/worker_sage.sage
============================
Sage computation for one work unit.

Called by worker.py via subprocess.  Parameters passed as environment vars:
    WU_T_VALUE  – integer t-value to process
    WU_M_LO     – lower bound for m (string, e.g. "100000000000000000000")
    WU_M_HI     – upper bound for m (string, e.g. "10"+"0"*29)
    WU_ID       – work unit id (for logging)

Output: prints a single JSON line to stdout:
    {"wu_id": ..., "t_value": ..., "points": [[X,Y], ...], "solutions": [...]}

Algorithm (per t):
  The equation m*y² = 36x³+36mx²+12m²x+m³-19 with substitution t = y-m-6x gives:
      m*t*(2m+12x+t) = 36x³-19            ... (*)

  For fixed t, the discriminant condition D_t(x) = s² is an elliptic curve:
      s² = 288t·x³ + 144t²·x² + 24t³·x + (t⁴-152t)

  Scale by A=288t: set X=A*x, Y=A*s to get short-ish Weierstrass:
      Y² = X³ + 144t²·X² + A·24t³·X + A²·(t⁴-152t)

  `integral_points()` finds ALL integer points (X,Y) on this curve.
  Back-transform X → x=X/A, Y → s=Y/A (need A | X and A | Y).
  Then recover m from: m = (-t*(12x+t) + s) / (4t)  [must be positive integer].
  Filter: M_LO ≤ m ≤ M_HI.
  Verify with the original equation.
"""

import sys, os, json
from sage.all import EllipticCurve, QQ, ZZ

# ── Read parameters ────────────────────────────────────────────────────────────
t_value = int(os.environ.get("WU_T_VALUE", "1"))
M_LO    = ZZ(os.environ.get("WU_M_LO",  "100000000000000000000"))   # 10^20
M_HI    = ZZ(os.environ.get("WU_M_HI",  "1" + "0"*29))             # 10^30
wu_id   = int(os.environ.get("WU_ID",   "0"))

eprint = lambda *a: print(*a, file=sys.stderr, flush=True)
eprint(f"[sage] WU={wu_id}  t={t_value}  M_LO={str(M_LO)[:10]}…  M_HI={str(M_HI)[:10]}…")

t = ZZ(t_value)
A = ZZ(288) * t

# Curve coefficients (in [a1,a2,a3,a4,a6] with a1=a3=0):
#   Y² = X³ + a2*X² + a4*X + a6
a2 = ZZ(144) * t**2
a4 = A * ZZ(24) * t**3          # = 6912*t^4
a6 = A**2 * (t**4 - ZZ(152)*t)  # = 82944*t^2*(t^4-152t)

eprint(f"[sage] EC: Y²=X³ + {a2}·X² + {a4}·X + {a6}")

# ── Build elliptic curve ───────────────────────────────────────────────────────
try:
    E = EllipticCurve(QQ, [0, a2, 0, a4, a6])
    disc = E.discriminant()
    eprint(f"[sage] disc={disc}  (nonzero={'yes' if disc != 0 else 'NO – SINGULAR!'})")
    if disc == 0:
        eprint("[sage] Curve is singular – skipping")
        print(json.dumps({"wu_id": wu_id, "t_value": t_value,
                          "points": [], "solutions": [], "error": "singular"}))
        sys.exit(0)
except Exception as exc:
    eprint(f"[sage] Could not construct curve: {exc}")
    print(json.dumps({"wu_id": wu_id, "t_value": t_value,
                      "points": [], "solutions": [], "error": str(exc)}))
    sys.exit(1)

# ── Compute rank and Mordell–Weil generators ──────────────────────────────────
try:
    rank = E.rank(proof=False)
    eprint(f"[sage] rank={rank}")
    if rank > 0:
        mw_base = E.gens(proof=False)
    else:
        mw_base = []
except Exception as exc:
    eprint(f"[sage] rank computation failed: {exc} – trying with empty base")
    mw_base = None   # let integral_points figure it out

# ── Find all integral points ──────────────────────────────────────────────────
try:
    if mw_base is None:
        pts = E.integral_points(both_signs=True)
    else:
        pts = E.integral_points(mw_base=mw_base, both_signs=True)
    eprint(f"[sage] {len(pts)} integral point(s)")
except Exception as exc:
    eprint(f"[sage] integral_points failed: {exc}")
    print(json.dumps({"wu_id": wu_id, "t_value": t_value,
                      "points": [], "solutions": [], "error": str(exc)}))
    sys.exit(1)

# ── Back-transform and filter ─────────────────────────────────────────────────
points_out   = []    # [[X, Y], ...]
solutions_out = []   # [{"m":…,"x":…,"t":…,"y":…}, ...]

for P in pts:
    X, Y = ZZ(P[0]), ZZ(P[1])
    points_out.append([int(X), int(Y)])

    # Need A | X and A | Y for integer back-transform
    if X % A != 0 or Y % A != 0:
        continue

    x_orig = X // A
    s      = Y // A

    # Recover m from: 4t*m = -t*(12x+t) + s  →  m = (s - t*(12x+t)) / (4t)
    numer_m = s - t * (12 * x_orig + t)
    denom_m = 4 * t

    if numer_m % denom_m != 0:
        continue
    m = numer_m // denom_m

    if m <= 0 or m < M_LO or m > M_HI:
        continue

    # Reconstruct y = m + 6x + t
    y = m + 6 * x_orig + t

    # Verify original equation: m*y² = 36x³+36mx²+12m²x+m³-19
    lhs = m * y * y
    rhs = 36*x_orig**3 + 36*m*x_orig**2 + 12*m**2*x_orig + m**3 - 19
    if lhs != rhs:
        eprint(f"[sage] VERIFY FAILED for m={m} x={x_orig} y={y}")
        continue

    eprint(f"[sage] *** SOLUTION: m={m}  x={x_orig}  t={t_value}  y={y} ***")
    solutions_out.append({
        "m": str(int(m)),
        "x": str(int(x_orig)),
        "t": int(t_value),
        "y": str(int(y)),
    })

# ── Output JSON ───────────────────────────────────────────────────────────────
result = {
    "wu_id"    : wu_id,
    "t_value"  : t_value,
    "points"   : points_out,      # all integral points as [X,Y]
    "solutions": solutions_out,   # solutions with m in range
}
print(json.dumps(result))
eprint(f"[sage] Done. {len(points_out)} pts, {len(solutions_out)} solutions.")
