# Integer-solution search for


$y^2 = \frac{36}{m}x^3 + 36x^2 + 12mx + \frac{m^3-19}{m},\quad m\neq 0$


This repository contains Charity-Engine-friendly search code targeting all
integer triples $(m,\,x,\,y)$ satisfying the equation above, and reporting
the associated value $n = m^3 - 19$ for each solution found.

## Key idea (fast candidate generation)

Multiplying both sides by $m$ and factoring the numerator gives

$$m\,y^2 = m\,(6x+m)^2 + (36x^3 - 19).$$

For $y^2$ to be an integer we therefore need

$$m \mid (36x^3 - 19) =: D.$$

So for each integer $x$ the **only** candidate values of $m$ are the
(signed) divisors of $D$, a *finite* set.  This avoids a double loop over
both $x$ and $m$.

## Files

| File | Purpose |
|------|---------|
| `ce_worker.py` | **x-driven worker** — scans one x-chunk `[x_start, x_end]`, uses divisors of $D$ to enumerate $m$. Emits JSONL solutions. |
| `ce_worker_m_driven.py` | **m-driven worker** — scans `[m_start, m_end]` × `[x_start, x_end]`. Useful for cross-validation and targeted m-range searches. |
| `ce_coordinator.py` | Perpetual bidirectional coordinator: expands both the positive and negative x frontiers symmetrically, checkpointing progress in `ce_state.json`. |

## Mathematical completeness

For any fixed x the x-driven worker finds **all** integer solutions because:

1. The integrality condition forces $m \mid D$ (proved above).
2. The set of divisors of $D$ is computed exactly via Pollard-rho factorisation.
3. Every divisor $m$ (both signs) is tested against the perfect-square condition.

Hence exhaustive coverage of $x \in (-\infty, +\infty)$ guarantees all integer solutions are found.

## Local quick start

```bash
# Single x-chunk, positive side
python3 ce_worker.py --x-start 0 --x-end 500 --out tmp/sol_pos.jsonl --verbose

# Single x-chunk, negative side
python3 ce_worker.py --x-start -500 --x-end -1 --out tmp/sol_neg.jsonl --verbose

# Perpetual bidirectional coordinator (alternates positive/negative chunks)
python3 ce_coordinator.py --chunk-size 500

# Stop after 6 chunks (3 positive + 3 negative)
python3 ce_coordinator.py --chunk-size 500 --max-chunks 6

# Cross-validation with m-driven worker on a small region
python3 ce_worker_m_driven.py --m-start -50 --m-end 50 \
    --x-start -200 --x-end 200 --out tmp/sol_m.jsonl --verbose
```

## Coordinator state

The coordinator writes `ce_state.json`:

```json
{
  "next_x_pos": 1500,
  "next_x_neg": -1501
}
```

Both frontiers advance symmetrically by `--chunk-size` per iteration.
Old state files with only `"next_x"` are auto-upgraded (the value becomes
`next_x_pos` and `next_x_neg` is initialised to `-1`).

## Charity Engine deployment pattern

1. Package `ce_worker.py` with a small wrapper that receives `--x-start` / `--x-end` from the CE scheduler.
2. Assign each CE task a **disjoint** x-range; use `ce_coordinator.py` output to generate non-overlapping ranges covering both positive and negative x.
3. Collect `*.jsonl` outputs from all tasks.
4. Deduplicate globally by `(m, n, x, y)`.
5. Continue assigning new chunks forever (or until a mathematical bound is established).

## Important note

No currently known finite bound proves *global completion* for all integers.
This is an exhaustive-by-range engine intended for perpetual distributed search.

