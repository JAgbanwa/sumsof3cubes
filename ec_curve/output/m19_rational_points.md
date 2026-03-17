# Rational Points on the m=19 Reframed Elliptic Curve

## Problem Statement

Find rational points on the **reframed curve** (from the original equation `19y² = 36x³ + 684x² + 4332x + 6840` with substitution `x = 19·10⁴⁵·D`, `y_user = Y/10⁴⁵`):

$$y^2 = 12996 \cdot 10^{45} D^3 + 12996 \cdot D^2 + \frac{4332}{10^{45}} D + \frac{360}{10^{90}}$$

---

## Derivation Chain (Reframed → Minimal Weierstrass)

### Step 1 — Clear denominators
Set `U = 10⁴⁵·D`, `W = 10⁴⁵·y_user`:

$$W^2 = 12996U^3 + 12996U^2 + 4332U + 360$$

### Step 2 — Scale to standard Weierstrass with x² term
Set `X = 12996·U`, `W₁ = 12996·W`:

$$W_1^2 = X^3 + 12996X^2 + 56298672X + 60802565760$$

### Step 3 — Complete the cube (eliminate x² term, shift `X → X - 4332`)
New variable `t = X + 4332`, call it `x_0`:

$$W_1^2 = x_0^3 - 20492716608 \qquad (E_0)$$

### Step 4 — Minimize (u = 2: `x₀ = 4·x_m`, `W₁ = 8·y_m`)

$$\boxed{y_m^2 = x_m^3 - 320198697} \qquad (E_m, \text{minimal model})$$

---

## Key Properties of Eₘ

| Property | Value |
|---|---|
| Minimal model | `[0, 0, 0, 0, −320198697]` |
| j-invariant | **0** (CM curve by ℤ[ω]) |
| Conductor | **430479504** = 2⁴·3²·7²·13²·19² |
| k factored | −3³·7·13·19⁴ |
| Analytic rank | **1** |
| Torsion | **Trivial** |
| Isogeny class | {Eₘ, E₂} with 3-isogeny `φ: Eₘ → E₂` |

---

## Finding the Generator via 3-Isogeny

Eₘ is 3-isogenous to **E₂: y² = x³ + 11859211** (same conductor).

### Generator of E₂
Found via `E2.gens(proof=False)` in SageMath 10.5:

$$G_2 = \left(\frac{142034936923377}{184320814276},\ \frac{-1714545428657134146263}{79133717909857976}\right)$$

- Canonical height: **32.77**
- Verification: `G2 on E₂` ✓

### Generator of Eₘ via dual isogeny φ̂: E₂ → Eₘ
Apply the dual 3-isogeny `P = φ̂(G₂)`:

$$\mathbf{x_P} = \frac{3162458334569358187062331231325853888109777}{3718473971056053130262473108993628225604}$$

$$\mathbf{y_P} = \frac{-3894228350436067038312065162575538689415800674650195219149913735}{226749907582434318777788131068516134019406869449488799306808}$$

- **Canonical height**: 98.32
- **Order**: +∞  
- **Indivisible**: `P.division_points(n) = []` for n = 2, 3, 5 ✓
- **Verification on Eₘ**: `yₚ² − (xₚ³ − 320198697) = 0` ✓

---

## Back-Transform to User Variables (D, y_user)

Using the reverse chain Eₘ → E₀ → E₁ → (U, W):

$$U = \frac{4x_P - 4332}{12996} = \frac{-864648976084347353011927145714245480219355}{12081321931961116620222775131120298104987396}$$

$$W = \frac{8y_P}{12996} = \frac{-3894228350436067038312065162575538689415800674650195219149913735}{368355224867664550854516818920804459714526459420694554473909596}$$

Then:

$$D_{\text{gen}} = \frac{U}{10^{45}}, \qquad y_{\text{gen}} = \frac{W}{10^{45}}$$

**Verification** of `W² = 12996U³ + 12996U² + 4332U + 360`: **0** ✓  
**Verification** of original equation `19W² = 36(19U)³ + 684(19U)² + 4332(19U) + 6840`: **0** ✓

---

## Full Solution Set

Since Eₘ(ℚ) ≅ ℤ (rank 1, trivial torsion), **all rational points** on the reframed curve are:

$$\{n \cdot P : n \in \mathbb{Z}\} \cup \{\infty\}$$

where P is the generator above and n·P denotes the n-th multiple under elliptic curve addition.

- n = 0: point at infinity (identity)
- n = ±1: P or −P (as computed above)
- n = ±2: 2P, −2P (computable by doubling formula)
- etc.

---

## Notes on Integer Points

The generator P corresponds to a **rational** (non-integer) U ≈ −0.0716, meaning `k = U` is rational but not an integer. By Siegel's theorem, Eₘ has finitely many integral points — these are separate from the generator and can be computed via `Em.integral_points()` in SageMath.

---

## SageMath Code to Reproduce

```python
from sage.all import *

Em = EllipticCurve(QQ, [0, 0, 0, 0, -320198697])
E2 = EllipticCurve(QQ, [0, 0, 0, 0, 11859211])

# Get E2 generator
G2 = E2.gens(proof=False)[0]

# 3-isogeny Em -> E2 and its dual E2 -> Em
phi = Em.isogenies_prime_degree(3)[0]
phi_hat = phi.dual()

# Mordell-Weil generator of Em
P = phi_hat(G2)

# Back-transform to user variables
xP, yP = QQ(P[0]), QQ(P[1])
U = (4*xP - 4332) / 12996
W = 8*yP / 12996

# Rational solution on user's curve
D_user  = U   # then D = D_user / 10^45
y_user  = W   # then y = y_user / 10^45
```
