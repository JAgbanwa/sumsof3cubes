# Makefile for the sum-of-cubes BOINC worker
#
# Targets:
#   make               — build standalone worker (no BOINC API)
#   make boinc         — build BOINC API worker
#   make boinc_linux   — cross-compile for Linux x86_64 (for server deployment)
#   make test          — run test with sample WU

CC      = gcc
CFLAGS  = -O3 -march=native -std=c99 -Wall -Wextra -lm
SRC     = worker.c
BIN     = worker
BIN_B   = worker_boinc

BOINC_INC ?= /usr/include/boinc
BOINC_LIB ?= /usr/lib

.PHONY: all boinc clean test

all: $(BIN)

$(BIN): $(SRC)
	$(CC) $(CFLAGS) -o $@ $^ -lm
	@echo "[build] Standalone worker: $(BIN)"

boinc: $(SRC)
	$(CC) $(CFLAGS) -DBOINC \
	    -I$(BOINC_INC) \
	    -L$(BOINC_LIB) \
	    -o $(BIN_B) $^ \
	    -lboinc_api -lboinc -lpthread -lm
	@echo "[build] BOINC worker: $(BIN_B)"

# Statically linked for distribution to BOINC clients
boinc_static: $(SRC)
	$(CC) $(CFLAGS) -DBOINC \
	    -I$(BOINC_INC) \
	    -L$(BOINC_LIB) \
	    -static -o $(BIN_B)_static $^ \
	    -lboinc_api -lboinc -lpthread -lm
	@echo "[build] BOINC static worker: $(BIN_B)_static"

test: $(BIN)
	@echo "n_start 0" > /tmp/wu_test.txt
	@echo "n_end   100" >> /tmp/wu_test.txt
	@echo "x_limit 10000" >> /tmp/wu_test.txt
	./$(BIN) /tmp/wu_test.txt /tmp/result_test.txt
	@echo "--- Results ---"
	@cat /tmp/result_test.txt

clean:
	rm -f $(BIN) $(BIN_B) $(BIN_B)_static
