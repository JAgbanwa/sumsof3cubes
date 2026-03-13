# sage_one_n.sage  —  search one n, print "SOLUTION n x y" lines
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
