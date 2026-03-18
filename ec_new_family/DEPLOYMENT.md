# ec_new_family — Charity Engine / BOINC Deployment Guide

## What this project searches

Integer solutions $(n, X, y) \in \mathbb{Z}^3$ where

$$E_n : y^2 = X^3 + a_4(n)\,X + a_6(n), \quad X \neq -3888\,n^2$$

$$a_4(n) = -45\,349\,632\,n^4 + 419\,904\,n^3, \quad
   a_6(n) = 3(39\,182\,082\,048\,n^6 - 544\,195\,584\,n^5 + 1\,259\,712\,n^4 - 19n)$$

This is a **short Weierstrass** elliptic curve family; no mathematical restriction
on which $n$ values can yield solutions is known yet (unlike `ec_curve/`, which
was restricted by an $n \mid 19$ argument).  We therefore search *all* $n \in [1, N_{HI}]$.

---

## Scale

| Search range | WUs | ~h/WU (volunteer) | ETA @ 10k CPUs |
|---|---|---|---|
| $n=1..500$, $|X| \leq 10^{12}$ | ~100,000 | ~4 h | ~40 h |
| $n=1..500$, $|X| \leq 10^{15}$ | ~100,000,000 | ~4 h | ~40,000 h |

Default run: $N_{HI} = 500$, $X_{MAX} = 10^{12}$, $X_{BLOCK} = 10^{10}$ per WU.

---

## Files

| File | Purpose |
|------|---------|
| `worker_ec.c` | C worker (QR sieve + `__int128`); dual CLI / BOINC mode |
| `boinc_queue.py` | **Main queue manager** — frontier-based WU generation |
| `assimilator.py` | Verifies results, records solutions, marks WUs done |
| `validator.py` | Cross-validates two result copies |
| `setup_boinc_project.sh` | One-shot server setup (Debian/Ubuntu) |
| `templates/ec_nf_wu` | BOINC WU input template |
| `templates/ec_nf_result` | BOINC result output template |
| `ec_nf_queue.db` | SQLite frontier DB (auto-created by `init`) |
| `wu_queue/` | Exported WU files for local testing |
| `output/solutions.txt` | Accumulated solutions (`n X y` per line) |

---

## Quick start (Charity Engine / live BOINC server)

### 1. Provision a server

Any Debian 11/12 or Ubuntu 22.04/24.04 VPS — 2 CPU, 4 GB RAM is sufficient
for the server itself; volunteers do all computation.

Register at [Charity Engine](https://www.charity-engine.com/) and request a
BOINC project slot, or self-host using the setup script.

### 2. Upload project files

```bash
scp -r ec_new_family/ user@your-server:~/ec_nf_src/
```

### 3. Run the setup script (as root on the server)

```bash
sudo bash ~/ec_nf_src/setup_boinc_project.sh --host ec-nf.yourdomain.com
```

This installs BOINC server software (+MySQL), compiles the BOINC-linked worker,
registers the app, writes daemon config, initialises the WU queue DB for
$n = 1..500$, and starts all daemons.

### 4. Initialise the WU queue DB (already done by setup script)

To run manually, or to reset / extend the range:

```bash
python3 /home/boincadm/projects/ec_nf/boinc_queue.py init --n-hi 1000
```

### 5. The submission daemon runs automatically

`config.xml` starts:

```
python3 boinc_queue.py submit --project_dir /home/boincadm/projects/ec_nf
```

It maintains up to **5 000 WUs in-flight** at all times, submitting 500 per
90-second cycle as volunteers consume them.

### 6. Monitor progress

```bash
python3 /home/boincadm/projects/ec_nf/boinc_queue.py status
```

Example output:

```
     n      done   in-flight    remaining         x_frontier       % done
  ──────────────────────────────────────────────────────────────────────────
     1       150        200         49750   +0000001500000000000    0.30%
     2       150        200         49750   +0000001500000000000    0.30%
   ...
  ──────────────────────────────────────────────────────────────────────────
   TOT     75000       3200      24,922,000                        0.30%
```

### 7. Collect solutions

```bash
cat /home/boincadm/projects/ec_nf/output/solutions.txt
```

Format: `n  X  y`

---

## Maintenance

### After server downtime

Submitted WUs not returned within 48 hours are automatically re-queued.
To force immediate re-queue of stuck WUs:

```bash
python3 boinc_queue.py reset_stuck --stuck_hours 24
```

### Extend the search range

Edit `X_MAX` and/or `N_HI` at the top of `boinc_queue.py`, then:

```bash
python3 boinc_queue.py init --n-hi 2000
```

New $n$-values are added; existing frontier rows are untouched.

---

## Standalone testing (no BOINC server)

```bash
# Build standalone worker
make

# Export 100 WU files (for testing)
python3 boinc_queue.py init --n-hi 20
python3 boinc_queue.py export --limit 100

# Run one WU manually
./worker_ec wu_queue/ec_nf_n+001_x*.txt result.txt ckpt.txt
cat result.txt   # empty = no solutions in block

# Run all exported WUs via local_search.py (8 workers)
python3 local_search.py --workers 8
```

---

## Worker binary

Build standalone (no BOINC):

```bash
gcc -O3 -march=native -std=c11 -o worker_ec worker_ec.c -lm
```

Build BOINC-linked (requires BOINC SDK):

```bash
gcc -O3 -march=native -std=c11 -DBOINC \
    -I/path/to/boinc/api -I/path/to/boinc/lib \
    -o worker_ec_boinc worker_ec.c \
    -L/path/to/boinc/api -L/path/to/boinc/lib \
    -lm -lboinc_api -lboinc
```

Or simply `make boinc BOINC_DIR=/path/to/boinc`.

### Expected throughput

After the QR sieve (~85% rejection on 10 primes), the effective pass-through
is ~15% of $X$ values that actually need an isqrt128 check:

| Core clock | Raw speed | After sieve | WU time ($10^{10}$ X) |
|---|---|---|---|
| Volunteer ~1 GHz equiv | ~1.5 M x/s | ~225 k isqrt/s | ~12 h |
| Modern desktop 3 GHz | ~10 M x/s | ~1.5 M isqrt/s | ~1.8 h |

$X_{BLOCK} = 10^{10}$ targets **2–4 h** on a typical Charity Engine volunteer.

---

## Relationship to Charity Engine

[Charity Engine](https://www.charity-engine.com/) is a BOINC-based volunteer
computing network.  Any BOINC-compatible application can be run on it once a
project slot is approved.  The `setup_boinc_project.sh` script produces a
fully BOINC-compliant project that can be submitted directly.

Steps to get on Charity Engine:
1. Set up your own BOINC server (script handles this)
2. Register your project URL with Charity Engine
3. Charity Engine adds your project to its network of volunteers

All volunteer computation is handled by the server's daemon pipeline:
`boinc_queue.py submit` → BOINC dispatches → `validator.py` cross-checks →
`assimilator.py` records results.
