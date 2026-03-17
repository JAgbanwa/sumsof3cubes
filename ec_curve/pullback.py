import sys
sys.stdout.reconfigure(line_buffering=True)
from sage.all import *

pari.allocatemem(2*1024*1024*1024)

Em = EllipticCurve(QQ, [0, 0, 0, 0, -320198697])
E2 = EllipticCurve(QQ, [0, 0, 0, 0, 11859211])

print("Em: y^2 = x^3 - 320198697")
print("E2: y^2 = x^3 + 11859211")
print("E2 conductor:", E2.conductor(), flush=True)

# Get the 3-isogeny phi: Em -> E2
print("\n--- 3-isogeny phi: Em -> E2 ---", flush=True)
isos = Em.isogenies_prime_degree(3)
phi = isos[0]
print("phi domain:  ", phi.domain().ainvs(), flush=True)
print("phi codomain:", phi.codomain().ainvs(), flush=True)

# Get E2 generator
print("\n--- E2 generator ---", flush=True)
G2_coords = (QQ(142034936923377)/QQ(184320814276),
             QQ(-1714545428657134146263)/QQ(79133717909857976))
G2 = E2.point(list(G2_coords) + [1])
print("G2 =", G2, flush=True)
x2, y2 = QQ(G2[0]), QQ(G2[1])
print("G2 on E2 check:", y2**2 == x2**3 + 11859211, flush=True)
print("G2 order: infinite (rank-1 generator)", flush=True)
print("G2 height:", float(G2.height()), flush=True)

# Compute dual isogeny phi_hat: E2 -> Em
print("\n--- Dual isogeny phi_hat: E2 -> Em ---", flush=True)
phi_hat = phi.dual()
print("phi_hat domain:  ", phi_hat.domain().ainvs(), flush=True)
print("phi_hat codomain:", phi_hat.codomain().ainvs(), flush=True)

# Map G2 back to Em via phi_hat
print("\n--- Pulling back G2 to Em ---", flush=True)
try:
    P_em = phi_hat(G2)
    print("phi_hat(G2) =", P_em, flush=True)
    chk2 = QQ(P_em[1])**2 - (QQ(P_em[0])**3 - 320198697)
    print("Is on Em (y^2-x^3+k=0):", chk2 == 0, flush=True)
    print("Height:", float(P_em.height()), flush=True)
    x0, y0 = QQ(P_em[0]), QQ(P_em[1])
    print("x =", x0, flush=True)
    print("y =", y0, flush=True)

    # Verify
    chk = y0**2 - (x0**3 - 320198697)
    print("Verify (y^2-x^3+320198697)=", chk, flush=True)

    # Back-transform to user variables
    # FORWARD chain:
    #   W^2 = 12996*U^3 + 12996*U^2 + 4332*U + 360
    #   Scale: X=12996*U, W1=12996*W -> W1^2 = X^3 + 12996*X^2 + 56298672*X + 60802565760
    #   Shift to E0: t = X + 4332, so X = t - 4332 (eliminates X^2 term) -> W1^2 = t^3 - 20492716608
    #   Minimize with u=2: x_E0 = 4*x_Em, y_E0 = 8*y_Em
    # BACKWARD chain (Em -> user vars):
    #   x_E0 = 4*x0 (from minimization u=2)
    #   X_E  = t - 4332 = x_E0 - 4332 = 4*x0 - 4332 (t = x_E0 = X + 4332)
    #   U    = X_E / 12996 = (4*x0 - 4332) / 12996
    #   W    = y_E0 / 12996 = 8*y0 / 12996
    print("\n--- Back-transform ---", flush=True)
    X_E = 4*x0 - 4332          # Note MINUS sign!
    y_E = 8*y0
    U = QQ(X_E) / 12996
    W = QQ(y_E) / 12996
    print("U = (4x-4332)/12996 =", U, flush=True)
    print("W = 8y/12996 =", W, flush=True)
    # Verify W^2 = 12996*U^3 + 12996*U^2 + 4332*U + 360
    chk_UW = W**2 - (12996*U**3 + 12996*U**2 + 4332*U + 360)
    print("UW curve verify (should be 0):", chk_UW, flush=True)
    print("D = U / 10^45 = (%s) / 10^45" % U, flush=True)
    print("y_user = W / 10^45 = (%s) / 10^45" % W, flush=True)
    # x_original = 19 * k where k = 10^45 * D = 10^45 * U / 10^45 = U
    # So x_original = 19 * U
    print("x_original (=19k=19*U) =", 19*U, flush=True)
    # Check original equation: my^2 = 36x^3 + 36mx^2 + 12m^2 x + m^3 - 19 with m=19
    m = 19
    x_orig = 19*U
    y_orig = W
    LHS = m * y_orig**2
    RHS = 36*x_orig**3 + 36*m*x_orig**2 + 12*m**2*x_orig + m**3 - 19
    print("Verify original eq (should be 0):", LHS - RHS, flush=True)

except Exception as e:
    print("Pullback error:", e, flush=True)

# Also try: compute gens of E2 from scratch to make sure G2 is correct
print("\n--- E2.gens() ---", flush=True)
try:
    g2 = E2.gens(proof=False)
    print("E2 gens:", g2, flush=True)
except Exception as e:
    print("E2 gens error:", e, flush=True)
