#!/usr/bin/env python3
"""
distributed/worker.py
=====================
Volunteer worker client – runs on any computer with SageMath installed.

Usage:
    python3 worker.py --server http://HOST:5555 --key volunteer_key_change_me

Options:
    --server      URL of the work server (required)
    --key         Shared work key (must match server's WORK_KEY)
    --workers     Number of parallel Sage processes to run (default: cpu_count-1)
    --sage        Path to the sage executable (default: sage)
    --worker-id   Human-readable name for this machine (default: hostname)
    --once        Process a single WU then exit
    --no-retry    Exit instead of retrying on errors

The script fetches work units from the server, launches worker_sage.sage as a
subprocess, and posts results back.  It runs in a loop until the server says
"no_work" or is interrupted with Ctrl-C.

Dependencies:  requests (pip install requests)
               SageMath must be on PATH (or specify --sage)
"""

import os, sys, json, time, socket, argparse, subprocess, threading
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required.  Run: pip install requests")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
SAGE_SCRIPT = str(SCRIPT_DIR / "worker_sage.sage")


# ── Worker loop ───────────────────────────────────────────────────────────────

def fetch_work(server_url, key, worker_id):
    """GET /api/work → dict or None."""
    url = f"{server_url}/api/work"
    resp = requests.get(url, params={"key": key, "worker_id": worker_id},
                        timeout=30)
    if resp.status_code == 204:
        return None   # no work
    resp.raise_for_status()
    return resp.json()


def post_result(server_url, payload, key):
    """POST /api/result → dict."""
    payload = dict(payload, key=key)
    resp = requests.post(f"{server_url}/api/result",
                         json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def run_sage_wu(wu, sage_bin):
    """
    Run worker_sage.sage for the given work unit.
    Returns (points, solutions, elapsed_s) or raises on failure.
    """
    # Pass WU params as environment variables so no shell-escaping issues
    env = os.environ.copy()
    env["WU_T_VALUE"] = str(wu["t_value"])
    env["WU_M_LO"]    = str(wu["M_LO"])
    env["WU_M_HI"]    = str(wu["M_HI"])
    env["WU_ID"]      = str(wu["wu_id"])

    t0 = time.time()
    proc = subprocess.run(
        [sage_bin, SAGE_SCRIPT],
        capture_output=True, text=True, env=env,
        timeout=7 * 3600   # 7-hour hard timeout
    )
    elapsed = time.time() - t0

    if proc.returncode != 0:
        raise RuntimeError(
            f"Sage exited {proc.returncode}:\n{proc.stderr[-1000:]}"
        )

    # Parse JSON output from sage script (last line that starts with '{')
    result_json = None
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            result_json = json.loads(line)
            break
    if result_json is None:
        raise RuntimeError(
            f"No JSON output from sage:\nstdout={proc.stdout[-500:]}\n"
            f"stderr={proc.stderr[-500:]}"
        )

    return result_json.get("points", []), result_json.get("solutions", []), elapsed


def worker_loop(server_url, key, worker_id, sage_bin, once=False, no_retry=False):
    """Main loop: fetch WU → compute → post result → repeat."""
    consecutive_errors = 0
    total_done = 0

    print(f"[worker] {worker_id}  server={server_url}  sage={sage_bin}")

    while True:
        # ── Fetch work ────────────────────────────────────────────────
        try:
            wu = fetch_work(server_url, key, worker_id)
        except Exception as e:
            consecutive_errors += 1
            print(f"[worker] fetch error (#{consecutive_errors}): {e}")
            if no_retry or consecutive_errors > 10:
                print("[worker] too many errors, exiting")
                return
            time.sleep(min(30 * consecutive_errors, 300))
            continue

        if wu is None:
            print("[worker] No work available – all WUs exhausted or confirmed.")
            print(f"[worker] Total completed by this instance: {total_done}")
            return

        t_value   = wu["t_value"]
        wu_id     = wu["wu_id"]
        assign_id = wu.get("assign_id", 0)

        print(f"[worker] Got WU {wu_id}  t={t_value}  "
              f"M∈[{wu['M_LO'][:6]}…, {wu['M_HI'][:6]}…]")

        # ── Compute ───────────────────────────────────────────────────
        try:
            points, solutions, elapsed = run_sage_wu(wu, sage_bin)
            consecutive_errors = 0
            print(f"[worker] WU {wu_id} t={t_value}  "
                  f"{len(points)} EC points  {len(solutions)} solutions  "
                  f"({elapsed:.1f}s)")
        except subprocess.TimeoutExpired:
            print(f"[worker] WU {wu_id} TIMEOUT – skipping")
            # Post empty result so server knows we tried
            points, solutions, elapsed = [], [], 7 * 3600
        except Exception as e:
            print(f"[worker] WU {wu_id} SAGE ERROR: {e}")
            consecutive_errors += 1
            if no_retry:
                return
            time.sleep(10)
            continue

        # ── Post result ───────────────────────────────────────────────
        payload = {
            "wu_id"    : wu_id,
            "assign_id": assign_id,
            "t_value"  : t_value,
            "worker_id": worker_id,
            "points"   : points,
            "solutions": solutions,
            "elapsed_s": elapsed,
        }
        try:
            resp = post_result(server_url, payload, key)
            print(f"[worker] Posted result → {resp.get('status')}  "
                  f"confirmed={resp.get('wu_confirmed')}  "
                  f"progress={resp.get('progress')}")
            if resp.get("new_solutions"):
                print(f"  *** {len(resp['new_solutions'])} NEW SOLUTIONS! ***")
                for s in resp["new_solutions"]:
                    print(f"      m={s['m']}  x={s['x']}  t={s['t']}  y={s['y']}")
        except Exception as e:
            print(f"[worker] post error: {e}")
            consecutive_errors += 1

        total_done += 1
        if once:
            return
        time.sleep(1)   # brief pause between WUs


# ── Parallel worker threads ───────────────────────────────────────────────────

def run_parallel(server_url, key, worker_id, sage_bin, n_workers, no_retry):
    if n_workers == 1:
        worker_loop(server_url, key, worker_id, sage_bin,
                    no_retry=no_retry)
        return

    threads = []
    for i in range(n_workers):
        wid = f"{worker_id}-p{i}"
        th = threading.Thread(
            target=worker_loop,
            args=(server_url, key, wid, sage_bin, False, no_retry),
            daemon=True
        )
        th.start()
        threads.append(th)
        time.sleep(2)   # stagger starts

    for th in threads:
        th.join()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    cpu_count = os.cpu_count() or 2
    ap = argparse.ArgumentParser(description="Volunteer worker for sums-of-cubes search")
    ap.add_argument("--server",    required=True,
                    help="Work server URL, e.g. http://192.168.1.10:5555")
    ap.add_argument("--key",       default="volunteer_key_change_me",
                    help="Shared work key (must match server WORK_KEY)")
    ap.add_argument("--workers",   type=int, default=max(1, cpu_count - 1),
                    help="Parallel Sage processes (default: cpu_count-1)")
    ap.add_argument("--sage",      default="sage",
                    help="Path to sage executable")
    ap.add_argument("--worker-id", default=socket.gethostname(),
                    help="Identifier for this machine")
    ap.add_argument("--once",      action="store_true",
                    help="Process a single WU and exit")
    ap.add_argument("--no-retry",  action="store_true",
                    help="Exit on error instead of retrying")
    args = ap.parse_args()

    # Verify sage is accessible
    try:
        out = subprocess.check_output([args.sage, "--version"],
                                       stderr=subprocess.STDOUT,
                                       timeout=10).decode()
        print(f"[worker] Sage version: {out.strip().splitlines()[0]}")
    except Exception as e:
        print(f"ERROR: Cannot run sage at '{args.sage}': {e}")
        print("       Install SageMath or pass --sage /path/to/sage")
        sys.exit(1)

    # Verify worker_sage.sage exists
    if not Path(SAGE_SCRIPT).exists():
        print(f"ERROR: {SAGE_SCRIPT} not found. "
              "Make sure worker_sage.sage is in the same directory as worker.py")
        sys.exit(1)

    print(f"[worker] Starting {args.workers} parallel worker(s)")
    try:
        if args.once:
            worker_loop(args.server, args.key, args.worker_id, args.sage,
                        once=True, no_retry=args.no_retry)
        else:
            run_parallel(args.server, args.key, args.worker_id,
                         args.sage, args.workers, args.no_retry)
    except KeyboardInterrupt:
        print("\n[worker] Interrupted by user.")


if __name__ == "__main__":
    main()
