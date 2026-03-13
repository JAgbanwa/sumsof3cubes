#!/usr/bin/env python3
"""
work_generator_large.py  —  Charity Engine / BOINC work generator
══════════════════════════════════════════════════════════════════════

Produces work-units covering m ∈ (−∞, −10^20] ∪ [10^20, +∞),
expanding outward sector by sector.

Each WU covers BLOCK_SIZE consecutive m values.
WU files are written to wu_dir/ and optionally submitted via
BOINC's `bin/create_work` command.

── State DB ─────────────────────────────────────────────────────────
SQLite3 file `wg_state.db` records every submitted range so the
generator can be safely restarted.

── Usage ─────────────────────────────────────────────────────────────
Standalone (writes WU files, no BOINC submission):
    python3 work_generator_large.py \\
        --wu_dir ./wu_files --count 200 --dry_run

With BOINC project:
    python3 work_generator_large.py \\
        --boinc_project_dir /home/boincadm/projects/sumsof3cubes \\
        --app_name ec_large --count 500

── WU file format ────────────────────────────────────────────────────
m_start  <big int>
m_end    <big int>
timeout_per_m  600
gp_stack_mb    512
══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import os
import sys
import time
import sqlite3
import argparse
import subprocess
import shutil
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────
BLOCK_SIZE        = 50       # m values per work unit (tune to ~10 min per WU)
SEARCH_FLOOR      = 10**20   # start |m| from here
TIMEOUT_PER_M     = 600      # seconds per m in the WU
GP_STACK_MB       = 512
APP_NAME          = "ec_large"
FANOUT            = 2        # redundant copies per WU
MAX_OUTSTANDING   = 5000     # stop submitting above this count in-flight
DELAY_BOUND       = 86400 * 14  # 14-day deadline per WU

DB_FILE           = "wg_state.db"

# ─────────────────────────────────────────────────────────────────────
# State database
# ─────────────────────────────────────────────────────────────────────

def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wu_state (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            wu_name    TEXT UNIQUE,
            m_start    TEXT NOT NULL,
            m_end      TEXT NOT NULL,
            direction  TEXT NOT NULL,  -- 'pos' or 'neg'
            sent_at    REAL,
            status     TEXT DEFAULT 'sent'  -- sent / completed / failed
        )
    """)
    conn.commit()
    return conn


def frontier(conn: sqlite3.Connection) -> tuple[int, int]:
    """Return (pos_frontier, neg_frontier) = next m values to assign."""
    row = conn.execute(
        "SELECT MAX(CAST(m_end AS INTEGER)) FROM wu_state WHERE direction='pos'"
    ).fetchone()
    pos = int(row[0]) if row[0] is not None else SEARCH_FLOOR - 1

    row = conn.execute(
        "SELECT MIN(CAST(m_start AS INTEGER)) FROM wu_state WHERE direction='neg'"
    ).fetchone()
    neg = int(row[0]) if row[0] is not None else -(SEARCH_FLOOR - 1)

    return pos, neg


def record_sent(conn: sqlite3.Connection, wu_name: str,
                direction: str, m_start: int, m_end: int):
    conn.execute(
        "INSERT OR IGNORE INTO wu_state "
        "(wu_name, m_start, m_end, direction, sent_at) VALUES (?,?,?,?,?)",
        (wu_name, str(m_start), str(m_end), direction, time.time()),
    )
    conn.commit()


def outstanding_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM wu_state WHERE status='sent'"
    ).fetchone()
    return int(row[0])

# ─────────────────────────────────────────────────────────────────────
# WU file creation
# ─────────────────────────────────────────────────────────────────────

def write_wu_file(wu_dir: Path, wu_name: str,
                  m_start: int, m_end: int) -> Path:
    wu_dir.mkdir(parents=True, exist_ok=True)
    p = wu_dir / wu_name
    p.write_text(
        f"m_start {m_start}\n"
        f"m_end   {m_end}\n"
        f"timeout_per_m {TIMEOUT_PER_M}\n"
        f"gp_stack_mb   {GP_STACK_MB}\n"
    )
    return p

# ─────────────────────────────────────────────────────────────────────
# BOINC submission
# ─────────────────────────────────────────────────────────────────────

def submit_boinc(boinc_proj: str, app_name: str,
                 wu_file: Path, wu_name: str, dry_run: bool):
    """
    Submit a WU using BOINC's bin/create_work command.

    create_work args:
        --appname NAME
        --wu_name  NAME
        --wu_template  templates/wu_template.xml
        --result_template templates/result_template.xml
        --delay_bound SECONDS
        --min_quorum FANOUT
        --target_nresults FANOUT
        <input_file>
    """
    create_work = os.path.join(boinc_proj, "bin", "create_work")
    wu_template  = os.path.join(
        boinc_proj, "templates", "ec_large_wu.xml")
    res_template = os.path.join(
        boinc_proj, "templates", "ec_large_result.xml")

    # Copy input file to BOINC download hierarchy
    download_dir = os.path.join(boinc_proj, "download")
    dest = os.path.join(download_dir, wu_file.name)
    if not dry_run:
        shutil.copy(wu_file, dest)

    cmd = [
        create_work,
        "--appname",         app_name,
        "--wu_name",         wu_name,
        "--wu_template",     wu_template,
        "--result_template", res_template,
        "--delay_bound",     str(DELAY_BOUND),
        "--min_quorum",      str(FANOUT),
        "--target_nresults", str(FANOUT),
        wu_file.name,
    ]
    if dry_run:
        print(f"[dry-run] {' '.join(cmd)}")
        return True

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=boinc_proj)
        if r.returncode == 0:
            return True
        print(f"[create_work] FAIL {wu_name}: {r.stderr.strip()}",
              file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[create_work] error: {exc}", file=sys.stderr)
        return False

# ─────────────────────────────────────────────────────────────────────
# Generate one batch of work units
# ─────────────────────────────────────────────────────────────────────

def generate_batch(
    conn: sqlite3.Connection,
    count: int,
    wu_dir: Path,
    boinc_proj: str | None,
    app_name: str,
    dry_run: bool,
):
    pos_max, neg_min = frontier(conn)
    submitted = 0

    while submitted < count:
        if outstanding_count(conn) >= MAX_OUTSTANDING:
            print(f"[wg] MAX_OUTSTANDING={MAX_OUTSTANDING} reached, waiting…")
            break

        # ── positive side ─────────────────────────────────────────
        m_s_pos = pos_max + 1
        m_e_pos = m_s_pos + BLOCK_SIZE - 1
        wu_name_pos = f"ec_large_pos_{m_s_pos}_{m_e_pos}"
        wu_file_pos = write_wu_file(wu_dir, wu_name_pos + ".wu",
                                    m_s_pos, m_e_pos)

        ok = True
        if boinc_proj:
            ok = submit_boinc(boinc_proj, app_name,
                              wu_file_pos, wu_name_pos, dry_run)
        else:
            print(f"[wg] wrote {wu_file_pos}")

        if ok:
            record_sent(conn, wu_name_pos, "pos", m_s_pos, m_e_pos)
            pos_max = m_e_pos
            submitted += 1

        # ── negative side ─────────────────────────────────────────
        m_e_neg = neg_min - 1
        m_s_neg = m_e_neg - BLOCK_SIZE + 1
        wu_name_neg = f"ec_large_neg_{m_s_neg}_{m_e_neg}"
        wu_file_neg = write_wu_file(wu_dir, wu_name_neg + ".wu",
                                    m_s_neg, m_e_neg)

        ok = True
        if boinc_proj:
            ok = submit_boinc(boinc_proj, app_name,
                              wu_file_neg, wu_name_neg, dry_run)
        else:
            print(f"[wg] wrote {wu_file_neg}")

        if ok:
            record_sent(conn, wu_name_neg, "neg", m_s_neg, m_e_neg)
            neg_min = m_s_neg
            submitted += 1

    print(f"[wg] submitted {submitted} new WUs  "
          f"pos_frontier={pos_max}  neg_frontier={neg_min}")

# ─────────────────────────────────────────────────────────────────────
# Continuous daemon mode
# ─────────────────────────────────────────────────────────────────────

def daemon_loop(
    conn: sqlite3.Connection,
    wu_dir: Path,
    boinc_proj: str | None,
    app_name: str,
    batch_size: int,
    interval: int,
    dry_run: bool,
):
    print(f"[wg] daemon started  interval={interval}s  batch={batch_size}")
    while True:
        try:
            generate_batch(conn, batch_size, wu_dir,
                           boinc_proj, app_name, dry_run)
        except KeyboardInterrupt:
            print("\n[wg] interrupted")
            break
        except Exception as exc:
            print(f"[wg] error in batch: {exc}", file=sys.stderr)
        time.sleep(interval)

# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Charity Engine work generator for large-m EC search")
    ap.add_argument("--wu_dir",
        default="wu_files", help="Directory to write WU input files")
    ap.add_argument("--db",
        default=DB_FILE, help="State database path")
    ap.add_argument("--boinc_project_dir",
        default=None, help="BOINC project root (enables create_work submission)")
    ap.add_argument("--app_name",
        default=APP_NAME, help="BOINC app name")
    ap.add_argument("--count",
        type=int, default=100,
        help="Number of WUs to generate per batch")
    ap.add_argument("--block_size",
        type=int, default=BLOCK_SIZE,
        help="m values per work unit")
    ap.add_argument("--search_floor",
        type=int, default=SEARCH_FLOOR,
        help="Minimum |m| (default 10^20)")
    ap.add_argument("--daemon",
        action="store_true",
        help="Run continuously, replenishing WUs every --interval seconds")
    ap.add_argument("--interval",
        type=int, default=300,
        help="Polling interval in daemon mode (seconds)")
    ap.add_argument("--dry_run",
        action="store_true",
        help="Print commands but don't actually submit to BOINC")
    args = ap.parse_args()

    global BLOCK_SIZE, SEARCH_FLOOR
    BLOCK_SIZE   = args.block_size
    SEARCH_FLOOR = args.search_floor

    conn    = init_db(args.db)
    wu_dir  = Path(args.wu_dir)

    if args.daemon:
        daemon_loop(conn, wu_dir, args.boinc_project_dir,
                    args.app_name, args.count, args.interval, args.dry_run)
    else:
        generate_batch(conn, args.count, wu_dir,
                       args.boinc_project_dir, args.app_name, args.dry_run)


if __name__ == "__main__":
    main()
