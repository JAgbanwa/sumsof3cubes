#!/usr/bin/env python3
"""
fast_search.py — Multi-process local search driver.

Spawns one worker per CPU core. Each worker handles a dedicated slab of n.
Solutions are written to solutions.txt immediately as found.

Usage:
    python3 fast_search.py                   # cores=all, n=0 expanding outward
    python3 fast_search.py --cores 8 --x_limit 1000000
    python3 fast_search.py --n_start -50000 --n_end 50000 --x_limit 5000000
"""

import os
import sys
import time
import argparse
import multiprocessing
from multiprocessing import Pool, Queue, Manager
import gmpy2
from gmpy2 import mpz, isqrt_rem
import math

SOLUTIONS_FILE = "solutions.txt"

# =========================================================================
# Arithmetic kernel (runs in child processes — no shared state)
# =========================================================================

SIEVE_MODS = [3, 5, 7, 11, 13, 17, 19, 23, 29, 31]

def _build_qr_sets():
    """For each small prime p, build the set of quadratic residues mod p."""
    qr = {}
    for p in SIEVE_MODS:
        qr[p] = frozenset((x * x) % p for x in range(p))
    return qr

QR_SETS = _build_qr_sets()

def _coeff(n):
    """Return (A, B, C) as Python ints (exact)."""
    t = 4 * n + 3
    A = 81 * t * t
    B = 243 * t * t * t
    C = t * (11664 * n**3 + 26244 * n**2 + 19683 * n + 4916)
    return A, B, C

def _f(x, A, B, C):
    """Evaluate f(x) = x^3 + A*x^2 + B*x + C exactly."""
    return x*x*x + A*x*x + B*x + C

def _is_square(v):
    if v < 0:
        return False, 0
    s, r = isqrt_rem(mpz(v))
    if r == 0:
        return True, int(s)
    return False, 0

def _sieve_pass(x, A, B, C, n):
    """Return False if f(x) is provably not a perfect square (mod small primes)."""
    for p in SIEVE_MODS:
        xm = x % p
        Am = A % p
        Bm = B % p
        Cm = C % p
        fm = (xm*xm*xm + Am*xm*xm + Bm*xm + Cm) % p
        if fm not in QR_SETS[p]:
            return False
    return True

def _lower_bound_float(n, A_f, B_f, C_f):
    """Rough lower bound via Newton on float cubic."""
    xf = -abs(A_f) - 10.0
    for _ in range(80):
        fv = xf**3 + A_f*xf**2 + B_f*xf + C_f
        dfv = 3*xf**2 + 2*A_f*xf + B_f
        if abs(dfv) < 1e-40:
            break
        xf -= fv / dfv
    return int(math.floor(xf)) - 3

def _search_n(n, x_limit):
    """Search all valid x for a single n. Returns list of (n,x,y)."""
    A, B, C = _coeff(n)
    A_f, B_f, C_f = float(A), float(B), float(C)
    solutions = []

    # --- Positive x: 0 .. x_limit ---
    for x in range(0, x_limit + 1):
        if not _sieve_pass(x, A, B, C, n):
            continue
        v = _f(x, A, B, C)
        ok, y = _is_square(v)
        if ok:
            solutions.append((n, x, y))
            if y > 0:
                solutions.append((n, x, -y))

    # --- Negative x: lower_bound .. -1 ---
    lb = _lower_bound_float(n, A_f, B_f, C_f)
    for x in range(max(-x_limit, lb), 0):
        if not _sieve_pass(x, A, B, C, n):
            continue
        v = _f(x, A, B, C)
        if v < 0:
            continue
        ok, y = _is_square(v)
        if ok:
            solutions.append((n, x, y))
            if y > 0:
                solutions.append((n, x, -y))

    return solutions

# =========================================================================
# Worker process: receives n values from a queue, writes results
# =========================================================================

def worker_proc(n_queue, result_queue, x_limit):
    """
    Pull n values from n_queue, search each, put solutions into result_queue.
    Sentinel: None in n_queue means this worker should stop.
    """
    while True:
        n = n_queue.get()
        if n is None:
            break
        sols = _search_n(n, x_limit)
        if sols:
            result_queue.put(sols)
        result_queue.put(("done", n))  # progress token

# =========================================================================
# Result writer process
# =========================================================================

def writer_proc(result_queue, solutions_file, n_total_hint):
    """Drains result_queue and writes solutions + progress."""
    import time
    t0 = time.time()
    done_count = 0
    sol_count = 0

    with open(solutions_file, "a", buffering=1) as fout:
        fout.write(f"# Search started at {time.ctime()}\n")
        while True:
            item = result_queue.get()
            if item is None:
                break
            if isinstance(item, tuple) and item[0] == "done":
                done_count += 1
                n = item[1]
                if done_count % 1000 == 0:
                    elapsed = time.time() - t0
                    rate = done_count / elapsed
                    print(f"[progress] {done_count:,} n values done | "
                          f"{rate:.0f} n/s | solutions: {sol_count} | last n={n}",
                          flush=True)
            else:
                # List of solutions
                for (n, x, y) in item:
                    line = f"n={n}  x={x}  y={y}\n"
                    fout.write(line)
                    print(f"*** SOLUTION: {line}", end="", flush=True)
                    sol_count += len(item)
                    break  # only count/print once per batch

# =========================================================================
# Main driver
# =========================================================================

def n_generator(n_start, n_end):
    """Yield n values in the order: 0, -1, 1, -2, 2, ... if no range given."""
    if n_start is not None and n_end is not None:
        yield from range(n_start, n_end + 1)
        return
    radius = 0
    while True:
        if radius == 0:
            yield 0
        else:
            yield -radius
            yield radius
        radius += 1

def main():
    parser = argparse.ArgumentParser(
        description="Multi-core real-time search for integer solutions"
    )
    parser.add_argument("--cores", type=int,
                        default=max(1, multiprocessing.cpu_count() - 1),
                        help="Number of worker cores")
    parser.add_argument("--x_limit", type=int, default=1_000_000,
                        help="Max |x| to test per n")
    parser.add_argument("--n_start", type=int, default=None)
    parser.add_argument("--n_end",   type=int, default=None)
    parser.add_argument("--output",  default=SOLUTIONS_FILE)
    args = parser.parse_args()

    n_cores = args.cores
    x_limit = args.x_limit

    print(f"[fast_search] Cores: {n_cores}  |  x_limit: {x_limit:,}")
    print(f"[fast_search] Output: {args.output}")
    print(f"[fast_search] QR sieve primes: {SIEVE_MODS}")

    ctx = multiprocessing.get_context("fork")
    n_queue    = ctx.Queue(maxsize=n_cores * 4)
    result_queue = ctx.Queue()

    # Start workers
    workers = []
    for _ in range(n_cores):
        p = ctx.Process(target=worker_proc,
                        args=(n_queue, result_queue, x_limit),
                        daemon=True)
        p.start()
        workers.append(p)

    # Start writer
    writer = ctx.Process(target=writer_proc,
                         args=(result_queue, args.output, 0),
                         daemon=True)
    writer.start()

    # Feed n values
    try:
        for n in n_generator(args.n_start, args.n_end):
            n_queue.put(n)
    except KeyboardInterrupt:
        print("\n[fast_search] Interrupted by user.")

    # Send sentinels
    for _ in workers:
        n_queue.put(None)

    for p in workers:
        p.join()

    result_queue.put(None)
    writer.join()
    print("[fast_search] Done.")

if __name__ == "__main__":
    main()
