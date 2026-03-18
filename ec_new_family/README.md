# ec_new_family — Integral Points on a New Elliptic Curve Family

## The Equation

This folder searches for **integral points** $(X, y) \in \mathbb{Z}^2$ on the short-Weierstrass
elliptic curve parameterised by $n \in \mathbb{Z} \setminus \{0\}$:

$$E_n : y^2 = X^3 + a_4(n) \cdot X + a_6(n)$$

where

$$a_4(n) = -45349632 n^4 + 419904 n^3$$

$$a_6(n) = 3\cdot \left(39182082048 n^6 - 544195584 n^5 + 1259712 n^4 - 19 n\right)$$

with the **excluded point** $X = -3888\,n^2$ (a special trivial locus).

### Factored form of the coefficients

$$a_4(n) = -419904 n^3\,(108n - 1)$$

$$a_6(n) = 3n\cdot\left(39182082048\, n^5 - 544195584\, n^4 + 1259712\, n^3 - 19\right)$$

Note $419904 = 648^2$ and $3888 = 6 \cdot 648$.

### At $n = 0$

The curve degenerates ($\Delta = 0$, $y^2 = X^3$) — skip.

---

## Relation to the sister family `ec_curve/`

The sister family (`ec_curve/`) uses the **general** Weierstrass form
$y^2 = x^3 + 1296n^2 x^2 + 15552n^3 x + 46656n^4 - 19n$.
The present family is structurally distinct: it has **no $x^2$ term** (short Weierstrass)
and $a_4, a_6$ grow as $n^4$ and $n^6$ respectively, vs $n^2, n^4$ there.

---

## Repository Layout

```
ec_new_family/
├── README.md                  ← this file
├── local_search.py            ← multi-core Python search (quick start)
├── ec_pari_search.py          ← provably-complete PARI/GP parallel search
├── worker_ec.gp               ← PARI/GP inner script (ellintegralpoints)
├── worker_ec.c                ← C brute-force worker (fast finite search)
├── Makefile                   ← builds worker_ec
└── output/
    └── solutions.txt          ← discovered solutions (git-tracked)
```

---

## Quick start

```bash
# 1) Build the C worker
make

# 2) Local Python search (brute-force, n = 1..500, |X| up to 10^7)
python3 local_search.py

# 3) Provably-complete PARI search (requires `gp`)
#    Needs gp ≥ 2.9 with ellintegralpoints() support
python3 ec_pari_search.py --n-lo 1 --n-hi 200 --workers 4
```

---

## Method

### Brute-force (`worker_ec.c`, `local_search.py`)
For each $n$, scan $X \in [X_{\min}(n),\, B]$ where $X_{\min}$ is the
leftmost real root of the cubic and $B$ is a configurable bound.
Checks whether $X^3 + a_4 X + a_6$ is a perfect square.

### Provably-complete PARI (`worker_ec.gp`, `ec_pari_search.py`)
Uses PARI/GP's `ellintegralpoints(E)`:
1. Nagell–Tate torsion enumeration  
2. Mordell–Weil rank via 2-descent  
3. Baker–Wüstholz elliptic logarithm height bound  
4. LLL + sieving to certifiably enumerate **all** integral points  

The excluded point $X = -3888n^2$ is filtered from all output.

---

## Known solutions

None discovered yet for $|n| \leq 500$.  
*(Results will appear in `output/solutions.txt` as they are found.)*
