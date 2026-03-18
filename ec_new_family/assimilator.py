#!/usr/bin/env python3
"""
assimilator.py  —  Charity Engine / BOINC Assimilator for ec_new_family

Watches a results directory for completed result files, verifies each
claimed solution algebraically, merges new solutions into the master file,
and marks the work unit as done in the queue DB.

Equation:
    E_n : y² = X³ + a4(n)·X + a6(n),   X ≠ -3888·n²
    a4(n) = -45349632·n⁴ + 419904·n³
    a6(n) = 3·(39182082048·n⁶ - 544195584·n⁵ + 1259712·n⁴ - 19·n)

Usage (run continuously):
    while true; do
        python3 assimilator.py --results_dir ./results --master output/solutions.txt
        sleep 5
    done
"""

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR       = Path(__file__).parent
DEFAULT_MASTER = BASE_DIR / "output" / "solutions.txt"
PROCESSED_LOG  = BASE_DIR / "assimilated_results.log"

# ── Curve helpers ─────────────────────────────────────────────────────────────

def ec_rhs(n: int, X: int) -> int:
    a4 = -45349632 * n**4 + 419904 * n**3
    a6 = 3 * (39182082048 * n**6
              - 544195584 * n**5
              + 1259712   * n**4
              - 19        * n)
    return X**3 + a4*X + a6

def verify(n: int, X: int, y: int) -> bool:
    if X == -3888 * n * n:
        return False   # excluded point
    return y * y == ec_rhs(n, X)

# ── State helpers ─────────────────────────────────────────────────────────────

def load_processed() -> set:
    if not PROCESSED_LOG.exists():
        return set()
    return set(PROCESSED_LOG.read_text().splitlines())

def mark_processed(file_hash: str):
    with open(PROCESSED_LOG, "a") as f:
        f.write(file_hash + "\n")

def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def load_existing_solutions(master_path: str) -> set:
    seen = set()
    p = Path(master_path)
    if not p.exists():
        return seen
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            seen.add((int(parts[0]), int(parts[1]), int(parts[2])))
    return seen

def wu_name_to_n_xlo(fname: str):
    """Parse 'ec_nf_n+1_x+0000010000000000.txt' → (n, x_lo)."""
    stem = Path(fname).stem          # ec_nf_n+1_x+0000010000000000
    parts = stem.split("_")          # ['ec', 'nf', 'n+1', 'x+0000010000000000']
    n_str  = next((p[1:] for p in parts if p.startswith("n")), None)
    x_str  = next((p[1:] for p in parts if p.startswith("x")), None)
    if n_str is None or x_str is None:
        return None, None
    try:
        return int(n_str), int(x_str)
    except ValueError:
        return None, None

# ── Main loop ─────────────────────────────────────────────────────────────────

def process_file(result_path: str, master_path: str,
                 existing: set, verbose: bool) -> list:
    """Parse one result file, verify solutions, return list of new (n,X,y)."""
    new_solutions = []
    with open(result_path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                n, X, y = int(parts[0]), int(parts[1]), int(parts[2])
            except ValueError:
                continue

            if (n, X, y) in existing:
                continue

            if not verify(n, X, y):
                print(f"  [INVALID] {line}  — REJECTED (curve equation not satisfied)",
                      file=sys.stderr)
                continue

            if verbose:
                print(f"  [NEW] n={n}  X={X}  y={y}")
            new_solutions.append((n, X, y))
            existing.add((n, X, y))

    return new_solutions


def append_solutions(solutions: list, master_path: str):
    Path(master_path).parent.mkdir(parents=True, exist_ok=True)
    with open(master_path, "a") as f:
        for (n, X, y) in solutions:
            f.write(f"{n} {X} {y}\n")


def notify_queue(n: int, x_lo: int):
    """Call boinc_queue.py mark_done so the DB advances the frontier."""
    script = BASE_DIR / "boinc_queue.py"
    if script.exists():
        try:
            subprocess.run(
                [sys.executable, str(script), "mark_done", str(n), str(x_lo)],
                timeout=10, capture_output=True)
        except Exception:
            pass


def run(results_dir: str, master_path: str, verbose: bool):
    processed = load_processed()
    existing  = load_existing_solutions(master_path)
    results_dir_p = Path(results_dir)

    if not results_dir_p.exists():
        print(f"Results dir does not exist: {results_dir}", file=sys.stderr)
        return

    for rf in sorted(results_dir_p.glob("*.txt")):
        fhash = file_sha256(str(rf))
        if fhash in processed:
            continue

        if verbose:
            print(f"Processing: {rf.name}")

        new_sols = process_file(str(rf), master_path, existing, verbose)
        if new_sols:
            append_solutions(new_sols, master_path)
            print(f"  Added {len(new_sols)} solution(s) from {rf.name}")

        # Notify queue DB
        n_val, x_lo = wu_name_to_n_xlo(rf.name)
        if n_val is not None:
            notify_queue(n_val, x_lo)

        mark_processed(fhash)


def main():
    p = argparse.ArgumentParser(description="ec_new_family BOINC assimilator")
    p.add_argument("--results_dir", default=str(BASE_DIR / "results"),
                   help="Directory containing result files from volunteers")
    p.add_argument("--master", default=str(DEFAULT_MASTER),
                   help="Master solutions output file")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    run(args.results_dir, args.master, args.verbose)

if __name__ == "__main__":
    main()
