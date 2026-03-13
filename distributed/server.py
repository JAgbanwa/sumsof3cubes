#!/usr/bin/env python3
"""
distributed/server.py
=====================
Lightweight volunteer-computing work server for the m∈[10^20,10^30] search.

Architecture mirrors Charity Engine / BOINC:
  1. Server holds a queue of work units (WUs), one per t-value.
  2. Volunteers poll GET /api/work  → receive a WU (JSON).
  3. Volunteers compute, POST /api/result → submit result (JSON).
  4. Server validates: require N_CONFIRM independent agreements before
     marking a WU "done"; if results disagree, it re-issues the WU.
  5. Confirmed solutions auto-committed to GitHub.

Run:
    pip install flask
    python3 server.py [--host 0.0.0.0] [--port 5555] [--t-max 10000]

Environment variables (or edit CONFIG below):
    WORK_KEY   – shared secret that workers must send
    ADMIN_KEY  – stronger secret for admin endpoints
    GITHUB_DIR – path to the git repo root (for auto-push)
"""

import os, sys, json, time, sqlite3, hashlib, subprocess, argparse, threading
from pathlib import Path
from flask import Flask, request, jsonify, abort

# ── Configuration ─────────────────────────────────────────────────────────────
CONFIG = {
    "T_MAX"        : int(os.getenv("T_MAX",    "10000")),
    "M_LO"         : int(os.getenv("M_LO",    "10") + "0" * 19),    # 10^20
    "M_HI"         : int(os.getenv("M_HI",    "10") + "0" * 29),    # 10^30
    "N_CONFIRM"    : int(os.getenv("N_CONFIRM", "2")),   # agreements to confirm
    "WU_TIMEOUT_S" : int(os.getenv("WU_TIMEOUT", str(6 * 3600))),  # 6 hours
    "WORK_KEY"     : os.getenv("WORK_KEY",  "volunteer_key_change_me"),
    "ADMIN_KEY"    : os.getenv("ADMIN_KEY", "admin_key_change_me"),
    "GITHUB_DIR"   : os.getenv("GITHUB_DIR",
        str(Path(__file__).resolve().parent.parent)),
    "DB_PATH"      : str(Path(__file__).resolve().parent / "server_state.db"),
    "SOL_FILE"     : str(Path(__file__).resolve().parent / "solutions.txt"),
}

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Database helpers ──────────────────────────────────────────────────────────

def get_db():
    """Return a thread-local SQLite connection."""
    import threading
    local = threading.local()
    if not hasattr(local, "conn"):
        local.conn = sqlite3.connect(CONFIG["DB_PATH"], check_same_thread=False)
        local.conn.row_factory = sqlite3.Row
    return local.conn

_db_lock = threading.Lock()

def db_exec(sql, params=()):
    with _db_lock:
        conn = sqlite3.connect(CONFIG["DB_PATH"])
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        rows = cur.fetchall()
        conn.close()
        return rows

def init_db():
    db_exec("""
        CREATE TABLE IF NOT EXISTS work_units (
            wu_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            t_value     INTEGER UNIQUE NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            -- pending | in_flight | confirmed | failed
            confirm_count INTEGER NOT NULL DEFAULT 0,
            created_at  REAL,
            updated_at  REAL
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS assignments (
            assign_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            wu_id       INTEGER NOT NULL,
            worker_id   TEXT,
            issued_at   REAL,
            returned_at REAL,
            status      TEXT DEFAULT 'in_flight'
            -- in_flight | returned | timeout | rejected
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS results (
            result_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            wu_id       INTEGER NOT NULL,
            assign_id   INTEGER,
            worker_id   TEXT,
            points_json TEXT,   -- JSON list of [X,Y] integral points
            solutions   TEXT,   -- JSON list of {m,x,t,y}
            elapsed_s   REAL,
            received_at REAL,
            accepted    INTEGER DEFAULT 0  -- 1 once counted toward confirm
        )
    """)
    db_exec("""
        CREATE TABLE IF NOT EXISTS solutions (
            sol_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            m           TEXT NOT NULL,
            x           TEXT NOT NULL,
            t_value     INTEGER NOT NULL,
            y           TEXT NOT NULL,
            verified    INTEGER DEFAULT 0,
            found_at    REAL
        )
    """)

# ── Work-unit population ───────────────────────────────────────────────────────

def t_is_feasible(t):
    """True iff ∃ integer x with t | 36x³ − 19."""
    if t == 1:
        return True
    for x in range(t):
        if (36 * x**3 - 19) % t == 0:
            return True
    return False

def populate_work_units(t_max):
    """Insert all valid odd t in [1, t_max] if not already present."""
    print(f"[server] Computing valid t-values up to {t_max} …", flush=True)
    inserted = 0
    existing = {row["t_value"] for row in db_exec("SELECT t_value FROM work_units")}
    now = time.time()
    for t in range(1, t_max + 1, 2):   # odd only
        if t in existing:
            continue
        # Quick feasibility filter: odd t with 3∤t is always feasible (36≡0 mod 3
        # so we only need the gcd condition). Fallback to brute check for 3|t.
        if t % 3 != 0 or t_is_feasible(t):
            db_exec(
                "INSERT OR IGNORE INTO work_units (t_value,status,created_at,updated_at)"
                " VALUES (?,?,?,?)",
                (t, "pending", now, now)
            )
            inserted += 1
        if t % 1000 == 1:
            print(f"  … checking t={t}", flush=True)
    print(f"[server] Inserted {inserted} new work units.", flush=True)

# ── Timeout recycler ──────────────────────────────────────────────────────────

def recycle_timeouts():
    """Background thread: reclaim in_flight WUs that exceeded WU_TIMEOUT_S."""
    while True:
        time.sleep(300)   # check every 5 min
        cutoff = time.time() - CONFIG["WU_TIMEOUT_S"]
        stale = db_exec(
            "SELECT a.assign_id, a.wu_id FROM assignments a"
            " WHERE a.status='in_flight' AND a.issued_at < ?",
            (cutoff,)
        )
        for row in stale:
            db_exec("UPDATE assignments SET status='timeout' WHERE assign_id=?",
                    (row["assign_id"],))
            db_exec("UPDATE work_units SET status='pending', updated_at=?"
                    " WHERE wu_id=? AND status='in_flight'",
                    (time.time(), row["wu_id"]))
            print(f"[recycle] WU {row['wu_id']} timed out → pending", flush=True)

# ── Auth helpers ──────────────────────────────────────────────────────────────

def require_work_key():
    key = request.args.get("key") or request.json.get("key", "") if request.is_json else ""
    if key != CONFIG["WORK_KEY"]:
        abort(403, "Invalid work key")

def require_admin_key():
    key = request.args.get("key") or ""
    if key != CONFIG["ADMIN_KEY"]:
        abort(403, "Forbidden")

# ── API: GET /api/work ────────────────────────────────────────────────────────

@app.route("/api/work", methods=["GET"])
def get_work():
    require_work_key()
    worker_id = request.args.get("worker_id", "unknown")

    with _db_lock:
        conn = sqlite3.connect(CONFIG["DB_PATH"])
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # grab oldest pending WU
        cur.execute(
            "SELECT wu_id, t_value FROM work_units"
            " WHERE status='pending' ORDER BY t_value ASC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            conn.close()
            return jsonify({"status": "no_work",
                            "message": "All work units exhausted or in-flight"}), 204

        wu_id   = row["wu_id"]
        t_value = row["t_value"]
        now     = time.time()

        cur.execute(
            "UPDATE work_units SET status='in_flight', updated_at=? WHERE wu_id=?",
            (now, wu_id)
        )
        cur.execute(
            "INSERT INTO assignments (wu_id, worker_id, issued_at, status)"
            " VALUES (?,?,?,'in_flight')",
            (wu_id, worker_id, now)
        )
        conn.commit()
        assign_id = cur.lastrowid
        conn.close()

    payload = {
        "wu_id"    : wu_id,
        "assign_id": assign_id,
        "t_value"  : t_value,
        "M_LO"     : str(CONFIG["M_LO"]),
        "M_HI"     : str(CONFIG["M_HI"]),
        "issued_at": now,
    }
    return jsonify(payload), 200

# ── API: POST /api/result ─────────────────────────────────────────────────────

@app.route("/api/result", methods=["POST"])
def post_result():
    if not request.is_json:
        abort(400, "Expected JSON")
    data = request.get_json()
    if data.get("key") != CONFIG["WORK_KEY"]:
        abort(403, "Invalid work key")

    wu_id     = int(data["wu_id"])
    assign_id = int(data.get("assign_id", 0))
    worker_id = data.get("worker_id", "unknown")
    points    = data.get("points", [])   # list of [X,Y]
    solutions = data.get("solutions", []) # list of {m,x,t,y}
    elapsed_s = float(data.get("elapsed_s", 0))

    now = time.time()

    # Validate the wu_id exists and is in_flight
    rows = db_exec("SELECT wu_id, status FROM work_units WHERE wu_id=?", (wu_id,))
    if not rows:
        abort(404, "Unknown wu_id")
    wu_status = rows[0]["status"]
    if wu_status == "confirmed":
        return jsonify({"status": "already_confirmed"}), 200

    # Record result
    db_exec(
        "INSERT INTO results (wu_id,assign_id,worker_id,points_json,solutions,"
        "elapsed_s,received_at) VALUES (?,?,?,?,?,?,?)",
        (wu_id, assign_id, worker_id,
         json.dumps(points), json.dumps(solutions),
         elapsed_s, now)
    )

    # Mark assignment returned
    db_exec(
        "UPDATE assignments SET status='returned', returned_at=? WHERE assign_id=?",
        (now, assign_id)
    )

    # Handle any reported solutions
    new_solutions = []
    for sol in solutions:
        m_s = str(sol["m"]); x_s = str(sol["x"])
        t_v = int(sol["t"]); y_s = str(sol["y"])
        if _verify_solution(int(m_s), int(x_s), int(y_s)):
            db_exec(
                "INSERT INTO solutions (m,x,t_value,y,verified,found_at)"
                " VALUES (?,?,?,?,1,?)",
                (m_s, x_s, t_v, y_s, now)
            )
            _write_and_push_solution(m_s, x_s, t_v, y_s)
            new_solutions.append(sol)
        else:
            print(f"[warn] FAILED VERIFY: {sol}", flush=True)

    # Confirmation logic: compare with previous results for same WU
    # A WU is confirmed when N_CONFIRM results agree on point count (and points).
    prev_results = db_exec(
        "SELECT points_json FROM results WHERE wu_id=?", (wu_id,)
    )
    # Tally: map canonical-hash → count
    tally = {}
    for r in prev_results:
        canonical = _canonical_points(json.loads(r["points_json"]))
        tally[canonical] = tally.get(canonical, 0) + 1

    confirmed = any(v >= CONFIG["N_CONFIRM"] for v in tally.values())
    if confirmed:
        db_exec(
            "UPDATE work_units SET status='confirmed', confirm_count=?,"
            " updated_at=? WHERE wu_id=?",
            (max(tally.values()), now, wu_id)
        )
        print(f"[confirm] WU {wu_id} (t={data.get('t_value')}) confirmed "
              f"({len(solutions)} solutions)", flush=True)
    else:
        # Not yet confirmed – but we already have one result, need another
        db_exec(
            "UPDATE work_units SET status='pending', updated_at=? WHERE wu_id=?",
            (now, wu_id)
        )

    confirmed_total = db_exec(
        "SELECT COUNT(*) AS c FROM work_units WHERE status='confirmed'"
    )[0]["c"]
    total = db_exec("SELECT COUNT(*) AS c FROM work_units")[0]["c"]

    return jsonify({
        "status"           : "accepted",
        "wu_confirmed"     : confirmed,
        "progress"         : f"{confirmed_total}/{total}",
        "new_solutions"    : new_solutions,
    }), 200

def _canonical_points(pts):
    """Produce a stable hash of a list of [X,Y] pairs."""
    normalized = sorted((str(p[0]), str(p[1])) for p in pts)
    return hashlib.sha256(json.dumps(normalized).encode()).hexdigest()

def _verify_solution(m, x, y):
    return m * y * y == 36*x**3 + 36*m*x**2 + 12*m**2*x + m**3 - 19

def _write_and_push_solution(m_s, x_s, t_v, y_s):
    sol_line = f"m={m_s}  x={x_s}  t={t_v}  y={y_s}\n"
    print(f"*** SOLUTION FOUND: {sol_line.strip()} ***", flush=True)
    with open(CONFIG["SOL_FILE"], "a") as f:
        f.write(sol_line)
    # Also write to repo master file
    master = Path(CONFIG["GITHUB_DIR"]) / "solutions_disc.txt"
    with open(master, "a") as f:
        f.write(sol_line)
    # Git push
    gdir = CONFIG["GITHUB_DIR"]
    subprocess.Popen(
        ["git", "-C", gdir, "add", "-A"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).wait()
    subprocess.Popen(
        ["git", "-C", gdir, "commit", "-m",
         f"[auto] Solution: m={m_s[:20]}… t={t_v}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).wait()
    subprocess.Popen(
        ["git", "-C", gdir, "push", "origin", "main"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

# ── API: GET /api/status ──────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def status():
    rows = db_exec(
        "SELECT status, COUNT(*) AS c FROM work_units GROUP BY status"
    )
    counts = {r["status"]: r["c"] for r in rows}
    total = sum(counts.values())
    confirmed = counts.get("confirmed", 0)

    sol_rows = db_exec("SELECT m, x, t_value, y, found_at FROM solutions ORDER BY found_at")
    solutions = [dict(r) for r in sol_rows]

    recent = db_exec(
        "SELECT w.t_value, r.worker_id, r.elapsed_s, r.received_at"
        " FROM results r JOIN work_units w ON r.wu_id=w.wu_id"
        " ORDER BY r.received_at DESC LIMIT 20"
    )
    recent_list = [dict(r) for r in recent]

    return jsonify({
        "total_work_units": total,
        "confirmed"       : confirmed,
        "pending"         : counts.get("pending", 0),
        "in_flight"       : counts.get("in_flight", 0),
        "pct_done"        : round(100 * confirmed / total, 2) if total else 0,
        "solutions_found" : len(solutions),
        "solutions"       : solutions,
        "recent_results"  : recent_list,
        "config"          : {
            "T_MAX"    : CONFIG["T_MAX"],
            "M_LO"     : str(CONFIG["M_LO"]),
            "M_HI"     : str(CONFIG["M_HI"]),
            "N_CONFIRM": CONFIG["N_CONFIRM"],
        }
    })

# ── API: GET /api/admin/reset_wu  (admin only) ────────────────────────────────

@app.route("/api/admin/reset_wu", methods=["POST"])
def admin_reset_wu():
    require_admin_key()
    wu_id = request.json.get("wu_id")
    if wu_id is None:
        abort(400, "Need wu_id")
    db_exec(
        "UPDATE work_units SET status='pending', updated_at=? WHERE wu_id=?",
        (time.time(), wu_id)
    )
    return jsonify({"reset": wu_id})

@app.route("/api/admin/set_t_max", methods=["POST"])
def admin_set_t_max():
    require_admin_key()
    new_t = int(request.json.get("t_max", CONFIG["T_MAX"]))
    CONFIG["T_MAX"] = new_t
    populate_work_units(new_t)
    return jsonify({"t_max": new_t})

# ── HTML dashboard ────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    rows = db_exec(
        "SELECT status, COUNT(*) AS c FROM work_units GROUP BY status"
    )
    counts = {r["status"]: r["c"] for r in rows}
    total      = sum(counts.values())
    confirmed  = counts.get("confirmed", 0)
    in_flight  = counts.get("in_flight", 0)
    pending    = counts.get("pending", 0)
    pct        = round(100 * confirmed / total, 1) if total else 0

    sol_rows = db_exec(
        "SELECT m, x, t_value, y FROM solutions ORDER BY found_at DESC LIMIT 50"
    )
    sol_html = "".join(
        f"<tr><td>{r['t_value']}</td><td>{r['m'][:30]}…</td>"
        f"<td>{r['x']}</td><td>{r['y'][:30]}…</td></tr>"
        for r in sol_rows
    ) or "<tr><td colspan=4><i>None yet</i></td></tr>"

    last_results = db_exec(
        "SELECT w.t_value, r.worker_id, ROUND(r.elapsed_s,1) AS e, r.received_at"
        " FROM results r JOIN work_units w ON r.wu_id=w.wu_id"
        " ORDER BY r.received_at DESC LIMIT 15"
    )
    last_html = "".join(
        f"<tr><td>t={r['t_value']}</td><td>{r['worker_id']}</td>"
        f"<td>{r['e']}s</td></tr>"
        for r in last_results
    )

    return f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<title>m∈[10²⁰,10³⁰] Search Dashboard</title>
<meta http-equiv="refresh" content="30">
<style>body{{font-family:monospace;background:#0a0a1a;color:#c8d8e8;padding:2em}}
h1{{color:#7af}}table{{border-collapse:collapse;margin-top:1em}}
td,th{{border:1px solid #344;padding:4px 12px}}th{{background:#1a2a3a}}
.big{{font-size:2em;color:#4fa}} .bar{{background:#1a2a4a;height:24px;
border-radius:4px;overflow:hidden}} .fill{{background:#3af;height:100%}}
</style></head><body>
<h1>Sums-of-3-Cubes: m∈[10²⁰,10³⁰] Distributed Search</h1>
<p>Strategy: for each t, solve elliptic curve Y²=X³+aX²+bX+c → find ALL solutions.</p>
<div class=bar><div class=fill style="width:{pct}%"></div></div>
<p><span class=big>{pct}%</span> complete &nbsp;|&nbsp;
  <b>{confirmed}</b> confirmed / {in_flight} in-flight / {pending} pending / {total} total WUs</p>
<h2>Solutions Found</h2>
<table><tr><th>t</th><th>m (truncated)</th><th>x</th><th>y (truncated)</th></tr>
{sol_html}</table>
<h2>Recent Results</h2>
<table><tr><th>WU</th><th>Worker</th><th>Time</th></tr>{last_html}</table>
<p style="color:#567;font-size:.8em">Auto-refreshes every 30s.
  To join: <code>python3 worker.py --server http://THIS_SERVER:5555 --key {CONFIG["WORK_KEY"]}</code></p>
</body></html>"""

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host",  default="0.0.0.0")
    ap.add_argument("--port",  type=int, default=5555)
    ap.add_argument("--t-max", type=int, default=CONFIG["T_MAX"])
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    CONFIG["T_MAX"] = args.t_max

    init_db()
    populate_work_units(args.t_max)

    # Background recycler
    th = threading.Thread(target=recycle_timeouts, daemon=True)
    th.start()

    total = db_exec("SELECT COUNT(*) AS c FROM work_units")[0]["c"]
    print(f"[server] {total} work units ready. "
          f"Listening on {args.host}:{args.port}", flush=True)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)

if __name__ == "__main__":
    main()
