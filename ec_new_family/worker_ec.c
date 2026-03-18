/*
 * worker_ec.c  —  brute-force search for integral points on E_n
 *
 *   y² = X³ + a4(n)·X + a6(n)
 *   a4(n) = -45349632·n⁴ + 419904·n³
 *   a6(n) = 3·(39182082048·n⁶ − 544195584·n⁵ + 1259712·n⁴ − 19·n)
 *   excluding  X = −3888·n²
 *
 * Uses __int128 throughout to handle large intermediate values.
 * Prints "n X y" to stdout for each non-excluded integral point found.
 *
 * Build:  gcc -O2 -o worker_ec worker_ec.c -lm
 * Usage:  ./worker_ec <n_start> <n_end> <x_bound>
 *   e.g.  ./worker_ec 1 100 10000000
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <math.h>

typedef __int128 int128_t;

/* ---- helpers for __int128 printing (decimal) ---- */
static void print128(int128_t v)
{
    if (v < 0) { putchar('-'); v = -v; }
    if (v > 9) print128(v / 10);
    putchar('0' + (int)(v % 10));
}

/* ---- integer square root of non-negative __int128 ---- */
static int128_t isqrt128(int128_t n)
{
    if (n < 0) return -1;
    if (n == 0) return 0;

    /* start with a double-based estimate */
    int128_t x = (int128_t)sqrtl((long double)n);

    /* Newton correction steps */
    int128_t x1 = (x + n / x) / 2;
    while (x1 < x) {
        x  = x1;
        x1 = (x + n / x) / 2;
    }
    /* x may be off by 1 due to rounding — correct */
    while (x * x > n) x--;
    while ((x + 1) * (x + 1) <= n) x++;
    return x;
}

/* ---- curve coefficients as __int128 ---- */
static int128_t compute_a4(long long n)
{
    int128_t N = (int128_t)n;
    return (int128_t)(-45349632) * N*N*N*N
         + (int128_t)( 419904)   * N*N*N;
}

static int128_t compute_a6(long long n)
{
    int128_t N = (int128_t)n;
    int128_t inner =
          (int128_t)(39182082048LL) * N*N*N*N*N*N
        - (int128_t)(544195584LL)   * N*N*N*N*N
        + (int128_t)(1259712LL)     * N*N*N*N
        - (int128_t)(19LL)          * N;
    return (int128_t)3 * inner;
}

/* ---- search one n over X in [x_lo, x_hi] ---- */
static void search_n(long long n, long long x_lo, long long x_hi)
{
    int128_t a4  = compute_a4(n);
    int128_t a6  = compute_a6(n);
    int128_t excl = (int128_t)(-3888LL) * (int128_t)n * (int128_t)n;

    int128_t X_lo = (int128_t)x_lo;
    int128_t X_hi = (int128_t)x_hi;

    for (int128_t X = X_lo; X <= X_hi; X++) {
        if (X == excl) continue;

        int128_t rhs = X*X*X + a4*X + a6;
        if (rhs < 0) continue;

        int128_t y = isqrt128(rhs);
        if (y * y == rhs) {
            /* found an integral point */
            printf("%lld ", n);
            print128(X);
            putchar(' ');
            print128(y);
            putchar('\n');
            if (y > 0) {
                /* also print negative y */
                printf("%lld ", n);
                print128(X);
                putchar(' ');
                putchar('-');
                print128(y);
                putchar('\n');
            }
        }
    }
}

int main(int argc, char *argv[])
{
    if (argc < 4) {
        fprintf(stderr, "Usage: %s <n_start> <n_end> <x_bound>\n", argv[0]);
        fprintf(stderr, "  Searches X in [max(-x_bound, x_min(n)), x_bound]\n");
        return 1;
    }

    long long n_start = atoll(argv[1]);
    long long n_end   = atoll(argv[2]);
    long long x_bound = atoll(argv[3]);

    for (long long n = n_start; n <= n_end; n++) {
        if (n == 0) continue;

        /* Estimate left bound from cubic dominant term: X ~ -cbrt(a6) for a6>0 */
        int128_t a6v = compute_a6(n);
        long long x_lo;
        if (a6v > 0) {
            /* rough cbrt upper bound */
            long long tmp = (long long)cbrtl((long double)((int128_t)(a6v > 0 ? a6v : -a6v)));
            x_lo = -(tmp + 500);
        } else {
            int128_t a4v = compute_a4(n);
            if (a4v < 0) {
                long long tmp = (long long)sqrtl((long double)((-a4v) / 3));
                x_lo = -(tmp + 500);
            } else {
                x_lo = -500;
            }
        }
        if (x_lo < -x_bound) x_lo = -x_bound;

        search_n(n, x_lo, x_bound);
        fflush(stdout);
    }

    return 0;
}
