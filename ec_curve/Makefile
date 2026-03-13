# Makefile for ec_curve  —  y² = x³ + 1296n²x² + 15552n³x + (46656n⁴−19n)
#
# Targets:
#   make               build standalone C worker
#   make boinc         build BOINC API–linked worker
#   make boinc_static  statically linked for CE distribution
#   make test          quick self-test: n ∈ [−100, 100], |x| ≤ 10⁶
#   make run_local     launch real-time local search (all CPU cores)
#   make clean

CC       = gcc
CFLAGS   = -O3 -march=native -std=c99 -Wall -Wextra
SRC      = worker_ec.c
BIN      = worker_ec
BIN_B    = worker_ec_boinc

BOINC_INC ?= /usr/include/boinc
BOINC_LIB ?= /usr/lib

.PHONY: all boinc boinc_static test run_local clean

# ── Default: standalone (no BOINC API) ───────────────────────────────
all: $(BIN)

$(BIN): $(SRC)
	$(CC) $(CFLAGS) -o $@ $< -lm
	@echo "[make] Built standalone: $(BIN)"

# ── BOINC API linked ─────────────────────────────────────────────────
boinc: $(SRC)
	$(CC) $(CFLAGS) -DBOINC \
	    -I$(BOINC_INC) -L$(BOINC_LIB) \
	    -o $(BIN_B) $< \
	    -lboinc_api -lboinc -lpthread -lm
	@echo "[make] Built BOINC worker: $(BIN_B)"

# ── Statically linked for CE distribution ────────────────────────────
boinc_static: $(SRC)
	$(CC) $(CFLAGS) -DBOINC \
	    -I$(BOINC_INC) -L$(BOINC_LIB) \
	    -static -o $(BIN_B)_static $< \
	    -lboinc_api -lboinc -lpthread -lm
	@echo "[make] Built static BOINC worker: $(BIN_B)_static"

# ── Self-test ─────────────────────────────────────────────────────────
test: $(BIN)
	@echo "n_start  -100"  >  /tmp/ec_wu_test.txt
	@echo "n_end    100"   >> /tmp/ec_wu_test.txt
	@echo "x_limit  1000000" >> /tmp/ec_wu_test.txt
	./$(BIN) /tmp/ec_wu_test.txt /tmp/ec_result_test.txt
	@echo "=== Solutions found ==="
	@cat /tmp/ec_result_test.txt
	@echo "=== Algebraic verification ==="
	@python3 -c "
import sys
count = 0
with open('/tmp/ec_result_test.txt') as f:
    for ln in f:
        parts = ln.split()
        if len(parts) != 3: continue
        n,x,y = int(parts[0]),int(parts[1]),int(parts[2])
        rhs = x**3 + 1296*n**2*x**2 + 15552*n**3*x + 46656*n**4 - 19*n
        assert y*y == rhs, f'FAIL n={n} x={x} y={y}'
        count += 1
print(f'All {count} solutions verified ✓')
"

# ── Launch real-time local search ─────────────────────────────────────
run_local: $(BIN)
	mkdir -p output
	python3 local_search.py --workers $(shell nproc 2>/dev/null || sysctl -n hw.logicalcpu 2>/dev/null || echo 4)

# ── PARI-only search for small |n| (exact, no x bound) ───────────────
run_pari:
	mkdir -p output
	python3 local_search.py --workers 2 --pari --wu_size 10

clean:
	rm -f $(BIN) $(BIN_B) $(BIN_B)_static
	rm -f /tmp/ec_wu_test.txt /tmp/ec_result_test.txt
