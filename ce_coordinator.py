#!/usr/bin/env python3
"""Perpetual coordinator for Charity Engine style batch execution.

Generates non-overlapping x-chunks in *both* directions (positive and negative)
and runs ce_worker.py on each chunk forever, checkpointing progress in a state
file.  Positive and negative frontiers are expanded alternately so coverage is
symmetric around zero.

In real CE usage each chunk should be submitted as an independent distributed
work unit instead of being executed locally.

State file format::

    {
      "next_x_pos": <int>,   # start of next positive-side chunk  (>= 0)
      "next_x_neg": <int>    # end   of next negative-side chunk  (<  0)
    }

Backward-compatibility: legacy state files that contain only ``"next_x"`` are
automatically upgraded; the legacy value becomes ``next_x_pos`` and
``next_x_neg`` is initialised to ``-1``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def _load_state(state_path: Path) -> tuple[int, int]:
    """Return (next_x_pos, next_x_neg) from the state file (or defaults)."""
    if not state_path.exists():
        return 0, -1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if "next_x_pos" in state:
        return int(state["next_x_pos"]), int(state["next_x_neg"])
    # Legacy format: only "next_x" present.
    return int(state["next_x"]), -1


def _save_state(state_path: Path, next_x_pos: int, next_x_neg: int) -> None:
    state_path.write_text(
        json.dumps({"next_x_pos": next_x_pos, "next_x_neg": next_x_neg}, indent=2),
        encoding="utf-8",
    )


def _run_chunk(
    x_start: int,
    x_end: int,
    results_dir: str,
    worker: str,
    verbose: bool,
) -> None:
    out = Path(results_dir) / f"sol_{x_start}_{x_end}.jsonl"
    cmd = [
        "python3",
        worker,
        "--x-start",
        str(x_start),
        "--x-end",
        str(x_end),
        "--out",
        str(out),
    ]
    if verbose:
        cmd.append("--verbose")
    print("running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Perpetual bidirectional coordinator for the sumsof3cubes CE search."
    )
    ap.add_argument("--chunk-size", type=int, default=500, help="x-values per chunk")
    ap.add_argument("--state", type=str, default="ce_state.json")
    ap.add_argument("--results-dir", type=str, default="results")
    ap.add_argument(
        "--max-chunks",
        type=int,
        default=0,
        help="stop after this many chunks (0 = run forever)",
    )
    ap.add_argument(
        "--worker",
        type=str,
        default="ce_worker.py",
        help="path to the worker script",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="pass --verbose to each worker invocation",
    )
    ap.add_argument(
        "--positive-only",
        action="store_true",
        help="only expand the positive x frontier (legacy / single-sided mode)",
    )
    args = ap.parse_args()

    state_path = Path(args.state)
    next_x_pos, next_x_neg = _load_state(state_path)

    os.makedirs(args.results_dir, exist_ok=True)

    chunks_done = 0
    # Alternate: positive chunk, then negative chunk (unless --positive-only).
    do_positive = True

    while True:
        if args.max_chunks and chunks_done >= args.max_chunks:
            break

        if do_positive or args.positive_only:
            x_start = next_x_pos
            x_end = x_start + args.chunk_size - 1
            _run_chunk(x_start, x_end, args.results_dir, args.worker, args.verbose)
            next_x_pos = x_end + 1
            chunks_done += 1
        else:
            x_end = next_x_neg
            x_start = x_end - args.chunk_size + 1
            _run_chunk(x_start, x_end, args.results_dir, args.worker, args.verbose)
            next_x_neg = x_start - 1
            chunks_done += 1

        _save_state(state_path, next_x_pos, next_x_neg)

        if not args.positive_only:
            do_positive = not do_positive


if __name__ == "__main__":
    main()
