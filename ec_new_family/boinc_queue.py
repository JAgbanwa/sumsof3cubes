#!/usr/bin/env python3
"""
boinc_queue.py  —  BOINC / Charity Engine work-queue manager for ec_new_family.

Equation searched:
    E_n : y² = X³ + a4(n)·X + a6(n),   X ≠ -3888·n²
    a4(n) = -45349632·n⁴ + 419904·n³
    a6(n) = 3·(39182082048·n⁶ - 544195584·n⁵ + 1259712·n⁴ - 19·n)

Architecture: FRONTIER-BASED
─────────────────────────────────────────────────────────────────────
For each n-value in [N_LO, N_HI], the DB tracks how far into [x_min(n), X_MAX]
we have submitted work units.  The submission daemon advances the frontier as
volunteers consume WUs, maintaining up to MAX_PENDING WUs in-flight at all times.

WU sizing:
    Target ~4 h on a typical Charity Engine volunteer core (~1–2 M x/s after sieve).
    X_BLOCK = 10^10 gives ~1.4–2.8 h on a 2M x/s core.

Total scale (N_HI = 500, X_MAX = 10^12):
    Each n has ~2·X_MAX / X_BLOCK = 200 WUs  →  500 × 200 = 100,000 WUs total
    At 100,000 CPUs: ~1,000 h total/CPU = ~0.01 h = well within reason
    Realistic: 10,000 CPUs × 100,000 WUs × 4 h / 10,000 = ~40 h

Commands
────────
  init          Create (or reset) the DB.
  submit        Daemon: fill BOINC queue to MAX_PENDING, replenish every 90 s.
  export        Write WU files to wu_queue/ without a BOINC server (standalone test).
  status        Print per-n progress and estimated completion.
  reset_stuck   Re-open 'submitted' WUs older than --stuck_hours.
  mark_done     Called by assimilator: record a completed WU.
"""

import argparse
import math
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# ── Project layout ───────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
STATE_DB  = BASE_DIR / "ec_nf_queue.db"
QUEUE_DIR = BASE_DIR / "wu_queue"
TMPLS_DIR = BASE_DIR / "templates"
APP_NAME  = "ec_nf"

# ── Search parameters ────────────────────────────────────────────────────────
N_LO    = 1
N_HI    = 500           # default; override with --n-hi
X_MAX   = 10**12        # search X ∈ [x_min(n), X_MAX] per n
X_BLOCK = 10_000_000_000   # 10^10 x-values per WU  (~2–4 h volunteer)

# ── BOINC parameters ─────────────────────────────────────────────────────────
FANOUT        = 2            # result copies per WU
MAX_PENDING   = 5_000
BATCH_SIZE    = 500
POLL_INTERVAL = 90           # seconds
FPOPS_EST     = 1.4e13       # flop estimate per WU (for BOINC credit)
RAM_BOUND     = 64 * 2**20   # 64 MB

# ── Coefficient helpers (Python integers, exact) ────────────────────────────

def a4(n: int) -> int:
    return -45349632 * n**4 + 419904 * n**3

def a6(n: int) -> int:
    return 3 * (39182082048 * n**6
                - 544195584 * n**5
                + 1259712   * n**4
                - 19        * n)

def x_min(n: int) -> int:
    """Lower bound: smallest X where X³ + a4(n)·X + a6(n) ≥ 0."""
    a4n = a4(n); a6n = a6(n)
    if a6n > 0:
        xf = -(a6n ** (1/3)) - 50
    elif a6n < 0:
        xf = ((-a6n) ** (1/3)) + 50
    else:
        xf = -1e6
    # Newton refinement
    for _ in range(200):
        fv = xf**3 + a4n * xf + a6n
        dv = 3*xf**2 + a4n
        if abs(dv) < 1e-30:
            break
        dx = fv / dv; xf -= dx
        if abs(dx) < 0.5:
            break
    lb = int(math.floor(xf)) - 10
    # Exact correction
    def rhs(x): return x**3 + a4n*x + a6n
    while rhs(lb) >= 0:
        lb -= 1000
    while rhs(lb + 1) < 0:
        lb += 1
    return lb + 1

# ── DB schema ────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier (
    n              INTEGER PRIMARY KEY,
    x_start        INTEGER NOT NULL,    -- x_min(n): the natural start
    x_next         INTEGER NOT NULL,    -- next x to submit
    x_max          INTEGER NOT NULL,    -- X_MAX
    x_block        INTEGER NOT NULL,    -- X_BLOCK
    wus_submitted  INTEGER NOT NULL DEFAULT 0,
    wus_done       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inflight (
    wu_id   TEXT    PRIMARY KEY,
    n       INTEGER NOT NULL,
    x_lo    INTEGER NOT NULL,
    x_hi    INTEGER NOT NULL,
    submitted_at INTEGER NOT NULL
);
"""

def get_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ── WU file helpers ──────────────────────────────────────────────────────────

def wu_id(n: int, x_lo: int) -> str:
    return f"{APP_NAME}_n{n:+d}_x{x_lo:+024d}"

def write_wu_file(n: int, x_lo: int, x_hi: int, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / (wu_id(n, x_lo) + ".txt")
    with open(path, "w") as f:
        f.write(f"n       {n}\n")
        f.write(f"x_start {x_lo}\n")
        f.write(f"x_end   {x_hi}\n")
    return path

def submit_wu_to_boinc(wu_file: Path, project_dir: str) -> bool:
    """Call BOINC create_work to inject one WU.  Returns True on success."""
    tmpl_in  = str(TMPLS_DIR / "ec_nf_wu")
    tmpl_out = str(TMPLS_DIR / "ec_nf_result")
    wuid     = wu_file.stem

    cmd = [
        "create_work",
        "--project_dir", project_dir,
        "--appname", APP_NAME,
        "--wu_name", wuid,
        "--wu_template", tmpl_in,
        "--result_template", tmpl_out,
        "--rsc_fpops_est", str(FPOPS_EST),
        "--rsc_fpops_bound", str(FPOPS_EST * 50),
        "--rsc_memory_bound", str(RAM_BOUND),
        "--delay_bound", str(86400 * 7),   # 7 days
        str(wu_file),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        print(f"  [warn] create_work failed for {wuid}: {e}", file=sys.stderr)
        return False


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_init(args):
    """Populate the frontier table for n in [N_LO, args.n_hi]."""
    n_hi = args.n_hi
    conn = get_db(STATE_DB)
    cur  = conn.cursor()
    added = 0
    for n in range(N_LO, n_hi + 1):
        xstart = x_min(n)
        cur.execute("""
            INSERT OR IGNORE INTO frontier
                (n, x_start, x_next, x_max, x_block)
            VALUES (?, ?, ?, ?, ?)
        """, (n, xstart, xstart, X_MAX, X_BLOCK))
        added += cur.rowcount
    conn.commit()
    conn.close()
    print(f"Initialised frontier for n=1..{n_hi}  ({added} new rows).")
    print(f"Total WUs (estimate): {sum(max(0, (X_MAX - x_min(n)) // X_BLOCK + 1) for n in range(1, n_hi+1)):,}")


def cmd_submit(args):
    """Daemon: maintain MAX_PENDING WUs in flight."""
    project_dir = args.project_dir
    print(f"[submit daemon] project={project_dir}  max_pending={MAX_PENDING}")
    QUEUE_DIR.mkdir(exist_ok=True)

    while True:
        conn   = get_db(STATE_DB)
        now    = int(time.time())
        # Count currently in-flight
        (in_flight,) = conn.execute(
            "SELECT COUNT(*) FROM inflight").fetchone()

        slots = MAX_PENDING - in_flight
        if slots <= 0:
            conn.close()
            time.sleep(POLL_INTERVAL)
            continue

        submitted = 0
        rows = conn.execute("""
            SELECT n, x_next, x_max, x_block
            FROM frontier
            WHERE x_next <= x_max
            ORDER BY n
            LIMIT ?
        """, (min(slots, BATCH_SIZE),)).fetchall()

        for (n, x_next, x_max, x_block) in rows:
            x_lo = x_next
            x_hi = min(x_lo + x_block - 1, x_max)
            wf   = write_wu_file(n, x_lo, x_hi, QUEUE_DIR)
            if submit_wu_to_boinc(wf, project_dir):
                conn.execute(
                    "UPDATE frontier SET x_next=?, wus_submitted=wus_submitted+1 WHERE n=?",
                    (x_hi + 1, n))
                conn.execute(
                    "INSERT OR REPLACE INTO inflight VALUES (?,?,?,?,?)",
                    (wu_id(n, x_lo), n, x_lo, x_hi, now))
                submitted += 1
            else:
                wf.unlink(missing_ok=True)

        conn.commit()
        conn.close()
        if submitted:
            print(f"[{time.strftime('%H:%M:%S')}] submitted {submitted} WUs  "
                  f"(in-flight ~{in_flight + submitted})")
        time.sleep(POLL_INTERVAL)


def cmd_export(args):
    """Write WU files to wu_queue/ without BOINC (standalone testing)."""
    limit = args.limit
    conn  = get_db(STATE_DB)
    QUEUE_DIR.mkdir(exist_ok=True)
    rows  = conn.execute("""
        SELECT n, x_next, x_max, x_block
        FROM frontier
        WHERE x_next <= x_max
        ORDER BY n
        LIMIT ?
    """, (limit,)).fetchall()
    count = 0
    for (n, x_next, x_max, x_block) in rows:
        x_lo = x_next; x_hi = min(x_lo + x_block - 1, x_max)
        write_wu_file(n, x_lo, x_hi, QUEUE_DIR)
        conn.execute("UPDATE frontier SET x_next=? WHERE n=?", (x_hi + 1, n))
        count += 1
    conn.commit(); conn.close()
    print(f"Exported {count} WU files to {QUEUE_DIR}/")


def cmd_status(args):
    """Print per-n progress and ETA."""
    conn = get_db(STATE_DB)
    rows = conn.execute("""
        SELECT n, x_start, x_next, x_max, x_block, wus_submitted, wus_done
        FROM frontier ORDER BY n
    """).fetchall()
    if not rows:
        print("No frontier data. Run: python3 boinc_queue.py init")
        conn.close(); return

    (in_flight,) = conn.execute("SELECT COUNT(*) FROM inflight").fetchone()

    total_done = total_rem = 0
    print(f"{'n':>6}  {'done':>8}  {'in-flight':>9}  {'remaining':>12}  "
          f"{'x_frontier':>22}  {'% done':>7}")
    print("─" * 70)
    for (n, x_start, x_next, x_max, x_block, wus_sub, wus_done) in rows:
        wus_total = max(1, (x_max - x_start) // x_block + 1)
        remaining = max(0, (x_max - x_next) // x_block + 1)
        pct = 100.0 * wus_done / wus_total
        print(f"{n:>6}  {wus_done:>8,}  {in_flight:>9}  "
              f"{remaining:>12,}  {x_next:>+22,}  {pct:>6.2f}%")
        total_done += wus_done; total_rem += remaining
    print("─" * 70)
    grand_total = total_done + total_rem
    gpct = 100.0 * total_done / grand_total if grand_total else 0
    print(f"{'TOT':>6}  {total_done:>8,}  {in_flight:>9}  "
          f"{total_rem:>12,}  {'':>22}  {gpct:>6.2f}%")
    conn.close()


def cmd_reset_stuck(args):
    """Re-open inflight WUs older than --stuck_hours back into the frontier."""
    cutoff = int(time.time()) - args.stuck_hours * 3600
    conn   = get_db(STATE_DB)
    stuck  = conn.execute(
        "SELECT wu_id, n, x_lo FROM inflight WHERE submitted_at < ?",
        (cutoff,)).fetchall()
    for (wuid, n, x_lo) in stuck:
        conn.execute(
            "UPDATE frontier SET x_next = MIN(x_next, ?) WHERE n = ?",
            (x_lo, n))
        conn.execute("DELETE FROM inflight WHERE wu_id = ?", (wuid,))
        print(f"  reset: {wuid}")
    conn.commit(); conn.close()
    print(f"Reset {len(stuck)} stuck WUs (older than {args.stuck_hours} h).")


def cmd_mark_done(args):
    """Called by the assimilator: mark one WU complete."""
    conn = get_db(STATE_DB)
    conn.execute(
        "UPDATE frontier SET wus_done = wus_done + 1 WHERE n = ?",
        (args.n,))
    conn.execute(
        "DELETE FROM inflight WHERE n = ? AND x_lo = ?",
        (args.n, args.x_lo))
    conn.commit(); conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="ec_new_family BOINC queue manager")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="Initialise frontier DB")
    pi.add_argument("--n-hi", type=int, default=N_HI,
                    help=f"Highest n to include (default {N_HI})")

    ps = sub.add_parser("submit", help="Submission daemon (needs BOINC server)")
    ps.add_argument("--project_dir", default="/home/boincadm/projects/ec_nf",
                    help="BOINC project directory on server")

    pe = sub.add_parser("export", help="Export WU files to wu_queue/ (standalone)")
    pe.add_argument("--limit", type=int, default=200)

    sub.add_parser("status", help="Show per-n progress")

    pr = sub.add_parser("reset_stuck", help="Re-queue stalled WUs")
    pr.add_argument("--stuck_hours", type=int, default=48)

    pm = sub.add_parser("mark_done", help="Mark WU done (called by assimilator)")
    pm.add_argument("n",    type=int)
    pm.add_argument("x_lo", type=int)

    args = p.parse_args()
    {"init": cmd_init, "submit": cmd_submit, "export": cmd_export,
     "status": cmd_status, "reset_stuck": cmd_reset_stuck,
     "mark_done": cmd_mark_done}[args.cmd](args)

if __name__ == "__main__":
    main()
