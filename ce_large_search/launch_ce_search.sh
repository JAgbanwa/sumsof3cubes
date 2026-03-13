#!/usr/bin/env bash
# launch_ce_search.sh  —  master launcher for the large-m EC search
# ══════════════════════════════════════════════════════════════════════
# Starts three long-running processes:
#   1. Local parallel PARI worker pool (N_LOCAL_WORKERS processes)
#   2. Work-unit queue daemon (replenishes WU files from 10^20 outward)
#   3. Assimilator daemon (pushes new solutions to GitHub)
#
# When a BOINC project path is provided (--boinc), it also starts the
# CE work generator which submits to BOINC.
#
# Usage:
#   ./launch_ce_search.sh [--boinc PROJ_DIR] [--workers N] [--token TOKEN]
# ══════════════════════════════════════════════════════════════════════

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CE_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── defaults ──────────────────────────────────────────────────────────
N_LOCAL_WORKERS=4
BOINC_PROJ_DIR=""
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
WU_DIR="$CE_DIR/wu_files"
RESULT_DIR="$CE_DIR/results"
MASTER_FILE="$REPO_DIR/solutions_large.txt"
LOG_DIR="$CE_DIR/logs"
SEARCH_FLOOR=100000000000000000000   # 10^20

# ── argument parsing ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --boinc)     BOINC_PROJ_DIR="$2"; shift 2 ;;
        --workers)   N_LOCAL_WORKERS="$2"; shift 2 ;;
        --token)     GITHUB_TOKEN="$2"; shift 2 ;;
        --floor)     SEARCH_FLOOR="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

mkdir -p "$WU_DIR" "$RESULT_DIR" "$LOG_DIR"
touch "$MASTER_FILE"

echo "═══════════════════════════════════════════════════════════════"
echo " CE Large-m EC Search Launcher"
echo " Equation: y² = (36/m)x³ + 36x² + 12mx + (m³−19)/m"
echo " Search floor: |m| ≥ $SEARCH_FLOOR"
echo " Local workers: $N_LOCAL_WORKERS"
echo "═══════════════════════════════════════════════════════════════"

# ── 1. Pre-generate local WU queue ───────────────────────────────────
echo "[launch] Generating initial WU queue…"
python3 "$CE_DIR/work_generator_large.py" \
    --wu_dir "$WU_DIR" \
    --count 200 \
    --search_floor "$SEARCH_FLOOR" \
    --block_size 50 \
    --dry_run \
    2>&1 | tee "$LOG_DIR/wg_init.log"

# ── 2. Assimilator daemon ─────────────────────────────────────────────
echo "[launch] Starting assimilator daemon…"
GITHUB_TOKEN="$GITHUB_TOKEN" \
python3 "$CE_DIR/assimilator_github.py" \
    --result_dir  "$RESULT_DIR" \
    --master_file "$MASTER_FILE" \
    --repo_dir    "$REPO_DIR" \
    --daemon \
    --poll_interval 120 \
    > "$LOG_DIR/assimilator.log" 2>&1 &
ASSIM_PID=$!
echo "  assimilator PID=$ASSIM_PID"

# ── 3. Local parallel worker pool ────────────────────────────────────
echo "[launch] Starting $N_LOCAL_WORKERS local workers…"

# Worker dispatch loop — picks next available wu file and runs it
run_worker() {
    local wid="$1"
    local logfile="$LOG_DIR/worker_${wid}.log"
    echo "[worker $wid] started" >> "$logfile"

    while true; do
        # Grab the next unprocessed wu file (atomic via mv)
        local wu_file
        wu_file=$(find "$WU_DIR" -name "*.wu" | sort | head -1 2>/dev/null || true)
        if [[ -z "$wu_file" ]]; then
            echo "[worker $wid] queue empty, waiting 30s…" >> "$logfile"
            sleep 30
            # Try to generate more WUs
            python3 "$CE_DIR/work_generator_large.py" \
                --wu_dir "$WU_DIR" --count 20 \
                --search_floor "$SEARCH_FLOOR" \
                >> "$LOG_DIR/wg_refill.log" 2>&1 || true
            continue
        fi

        # Claim the WU by moving it to in-progress
        local wu_name
        wu_name=$(basename "$wu_file" .wu)
        local in_dir="$WU_DIR/in_progress"
        mkdir -p "$in_dir"
        local claimed="$in_dir/${wu_name}.wu"
        mv "$wu_file" "$claimed" 2>/dev/null || continue

        local result_file="$RESULT_DIR/${wu_name}.result"
        local ckpt_file="$in_dir/${wu_name}.checkpoint.json"

        echo "[worker $wid] processing $wu_name" >> "$logfile"

        python3 "$CE_DIR/worker_pari_large.py" \
            "$claimed" "$result_file" "$ckpt_file" \
            >> "$logfile" 2>&1

        # Mark done
        mv "$claimed" "$in_dir/${wu_name}.done" 2>/dev/null || true
        echo "[worker $wid] done $wu_name" >> "$logfile"
    done
}

WORKER_PIDS=()
for i in $(seq 1 "$N_LOCAL_WORKERS"); do
    run_worker "$i" &
    WORKER_PIDS+=($!)
    echo "  local worker $i PID=${WORKER_PIDS[-1]}"
    sleep 0.5   # stagger starts
done

# ── 4. Optional: BOINC CE work generator ────────────────────────────
if [[ -n "$BOINC_PROJ_DIR" ]]; then
    echo "[launch] Starting BOINC work generator for $BOINC_PROJ_DIR…"
    python3 "$CE_DIR/work_generator_large.py" \
        --wu_dir "$WU_DIR" \
        --boinc_project_dir "$BOINC_PROJ_DIR" \
        --count 500 \
        --search_floor "$SEARCH_FLOOR" \
        --daemon \
        --interval 300 \
        > "$LOG_DIR/boinc_wg.log" 2>&1 &
    BWGPID=$!
    echo "  BOINC work-gen PID=$BWGPID"
fi

# ── 5. Monitor ────────────────────────────────────────────────────────
echo ""
echo "[launch] All processes started.  Monitoring every 60s…"
echo "  Logs:      $LOG_DIR/"
echo "  Solutions: $MASTER_FILE"
echo "  Press Ctrl+C to stop all."
echo ""

trap 'echo "Stopping…"; kill "${WORKER_PIDS[@]}" $ASSIM_PID ${BWGPID:-} 2>/dev/null; exit 0' INT TERM

while true; do
    n_sol=$(wc -l < "$MASTER_FILE" 2>/dev/null || echo 0)
    n_wu_pending=$(find "$WU_DIR" -name "*.wu" 2>/dev/null | wc -l)
    n_wu_done=$(find "$WU_DIR/in_progress" -name "*.done" 2>/dev/null | wc -l)
    echo "$(date '+%H:%M:%S')  solutions=$n_sol  pending_wus=$n_wu_pending  done_wus=$n_wu_done"
    sleep 60
done
