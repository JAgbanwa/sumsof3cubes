# Integer-solution search for


$y^2 = \frac{36}{m}x^3 + 36x^2 + 12mx + \frac{m^3-19}{m},\quad m\neq 0$


This repository contains Charity-Engine-friendly search code.

## Key idea (fast candidate generation)
For integer solutions, we must have
\[
m \mid (36x^3-19).
\]
So for each integer `x`, candidate `m` values are exactly divisors of
`D = 36*x^3 - 19`, which is a finite set. This avoids scanning all `m`.

## Files
- `ce_worker.py`: scans one `x` chunk `[x_start, x_end]`, emits JSONL solutions.
- `ce_coordinator.py`: simple perpetual loop with checkpointing (`ce_state.json`).

## Local quick start
```bash
python3 ce_worker.py --x-start -200 --x-end 200 --out tmp/sol.jsonl
python3 ce_coordinator.py --chunk-size 200 --max-chunks 3
```

## Charity Engine deployment pattern
1. Package `ce_worker.py` with a small wrapper that receives chunk bounds.
2. Assign each CE task a disjoint range `[x_start, x_end]`.
3. Collect `*.jsonl` outputs.
4. Deduplicate globally by `(m,n,x,y)`.
5. Continue assigning chunks forever (or until a mathematically proven bound is established).

## Important note
No currently known finite bound is encoded here that proves *global completion* for all integers. This is an exhaustive-by-range engine intended for perpetual distributed search.

---

## Elliptic Curve Analysis — m = 19

The special case `m = 19` admits an elliptic curve reformulation. Under the substitution `x = 19·10⁴⁵·D`, `Y = 10⁴⁵·y`, the equation becomes:

$$y^2 = 12996 \cdot 10^{45} D^3 + 12996 D^2 + \frac{4332}{10^{45}} D + \frac{360}{10^{90}}$$

Through a chain of Weierstrass transformations this reduces to the **minimal model**:

$$E_m:\quad y_m^2 = x_m^3 - 320198697$$

### Key properties

| Property | Value |
|---|---|
| j-invariant | **0** (CM by ℤ[ω]) |
| Conductor | 430,479,504 = 2⁴·3²·7²·13²·19² |
| Analytic rank | **1** |
| Torsion | Trivial |

### Mordell-Weil generator

$E_m$ is 3-isogenous to $E_2: y^2 = x^3 + 11859211$ (same conductor). The primitive generator
was found via the dual 3-isogeny $\hat{\varphi}: E_2 \to E_m$ applied to the generator of $E_2(\mathbb{Q})$:

$$x_P = \frac{3162458334569358187062331231325853888109777}{3718473971056053130262473108993628225604}$$

$$y_P = \frac{-3894228350436067038312065162575538689415800674650195219149913735}{226749907582434318777788131068516134019406869449488799306808}$$

Canonical height ≈ 98.32. Verified: indivisible by 2, 3, 5; $y_P^2 - (x_P^3 - 320198697) = 0$ ✓

**Back-transformed user variables** (`W² = 12996U³ + 12996U² + 4332U + 360`):

$$U = \frac{4x_P - 4332}{12996}, \qquad W = \frac{8y_P}{12996}$$

then $D = U/10^{45}$, $y_\text{user} = W/10^{45}$. Both the intermediate and original equations verify to 0 ✓.

**All rational points** on this curve are $\{n \cdot P \mid n \in \mathbb{Z}\} \cup \{\infty\}$.

Full derivation and SageMath code: [`ec_curve/output/m19_rational_points.md`](ec_curve/output/m19_rational_points.md)
