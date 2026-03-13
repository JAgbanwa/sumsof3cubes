#!/usr/bin/env python3
"""
assimilator_github.py  —  BOINC result assimilator with GitHub push
══════════════════════════════════════════════════════════════════════

Reads result files from completed BOINC work units, verifies every
solution,  deduplicates against the master solutions file, appends
new finds, and pushes the updated file to GitHub.

── Usage (standalone) ───────────────────────────────────────────────
python3 assimilator_github.py \\
    --result_dir  /path/to/boinc/results \\
    --master_file ./solutions_large.txt  \\
    --github_repo JAgbanwa/sumsof3cubes  \\
    --github_token $GITHUB_TOKEN         \\
    [--dry_run]

── Usage (BOINC daemon via handle_results) ──────────────────────────
Called automatically by BOINC's Python handler framework when a WU
reaches quorum.  See boinc_result_handler() below.

── solutions_large.txt format ───────────────────────────────────────
Each line:  m  X  Y  [x  y]
  • m, X, Y  = Weierstrass coordinates
  • x, y     = user coordinates (x=X/(6m), y=Y/(6m²)) if integer
══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import os
import sys
import re
import json
import time
import hashlib
import argparse
import subprocess
from pathlib import Path
from typing import Iterator

# ─────────────────────────────────────────────────────────────────────
# Verifier (pure Python, arbitrary precision)
# ─────────────────────────────────────────────────────────────────────

def verify_weierstrass(m: int, X: int, Y: int) -> bool:
    rhs = (X**3
           + 1296 * m**2 * X**2
           + 15552 * m**3 * X
           + 46656 * m**4
           - 19 * m)
    return Y * Y == rhs


def user_coords(m: int, X: int, Y: int):
    """Return (x, y) in user equation form, or None if not integer."""
    if m == 0:
        return None
    dx = 6 * m
    dy = 6 * m * m
    if X % dx == 0 and Y % dy == 0:
        return X // dx, Y // dy
    return None

# ─────────────────────────────────────────────────────────────────────
# Load / save master solutions file
# ─────────────────────────────────────────────────────────────────────

def load_solutions(path: Path) -> set[tuple[int, int, int]]:
    """Return set of (m, X, Y) already in master file."""
    sols: set[tuple[int, int, int]] = set()
    if not path.exists():
        return sols
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                sols.add((int(parts[0]), int(parts[1]), int(parts[2])))
            except ValueError:
                pass
    return sols


def append_solution(path: Path, m: int, X: int, Y: int):
    """Append a verified solution to the master file."""
    uvs = user_coords(m, X, Y)
    extra = f"  # user: x={uvs[0]} y={uvs[1]}" if uvs else ""
    with open(path, "a") as fh:
        fh.write(f"{m} {X} {Y}{extra}\n")

# ─────────────────────────────────────────────────────────────────────
# Parse a single result file
# ─────────────────────────────────────────────────────────────────────

def parse_result_file(rpath: Path) -> Iterator[tuple[int, int, int]]:
    """Yield verified (m, X, Y) from a worker result file."""
    for line in rpath.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
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
                print(f"[assimilator] VERIFY_FAIL {line}", file=sys.stderr)

# ─────────────────────────────────────────────────────────────────────
# GitHub push via git CLI
# ─────────────────────────────────────────────────────────────────────

def git_push(repo_dir: Path, commit_msg: str, token: str | None) -> bool:
    """Stage solutions_large.txt, commit, and push."""
    env = os.environ.copy()
    if token:
        env["GIT_ASKPASS"] = "echo"
        # Embed token in remote URL
        try:
            r = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=repo_dir, capture_output=True, text=True
            )
            origin = r.stdout.strip()
            if origin.startswith("https://") and "@" not in origin:
                new_origin = origin.replace(
                    "https://", f"https://x-token:{token}@")
                subprocess.run(
                    ["git", "remote", "set-url", "origin", new_origin],
                    cwd=repo_dir, env=env, check=True
                )
        except Exception as exc:
            print(f"[git] could not patch remote URL: {exc}", file=sys.stderr)

    cmds = [
        ["git", "add", "solutions_large.txt",
                       "ce_large_search/solutions_large.txt",
                       "."],
        ["git", "commit", "--allow-empty", "-m", commit_msg],
        ["git", "push", "origin", "HEAD"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, cwd=repo_dir, capture_output=True,
                           text=True, env=env)
        if r.returncode != 0:
            # "nothing to commit" is OK
            if "nothing to commit" in r.stdout + r.stderr:
                return True
            print(f"[git] {' '.join(cmd)} → {r.stderr.strip()}",
                  file=sys.stderr)
            return False
    return True

# ─────────────────────────────────────────────────────────────────────
# Main assimilation loop
# ─────────────────────────────────────────────────────────────────────

def assimilate(
    result_dir: Path,
    master_file: Path,
    repo_dir: Path | None,
    github_token: str | None,
    dry_run: bool,
    poll_interval: int,
    daemon: bool,
):
    """Process result files in result_dir, update master_file, push to GitHub."""
    processed_log = master_file.parent / "processed_results.log"
    processed: set[str] = set()
    if processed_log.exists():
        processed = set(processed_log.read_text().splitlines())

    def _run_once():
        nonlocal processed
        known = load_solutions(master_file)
        new_solutions = 0

        for rfile in sorted(result_dir.glob("*.result")):
            key = rfile.name
            if key in processed:
                continue

            print(f"[assimilator] processing {rfile.name}")
            found_in_file = 0
            for m, X, Y in parse_result_file(rfile):
                triple = (m, X, Y)
                if triple not in known:
                    if not dry_run:
                        append_solution(master_file, m, X, Y)
                    known.add(triple)
                    new_solutions += 1
                    found_in_file += 1
                    uvs = user_coords(m, X, Y)
                    print(f"  NEW SOLUTION m={m} X={X} Y={Y}"
                          + (f"  user x={uvs[0]} y={uvs[1]}" if uvs else ""))

            if not dry_run:
                with open(processed_log, "a") as fh:
                    fh.write(key + "\n")
                processed.add(key)

        if new_solutions > 0 and repo_dir and not dry_run:
            msg = (f"[CE auto] +{new_solutions} new solution(s) "
                   f"for y²=(36/m)x³+36x²+12mx+(m³−19)/m  "
                   f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
            ok = git_push(repo_dir, msg, github_token)
            if ok:
                print(f"[assimilator] pushed {new_solutions} solutions to GitHub")
            else:
                print("[assimilator] GitHub push failed", file=sys.stderr)
        elif new_solutions > 0:
            print(f"[assimilator] dry-run: would commit {new_solutions} solutions")

    if daemon:
        print(f"[assimilator] daemon mode, polling every {poll_interval}s")
        while True:
            try:
                _run_once()
            except KeyboardInterrupt:
                print("\n[assimilator] stopped")
                break
            except Exception as exc:
                print(f"[assimilator] error: {exc}", file=sys.stderr)
            time.sleep(poll_interval)
    else:
        _run_once()


# ─────────────────────────────────────────────────────────────────────
# BOINC handle_results hook (called by BOINC's Python handler)
# ─────────────────────────────────────────────────────────────────────

def boinc_result_handler(wu, results, canonical_result):
    """
    Called by BOINC's Python assimilator framework.
    wu                — WorkUnit object
    results           — list of Result objects
    canonical_result  — the validated canonical result
    """
    master = Path(os.environ.get("MASTER_SOLUTIONS",
                                 "solutions_large.txt"))
    repo   = Path(os.environ.get("REPO_DIR", "."))
    token  = os.environ.get("GITHUB_TOKEN")

    result_path = Path(canonical_result.output_files[0].path)
    known = load_solutions(master)
    new_count = 0

    for m, X, Y in parse_result_file(result_path):
        t = (m, X, Y)
        if t not in known:
            append_solution(master, m, X, Y)
            known.add(t)
            new_count += 1

    if new_count > 0:
        msg = (f"[CE auto] +{new_count} new solution(s) "
               f"wu={wu.name}  "
               f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
        git_push(repo, msg, token)

# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Assimilate CE results and push new solutions to GitHub")
    ap.add_argument("--result_dir",
        default="results", help="Directory containing *.result files")
    ap.add_argument("--master_file",
        default="solutions_large.txt",
        help="Path to master solutions file")
    ap.add_argument("--repo_dir",
        default=".", help="Local git clone of JAgbanwa/sumsof3cubes")
    ap.add_argument("--github_token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub PAT for push (or set $GITHUB_TOKEN)")
    ap.add_argument("--dry_run", action="store_true")
    ap.add_argument("--daemon",  action="store_true",
        help="Poll result_dir indefinitely")
    ap.add_argument("--poll_interval",
        type=int, default=60,
        help="Seconds between polls in daemon mode")
    args = ap.parse_args()

    assimilate(
        result_dir    = Path(args.result_dir),
        master_file   = Path(args.master_file),
        repo_dir      = Path(args.repo_dir),
        github_token  = args.github_token,
        dry_run       = args.dry_run,
        poll_interval = args.poll_interval,
        daemon        = args.daemon,
    )


if __name__ == "__main__":
    main()
