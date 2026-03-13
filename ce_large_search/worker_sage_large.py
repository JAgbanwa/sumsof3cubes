#!/usr/bin/env python3
"""
worker_sage_large.py  --  CE/BOINC worker using a persistent Sage daemon
=====================================================================

Equation (user form, parameter m != 0):
    y^2 = (36/m)*x^3 + 36*x^2 + 12*m*x + (m^3-19)/m

Weierstrass form (X=6*m*x, Y=6*m^2*y):
    E_m: Y^2 = X^3 + 1296*m^2*X^2 + 15552*m^3*X + (46656*m^4 - 19*m)

Uses a long-running "sage sage_large_daemon.sage" subprocess so Sage
is started ONCE per worker, not once per m. E.integral_points() is
provably complete by Siegel's theorem.

Work-unit format (wu.txt):
    m_start  <big int>
    m_end    <big int>
    timeout_per_m  <seconds>   (default 600)

Output (result.txt):
    SOL m X Y
    USR m x y   (back-computed user coords when integer)

Checkpoint (checkpoint.json):
    {"last_m": <int>}

Usage:
    python3 worker_sage_large.py wu.txt result.txt checkpoint.json
    BOINC=1 python3 worker_sage_large.py wu.txt result.txt checkpoint.json
=====================================================================
"""

from __future__ import annotations
import sys, os, json, time, subprocess, threading, argparse, select
from pathlib import Path
from typing import Optional

BOINC_MODE    = os.environ.get("BOINC", "0") == "1"
SAGE_BIN      = os.environ.get("SAGE_BIN", "sage")
CKPT_INTERVAL = 30
DEFAULT_TIMEOUT_PER_M = 600

HERE        = Path(__file__).parent
DAEMON_SAGE = HERE / "sage_large_daemon.sage"

# ---- BOINC heartbeat ----------------------------------------------------
_stop_event = threading.Event()
_total_work = 1
_work_done  = 0

def _heartbeat():
    while not _stop_event.is_set():
        try:
            frac = _work_done / max(_total_work, 1)
            with open("fraction_done", "w") as fh:
                fh.write(f"{frac:.6f}\n")
        except Exception:
            pass
        _stop_event.wait(10)

if BOINC_MODE:
    threading.Thread(target=_heartbeat, daemon=True).start()

# ---- Verifier -----------------------------------------------------------

def verify(m: int, X: int, Y: int) -> bool:
    rhs = (X**3
           + 1296 * m**2 * X**2
           + 15552 * m**3 * X
           + 46656 * m**4
           - 19 * m)
    return Y * Y == rhs

# ---- Sage daemon --------------------------------------------------------

class SageDaemon:
    """One long-running sage sage_large_daemon.sage process."""

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None

    def start(self):
        self._proc = subprocess.Popen(
            [SAGE_BIN, str(DAEMON_SAGE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        print(f"[sage daemon] started PID={self._proc.pid}, waiting for READY...", flush=True)
        # Wait up to 120 s for the daemon to emit READY
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                ready = select.select([self._proc.stdout], [], [], 5.0)[0]
            except (ValueError, OSError):
                break
            if not ready:
                if self._proc.poll() is not None:
                    raise RuntimeError("daemon died before READY")
                continue
            line = self._proc.stdout.readline().strip()
            if line == "READY":
                print("[sage daemon] READY", flush=True)
                return
            # Pre-READY output (startup messages) — ignore
            sys.stderr.write(f"  [daemon pre-ready] {line}\n")
        raise RuntimeError("daemon never sent READY within 120s")

    def stop(self):
        if self._proc:
            try:
                self._proc.stdin.write("QUIT\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=10)
            except Exception:
                self._proc.kill()
            self._proc = None
            print("[sage daemon] stopped", flush=True)

    def _ensure_alive(self):
        if self._proc is None or self._proc.poll() is not None:
            print("[sage daemon] (re)starting...", flush=True)
            self.start()

    def search(self, m: int, timeout: int) -> list:
        self._ensure_alive()
        solutions = []
        deadline  = time.monotonic() + timeout

        try:
            self._proc.stdin.write(f"{m}\n")
            self._proc.stdin.flush()
        except BrokenPipeError:
            print(f"[daemon] broken pipe m={m}, restarting", file=sys.stderr)
            self.stop()
            self.start()
            return solutions

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(f"[daemon] TIMEOUT m={m}", file=sys.stderr, flush=True)
                self.stop()
                break

            try:
                ready = select.select([self._proc.stdout], [], [], min(remaining, 2.0))[0]
            except (ValueError, OSError):
                break

            if not ready:
                if self._proc.poll() is not None:
                    print(f"[daemon] process died m={m}", file=sys.stderr)
                    self._proc = None
                    break
                continue

            try:
                line = self._proc.stdout.readline()
            except Exception:
                break

            if not line:
                self._proc = None
                break

            line = line.strip()
            if line.startswith("SOL "):
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        m_, X, Y = int(parts[1]), int(parts[2]), int(parts[3])
                        if verify(m_, X, Y):
                            solutions.append((m_, X, Y))
                        else:
                            print(f"[daemon] VERIFY_FAIL m={m_} X={X} Y={Y}", file=sys.stderr)
                    except ValueError:
                        pass
            elif line.startswith(("DONE ", "ERR ", "SKIP ")):
                if line.startswith("ERR "):
                    print(f"[daemon] {line}", file=sys.stderr)
                elif line.startswith("SKIP "):
                    sys.stderr.write(f"  [daemon] {line}\n")
                break

        # Drain stderr non-blocking
        try:
            import fcntl
            if self._proc:
                fd = self._proc.stderr.fileno()
                fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
                try:
                    err_text = self._proc.stderr.read()
                    if err_text:
                        for ln in err_text.splitlines():
                            if ln.strip():
                                print(f"  [sage] {ln}", file=sys.stderr)
                except Exception:
                    pass
                fcntl.fcntl(fd, fcntl.F_SETFL, fl)
        except Exception:
            pass

        return solutions

# ---- Work-unit I/O ------------------------------------------------------

def read_wu(wu_path: str) -> dict:
    d: dict = {"timeout_per_m": DEFAULT_TIMEOUT_PER_M}
    with open(wu_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition(" ")
            if key in ("m_start", "m_end"):
                d[key] = int(val.strip())
            elif key == "timeout_per_m":
                d[key] = int(val.strip())
    if "m_start" not in d or "m_end" not in d:
        raise ValueError("wu.txt must have m_start and m_end")
    return d


def load_ckpt(path: str) -> int:
    try:
        return int(json.loads(Path(path).read_text()).get("last_m", 0))
    except Exception:
        return 0


def save_ckpt(path: str, last_m: int):
    Path(path).write_text(json.dumps({"last_m": last_m}))

# ---- Main ---------------------------------------------------------------

def run(wu_path: str, result_path: str, ckpt_path: str):
    global _total_work, _work_done

    wu            = read_wu(wu_path)
    m_start       = wu["m_start"]
    m_end         = wu["m_end"]
    timeout_per_m = wu["timeout_per_m"]

    last_m  = load_ckpt(ckpt_path)
    resume  = max(m_start, last_m + 1) if last_m else m_start

    _total_work = abs(m_end - m_start) + 1
    _work_done  = max(0, abs(resume - m_start))

    step      = 1 if m_end >= m_start else -1
    last_ckpt = time.monotonic()
    total_sols = 0

    print(f"[worker] m=[{m_start},{m_end}] resume={resume} backend=sage_daemon", flush=True)

    daemon = SageDaemon()
    daemon.start()

    try:
        with open(result_path, "a") as res_fh:
            for m in range(resume, m_end + step, step):
                if m == 0:
                    _work_done += 1
                    continue

                t0   = time.monotonic()
                sols = daemon.search(m, timeout_per_m)
                elapsed = time.monotonic() - t0

                for m_, X, Y in sols:
                    res_fh.write(f"SOL {m_} {X} {Y}\n")
                    res_fh.flush()
                    total_sols += 1
                    dx = 6 * m_
                    dy = 6 * m_ * m_
                    if dx and X % dx == 0 and dy and Y % dy == 0:
                        x = X // dx
                        y = Y // dy
                        res_fh.write(f"USR {m_} {x} {y}\n")
                        res_fh.flush()
                    print(f"  SOLUTION m={m_} X={X} Y={Y}", flush=True)

                print(f"[m={m}] {len(sols)} sols  {elapsed:.1f}s", flush=True)
                _work_done += 1

                now = time.monotonic()
                if now - last_ckpt >= CKPT_INTERVAL:
                    save_ckpt(ckpt_path, m)
                    last_ckpt = now

    finally:
        daemon.stop()

    save_ckpt(ckpt_path, m_end)
    print(f"[worker] DONE solutions={total_sols} m=[{m_start},{m_end}]", flush=True)


def main():
    ap = argparse.ArgumentParser(description="CE worker: daemon Sage integral_points")
    ap.add_argument("wu",         nargs="?", default="wu.txt")
    ap.add_argument("result",     nargs="?", default="result.txt")
    ap.add_argument("checkpoint", nargs="?", default="checkpoint.json")
    args = ap.parse_args()
    try:
        run(args.wu, args.result, args.checkpoint)
    except KeyboardInterrupt:
        print("\n[worker] interrupted")
        sys.exit(0)
    except Exception as exc:
        print(f"[worker] FATAL: {exc}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)
    finally:
        _stop_event.set()


if __name__ == "__main__":
    main()
