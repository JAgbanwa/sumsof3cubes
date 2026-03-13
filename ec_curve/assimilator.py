#!/usr/bin/env python3
"""
assimilator.py  —  Charity Engine / BOINC Assimilator

Watches a results directory for new result files, verifies each
claimed solution algebraically, and merges new solutions into a
master solutions file.

Equation:
    y² = x³ + 1296·n²·x² + 15552·n³·x + (46656·n⁴ − 19·n)

Usage:
    python3 assimilator.py \
        --results_dir ./results \
        --master      solutions_master.txt

Run continuously (e.g. via systemd or screen):
    while true; do python3 assimilator.py ...; sleep 5; done
"""

import os
import sys
import time
import argparse
import hashlib
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════
# Equation helpers
# ══════════════════════════════════════════════════════════════════════

def ec_rhs(n: int, x: int) -> int:
    return (x**3
            + 1296 * n**2 * x**2
            + 15552 * n**3 * x
            + 46656 * n**4
            - 19 * n)


def verify(n: int, x: int, y: int) -> bool:
    return y * y == ec_rhs(n, x)


# ══════════════════════════════════════════════════════════════════════
# State helpers
# ══════════════════════════════════════════════════════════════════════

PROCESSED_LOG = "assimilated_results.log"


def load_processed(log_path: str) -> set:
    processed = set()
    if os.path.exists(log_path):
        with open(log_path) as f:
            for line in f:
                processed.add(line.strip())
    return processed


def file_hash(path: str) -> str:
    """SHA-256 hex digest of a file (used to avoid double-processing)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_existing_solutions(master_path: str) -> set:
    seen = set()
    if not os.path.exists(master_path):
        return seen
    with open(master_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 3:
                try:
                    n, x, y = int(parts[0]), int(parts[1]), int(parts[2])
                    seen.add((n, x, abs(y)))
                except ValueError:
                    pass
    return seen


# ══════════════════════════════════════════════════════════════════════
# Core: process one result file
# ══════════════════════════════════════════════════════════════════════

def process_result(rpath: str, master_path: str,
                   seen_solutions: set, proc_log: str) -> int:
    new_found = 0
    lines = Path(rpath).read_text(errors="replace").splitlines()

    with open(master_path, "a", buffering=1) as mf:
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 3:
                continue
            try:
                n, x, y = int(parts[0]), int(parts[1]), int(parts[2])
            except ValueError:
                continue

            key = (n, x, abs(y))
            if key in seen_solutions:
                continue

            if not verify(n, x, abs(y)):
                print(f"[assimilator] INVALID (skipped): n={n} x={x} y={y}")
                continue

            seen_solutions.add(key)
            mf.write(f"{n} {x} {y}\n")
            print(f"  ★ NEW SOLUTION   n={n:>14}   x={x:>22}   y={y:>22}")
            new_found += 1

    # Mark file as processed
    with open(proc_log, "a") as pf:
        pf.write(file_hash(rpath) + "\n")

    return new_found


# ══════════════════════════════════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════════════════════════════════

def run(args):
    results_dir  = Path(args.results_dir)
    master_path  = args.master
    proc_log     = args.processed_log

    results_dir.mkdir(parents=True, exist_ok=True)

    processed      = load_processed(proc_log)
    seen_solutions = load_existing_solutions(master_path)

    print(f"[assimilator] Started. Watching {results_dir}")
    print(f"[assimilator] Master: {master_path}   "
          f"existing_solutions={len(seen_solutions)}")

    total_new = 0
    while True:
        result_files = sorted(results_dir.glob("result_*.txt")) + \
                       sorted(results_dir.glob("*.result"))
        for rpath in result_files:
            fh = file_hash(str(rpath))
            if fh in processed:
                continue
            print(f"[assimilator] Processing {rpath.name} …")
            count = process_result(str(rpath), master_path,
                                   seen_solutions, proc_log)
            processed.add(fh)
            total_new += count
            print(f"[assimilator] {rpath.name}: +{count} new  "
                  f"total_all_time={total_new}")

        time.sleep(args.poll)


# ══════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="Assimilator for ec_curve Charity Engine project")
    ap.add_argument("--results_dir", default="./results",
                    help="Directory where BOINC drops result files")
    ap.add_argument("--master", default="solutions_master.txt",
                    help="Master solutions output file")
    ap.add_argument("--processed_log", default=PROCESSED_LOG,
                    help="Log of processed result file hashes")
    ap.add_argument("--poll", type=float, default=5.0,
                    help="Polling interval in seconds (default 5)")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
