#!/usr/bin/env python3
"""
BOINC Work Generator for the sum-of-cubes searcher.

Produces work units (WUs) that partition the n-space.
Each WU covers a range [n_start, n_end] with a fixed x_limit.

Usage:
    python3 work_generator.py --boinc_project_dir /home/boincadm/projects/sumsof3cubes

Requires:
    pip install boinc_client  (or use subprocess to call bin/create_work)

WU file format:
    n_start <int>
    n_end   <int>
    x_limit <int>
"""

import os
import sys
import time
import sqlite3
import argparse
import subprocess
import shutil

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
APP_NAME         = "sumsof3cubes"
WU_SIZE          = 1_000          # number of n values per WU
X_LIMIT          = 10_000_000     # max |x| to search per n
FANOUT           = 2              # redundant copies per WU for validation
DELAY_BOUND      = 86400 * 7      # 7 days deadline
MAX_OUTSTANDING  = 2000           # max in-flight WUs

DB_PATH = "work_generator_state.db"

# ---------------------------------------------------------------------------
# State DB: track which n-ranges have been sent
# ---------------------------------------------------------------------------

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS wu_state (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            n_start     INTEGER NOT NULL,
            n_end       INTEGER NOT NULL,
            x_limit     INTEGER NOT NULL,
            sent_at     REAL,
            direction   TEXT     -- 'pos' or 'neg'
        )
    """)
    conn.commit()
    return conn

def next_ranges(conn, count=10):
    """Generate the next `count` (n_start, n_end) pairs to submit."""
    c = conn.cursor()
    c.execute("SELECT MAX(n_end) FROM wu_state WHERE direction='pos'")
    row = c.fetchone()
    pos_max = row[0] if row[0] is not None else -1

    c.execute("SELECT MIN(n_start) FROM wu_state WHERE direction='neg'")
    row = c.fetchone()
    neg_min = row[0] if row[0] is not None else 1

    ranges = []
    for _ in range(count // 2 + 1):
        # positive side
        ns = pos_max + 1
        ne = ns + WU_SIZE - 1
        ranges.append(('pos', ns, ne))
        pos_max = ne
        # negative side
        ne_n = neg_min - 1
        ns_n = ne_n - WU_SIZE + 1
        ranges.append(('neg', ns_n, ne_n))
        neg_min = ns_n

    return ranges[:count]

def record_sent(conn, direction, n_start, n_end, x_limit):
    c = conn.cursor()
    c.execute(
        "INSERT INTO wu_state (n_start, n_end, x_limit, sent_at, direction) "
        "VALUES (?,?,?,?,?)",
        (n_start, n_end, x_limit, time.time(), direction)
    )
    conn.commit()

# ---------------------------------------------------------------------------
# Create WU files and submit via create_work
# ---------------------------------------------------------------------------

def make_wu_file(wu_dir, wu_name, n_start, n_end, x_limit):
    path = os.path.join(wu_dir, wu_name)
    with open(path, "w") as f:
        f.write(f"n_start {n_start}\n")
        f.write(f"n_end   {n_end}\n")
        f.write(f"x_limit {x_limit}\n")
    return path

def submit_wu(project_dir, wu_name, wu_file):
    """Call BOINC create_work to submit a WU."""
    cmd = [
        os.path.join(project_dir, "bin", "create_work"),
        "--appname",       APP_NAME,
        "--wu_name",       wu_name,
        "--wu_template",   f"templates/{APP_NAME}_wu",
        "--result_template", f"templates/{APP_NAME}_result",
        "--min_quorum",    str(FANOUT),
        "--target_nresults", str(FANOUT),
        "--delay_bound",   str(DELAY_BOUND),
        wu_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[WARN] create_work failed for {wu_name}: {result.stderr.strip()}")
        return False
    return True

def count_outstanding(project_dir):
    """Query BOINC DB for number of unsent/in-progress results."""
    try:
        result = subprocess.run(
            ["mysql", "-u", "boincadm", "-e",
             f"USE {APP_NAME}; "
             f"SELECT COUNT(*) FROM result WHERE server_state IN (2,4);"],
            capture_output=True, text=True
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            return int(lines[-1].strip())
    except Exception as e:
        print(f"[WARN] DB query failed: {e}")
    return 0

# ---------------------------------------------------------------------------
# Main generator loop
# ---------------------------------------------------------------------------

def run(project_dir, wu_dir, dry_run=False):
    conn = init_db(DB_PATH)
    print(f"[work_generator] Starting. Project dir: {project_dir}")
    print(f"[work_generator] WU size: {WU_SIZE} | x_limit: {X_LIMIT:,}")

    while True:
        outstanding = count_outstanding(project_dir) if not dry_run else 0
        slots = MAX_OUTSTANDING - outstanding
        batch = min(slots, 20)

        if batch <= 0:
            print(f"[work_generator] Outstanding={outstanding}, sleeping 60s...")
            time.sleep(60)
            continue

        ranges = next_ranges(conn, count=batch)
        for (direction, n_start, n_end) in ranges:
            wu_name = f"{APP_NAME}_{direction}_{n_start}_{n_end}"
            wu_file = make_wu_file(wu_dir, wu_name + ".txt",
                                   n_start, n_end, X_LIMIT)
            if not dry_run:
                ok = submit_wu(project_dir, wu_name, wu_file)
            else:
                ok = True
                print(f"[dry_run] Would submit WU: {wu_name}")

            if ok:
                record_sent(conn, direction, n_start, n_end, X_LIMIT)
                print(f"[submitted] {wu_name}")

        time.sleep(5)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BOINC work generator")
    parser.add_argument("--boinc_project_dir",
                        default="/home/boincadm/projects/sumsof3cubes")
    parser.add_argument("--wu_dir", default="wu_files")
    parser.add_argument("--dry_run", action="store_true",
                        help="Print WUs without actually submitting")
    args = parser.parse_args()

    os.makedirs(args.wu_dir, exist_ok=True)
    run(args.boinc_project_dir, args.wu_dir, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
