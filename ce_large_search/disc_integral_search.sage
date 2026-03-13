#!/usr/bin/env sage
"""
disc_integral_search.sage
=========================
Complete search for integer solutions (m, x, y) to
    m*y^2 = 36*x^3 + 36*m*x^2 + 12*m^2*x + m^3 - 19
with |m| in [M_LO, M_HI].

KEY INSIGHT:
  Setting t = y - m - 6*x, the equation factors as
      m * t * (2*m + 12*x + t) = 36*x^3 - 19    ... (*)

  For each fixed t, (*) is a quadratic in m whose discriminant is
      D_t(x) = (t*(12x+t))^2 + 8*t*(36x^3-19)

  D_t(x) being a perfect square is itself an ELLIPTIC CURVE in (x, s)
  with s^2 = D_t(x).  Sage's integral_points() finds ALL solutions
  in one call, no matter how large (m is recovered algebraically).

  For t and x fixed, if D_t(x) = s^2 then:
      m = (-t*(12x+t) + s) / (4t)    [positive root]
     m' = (-t*(12x+t) - s) / (4t)    [negative root, gives m < 0]

This script:
  1. Determines which t values in [1, T_MAX] can possibly yield solutions
     (requires t | 36x^3-19 for some x).
  2. For each valid t, transforms D_t(x) = s^2 to short Weierstrass form.
  3. Calls E.integral_points() to get all (X, Y) integer points.
  4. Back-transforms and filters for integer (x, m) with |m| in [M_LO, M_HI].
  5. Verifies each candidate and writes to output file.

Output format (solutions.txt):
    SOL m x t y     (one per line)
"""

import sys, os, json, time
from sage.all import (EllipticCurve, QQ, ZZ, Integer, isqrt,
                      GF, Factorization)

# ── Configuration ─────────────────────────────────────────────────────
M_LO  = ZZ(10)**20
M_HI  = ZZ(10)**30
T_MAX = 200         # max t to check

OUT_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
CKPT_FILE  = os.path.join(OUT_DIR, "disc_ckpt.json")
SOL_FILE   = os.path.join(OUT_DIR, "disc_solutions.txt")
MASTER_FILE = os.path.join(os.path.dirname(OUT_DIR), "solutions_disc.txt")

os.makedirs(OUT_DIR, exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────────────

def verify(m, x, y):
    return m * y * y == 36*x**3 + 36*m*x**2 + 12*m**2*x + m**3 - 19

def write_solution(m, x, t, y, sol_file):
    line = f"SOL {m} {x} {t} {y}"
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    with open(sol_file, "a") as f:
        f.write(line + "\n")
    with open(MASTER_FILE, "a") as f:
        f.write(f"m={m}  x={x}  t={t}  y={y}\n")

# ── Determine valid t values ─────────────────────────────────────────
# t must divide 36x^3-19 for some integer x (necessary condition).
# Since 36x^3-19 is always odd, t must be odd.
# For odd t coprime to 3, we need 36x^3 ≡ 19 (mod t), i.e., x^3 ≡ 19*(36^{-1}) (mod t).

def t_is_feasible(t):
    """True if there exists x with t | 36*x^3 - 19."""
    t = ZZ(t)
    if t == 1:
        return True
    for x in range(t):
        if (36 * x**3 - 19) % t == 0:
            return True
    return False

valid_t = [t for t in range(1, T_MAX + 1) if t % 2 == 1 and t_is_feasible(t)]
print(f"Valid t values in [1,{T_MAX}]: {len(valid_t)}")
print(f"First 20: {valid_t[:20]}")
sys.stdout.flush()

# ── Short Weierstrass transformation for each t ───────────────────────
#
# The discriminant curve for fixed t is:
#   s^2 = (t*(12x+t))^2 + 8*t*(36x^3-19)
#       = 288*t*x^3 + (144*t^2 + 96*t^2)*x^2 + ... let me expand fully
#
#   s^2 = t^2*(12x+t)^2 + 8t*(36x^3-19)
#       = 144*t^2*x^2 + 24*t^3*x + t^4 + 288*t*x^3 - 152*t
#       = 288t * x^3 + 144t^2 * x^2 + 24t^3 * x + (t^4 - 152t)
#
# Standard form: s^2 = A*x^3 + B*x^2 + C*x + D
# where A=288t, B=144t^2, C=24t^3, D=t^4-152t
#
# Eliminate x^2 term: shift x -> x - B/(3A) = x - 144t^2/(3*288t) = x - t/6
# After shift (x_new = x_old + t/6, x_old = x_new - t/6):
# Note the shift is NOT in general integer-valued, so we work over Q then
# re-impose integrality conditions.
#
# Scale to Y^2 = X^3 + p*X + q via X = 288t*x_new + offset, Y = (288t)*s
#   (standard Nagell procedure)
#
# Actually, use sage's built-in curve isomorphism:

from sage.all import EllipticCurve, QQ

def weierstrass_for_t(t):
    """
    Return (E, back_transform) where:
      E is a short Weierstrass curve Y^2 = X^3 + a4*X + a6 over QQ,
      back_transform(X, Y) -> (x_orig, s) or None if not integer.
    The curve E is isomorphic to s^2 = 288t*x^3 + 144t^2*x^2 + 24t^3*x + (t^4-152t).
    """
    t = ZZ(t)
    # Coefficients: s^2 = A*x^3 + B*x^2 + C*x + D
    A = 288 * t
    B = 144 * t**2
    C = 24  * t**3
    D = t**4 - 152 * t

    # EllipticCurve([a1,a2,a3,a4,a6]) where y^2+a1xy+a3y = x^3+a2x^2+a4x+a6
    # Our curve: s^2 = A*x^3 + B*x^2 + C*x + D  => divide by A^2:
    # (s/A)^2 ... messy. Use general_weierstrass_model in Sage.
    # Better: use EllipticCurve([0, B/A, 0, C/A, D/A]) then rescale.
    # Sage's EllipticCurve([a2,a4,a6]) with implicit a1=a3=0 form:
    # y^2 = x^3 + a2*x^2 + a4*x + a6
    # So: divide our equation by A (set new y = s/sqrt(A), new x = x*A^(1/3)... )
    # Cleaner: Sage can work with this directly via the general Weierstrass form.

    # Use [a1,a2,a3,a4,a6] = [0, B, 0, A*C, A^2*D] with y^2 = x^3 + B*x^2 + A*C*x + A^2*D
    # This comes from multiplying s^2 = A*x^3+B*x^2+C*x+D by A^2:
    # (A*s)^2 = (A*x)^3 + B*(A*x)^2 + A*C*(A*x) + A^2*D
    # Let X = A*x, Y = A*s:
    # Y^2 = X^3 + B*X^2 + A*C*X + A^2*D

    a2 = B              # 144*t^2
    a4 = A * C          # 288t * 24t^3 = 6912*t^4
    a6 = A**2 * D       # (288t)^2 * (t^4-152t) = 82944*t^2*(t^4-152t)

    try:
        E = EllipticCurve(QQ, [0, a2, 0, a4, a6])
    except Exception as exc:
        return None, None

    # Back-transform: given integer point (X, Y) on E,
    # x_orig = X / A  (must be integer divisible by A=288t)
    # s      = Y / A  (must be integer divisible by A=288t)
    def back(X, Y):
        if X % A != 0 or Y % A != 0:
            return None
        x_orig = X // A
        s      = Y // A
        return (x_orig, s)

    return E, back

# ── Load checkpoint ───────────────────────────────────────────────────

ckpt = {}
if os.path.exists(CKPT_FILE):
    try:
        ckpt = json.loads(open(CKPT_FILE).read())
    except Exception:
        pass
done_t = set(ckpt.get("done_t", []))
total_solutions = ckpt.get("total_solutions", 0)

print(f"\nResuming: {len(done_t)}/{len(valid_t)} t-values done, "
      f"{total_solutions} solutions so far")
sys.stdout.flush()

# ── Main loop: one elliptic curve per t ──────────────────────────────

t_start = time.time()

for t_idx, t in enumerate(valid_t):
    if t in done_t:
        continue

    print(f"\n[t={t}] ({t_idx+1}/{len(valid_t)})  computing integral_points...",
          end=" ", flush=True)
    t0 = time.time()

    E, back = weierstrass_for_t(t)
    if E is None:
        print(f"ERROR: could not construct curve for t={t}")
        done_t.add(t)
        continue

    try:
        rank = E.rank(proof=False)
        if rank > 0:
            mw = E.gens(proof=False)
        else:
            mw = []
        pts = E.integral_points(mw_base=mw, both_signs=True)
    except Exception as exc:
        print(f"ERROR: integral_points failed: {exc}")
        done_t.add(t)
        ckpt["done_t"] = list(done_t)
        ckpt["total_solutions"] = int(total_solutions)
        open(CKPT_FILE, "w").write(json.dumps(ckpt))
        continue

    elapsed = time.time() - t0
    print(f"{len(pts)} points  ({elapsed:.1f}s)")

    for pt in pts:
        if pt.is_infinity():
            continue
        X = ZZ(pt[0])
        Y = ZZ(pt[1])

        result = back(X, Y)
        if result is None:
            continue
        x_orig, s = result

        # m = (-t*(12x+t) + s) / (4t)  for positive-m solution
        t_z = ZZ(t)
        numer_pos = s - t_z * (12 * x_orig + t_z)
        denom = 4 * t_z

        for sign in (+1, -1):
            numer = sign * s - t_z * (12 * x_orig + t_z)
            if numer <= 0:
                continue
            if numer % denom != 0:
                continue
            m = numer // denom
            if m < M_LO or m > M_HI:
                continue
            # Reconstruct y
            y = m + 6 * x_orig + t_z
            if not verify(m, x_orig, y):
                # try negative t adjustment
                y2 = m + 6 * x_orig - t_z
                if not verify(m, x_orig, y2):
                    print(f"  VERIFY_FAIL m={m} x={x_orig} t={t_z} y={y}")
                    continue
                y = y2
            total_solutions += 1
            write_solution(m, x_orig, t_z, y, SOL_FILE)

        # Also check negative m
        for sign in (+1, -1):
            numer = -sign * s - t_z * (12 * x_orig + t_z)
            # This gives m_neg = numer/(4t), we want |m_neg| in [M_LO, M_HI]
            if numer >= 0:
                continue
            m_neg = numer // denom  # negative
            if -m_neg < M_LO or -m_neg > M_HI:
                continue
            y = m_neg + 6 * x_orig + t_z
            if not verify(m_neg, x_orig, y):
                y2 = m_neg + 6 * x_orig - t_z
                if not verify(m_neg, x_orig, y2):
                    continue
                y = y2
            total_solutions += 1
            write_solution(m_neg, x_orig, t_z, y, SOL_FILE)

    done_t.add(t)
    ckpt["done_t"] = list(done_t)
    ckpt["total_solutions"] = int(total_solutions)
    open(CKPT_FILE, "w").write(json.dumps(ckpt))

total_elapsed = time.time() - t_start
print(f"\n{'='*60}")
print(f"COMPLETE: searched t in [1,{T_MAX}]  "
      f"({len(valid_t)} valid t-values)")
print(f"Total solutions with |m| in [1e20, 1e30]: {total_solutions}")
print(f"Elapsed: {total_elapsed/60:.1f} min")
print(f"Results in: {SOL_FILE}")
print("=" * 60)
