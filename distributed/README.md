# Distributed Search: m ∈ [10²⁰, 10³⁰]

Volunteer computing system for finding integer solutions to

```
m·y² = 36x³ + 36mx² + 12m²x + m³ − 19
```

with m in [10²⁰, 10³⁰], modelled after how **Charity Engine / BOINC**
distributes work — a central server issues work units, volunteers run the
computation, results are cross-validated before being accepted.

---

## How it works

```
                  ┌──────────────────────────────────────┐
                  │         Work Server (Flask)           │
                  │  • Holds queue of t-values [1..T_MAX] │
                  │  • Issues WUs via GET /api/work       │
                  │  • Collects results via POST /api/result│
                  │  • Requires N=2 agreements to confirm │
                  │  • Auto-pushes solutions to GitHub    │
                  └──────────┬─────────────────┬──────────┘
                             │                 │
               HTTP poll     │                 │ HTTP poll
                             ▼                 ▼
              ┌──────────────────┐   ┌──────────────────┐
              │  Volunteer A     │   │  Volunteer B      │
              │  worker.py       │   │  worker.py        │
              │  (any machine    │   │  (Docker / bare   │
              │   with Sage)     │   │   metal)          │
              └──────────────────┘   └──────────────────┘
```

**Math:** Substituting `t = y−m−6x` transforms the equation into
`m·t·(2m+12x+t) = 36x³−19`.  For each fixed `t`, the condition that the
discriminant is a perfect square is itself an **elliptic curve**:

```
Y² = X³ + 144t²·X² + 6912t⁴·X + 82944t²(t⁴−152t)
```

Sage's `integral_points()` finds **all** integer points on this curve in a
single call — no brute-force loop needed.  There are ~1850 valid t-values up
to t=10000; each is one work unit.

---

## Running the server

### Requirements
```bash
pip install flask
```

### Start
```bash
# Default: covers t ∈ [1, 10000], ~1850 work units
python3 server.py --host 0.0.0.0 --port 5555 --t-max 10000

# Larger coverage (more WUs, longer per WU):
python3 server.py --t-max 100000
```

### Configuration via environment variables

| Variable      | Default                   | Description |
|---------------|---------------------------|-------------|
| `WORK_KEY`    | `volunteer_key_change_me` | Shared secret for workers |
| `ADMIN_KEY`   | `admin_key_change_me`     | Admin endpoints |
| `N_CONFIRM`   | `2`                       | Independent results needed to confirm a WU |
| `T_MAX`       | `10000`                   | Maximum t-value |
| `GITHUB_DIR`  | parent of `distributed/`  | Repo dir for auto-push |

**Change `WORK_KEY` before sharing with volunteers.**

### Dashboard
Open `http://HOST:5555/` in a browser — shows progress bar, solutions, recent
worker activity.  Auto-refreshes every 30 seconds.

---

## Joining as a volunteer

### Option A – Docker (recommended, no Sage required)

```bash
docker run --rm \
  -e SERVER=http://HOST:5555 \
  -e KEY=volunteer_key_change_me \
  -e WORKERS=4 \
  ghcr.io/jagbanwa/sumsof3cubes-worker:latest
```

Or build locally:
```bash
git clone https://github.com/JAgbanwa/sumsof3cubes.git
cd sumsof3cubes/distributed
docker build -t cubes-worker .
docker run --rm -e SERVER=http://HOST:5555 -e KEY=... -e WORKERS=4 cubes-worker
```

Scale with docker-compose:
```bash
SERVER=http://HOST:5555 KEY=... docker-compose up --scale worker=8
```

### Option B – bare metal (needs SageMath ≥ 10.0)

```bash
git clone https://github.com/JAgbanwa/sumsof3cubes.git
cd sumsof3cubes/distributed
pip install requests
python3 worker.py \
    --server http://HOST:5555 \
    --key    volunteer_key_change_me \
    --workers 4        # number of parallel Sage processes
```

The worker detects your CPU count and defaults to `cpu_count - 1` parallel
processes automatically.

---

## Work unit details

Each WU is a single t-value.  The worker:
1. Builds the elliptic curve for that t.
2. Calls `E.integral_points()` — provably finds ALL integer points.
3. Back-transforms any points to `(x, m)` pairs.
4. Filters for `m ∈ [10²⁰, 10³⁰]` and verifies.
5. Posts `{wu_id, t_value, points, solutions}` JSON to the server.

Typical times per WU:
- Small t (t < 100): 3–10 min
- Medium t (100–1000): 5–30 min  
- Large t (1000–10000): 1–60 min

Memory: Sage typically uses 1–4 GB per process.

---

## API reference

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/work?key=K&worker_id=W` | WORK_KEY | Fetch a work unit |
| POST | `/api/result` (JSON body) | WORK_KEY in body | Submit result |
| GET | `/api/status` | none | Progress JSON |
| GET | `/` | none | HTML dashboard |
| POST | `/api/admin/reset_wu` | ADMIN_KEY | Requeue a WU by wu_id |
| POST | `/api/admin/set_t_max` | ADMIN_KEY | Expand T_MAX at runtime |

### Work unit response (GET /api/work)
```json
{
  "wu_id":     42,
  "assign_id": 17,
  "t_value":   83,
  "M_LO":      "100000000000000000000",
  "M_HI":      "1000000000000000000000000000000",
  "issued_at": 1741900000.0
}
```

### Result submission (POST /api/result)
```json
{
  "key":       "volunteer_key_change_me",
  "wu_id":     42,
  "assign_id": 17,
  "t_value":   83,
  "worker_id": "my-laptop",
  "points":    [[X1,Y1], [X2,Y2]],
  "solutions": [{"m":"...", "x":"...", "t":83, "y":"..."}],
  "elapsed_s": 127.3
}
```

---

## Security notes

- Change `WORK_KEY` and `ADMIN_KEY` before deploying publicly.
- The server **verifies every claimed solution** algebraically before accepting it.
- A WU is only marked confirmed when N_CONFIRM (default 2) independent workers
  agree — a single malicious/buggy result cannot inject a false solution.
- Results with failing algebraic verification are silently rejected and logged.

---

## Progress

Solutions found are written to:
- `distributed/solutions.txt` (local)
- `solutions_disc.txt` (repo root)
- Automatically committed and pushed to GitHub on each new solution.
