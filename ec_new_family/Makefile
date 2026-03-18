CC      = gcc
CFLAGS  = -O2 -Wall -Wextra -std=c11
LDFLAGS = -lm

all: worker_ec

worker_ec: worker_ec.c
	$(CC) $(CFLAGS) -o worker_ec worker_ec.c $(LDFLAGS)

clean:
	rm -f worker_ec

.PHONY: all clean
