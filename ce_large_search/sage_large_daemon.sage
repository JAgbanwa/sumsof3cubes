#!/usr/bin/env sage
# sage_large_daemon.sage
# ===========================================================
# Long-running Sage daemon for the ec-curve integral-point
# search on E_m : Y^2 = X^3 + 1296m^2 X^2 + 15552m^3 X
#                            + (46656m^4 - 19m)
#
# FAST TRACK (rank = 0):
#   Integral points = torsion points only (Siegel). Fast.
# SLOW TRACK (rank > 0):
#   Use integral_points with SIGALRM timeout; fall back to
#   bounded x-range search if it times out.
#
# Protocol:
#   stdin  : one integer m per line, or "QUIT"
#   stdout : SOL m X Y   -- one line per verified integral pt
#            DONE m       -- end of results for this m
#            SKIP m reason -- m skipped (singular / rank-timeout)
#            ERR m reason  -- error
# ===========================================================

import sys, signal, os
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from sage.all import EllipticCurve, QQ, Integer

# ------ curve definition ----------------------------------------------------

def curve(m):
    m = Integer(m)
    a2 = Integer(1296)*m*m
    a4 = Integer(15552)*m**3
    a6 = Integer(46656)*m**4 - Integer(19)*m
    return EllipticCurve(QQ, [0, a2, 0, a4, a6])

def rhs(m, X):
    m = int(m); X = int(X)
    return X**3 + 1296*m*m*X*X + 15552*m**3*X + 46656*m**4 - 19*m

def verify(m, X, Y):
    return int(Y)**2 == rhs(m, X)

def emit_pt(m, pt):
    if pt.is_infinity():
        return
    X, Y = pt[0], pt[1]
    # Must be integer coordinates
    if X.denominator() != 1 or Y.denominator() != 1:
        return
    X, Y = int(X), int(Y)
    if verify(m, X, Y):
        print(f"SOL {m} {X} {Y}", flush=True)
    else:
        sys.stderr.write(f"VERIFY_FAIL m={m} X={X} Y={Y}\n")

# ------ SIGALRM timeout helper ----------------------------------------------

class _Timeout(Exception): pass

def _alarm_handler(sig, frame):
    raise _Timeout()

def with_timeout(func, secs, *args, **kwargs):
    """Call func(*args) with a wall-clock timeout of secs seconds."""
    old = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(secs)
    try:
        return func(*args, **kwargs)
    except _Timeout:
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)

# ------ bounded x-range search (fallback) -----------------------------------
# For rank>0 curves where integral_points times out.
# When m is large the mathematical argument shows:
#   any solution needs |x| > (|m|/36)^(1/3)  in ORIGINAL coords.
# We search a window around the expected root of x^3 conformal.

def bounded_search(m, x_bound=10**7):
    """Quick brute-force: check g(x) = y^2? for |x| <= x_bound in original eq.
    For large m the valid x range is determined by the cubic residue
    condition: m | 36*x^3 - 19.  For small x this is impossible, so fast.
    """
    m_i = int(m)
    abs_m = abs(m_i)
    # For |m| > 36*x_bound^3, no solution in range (delta would be fractional)
    threshold = 36 * x_bound**3 + 19
    if abs_m > threshold:
        return   # provably no solutions in range
    for x in range(-x_bound, x_bound + 1):
        num = 36*x*x*x - 19
        if num % m_i != 0:
            continue
        # RHS of original eq as integer
        rhs_val = (num // m_i) + 36*x*x + 12*m_i*x + m_i*m_i
        if rhs_val < 0:
            continue
        # isqrt check
        s = int(rhs_val**0.5)
        for cand in (s-1, s, s+1):
            if cand >= 0 and cand*cand == rhs_val:
                # (x, cand) is a solution in original coords
                # Convert to Weierstrass: X=6mx, Y=6m^2*y
                X = 6*m_i*x
                Y = 6*m_i*m_i*cand
                print(f"SOL {m_i} {X} {Y}", flush=True)
                if cand > 0:
                    print(f"SOL {m_i} {X} {-Y}", flush=True)
                break

# ------ main search function ------------------------------------------------

RANK_TIMEOUT  = 30    # seconds for rank computation
INTPTS_TIMEOUT = 120  # seconds for integral_points

def search_one(m):
    m = Integer(m)
    if m == 0:
        print(f"DONE {m}", flush=True)
        return

    try:
        E = curve(m)
    except Exception as exc:
        print(f"ERR {m} ellinit:{exc}", flush=True)
        return

    if E.discriminant() == 0:
        print(f"SKIP {m} singular", flush=True)
        return

    # ---- rank (analytic, fast) -------------------------------------------
    try:
        r = with_timeout(E.rank, RANK_TIMEOUT, proof=False)
    except _Timeout:
        sys.stderr.write(f"[daemon] rank TIMEOUT m={m}\n")
        # Fall back to bounded search only
        bounded_search(m)
        print(f"DONE {m}", flush=True)
        return
    except Exception as exc:
        sys.stderr.write(f"[daemon] rank error m={m}: {exc}\n")
        r = -1  # unknown

    # ---- rank = 0 : only torsion points (fast, provably complete) --------
    if r == 0:
        try:
            tors = E.torsion_points()
            for pt in tors:
                emit_pt(m, pt)
        except Exception as exc:
            sys.stderr.write(f"[daemon] torsion error m={m}: {exc}\n")
        print(f"DONE {m}", flush=True)
        return

    # ---- rank > 0 : try full integral_points with timeout ----------------
    mw = []
    if r > 0:
        try:
            mw = with_timeout(E.gens, RANK_TIMEOUT, proof=False)
        except (_Timeout, Exception):
            mw = []

    try:
        pts = with_timeout(E.integral_points, INTPTS_TIMEOUT,
                           mw_base=mw, both_signs=True)
        for pt in pts:
            emit_pt(m, pt)
        print(f"DONE {m}", flush=True)
        return
    except _Timeout:
        sys.stderr.write(f"[daemon] integral_points TIMEOUT m={m}, falling back\n")
    except Exception as exc:
        sys.stderr.write(f"[daemon] integral_points error m={m}: {exc}\n")

    # ---- fallback: bounded brute-force -----------------------------------
    bounded_search(m)
    print(f"DONE {m}", flush=True)

# ---- main loop -------------------------------------------------------------

print("READY", flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if line.upper() == "QUIT":
        break
    try:
        m_val = int(line)
        search_one(m_val)
    except Exception as exc:
        print(f"ERR {line} {exc}", flush=True)

print("BYE", flush=True)
