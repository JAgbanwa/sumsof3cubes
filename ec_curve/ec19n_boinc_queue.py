#!/usr/bin/env python3
"""
ec19n_boinc_queue.py  —  BOINC work-queue manager for the ec19n project.

Architecture: FRONTIER-BASED (no precomputation of all WUs)
─────────────────────────────────────────────────────────────
The DB stores a "frontier" per n-value (how far we've submitted) plus a
small "inflight" table of currently-pending BOINC WUs.  The submission
daemon advances the frontier in real time as volunteers consume WUs.

Total search range: x ∈ [−10¹⁷, +10¹⁷] per n ∈ {1, −1, 19, −19}

WU sizing (targeting ~4 h on a typical Charity Engine volunteer):
  n=±1 : Mac rate ≈ 11 M x/s  →  volunteer ≈ 1.1 M x/s
          x_block = 1.5×10¹⁰  →  ~1.4 h Mac, ~4 h volunteer
  n=±19: Mac rate ≈ 33 M x/s  →  volunteer ≈ 3.3 M x/s
          x_block = 4.8×10¹⁰  →  ~1.5 h Mac, ~4 h volunteer

Total WUs: n=±1  → ~13.3 M each × 2 = 26.7 M
           n=±19 →  ~4.2 M each × 2 =  8.3 M
           Grand total ≈ 35 M WUs

At 100 000 Charity Engine CPUs running concurrently → ~140 h (≈ 6 days).
At 500 000 CPUs → ~28 h (≈ 1 day).

Commands
────────
  init        Create (or reset) the DB with frontier config.
  submit      Daemon: fill BOINC queue to MAX_PENDING, replenish every POLL_INTERVAL s.
  export      Write WU files to wu_queue/ without a BOINC server (test/standalone).
  status      Print per-n progress and estimated completion.
  reset_stuck Re-open 'submitted' WUs older than --stuck_hours.
  mark_done   (called by assimilator) Record a completed WU.
"""

import argparse, os, sys, sqlite3, subprocess, time, textwrap
from pathlib import Path

# ── Project layout ───────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
STATE_DB   = BASE_DIR / "ec19n_wuqueue.db"
QUEUE_DIR  = BASE_DIR / "wu_queue"
TMPLS_DIR  = BASE_DIR / "templates"
APP_NAME   = "ec19n"

# ── Search parameters ────────────────────────────────────────────────────────
VALID_N = [1, -1, 19, -19]

X_MAX = 10**17    # search x ∈ [−X_MAX, +X_MAX]

# Block size per n-value (x-range per WU) — tuned for ~4 h on slow volunteer
X_BLOCK = {
     1:  15_000_000_000,   # 1.5×10¹⁰
    -1:  15_000_000_000,
    19:  48_000_000_000,   # 4.8×10¹⁰
   -19:  48_000_000_000,
}

# ── BOINC parameters ─────────────────────────────────────────────────────────
FANOUT        = 2           # result copies per WU (quorum)
MAX_PENDING   = 5_000       # max WUs in 'submitted' state at any time
BATCH_SIZE    = 500         # WUs submitted per daemon iteration
POLL_INTERVAL = 90          # seconds between daemon checks
FPOPS_EST     = {1: 1.2e13, -1: 1.2e13, 19: 1.2e13, -19: 1.2e13}
RAM_BOUND     = 64 * 2**20  # 64 MB

# ── DB helpers ────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier (
    n             INTEGER PRIMARY KEY,
    x_next        INTEGER NOT NULL,
    x_max         INTEGER NOT NULL,
    x_min         INTEGER NOT NULL,
    x_block       INTEGER NOT NULL,
    wus_submitted INTEGER NOT NULL DEFAULT 0,
    wus_done      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inflight (
    wu_name      TEXT    PRIMARY KEY,
    n            INTEGER NOT NULL,
    x_start      INTEGER NOT NULL,
    x_end        INTEGER NOT NULL,
    state        TEXT    NOT NULL DEFAULT 'submitted',
    submitted_ts REAL,
    done_ts      REAL
);

CREATE INDEX IF NOT EXISTS idx_if_state ON inflight(state);
CREATE INDEX IF NOT EXISTS idx_if_n     ON inflight(n, state);
"""


def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(STATE_DB, timeout=30)
    db.executescript(SCHEMA)
    db.commit()
    return db


# ── WU content helpers ────────────────────────────────────────────────────────

def wu_content(n: int, x0: int, x1: int) -> str:
    return f"n {n}\nx_start {x0}\nx_end {x1}\n"


def make_wu_name(n: int, x0: int) -> str:
    ts_ms = int(time.time() * 1000) % (10**13)
    return f"ec19n_n{n:+d}_x{x0:+022d}_{ts_ms:013d}"


# ── init ──────────────────────────────────────────────────────────────────────

def cmd_init(args) -> None:
    """Create (or extend) the frontier table.  Safe to re-run."""
    db = open_db()
    for n in VALID_N:
        blk = X_BLOCK[n]
        db.execute(
            "INSERT OR IGNORE INTO frontier(n, x_next, x_max, x_min, x_block)"
            " VALUES (?, ?, ?, ?, ?)",
            (n, -X_MAX, X_MAX, -X_MAX, blk))
    db.commit()

    print("Frontier initialised:\n")
    print(f"  {'n':>4}  {'x_min':>22}  {'x_max':>22}  {'x_block':>14}"
          f"  {'total WUs':>12}  {'~h/WU (vol)':>12}")
    print("  " + "─" * 94)
    for n in VALID_N:
        row = db.execute(
            "SELECT x_min, x_max, x_block FROM frontier WHERE n=?", (n,)).fetchone()
        x_min, x_max, blk = row
        total = (x_max - x_min + blk) // blk
        mac_rate = 11e6 if abs(n) == 1 else 33e6
        vol_h    = blk / (mac_rate / 10) / 3600
        print(f"  n={n:>4}  {x_min:>22,}  {x_max:>22,}  {blk:>14,}"
              f"  {total:>12,}  {vol_h:>10.1f} h")
    db.close()

    total_wus = sum((2 * X_MAX) // X_BLOCK[n] for n in VALID_N)
    total_cpu_h = sum(
        (2 * X_MAX // X_BLOCK[n]) * (X_BLOCK[n] / (11e6 if abs(n) == 1 else 33e6) / 3600 * 10)
        for n in VALID_N)
    print(f"\n  Total WUs (all n):          {total_wus:>14,}")
    print(f"  Total CPU-hours needed:     {total_cpu_h:>14,.0f}")
    print(f"  At  10 000 volunteers:  {total_cpu_h/10_000/24:>7.0f} days")
    print(f"  At 100 000 volunteers:  {total_cpu_h/100_000/24:>7.1f} days")
    print(f"  At 500 000 volunteers:  {total_cpu_h/500_000/24:>7.2f} days")


# ── submit daemon ─────────────────────────────────────────────────────────────

def boinc_create_work(n: int, x0: int, x1: int, project_dir: str) -> str:
    """Call create_work.  Returns wu_name on success, '' on failure."""
    name = make_wu_name(n, x0)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_DIR / f"_tmp_{name}.txt"
    tmp.write_text(wu_content(n, x0, x1))

    cmd = [
        os.path.join(project_dir, "bin", "create_work"),
        "--appname",          APP_NAME,
        "--wu_name",          name,
        "--wu_template",      str(TMPLS_DIR / "ec19n_wu"),
        "--result_template",  str(TMPLS_DIR / "ec19n_result"),
        "--rsc_fpops_est",    str(FPOPS_EST[n]),
        "--rsc_memory_bound", str(RAM_BOUND),
        "--delay_bound",      str(7 * 86400),
        "--min_quorum",       str(FANOUT),
        "--target_nresults",  str(FANOUT),
        str(tmp),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=project_dir)
    tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        print(f"  [WARN] create_work failed: {r.stderr.strip()[:160]}")
        return ""
    return name


def cmd_submit(args) -> None:
    """
    Continuously feed the BOINC queue.
    Keeps at most MAX_PENDING WUs in 'submitted' state.
    Advances the per-n frontier as it goes.
    """
    project_dir = args.project_dir
    limit       = args.limit
    submitted_total = 0

    print(f"[submit] project={project_dir}  limit={limit or '∞'}")
    print(f"         MAX_PENDING={MAX_PENDING}  BATCH={BATCH_SIZE}"
          f"  POLL={POLL_INTERVAL}s\n")

    db = open_db()

    while True:
        in_flight = db.execute(
            "SELECT COUNT(*) FROM inflight WHERE state='submitted'").fetchone()[0]
        to_submit = min(BATCH_SIZE, MAX_PENDING - in_flight)
        stamp     = time.strftime("%H:%M:%S")

        # Check if all frontiers exhausted
        all_done = all(
            db.execute(
                "SELECT x_next > x_max FROM frontier WHERE n=?", (n,)).fetchone()[0]
            for n in VALID_N)

        if all_done and in_flight == 0:
            print("[submit] All frontiers exhausted and queue empty — DONE.")
            break

        if to_submit <= 0:
            print(f"[{stamp}]  in_flight={in_flight}/{MAX_PENDING}"
                  f"  submitted_total={submitted_total}  — queue full, sleeping…")
            time.sleep(POLL_INTERVAL)
            continue

        # Build a batch, cycling through n-values to interleave
        batch = []
        frontiers = db.execute(
            "SELECT n, x_next, x_max, x_block FROM frontier"
            " WHERE x_next <= x_max ORDER BY n").fetchall()

        i = 0
        while len(batch) < to_submit and frontiers:
            row = frontiers[i % len(frontiers)]
            n, x_next, x_max, x_block = row
            x_end = min(x_next + x_block - 1, x_max)
            batch.append((n, x_next, x_end))
            # advance frontier in place
            db.execute(
                "UPDATE frontier SET x_next=? WHERE n=?", (x_end + 1, n))
            # refresh this frontier's x_next
            updated = db.execute(
                "SELECT x_next, x_max FROM frontier WHERE n=?", (n,)).fetchone()
            if updated[0] > updated[1]:
                frontiers = [r for r in frontiers if r[0] != n]
            else:
                frontiers[i % len(frontiers)] = (n, updated[0], updated[1], x_block)
            i += 1
        db.commit()

        ok = 0
        for n, x0, x1 in batch:
            name = boinc_create_work(n, x0, x1, project_dir)
            if name:
                db.execute(
                    "INSERT OR IGNORE INTO inflight"
                    "(wu_name,n,x_start,x_end,state,submitted_ts)"
                    " VALUES(?,?,?,?,'submitted',?)",
                    (name, n, x0, x1, time.time()))
                db.execute(
                    "UPDATE frontier SET wus_submitted=wus_submitted+1 WHERE n=?",
                    (n,))
                ok += 1
            else:
                # Roll back frontier to retry this block
                db.execute(
                    "UPDATE frontier SET x_next=MIN(x_next,?) WHERE n=?",
                    (x0, n))
        db.commit()
        submitted_total += ok
        print(f"[{stamp}]  submitted {ok}  in_flight={in_flight+ok}"
              f"  session_total={submitted_total}")

        if limit and submitted_total >= limit:
            print(f"[submit] Limit {limit} reached.")
            break

        if ok < BATCH_SIZE:
            time.sleep(10)

    db.close()


# ── export (standalone — no BOINC server) ─────────────────────────────────────

def cmd_export(args) -> None:
    """Write WU files to wu_queue/ from the frontier (no BOINC server needed).

    Interleaves n-values: floor(limit/4) WUs per n, remainder given to n=1 and n=-1.
    """
    db    = open_db()
    limit = args.limit
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    written = 0

    # Distribute evenly: cycle round-robin across n-values
    # Build a generator per n, then interleave
    def gen_for_n(n):
        row = db.execute(
            "SELECT x_next, x_max, x_block FROM frontier WHERE n=?", (n,)).fetchone()
        if row is None:
            return
        x_next, x_max, x_block = row
        while x_next <= x_max:
            yield n, x_next, min(x_next + x_block - 1, x_max)
            x_next += x_block

    gens = {n: gen_for_n(n) for n in VALID_N}
    active = list(VALID_N)

    while written < limit and active:
        for n in list(active):
            if written >= limit:
                break
            try:
                _, x0, x1 = next(gens[n])
            except StopIteration:
                active.remove(n)
                continue
            name = make_wu_name(n, x0)
            path = QUEUE_DIR / f"{name}.txt"
            path.write_text(wu_content(n, x0, x1))
            db.execute(
                "INSERT OR IGNORE INTO inflight"
                "(wu_name,n,x_start,x_end,state,submitted_ts)"
                " VALUES(?,?,?,?,'submitted',?)",
                (name, n, x0, x1, time.time()))
            db.execute(
                "UPDATE frontier SET x_next=?, wus_submitted=wus_submitted+1 WHERE n=?",
                (x1 + 1, n))
            written += 1

    db.commit()
    db.close()
    print(f"Exported {written} WU files → {QUEUE_DIR}/")
    print("Run each file with:  ./ec19n_worker <wu_file> <result_file> <ckpt_file>")


# ── mark_done (called by assimilator) ────────────────────────────────────────

def cmd_mark_done(args) -> None:
    db = open_db()
    row = db.execute(
        "SELECT n FROM inflight WHERE wu_name=?", (args.wu_name,)).fetchone()
    db.execute(
        "UPDATE inflight SET state='done', done_ts=? WHERE wu_name=?",
        (time.time(), args.wu_name))
    if row:
        db.execute(
            "UPDATE frontier SET wus_done=wus_done+1 WHERE n=?", (row[0],))
    db.commit()
    db.close()
    print(f"Marked done: {args.wu_name}")


# ── reset_stuck ──────────────────────────────────────────────────────────────

def cmd_reset_stuck(args) -> None:
    cutoff = time.time() - args.stuck_hours * 3600
    db     = open_db()
    rows   = db.execute(
        "SELECT wu_name, n, x_start FROM inflight"
        " WHERE state='submitted' AND submitted_ts < ?", (cutoff,)).fetchall()
    if not rows:
        print(f"No stuck WUs older than {args.stuck_hours} h found.")
        db.close()
        return
    for (name, n, x0) in rows:
        db.execute(
            "UPDATE frontier SET x_next=MIN(x_next,?) WHERE n=?", (x0, n))
        db.execute("DELETE FROM inflight WHERE wu_name=?", (name,))
    db.commit()
    db.close()
    print(f"Reset {len(rows)} stuck WUs and rolled frontier back.")


# ── status ────────────────────────────────────────────────────────────────────

def cmd_status(args) -> None:
    if not STATE_DB.exists():
        print("No DB found.  Run:  python3 ec19n_boinc_queue.py init")
        return
    db = open_db()

    print(f"\n  {'n':>4}  {'done':>10}  {'in-flight':>10}  {'remaining≈':>13}"
          f"  {'x_frontier':>22}  {'% done':>7}")
    print("  " + "─" * 76)

    g_done = g_inf = g_rem = 0
    for n in VALID_N:
        fr = db.execute(
            "SELECT x_next, x_min, x_max, x_block, wus_done FROM frontier WHERE n=?",
            (n,)).fetchone()
        if fr is None:
            print(f"  n={n:>4}  (not initialised)")
            continue
        x_next, x_min, x_max, x_block, done = fr
        inf = db.execute(
            "SELECT COUNT(*) FROM inflight WHERE n=? AND state='submitted'",
            (n,)).fetchone()[0]
        total = (x_max - x_min + x_block) // x_block
        rem   = max(0, (x_max - x_next + x_block)) // x_block
        pct   = 100.0 * done / total if total else 0.0
        print(f"  n={n:>4}  {done:>10,}  {inf:>10,}  {rem:>13,}"
              f"  {x_next:>22,}  {pct:>6.2f}%")
        g_done += done; g_inf += inf; g_rem += rem

    total_all = g_done + g_inf + g_rem
    gpct = 100.0 * g_done / total_all if total_all else 0.0
    print("  " + "─" * 76)
    print(f"  {'TOT':>4}  {g_done:>10,}  {g_inf:>10,}  {g_rem:>13,}"
          f"  {'':>22}  {gpct:>6.2f}%")

    recent = db.execute(
        "SELECT COUNT(*) FROM inflight WHERE state='done' AND done_ts > ?",
        (time.time() - 86400,)).fetchone()[0]
    if recent > 0 and g_rem > 0:
        eta_h = g_rem / recent * 24
        print(f"\n  Last-24h throughput: {recent:,} WUs/day"
              f"  →  ETA: {eta_h:.0f} h  ({eta_h/24:.1f} days)")
    else:
        print("\n  (ETA unavailable — no completed WUs recorded yet)")
    db.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""\
            ec19n BOINC work-queue manager — frontier-based WU generation.

            Step 1: python3 ec19n_boinc_queue.py init
              Creates ec19n_wuqueue.db with frontier config (instant).

            Step 2a (live BOINC server):
              python3 ec19n_boinc_queue.py submit \\
                  --project_dir /home/boincadm/projects/ec19n
              Runs as a daemon; call from cron or systemd.

            Step 2b (standalone test — no server):
              python3 ec19n_boinc_queue.py export --limit 500
              ls wu_queue/   # ready to feed to ./ec19n_worker

            Step 3: python3 ec19n_boinc_queue.py status
        """))

    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init",       help="Initialise frontier DB (safe to re-run)")

    p_sub = sub.add_parser("submit",
                           help="Daemon: feed BOINC queue (needs live server)")
    p_sub.add_argument("--project_dir",
                       default="/home/boincadm/projects/ec19n",
                       help="BOINC project root (default /home/boincadm/projects/ec19n)")
    p_sub.add_argument("--limit", type=int, default=0,
                       help="Stop after N submissions (0=unlimited)")

    p_exp = sub.add_parser("export",
                            help="Write WU files without a BOINC server")
    p_exp.add_argument("--limit", type=int, default=500,
                       help="Number of WU files to write (default 500)")

    p_done = sub.add_parser("mark_done",
                             help="Mark a WU completed (called by assimilator)")
    p_done.add_argument("wu_name", help="WU name to mark as done")

    sub.add_parser("status",     help="Print sweep progress table")

    p_rst = sub.add_parser("reset_stuck",
                            help="Re-open WUs stuck in 'submitted' state")
    p_rst.add_argument("--stuck_hours", type=float, default=48,
                       help="Age threshold in hours (default 48)")

    args = ap.parse_args()
    {
        "init":        cmd_init,
        "submit":      cmd_submit,
        "export":      cmd_export,
        "mark_done":   cmd_mark_done,
        "status":      cmd_status,
        "reset_stuck": cmd_reset_stuck,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
