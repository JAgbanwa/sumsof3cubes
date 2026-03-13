#!/usr/bin/env python3
"""
work_generator.py  —  Charity Engine / BOINC Work Generator

Equation:
    y² = x³ + 1296·n²·x² + 15552·n³·x + (46656·n⁴ − 19·n)

Generates work units (WUs) that partition integer n-space.
Each WU is submitted to BOINC/CE and processed by worker_ec (C brute-force)
or worker_pari (PARI/GP exact).  Both workers are registered; BOINC
distributes based on platform capability.

WU file content:
    n_start   <int>
    n_end     <int>
    x_limit   <int>    (for C worker)
    batch     <int>    (for PARI worker: n values per GP call)

Usage:
    # Standalone (writes WU files to ./workunit_queue/):
    python3 work_generator.py --mode standalone --total 1000000

    # BOINC server (calls `bin/create_work`):
    python3 work_generator.py --mode boinc \
        --project_dir /home/boincadm/projects/ec_curve

Configuration constants below.
"""

import os
import sys
import time
import sqlite3
import argparse
import subprocess
import json
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════

APP_NAME        = "ec_curve"
APP_VERSION     = "1.00"
WU_SIZE         = 500             # number of consecutive n values per WU
X_LIMIT         = 50_000_000      # C worker: |x| search bound
PARI_BATCH      = 10              # PARI worker: n values per gp call
FANOUT          = 2               # redundant copies for Byzantine tolerance
DELAY_BOUND     = 86_400 * 7      # 7-day deadline
MAX_OUTSTANDING = 5_000           # max in-flight WUs before pausing

DB_PATH         = "work_generator_state.db"
QUEUE_DIR       = Path("workunit_queue")

# ══════════════════════════════════════════════════════════════════════
# State database
# ══════════════════════════════════════════════════════════════════════

def db_init(db_path: str):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS wu_state (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            direction   TEXT    NOT NULL,    -- 'pos' or 'neg'
            n_start     INTEGER NOT NULL,
            n_end       INTEGER NOT NULL,
            x_limit     INTEGER NOT NULL,
            sent_at     REAL,
            status      TEXT DEFAULT 'sent'  -- 'sent','done','error'
        )
    """)
    conn.commit()
    return conn


def db_next_ranges(conn, count: int = 20):
    """Return the next `count` (direction, n_start, n_end) pairs to submit."""
    c = conn.cursor()

    c.execute("SELECT MAX(n_end) FROM wu_state WHERE direction='pos'")
    row = c.fetchone()
    pos_max = row[0] if row[0] is not None else -1

    c.execute("SELECT MIN(n_start) FROM wu_state WHERE direction='neg'")
    row = c.fetchone()
    neg_min = row[0] if row[0] is not None else 1

    ranges = []
    half = count // 2 + 1
    for _ in range(half):
        # positive direction
        ns = pos_max + 1
        ne = ns + WU_SIZE - 1
        ranges.append(('pos', ns, ne))
        pos_max = ne
        # negative direction (mirror)
        ne_n = neg_min - 1
        ns_n = ne_n - WU_SIZE + 1
        ranges.append(('neg', ns_n, ne_n))
        neg_min = ns_n

    return ranges[:count]


def db_record(conn, direction, n_start, n_end):
    c = conn.cursor()
    c.execute("""
        INSERT INTO wu_state (direction, n_start, n_end, x_limit, sent_at)
        VALUES (?, ?, ?, ?, ?)
    """, (direction, n_start, n_end, X_LIMIT, time.time()))
    conn.commit()


def db_outstanding(conn) -> int:
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM wu_state WHERE status='sent'")
    return c.fetchone()[0]

# ══════════════════════════════════════════════════════════════════════
# WU file creation (standalone mode)
# ══════════════════════════════════════════════════════════════════════

def write_wu_file(wu_dir: Path, n_start: int, n_end: int, wu_id: str) -> Path:
    wu_dir.mkdir(parents=True, exist_ok=True)
    p = wu_dir / f"wu_{wu_id}.txt"
    p.write_text(
        f"n_start  {n_start}\n"
        f"n_end    {n_end}\n"
        f"x_limit  {X_LIMIT}\n"
        f"batch    {PARI_BATCH}\n"
    )
    return p


# ══════════════════════════════════════════════════════════════════════
# BOINC submission via `bin/create_work`
# ══════════════════════════════════════════════════════════════════════

def boinc_submit(project_dir: str, wu_file: Path, wu_name: str):
    """Submit one WU via BOINC's create_work utility."""
    tmpl_in  = "templates/ec_curve_wu"
    tmpl_out = "templates/ec_curve_result"
    cmd = [
        f"{project_dir}/bin/create_work",
        "--appname",       APP_NAME,
        "--wu_name",       wu_name,
        "--wu_template",   tmpl_in,
        "--result_template", tmpl_out,
        "--delay_bound",   str(DELAY_BOUND),
        "--fanout",        str(FANOUT),
        str(wu_file),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[gen] create_work failed: {result.stderr}", file=sys.stderr)
        return False
    return True


# ══════════════════════════════════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════════════════════════════════

def run(args):
    conn = db_init(DB_PATH)
    QUEUE_DIR.mkdir(exist_ok=True)

    print(f"[gen] Work generator started  mode={args.mode}"
          f"  wu_size={WU_SIZE}  x_limit={X_LIMIT:,}")

    generated = 0
    while True:
        outstanding = db_outstanding(conn)
        if outstanding >= MAX_OUTSTANDING:
            print(f"[gen] {outstanding} WUs in flight, sleeping 60s …")
            time.sleep(60)
            continue

        need = min(20, MAX_OUTSTANDING - outstanding)
        ranges = db_next_ranges(conn, count=need)

        for direction, n_start, n_end in ranges:
            wu_id   = f"{direction}_{n_start}_{n_end}"
            wu_file = write_wu_file(QUEUE_DIR, n_start, n_end, wu_id)

            if args.mode == "boinc":
                ok = boinc_submit(args.project_dir, wu_file, wu_id)
                if not ok:
                    continue

            db_record(conn, direction, n_start, n_end)
            generated += 1
            print(f"[gen] WU #{generated:>6}  n=[{n_start:>12},{n_end:>12}]"
                  f"  dir={direction}")

            if args.total > 0 and generated >= args.total // WU_SIZE:
                print(f"[gen] Generated {generated} WUs, stopping.")
                return

        if args.mode == "standalone":
            # in standalone mode, don't loop forever; the dispatcher
            # script (run_local.py) picks up the WU files
            time.sleep(1)
        else:
            time.sleep(30)


# ══════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="Work generator for the ec_curve Charity Engine project")
    ap.add_argument("--mode", choices=["standalone", "boinc"],
                    default="standalone",
                    help="'standalone' writes WU files; 'boinc' submits to BOINC")
    ap.add_argument("--project_dir", default="/home/boincadm/projects/ec_curve",
                    help="BOINC project root (only needed for --mode boinc)")
    ap.add_argument("--total", type=int, default=0,
                    help="Stop after generating this many n values (0=forever)")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
