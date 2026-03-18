CC      = gcc
CFLAGS  = -O3 -march=native -std=c11 -Wall -Wextra
LDFLAGS = -lm

# Standalone binary (no BOINC)
all: worker_ec

worker_ec: worker_ec.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

# BOINC-linked binary (requires BOINC SDK headers + libs)
# Set BOINC_DIR to your BOINC source tree, e.g.:
#   make boinc BOINC_DIR=/home/boincadm/boinc
BOINC_DIR ?= /home/boincadm/boinc
boinc: worker_ec.c
	$(CC) $(CFLAGS) -DBOINC \
	    -I$(BOINC_DIR)/api -I$(BOINC_DIR)/lib \
	    -o worker_ec_boinc $< \
	    -L$(BOINC_DIR)/api -L$(BOINC_DIR)/lib \
	    $(LDFLAGS) -lboinc_api -lboinc

clean:
	rm -f worker_ec worker_ec_boinc

.PHONY: all boinc clean
