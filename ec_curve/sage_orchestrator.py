#!/usr/bin/env python3
"""
sage_orchestrator.py  —  Orchestrates a pool of persistent Sage daemon processes
to search for integral points across many n values in parallel.

Architecture:
  - Spawns N_WORKERS long-lived Sage daemon processes
  - Dispatches SEARCH <n> jobs to idle workers
  - Collects RESULT/DONE/ERROR lines with a per-job timeout
  - On timeout, kills and restarts the worker, marks n as timed-out
  - Writes solutions to output/solutions_master.txt
  - Checkpoints state to output/sage_checkpoint.json
  - Skipped-n list saved to output/sage_skipped.txt
  
Usage:
  python3 sage_orchestrator.py [--workers 4] [--timeout 90]
"""
import subprocess, threading, time, json, sys, os, signal, queue
import argparse
from pathlib import Path

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

MASTER_FILE = OUTPUT_DIR / "solutions_master.txt"
CKPT_FILE   = OUTPUT_DIR / "sage_orch_checkpoint.json"
SKIP_FILE   = OUTPUT_DIR / "sage_skipped.txt"
DAEMON_SAGE = BASE_DIR / "sage_worker_daemon.sage"
SAGE_BIN    = "/usr/local/bin/sage"

def load_state():
    try:
        d = json.loads(CKPT_FILE.read_text())
        return d.get("last_radius", 0), set(d.get("skipped", []))
    except Exception:
        return 0, set()

def save_state(radius, skipped):
    CKPT_FILE.write_text(json.dumps({
        "last_radius": int(radius),
        "skipped": sorted(int(x) for x in skipped)
    }, indent=2))

def verify(n, x, y):
    return y*y == x**3 + 1296*n**2*x**2 + 15552*n**3*x + 46656*n**4 - 19*n


class SageDaemon:
    """One persistent Sage subprocess."""
    def __init__(self, worker_id, timeout):
        self.worker_id = worker_id
        self.timeout   = timeout
        self.proc      = None
        self._start()

    def _start(self):
        if self.proc:
            try: self.proc.kill()
            except: pass
        self.proc = subprocess.Popen(
            [SAGE_BIN, str(DAEMON_SAGE)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1
        )
        # Wait for READY
        deadline = time.time() + 60
        while time.time() < deadline:
            line = self.proc.stdout.readline().strip()
            if line == "READY":
                return
        raise RuntimeError(f"Worker {self.worker_id}: Sage never sent READY")

    def search(self, n):
        """Send SEARCH n, collect results until DONE/ERROR.
        Returns (list_of_(n,x,y), timed_out_bool)."""
        self.proc.stdin.write(f"SEARCH {n}\n")
        self.proc.stdin.flush()
        results = []
        deadline = time.time() + self.timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return results, True  # timed out
            # Use a reader thread to avoid blocking
            line = self._readline_timeout(remaining)
            if line is None:
                return results, True  # timed out
            line = line.strip()
            if line.startswith("RESULT"):
                parts = line.split()
                nn, x, y = int(parts[1]), int(parts[2]), int(parts[3])
                if verify(nn, x, y):
                    results.append((nn, x, y))
                else:
                    print(f"  [W{self.worker_id}] verify fail n={nn} x={x} y={y}", flush=True)
            elif line.startswith("DONE"):
                return results, False
            elif line.startswith("ERROR"):
                print(f"  [W{self.worker_id}] {line}", flush=True)
            # else: ignore blank/noise

    def _readline_timeout(self, timeout):
        """Read one line from stdout with timeout. Returns None on timeout."""
        result_q = queue.Queue()
        def reader():
            try:
                line = self.proc.stdout.readline()
                result_q.put(line)
            except Exception:
                result_q.put(None)
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            return result_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def restart(self):
        print(f"  [W{self.worker_id}] Restarting...", flush=True)
        try: self.proc.kill()
        except: pass
        self._start()
        print(f"  [W{self.worker_id}] Ready again", flush=True)

    def shutdown(self):
        try:
            self.proc.stdin.write("QUIT\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
        except:
            pass
        try: self.proc.kill()
        except: pass


def worker_thread(daemon, job_queue, result_queue, stop_event):
    """Thread that pulls jobs from job_queue, dispatches to daemon, pushes results."""
    while not stop_event.is_set():
        try:
            n = job_queue.get(timeout=1)
        except queue.Empty:
            continue
        sols, timed_out = daemon.search(n)
        result_queue.put((n, sols, timed_out))
        if timed_out:
            daemon.restart()
        job_queue.task_done()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2,
                        help="Number of parallel Sage workers (default 2)")
    parser.add_argument("--timeout", type=int, default=90,
                        help="Per-n timeout in seconds (default 90)")
    args = parser.parse_args()

    N_WORKERS = args.workers
    N_TIMEOUT = args.timeout

    print("=" * 66)
    print("  SAGE ORCHESTRATED INTEGRAL-POINT SEARCH")
    print(f"  workers={N_WORKERS}  timeout={N_TIMEOUT}s")
    print("  y^2 = x^3 + 1296n^2x^2 + 15552n^3x + (46656n^4 - 19n)")
    print("=" * 66, flush=True)

    start_radius, skipped = load_state()
    radius       = start_radius + 1
    total_new    = 0
    master_seen  = set()

    if MASTER_FILE.exists():
        for line in MASTER_FILE.read_text().splitlines():
            parts = line.split()
            if len(parts) == 3:
                try: master_seen.add(tuple(int(v) for v in parts))
                except: pass

    master_f = MASTER_FILE.open("a", buffering=1)
    skip_f   = SKIP_FILE.open("a", buffering=1)

    job_queue    = queue.Queue()
    result_queue = queue.Queue()
    stop_event   = threading.Event()

    print(f"  Spawning {N_WORKERS} Sage daemon(s). This takes ~20-30s each...", flush=True)
    daemons = []
    threads = []
    for i in range(N_WORKERS):
        d = SageDaemon(i, N_TIMEOUT)
        daemons.append(d)
        t = threading.Thread(target=worker_thread,
                             args=(d, job_queue, result_queue, stop_event),
                             daemon=True)
        t.start()
        threads.append(t)
        print(f"  Worker {i} ready.", flush=True)

    print(f"  Resuming from radius {radius}", flush=True)

    # Feed jobs while collecting results
    pending = 0
    t0 = time.time()

    try:
        while True:
            # Feed enough upcoming n values to keep all workers busy
            while pending < N_WORKERS * 2:
                for sign in [1, -1]:
                    n = sign * radius
                    job_queue.put(n)
                    pending += 1
                radius += 1

            # Collect one result
            n, sols, timed_out = result_queue.get(timeout=300)
            pending -= 1

            dt = time.time() - t0
            if timed_out:
                prefix = f"  [TIMEOUT n={n:>8}  {dt:.0f}s elapsed]"
                print(prefix, flush=True)
                skipped.add(n)
                skip_f.write(f"{n}\n")
                skip_f.flush()
            else:
                for trip in sols:
                    if trip not in master_seen:
                        master_seen.add(trip)
                        nn, xx, yy = trip
                        master_f.write(f"{nn} {xx} {yy}\n")
                        master_f.flush()
                        total_new += 1
                        print(f"  *** SOLUTION  n={nn:>10}  x={xx:>22}  y={yy:>22}",
                              flush=True)
                nsols = len(sols)
                if nsols or abs(n) % 10 == 0:
                    print(f"  n={n:>8}  nsols={nsols}  "
                          f"cum_new={total_new}  {dt:.0f}s", flush=True)

            # Checkpoint after every completed pair
            completed_radius = (abs(n) if not timed_out else 0)
            save_state(max(start_radius, completed_radius), skipped)

    except KeyboardInterrupt:
        print("\n[interrupted]", flush=True)
    except queue.Empty:
        print("[queue empty — all workers stalled]", flush=True)
    finally:
        stop_event.set()
        for d in daemons:
            d.shutdown()
        master_f.close()
        skip_f.close()
        save_state(radius - 1, skipped)
        print(f"\nDone. total_new={total_new}", flush=True)


if __name__ == "__main__":
    main()
