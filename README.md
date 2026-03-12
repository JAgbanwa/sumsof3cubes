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
