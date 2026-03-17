"""
Scan m values for integer solutions to:
  m*y^2 = 36*x^3 + 36*m*x^2 + 12*m^2*x + m^3 - 19

Strategy: multiply through by 36*m to get a Weierstrass curve, but also
use a direct point_search approach on the native model to avoid slow gens().

For a curve y^2 = M(x), integral points have |x| bounded by Szpiro/Baker.
We use two independent methods:
  1. Direct: for each m, search x in a wide range directly
  2. Sage: for curves where mw_rank can be determined fast, use integral_points()
"""
import sys
sys.stdout.reconfigure(line_buffering=True)
from sage.all import *
import signal

class TimeoutError(Exception): pass
def handler(signum, frame): raise TimeoutError()
signal.signal(signal.SIGALRM, handler)

results = {}
errors = {}

def check_m(m):
    # Direct brute force: for small x
    direct = []
    for x in range(-100000, 100001):
        rhs = 36*x**3 + 36*m*x**2 + 12*m**2*x + m**3 - 19
        if rhs == 0:
            continue
        if m == 0:
            break
        if rhs % m != 0:
            continue
        q = rhs // m
        if q < 0:
            continue
        sq = isqrt(q)
        if sq*sq == q:
            direct.append((x, int(sq)))
    return direct

# First: direct brute-force for ALL m in range (fast Python loop)
print("=== Direct brute-force: m in [-200,200], x in [-100000,100000] ===", flush=True)
print("(This covers x up to 100k)", flush=True)
for m in list(range(1, 201)) + list(range(-1, -201, -1)):
    sols = check_m(m)
    if sols:
        print("*** m=%d: FOUND %s" % (m, sols), flush=True)
        results[m] = sols

print("\n=== Summary ===", flush=True)
if results:
    for m in sorted(results.keys()):
        print("  m=%d => %s" % (m, results[m]), flush=True)
else:
    print("  No integer solutions found for m in [-200,200], |x| <= 100000", flush=True)

# Now try elliptic curve integral_points with SHORT timeout per m
# Only for m where rank is 0 (easy to verify)
print("\n=== Sage integral_points (rank-0 curves only, timeout=30s per m) ===", flush=True)
for m in list(range(1, 51)) + list(range(-1, -26, -1)):
    try:
        a2 = 36 * m**2
        a4 = 432 * m**4
        a6 = 1296 * m**2 * (m**4 - 19*m)
        E = EllipticCurve(QQ, [0, a2, 0, a4, a6])
        if E.is_singular():
            continue
        signal.alarm(30)  # 30s per curve
        try:
            r = E.rank(only_use_mwrank=False)
        finally:
            signal.alarm(0)
        if r == 0:
            signal.alarm(30)
            try:
                pts = E.integral_points(mw_base=[], both_signs=True)
            finally:
                signal.alarm(0)
            sols = []
            for P in pts:
                if P[2] == 0: continue
                V, W = ZZ(P[0]), ZZ(P[1])
                sx, sy = 36*m, 36*m**2
                if sx != 0 and V % sx == 0 and W % sy == 0:
                    x, y = V // sx, W // sy
                    if m*y**2 == 36*x**3 + 36*m*x**2 + 12*m**2*x + m**3 - 19:
                        sols.append((x, y))
            if sols:
                print("*** Sage m=%d rank=0: FOUND %s" % (m, sols), flush=True)
            else:
                print("Sage m=%d rank=0: no solutions (%d integral pts on E)" % (m, len(pts)), flush=True)
    except TimeoutError:
        print("m=%d: timeout" % m, flush=True)
    except Exception as e:
        print("m=%d: error %s" % (m, str(e)[:50]), flush=True)
