#!/usr/bin/env python3
"""
ec19n_assimilator.py  —  BOINC assimilator for ec19n project.

Watches the BOINC results directory (or a local output/ directory),
verifies each solution algebraically, deduplicates, and writes new
solutions to output/solutions_ec19n.txt.

Run modes:
  --mode boinc     : integrate with BOINC assimilator framework
  --mode standalone: watch output/ directory (default; for local use)

Solution file columns:   n  x  y  k
  where k = y/(6n), all integers.

BOINC integration:
  Called by the BOINC server once per canonical result with argv[1] = result path.
  Also calls `ec19n_boinc_queue.py mark_done <wu_name>` to advance the frontier.
"""

import argparse, subprocess, time, os, sys
from pathlib import Path

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
MASTER     = OUTPUT_DIR / "solutions_ec19n.txt"
OUTPUT_DIR.mkdir(exist_ok=True)


def verify(n: int, x: int, y: int, k: int) -> bool:
    if n not in (1, -1, 19, -19): return False
    if y != k * 6 * n:            return False
    rhs = x**3 + 1296*n**2*x**2 + 15552*n**3*x + 46656*n**4 - 19*n
    return y * y == rhs


def load_known() -> set:
    seen = set()
    if MASTER.exists():
        for line in MASTER.read_text().splitlines():
            parts = line.split()
            if len(parts) == 4:
                try:
                    seen.add(tuple(int(v) for v in parts))
                except ValueError:
                    pass
    return seen


def process_result_file(path: Path, seen: set, out_f) -> int:
    new = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 4:
            continue
        try:
            sol = tuple(int(v) for v in parts)
        except ValueError:
            continue
        if sol in seen:
            continue
        n, x, y, k = sol
        if not verify(n, x, y, k):
            print(f"  [WARN] verify failed: {sol}", flush=True)
            continue
        seen.add(sol)
        out_f.write(f"{n} {x} {y} {k}\n")
        out_f.flush()
        new += 1
        print(f"  ★ NEW SOLUTION  n={n}  x={x}  y={y}  k=y/(6n)={k}", flush=True)
    return new


def standalone_watch(scan_dirs: list[Path], interval: int = 30) -> None:
    seen   = load_known()
    print(f"  Loaded {len(seen)} known solutions.")
    scanned: set = set()

    with MASTER.open("a", buffering=1) as out_f:
        while True:
            new_total = 0
            for d in scan_dirs:
                for result_file in sorted(d.glob("*.txt")):
                    key = (str(result_file), result_file.stat().st_mtime)
                    if key in scanned:
                        continue
                    scanned.add(key)
                    new = process_result_file(result_file, seen, out_f)
                    new_total += new
            if new_total:
                print(f"  [{time.strftime('%H:%M:%S')}] +{new_total} new, "
                      f"total={len(seen)}", flush=True)
            time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="standalone",
                    choices=["standalone", "boinc"])
    ap.add_argument("--scan", nargs="*",
                    default=[str(OUTPUT_DIR)],
                    help="Directories to scan for result files")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()

    scan_dirs = [Path(d) for d in (args.scan or [])]

    print("=" * 60)
    print("  ec19n ASSIMILATOR")
    print("  Collecting solutions with y/(6n) ∈ ℤ")
    print(f"  Watching: {[str(d) for d in scan_dirs]}")
    print("=" * 60, flush=True)

    if args.mode == "standalone":
        standalone_watch(scan_dirs, args.interval)
    else:
        # BOINC mode: called once per canonical result by the BOINC framework.
        # argv[1] = canonical result file path; the WU name is the file's stem.
        if len(sys.argv) < 2:
            print("BOINC mode: pass canonical result file as argument")
            sys.exit(1)
        seen   = load_known()
        result = Path(sys.argv[1])
        with MASTER.open("a", buffering=1) as out_f:
            new = process_result_file(result, seen, out_f)
        print(f"Assimilated {new} new solutions.")

        # Notify the queue manager that this WU is done so the frontier advances
        wu_name = result.stem   # strip .txt suffix if present
        queue_script = Path(__file__).parent / "ec19n_boinc_queue.py"
        try:
            subprocess.run(
                [sys.executable, str(queue_script), "mark_done", wu_name],
                timeout=10, check=False)
        except Exception as exc:
            print(f"[WARN] mark_done failed for {wu_name}: {exc}")


if __name__ == "__main__":
    main()
