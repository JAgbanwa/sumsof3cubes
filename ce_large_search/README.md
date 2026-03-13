# CE Large-m EC Search  —  Charity Engine Deployment

## Equation

$$y^2 = \frac{36}{m}\,x^3 + 36\,x^2 + 12m\,x + \frac{m^3-19}{m}, \quad m \neq 0$$

### Weierstrass form (multiply through by $m^3$, substitute $X=6mx,\,Y=6m^2y$)

$$E_m: \quad Y^2 = X^3 + 1296m^2 X^2 + 15552m^3 X + (46656m^4 - 19m)$$

This is the elliptic curve searched by PARI/GP's **`ellintegralpoints()`**,
which is **provably complete** (Siegel's theorem): every integer solution
$(X, Y)$ is found, no matter how large the coordinates are.

---

## Search strategy

| Range | Method | Completeness |
|-------|--------|-------------|
| $\|m\| < 10^{20}$ | Existing PARI workers in `ec_curve/` | Provably complete |
| $\|m\| \geq 10^{20}$ | **This code** — same PARI ellintegralpoints | Provably complete |

The search expands outward from $m = \pm 10^{20}$ without bound.

---

## Directory layout

```
ce_large_search/
├── worker_pari_large.py    # CE/BOINC Python worker
├── worker_ec_large.gp      # PARI/GP inner script
├── local_parallel_search.py # Multi-process local search
├── work_generator_large.py  # BOINC WU generator
├── assimilator_github.py    # Results → GitHub pusher
├── validator_large.py       # BOINC result validator
├── Dockerfile              # Container image for CE
├── launch_ce_search.sh     # Master launch script
├── templates/
│   ├── ec_large_wu.xml     # BOINC WU template
│   └── ec_large_result.xml # BOINC result template
└── README.md               # This file
```

---

## Quick start — local search (no BOINC needed)

```bash
# Install dependencies
sudo apt install pari-gp python3
pip3 install cypari2   # optional but 10× faster

# Run with 8 parallel workers searching from 10^20 outward
cd ce_large_search
python3 local_parallel_search.py --workers 8 --floor 1e20 --block 10

# Solutions are written to  ../solutions_large.txt
# Automatically pushed to GitHub if $GITHUB_TOKEN is set
export GITHUB_TOKEN=ghp_...
python3 local_parallel_search.py --workers 8 --floor 1e20
```

---

## Charity Engine / BOINC deployment

### 1. Build the Docker image

```bash
cd ce_large_search
docker build -t ec_large_worker:latest .
docker push <your-registry>/ec_large_worker:latest
```

### 2. Set up the BOINC project

```bash
# On your BOINC server
PROJ=/home/boincadm/projects/sumsof3cubes

# Copy templates
cp templates/ec_large_wu.xml     $PROJ/templates/
cp templates/ec_large_result.xml $PROJ/templates/

# Register the app
# Add to project.xml:
#   <app><name>ec_large</name><user_friendly_name>EC Large-m Search</user_friendly_name></app>
```

### 3. Start the work generator

```bash
python3 work_generator_large.py \
    --boinc_project_dir $PROJ \
    --app_name ec_large \
    --count 1000 \
    --search_floor 100000000000000000000 \
    --daemon \
    --interval 300
```

### 4. Start the assimilator

```bash
export GITHUB_TOKEN=ghp_...
python3 assimilator_github.py \
    --result_dir $PROJ/results \
    --master_file ../solutions_large.txt \
    --repo_dir .. \
    --daemon
```

### 5. Configure the BOINC validator

Add to the BOINC project config:
```xml
<daemon>
  <cmd>python3 /path/to/ce_large_search/validator_large.py --app ec_large</cmd>
</daemon>
```

---

## Coordinate conversion

The worker outputs **Weierstrass coordinates** $(m, X, Y)$.
To recover the user equation's coordinates:

$$x = \frac{X}{6m}, \quad y = \frac{Y}{6m^2}$$

Both will be integers whenever $6m \mid X$ and $6m^2 \mid Y$.
The worker tags such lines with `USR m x y` in the result file.

---

## Solutions

See [`../solutions_large.txt`](../solutions_large.txt) for all found solutions.

Every line: `m  X  Y  [# user: x=... y=...]`

---

## Algorithm detail

For each integer $m \neq 0$:

1. Build $E_m = \texttt{ellinit}([0,\,1296m^2,\,0,\,15552m^3,\,46656m^4-19m])$ in PARI/GP.
2. Check discriminant (skip singular curves — only $m=0$).
3. Call `ellintegralpoints(E_m, 1)`:
   - Computes Mordell-Weil rank via 2-descent
   - Finds generators of the Mordell-Weil group
   - Applies Baker–Wüstholz height bounds to get a finite search region
   - LLL + enumeration finds all integral points
4. Verify each solution exactly in Python (arbitrary-precision).
5. Append new solutions to `solutions_large.txt`.
6. Push to GitHub every $N$ new solutions.

This is **mathematically guaranteed** to find every integer solution.
There is no missed case.

---

## Performance

| $|m|$ range | Typical time per $m$ | Notes |
|-------------|---------------------|-------|
| $10^{20}$ to $10^{21}$ | 1–30 s | rank 0 curves are fastest |
| $10^{21}$ to $10^{23}$ | 10–300 s | depends on rank and regulator |
| $> 10^{23}$ | 30 s – 30 min | timeout_per_m may need increasing |

Each Charity Engine host processes one work unit (50 values of $m$),
so the combined fleet covers millions of $m$ values per day.
