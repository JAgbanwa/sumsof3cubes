#!/usr/bin/env python3
"""
ec19n_work_generator.py  —  BOINC work generator for ec19n project.

Generates work units covering the 4 valid n values {1,-1,19,-19} and
partitions the x-axis into blocks.

Mathematically proved: ONLY n ∈ {1,-1,19,-19} can satisfy y/(6n) ∈ ℤ
for  y² = x³ + 1296n²x² + 15552n³x + (46656n⁴ - 19n).

Work Unit format (wu.txt fed to ec19n_worker):
    n       <n>
    x_start <x0>
    x_end   <x1>

Mode:
    --mode boinc     : call bin/create_work (BOINC server)
    --mode standalone: write WU files to queue/ directory
    --status         : print sweep progress

State stored in ec19n_state.db (SQLite).
"""

import argparse, os, sys, sqlite3, subprocess, time
from pathlib import Path

BASE_DIR   = Path(__file__).parent
STATE_DB   = BASE_DIR / "ec19n_state.db"
QUEUE_DIR  = BASE_DIR / "wu_queue"
RESULT_DIR = BASE_DIR / "output"
TMPLS_DIR  = BASE_DIR / "templates"
APP_NAME   = "ec19n"

# ── Search parameters ──────────────────────────────────────────────────────
VALID_N      = [1, -1, 19, -19]
X_BLOCK      = 10**12        # x-range per work unit (~8-24h per volunteer CPU)
X_MAX        = 10**17        # maximum |x| to search (expandable)
FANOUT       = 2             # redundancy per WU
MAX_PENDING  = 10000         # max unvalidated WUs before pausing
FPOPS_EST    = 5e13          # estimated FLOP for rsc_fpops_est

# ── DB schema ─────────────────────────────────────────────────────────────

def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(STATE_DB)
    db.execute("""
        CREATE TABLE IF NOT EXISTS sweep (
            n         INTEGER NOT NULL,
            x_start   INTEGER NOT NULL,
            x_end     INTEGER NOT NULL,
            state     TEXT NOT NULL DEFAULT 'pending',
            wu_name   TEXT,
            created   REAL,
            PRIMARY KEY (n, x_start)
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_state ON sweep(state)")
    db.commit()
    return db


def next_x_start(db: sqlite3.Connection, n: int) -> int:
    """Return the next x_start not yet enqueued for this n."""
    row = db.execute(
        "SELECT MAX(x_end) FROM sweep WHERE n=?", (n,)).fetchone()
    max_x_end = row[0]
    if max_x_end is None:
        # Start from -X_MAX
        return -X_MAX
    return max_x_end + 1


def enqueue(db: sqlite3.Connection, n: int, x0: int, x1: int,
            wu_name: str = "") -> None:
    db.execute(
        "INSERT OR IGNORE INTO sweep(n,x_start,x_end,state,wu_name,created)"
        " VALUES(?,?,?,'pending',?,?)",
        (n, x0, x1, wu_name, time.time()))
    db.commit()


def count_pending(db: sqlite3.Connection) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM sweep WHERE state='pending'").fetchone()[0]


# ── WU file writers ────────────────────────────────────────────────────────

def wu_content(n: int, x0: int, x1: int) -> str:
    return f"n {n}\nx_start {x0}\nx_end {x1}\n"


def write_standalone_wu(db: sqlite3.Connection, n: int, x0: int, x1: int) -> str:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ec19n_n{n:+d}_x{x0:+d}_{x1:+d}"
    wu_path = QUEUE_DIR / f"{name}.txt"
    wu_path.write_text(wu_content(n, x0, x1))
    enqueue(db, n, x0, x1, wu_name=name)
    return name


def submit_boinc_wu(db: sqlite3.Connection, n: int, x0: int, x1: int,
                    project_dir: str) -> str:
    name = f"ec19n_n{n:+d}_x{x0:+d}_{x1:+d}_{int(time.time())}"
    wu_file = f"/tmp/{name}_wu.txt"
    with open(wu_file, "w") as f:
        f.write(wu_content(n, x0, x1))

    cmd = [
        os.path.join(project_dir, "bin", "create_work"),
        "--appname",       APP_NAME,
        "--wu_name",       name,
        "--wu_template",   str(TMPLS_DIR / "ec19n_wu"),
        "--result_template", str(TMPLS_DIR / "ec19n_result"),
        "--rsc_fpops_est", str(FPOPS_EST),
        "--rsc_memory_bound", "536870912",
        "--delay_bound",   "604800",
        "--min_quorum",    str(FANOUT),
        "--target_nresults", str(FANOUT),
        wu_file,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [WARN] create_work failed: {r.stderr.strip()[:200]}")
        return ""
    os.unlink(wu_file)
    enqueue(db, n, x0, x1, wu_name=name)
    return name


# ── Main generator loop ────────────────────────────────────────────────────

def generate(mode: str, project_dir: str, max_new: int) -> None:
    db = open_db()
    created = 0

    for n in VALID_N:
        x0 = next_x_start(db, n)
        while x0 <= X_MAX and created < max_new:
            x1 = min(x0 + X_BLOCK - 1, X_MAX)
            if mode == "boinc":
                name = submit_boinc_wu(db, n, x0, x1, project_dir)
            else:
                name = write_standalone_wu(db, n, x0, x1)
            if name:
                print(f"  WU  n={n:>4}  x=[{x0:+d},{x1:+d}]  {name[:60]}")
                created += 1
            x0 = x1 + 1

    db.close()
    print(f"\nGenerated {created} new WUs.")


def print_status() -> None:
    if not STATE_DB.exists():
        print("No state DB found.")
        return
    db = open_db()
    print(f"{'n':>6}  {'pending':>8}  {'done':>8}  {'x_covered':>22}")
    for n in VALID_N:
        rows = db.execute(
            "SELECT state, COUNT(*), MIN(x_start), MAX(x_end)"
            " FROM sweep WHERE n=? GROUP BY state", (n,)).fetchall()
        stats = {r[0]: (r[1], r[2], r[3]) for r in rows}
        p = stats.get("pending", (0, None, None))
        d = stats.get("done",    (0, None, None))
        total_done = d[0] * X_BLOCK
        x_hi = d[2] if d[2] else "—"
        print(f"  n={n:>4}  pending={p[0]:>6}  done={d[0]:>6}  "
              f"x_max_done={x_hi}")
    db.close()


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="ec19n BOINC work generator")
    ap.add_argument("--mode", default="standalone",
                    choices=["standalone", "boinc"],
                    help="standalone: write WU files; boinc: call create_work")
    ap.add_argument("--project_dir", default="/home/boincadm/projects/ec19n",
                    help="BOINC project root (boinc mode only)")
    ap.add_argument("--max_new", type=int, default=100,
                    help="Max new WUs to generate per run")
    ap.add_argument("--status", action="store_true",
                    help="Print sweep status and exit")
    args = ap.parse_args()

    if args.status:
        print_status()
        return

    generate(args.mode, args.project_dir, args.max_new)


if __name__ == "__main__":
    main()
