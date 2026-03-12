#!/usr/bin/env python3
"""Simple perpetual coordinator for Charity Engine style batch execution.

Creates non-overlapping x-chunks and runs ce_worker.py on each chunk forever,
checkpointing progress in a state file. In real CE usage, each chunk should be
submitted as a distributed work unit instead of local execution.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk-size", type=int, default=500)
    ap.add_argument("--state", type=str, default="ce_state.json")
    ap.add_argument("--results-dir", type=str, default="results")
    ap.add_argument("--max-chunks", type=int, default=0, help="0 => run forever")
    args = ap.parse_args()

    state_path = Path(args.state)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        next_x = int(state["next_x"])
    else:
        next_x = 0

    os.makedirs(args.results_dir, exist_ok=True)

    chunks_done = 0
    while True:
        if args.max_chunks and chunks_done >= args.max_chunks:
            break

        x_start = next_x
        x_end = x_start + args.chunk_size - 1
        out = Path(args.results_dir) / f"sol_{x_start}_{x_end}.jsonl"

        cmd = [
            "python3",
            "ce_worker.py",
            "--x-start",
            str(x_start),
            "--x-end",
            str(x_end),
            "--out",
            str(out),
        ]
        print("running:", " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)

        next_x = x_end + 1
        state_path.write_text(json.dumps({"next_x": next_x}, indent=2), encoding="utf-8")
        chunks_done += 1


if __name__ == "__main__":
    main()
