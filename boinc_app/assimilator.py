#!/usr/bin/env python3
"""
BOINC Assimilator for sum-of-cubes searcher.

Collects validated results from all work units and merges them into
a single master solutions file. Keeps track of which (n,x,y) tuples
are already recorded to avoid duplicates.

Run continuously:
    python3 assimilator.py --results_dir /results --master solutions_master.txt

The assimilator watches for new result files dropped in results_dir
by the BOINC daemon and appends new solutions to the master file.
"""

import os
import sys
import time
import argparse
import hashlib

PROCESSED_LOG = "assimilated_results.log"

def verify_solution(n, x, y):
    t = 4 * n + 3
    A = 81 * t * t
    B = 243 * t * t * t
    C = t * (11664 * n**3 + 26244 * n**2 + 19683 * n + 4916)
    rhs = x**3 + A * x**2 + B * x + C
    return rhs == y * y

def load_processed(log_path):
    processed = set()
    if os.path.exists(log_path):
        with open(log_path) as f:
            for line in f:
                processed.add(line.strip())
    return processed

def load_existing_solutions(master_path):
    seen = set()
    if os.path.exists(master_path):
        with open(master_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("n="):
                    # format: n=... x=... y=...
                    parts = line.replace("n=","").replace("x=","").replace("y=","").split()
                    if len(parts) >= 3:
                        n, x, y = int(parts[0]), int(parts[1]), int(parts[2])
                        seen.add((n, x, abs(y)))
    return seen

def process_result_file(rpath, master_file, seen_solutions, processed_log):
    new_found = 0
    with open(rpath) as f:
        lines = f.readlines()

    with open(master_file, "a", buffering=1) as mf:
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
            if not verify_solution(n, x, abs(y)):
                print(f"[assimilator] INVALID (skipped): n={n} x={x} y={y}")
                continue
            seen_solutions.add(key)
            mf.write(f"n={n} x={x} y={y}\n")
            print(f"[NEW SOLUTION] n={n}  x={x}  y={y}")
            new_found += 1

    with open(processed_log, "a") as pl:
        pl.write(os.path.basename(rpath) + "\n")

    return new_found

def run(results_dir, master_path, poll_interval=30):
    processed = load_processed(PROCESSED_LOG)
    seen_solutions = load_existing_solutions(master_path)
    total_new = 0

    print(f"[assimilator] Watching {results_dir}")
    print(f"[assimilator] Master: {master_path} ({len(seen_solutions)} existing solutions)")

    while True:
        files = [f for f in os.listdir(results_dir)
                 if f.endswith(".txt") and f not in processed]
        for fname in sorted(files):
            fpath = os.path.join(results_dir, fname)
            n_new = process_result_file(fpath, master_path, seen_solutions, PROCESSED_LOG)
            total_new += n_new
            processed.add(fname)
            if n_new:
                print(f"[assimilator] {fname}: {n_new} new solutions (total={total_new})")

        time.sleep(poll_interval)

def main():
    parser = argparse.ArgumentParser(description="BOINC assimilator")
    parser.add_argument("--results_dir", default="results",
                        help="Directory of validated result files")
    parser.add_argument("--master", default="solutions_master.txt",
                        help="Master output file")
    parser.add_argument("--poll", type=int, default=30,
                        help="Poll interval in seconds")
    args = parser.parse_args()

    os.makedirs(args.results_dir, exist_ok=True)
    run(args.results_dir, args.master, args.poll)

if __name__ == "__main__":
    main()
