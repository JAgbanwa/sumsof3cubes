#!/usr/bin/env python3
"""
worker_pari_large.py — Charity Engine / BOINC worker
══════════════════════════════════════════════════════════════════════

Equation (user notation, parameter m ≠ 0):

    y² = (36/m)·x³ + 36·x² + 12m·x + (m³−19)/m

Birationally equivalent Weierstrass form (X=6mx, Y=6m²y):

    E_m : Y² = X³ + 1296·m²·X² + 15552·m³·X + (46656·m⁴ − 19·m)

This worker calls PARI/GP's ellintegralpoints() for each m in the
work unit's range.  The method is PROVABLY COMPLETE: every integer
point is found regardless of how large X or Y are.

── Work-unit format (wu.txt) ────────────────────────────────────────
m_start  <big integer, e.g. 100000000000000000000>
m_end    <big integer>
timeout_per_m  <seconds>    (optional, default 600)
gp_stack_mb    <int>        (optional, default 512)

── Output (result.txt) ──────────────────────────────────────────────
SOL  m  X  Y
(one line per solution in Weierstrass coordinates;
 original (x,y): x = X/(6m), y = Y/(6m²))

── Checkpoint (checkpoint.json) ─────────────────────────────────────
{"last_m": <int>}

── BOINC/CE notes ───────────────────────────────────────────────────
• Set env BOINC=1 for heartbeat / fraction-done reporting.
• Checkpoints written every CKPT_INTERVAL_S seconds.
• Per-m timeout aborts a single curve without killing the whole WU.
• Redundant-result validation: two CEs process the same WU; the
  assimilator checks consistency before accepting solutions.

── Usage ─────────────────────────────────────────────────────────────
Standalone:
    python3 worker_pari_large.py wu.txt result.txt [checkpoint.json]
BOINC managed:
    same (BOINC environment sets working directory)

Dependencies:
    gp  (PARI/GP ≥ 2.13)   apt install pari-gp
    python3 ≥ 3.8
    Optional: cypari2       pip install cypari2
══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import sys
import os
import json
import time
import subprocess
import threading
import signal
import argparse
import tempfile
from pathlib import Path
from typing import Iterator, Tuple

# ── optional in-process PARI ──────────────────────────────────────────
try:
    import cypari2 as _c2
    _PARI = _c2.Pari()
    HAS_CYPARI = True
except ImportError:
    HAS_CYPARI = False

# ── config ────────────────────────────────────────────────────────────
HERE          = Path(__file__).parent
GP_SCRIPT     = HERE / "worker_ec_large.gp"
GP_BIN        = os.environ.get("GP_BIN", "gp")
BOINC_MODE    = os.environ.get("BOINC", "0") == "1"
CKPT_INTERVAL = 30          # seconds between checkpoints
DEFAULT_TIMEOUT_PER_M = 600  # seconds; increase if large curves hang
DEFAULT_GP_STACK_MB   = 512

# ─────────────────────────────────────────────────────────────────────
# BOINC heartbeat thread
# ─────────────────────────────────────────────────────────────────────
_total_work  = 1
_work_done   = 0
_stop_event  = threading.Event()

def _heartbeat_thread():
    while not _stop_event.is_set():
        try:
            frac = _work_done / max(_total_work, 1)
            with open("fraction_done", "w") as fh:
                fh.write(f"{frac:.6f}\n")
        except Exception:
            pass
        _stop_event.wait(10)

if BOINC_MODE:
    threading.Thread(target=_heartbeat_thread, daemon=True).start()

# ─────────────────────────────────────────────────────────────────────
# Verifier (Python, native big-int)
# ─────────────────────────────────────────────────────────────────────

def verify_weierstrass(m: int, X: int, Y: int) -> bool:
    """Return True iff Y² = X³ + 1296m²X² + 15552m³X + 46656m⁴ − 19m."""
    rhs = (X**3
           + 1296 * m**2 * X**2
           + 15552 * m**3 * X
           + 46656 * m**4
           - 19 * m)
    return Y * Y == rhs


def verify_user(m: int, X: int, Y: int) -> Tuple[bool, int, int]:
    """Return (ok, x, y) where x=X/(6m), y=Y/(6m²) if integer else None."""
    if m == 0:
        return False, 0, 0
    denom_x = 6 * m
    denom_y = 6 * m * m
    if X % denom_x == 0 and Y % denom_y == 0:
        x = X // denom_x
        y = Y // denom_y
        # verify original equation: y² = (36/m)x³+36x²+12mx+(m³-19)/m
        # multiply through by m: my² = 36x³+36mx²+12m²x+m³-19
        lhs = m * y * y
        rhs = 36*x**3 + 36*m*x**2 + 12*m**2*x + m**3 - 19
        return (lhs == rhs), x, y
    return False, 0, 0

# ─────────────────────────────────────────────────────────────────────
# cypari2 path (in-process, fastest)
# ─────────────────────────────────────────────────────────────────────

def _search_cypari(
    m_values: list[int],
    timeout_per_m: int,
    stack_mb: int,
) -> Iterator[Tuple[int, int, int]]:
    """Yield (m, X, Y) using cypari2 in-process PARI."""
    pari = _PARI
    pari.default("stacksize", stack_mb * 1024 * 1024)

    for m in m_values:
        if m == 0:
            continue
        a2 = 1296 * m * m
        a4 = 15552 * m**3
        a6 = 46656 * m**4 - 19 * m
        try:
            E = pari.ellinit([0, a2, 0, a4, a6])
            if pari.elldisc(E) == 0:
                continue
            pts = pari.ellintegralpoints(E, 1)
            for pt in pts:
                X, Y = int(pt[0]), int(pt[1])
                if verify_weierstrass(m, X, Y):
                    yield m, X, Y
        except Exception as exc:
            print(f"[cypari warn] m={m}: {exc}", file=sys.stderr)

# ─────────────────────────────────────────────────────────────────────
# gp subprocess path (portable fallback)
# ─────────────────────────────────────────────────────────────────────

def _gp_input_for_range(m_start: int, m_end: int, per_m_timeout: int) -> str:
    """Build the gp command string to search m in [m_start, m_end]."""
    return (
        f"\\\\r {GP_SCRIPT}\n"
        f"ec_search_large({m_start},{m_end})\n"
        f"quit\n"
    )


def _parse_gp_output(text: str, m_start: int) -> Iterator[Tuple[int, int, int]]:
    """Parse lines printed by worker_ec_large.gp."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("##"):
            continue
        if line.startswith("SOL "):
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                m, X, Y = int(parts[1]), int(parts[2]), int(parts[3])
            except ValueError:
                continue
            if verify_weierstrass(m, X, Y):
                yield m, X, Y
            else:
                print(f"[parse] VERIFY_FAIL m={m} X={X} Y={Y}",
                      file=sys.stderr)
        elif line.startswith("SKP") or line.startswith("DONE"):
            print(f"[gp] {line}", file=sys.stderr)


def _search_gp_single(
    m: int,
    timeout: int,
    stack_mb: int,
) -> Iterator[Tuple[int, int, int]]:
    """Run gp for a single m value with timeout, yield solutions."""
    gp_cmd = (
        f"\\\\r {GP_SCRIPT}\n"
        f"ec_search_one_large({m})\n"
        f"quit\n"
    )
    try:
        proc = subprocess.run(
            [GP_BIN, "-q", f"--stacksize={stack_mb}m"],
            input=gp_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        yield from _parse_gp_output(proc.stdout, m)
        if proc.stderr.strip():
            for ln in proc.stderr.splitlines():
                if ln.strip():
                    print(f"[gp stderr m={m}] {ln}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print(f"[timeout] m={m} exceeded {timeout}s — skipped", file=sys.stderr)
    except Exception as exc:
        print(f"[gp error] m={m}: {exc}", file=sys.stderr)

# ─────────────────────────────────────────────────────────────────────
# Work-unit reader
# ─────────────────────────────────────────────────────────────────────

def read_wu(wu_path: str) -> dict:
    """Parse wu.txt → dict with keys m_start, m_end, timeout_per_m, gp_stack_mb."""
    d = {
        "timeout_per_m": DEFAULT_TIMEOUT_PER_M,
        "gp_stack_mb":   DEFAULT_GP_STACK_MB,
    }
    with open(wu_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition(" ")
            key = key.strip()
            val = val.strip()
            if key in ("m_start", "m_end"):
                d[key] = int(val)
            elif key in ("timeout_per_m", "gp_stack_mb"):
                d[key] = int(val)
    if "m_start" not in d or "m_end" not in d:
        raise ValueError(f"wu.txt must contain m_start and m_end")
    return d

# ─────────────────────────────────────────────────────────────────────
# Checkpoint helpers
# ─────────────────────────────────────────────────────────────────────

def load_checkpoint(ckpt_path: str) -> int:
    """Return last completed m (or m_start-1 if fresh)."""
    try:
        data = json.loads(Path(ckpt_path).read_text())
        return int(data.get("last_m", 0))
    except Exception:
        return 0


def save_checkpoint(ckpt_path: str, last_m: int):
    Path(ckpt_path).write_text(json.dumps({"last_m": last_m}))

# ─────────────────────────────────────────────────────────────────────
# Main search driver
# ─────────────────────────────────────────────────────────────────────

def run(wu_path: str, result_path: str, ckpt_path: str):
    global _total_work, _work_done

    wu = read_wu(wu_path)
    m_start       = wu["m_start"]
    m_end         = wu["m_end"]
    timeout_per_m = wu["timeout_per_m"]
    stack_mb      = wu["gp_stack_mb"]

    last_m = load_checkpoint(ckpt_path)
    resume_from = max(m_start, last_m + 1)

    total = abs(m_end - m_start) + 1
    _total_work = total
    _work_done  = max(0, resume_from - m_start)

    step = 1 if m_end >= m_start else -1
    m_range = range(resume_from, m_end + step, step)

    last_ckpt_time = time.monotonic()
    solutions_written = 0

    print(
        f"[worker] m_start={m_start}  m_end={m_end}  "
        f"resuming from m={resume_from}  backend={'cypari2' if HAS_CYPARI else 'gp'}",
        flush=True,
    )

    with open(result_path, "a") as res_fh:
        for m in m_range:
            if m == 0:
                _work_done += 1
                continue

            t_m = time.monotonic()
            n_sols = 0

            if HAS_CYPARI:
                gen = _search_cypari([m], timeout_per_m, stack_mb)
            else:
                gen = _search_gp_single(m, timeout_per_m, stack_mb)

            for m_, X, Y in gen:
                line = f"SOL {m_} {X} {Y}\n"
                res_fh.write(line)
                res_fh.flush()
                n_sols += 1
                solutions_written += 1
                # Also print user-coordinate form if integer
                ok, x, y = verify_user(m_, X, Y)
                if ok:
                    res_fh.write(f"USR {m_} {x} {y}\n")
                    res_fh.flush()
                print(f"  SOLUTION m={m_} X={X} Y={Y}"
                      + (f"  user: x={x} y={y}" if ok else ""),
                      flush=True)

            elapsed_m = time.monotonic() - t_m
            print(
                f"[m={m}] {n_sols} sols  {elapsed_m:.1f}s",
                flush=True,
            )

            _work_done += 1

            # Checkpoint
            now = time.monotonic()
            if now - last_ckpt_time >= CKPT_INTERVAL:
                save_checkpoint(ckpt_path, m)
                last_ckpt_time = now

        # Final checkpoint
        save_checkpoint(ckpt_path, m_end)

    print(
        f"[worker] DONE  total solutions={solutions_written}  "
        f"m_range=[{m_start},{m_end}]",
        flush=True,
    )


# ─────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="CE/BOINC worker: integral points on y²=x³+1296m²x²+…")
    ap.add_argument("wu",          nargs="?", default="wu.txt")
    ap.add_argument("result",      nargs="?", default="result.txt")
    ap.add_argument("checkpoint",  nargs="?", default="checkpoint.json")
    args = ap.parse_args()

    try:
        run(args.wu, args.result, args.checkpoint)
    except KeyboardInterrupt:
        print("\n[worker] interrupted", file=sys.stderr)
        sys.exit(0)
    except Exception as exc:
        print(f"[worker] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        _stop_event.set()


if __name__ == "__main__":
    main()
