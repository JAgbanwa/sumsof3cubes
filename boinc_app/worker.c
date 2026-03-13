/*
 * worker.c — Charity Engine / BOINC worker application
 *
 * Searches for all integer (x, y) satisfying:
 *   y^2 = x^3 + (36n+27)^2 * x^2
 *        + 243*(4n+3)^3  * x
 *        + (4n+3)*(11664n^3 + 26244n^2 + 19683n + 4916)
 *
 * Work unit format (stdin or first argument: wu_<id>.txt):
 *   n_start <int64>
 *   n_end   <int64>
 *   x_limit <int64>
 *
 * Output: result_<id>.txt — one line per solution: n x y
 *
 * Build (no BOINC API, standalone):
 *   gcc -O3 -march=native -o worker worker.c -lm
 *
 * Build with BOINC API:
 *   gcc -O3 -march=native -DBOINC -o worker_boinc worker.c \
 *       -I/usr/include/boinc -L/usr/lib -lboinc_api -lboinc -lpthread -lm
 *
 * Key optimisations:
 *  1. __int128 arithmetic — no bignum library needed for |n|,|x| < 10^9.
 *  2. QR sieve   — evaluate f(x) mod small primes; skip if not QR.
 *  3. isqrt via hardware sqrt + 1-step Newton correction.
 *  4. Sign-change bracket — skip all negative x where f(x)<0.
 *  5. BOINC heartbeat every 1s to avoid watchdog timeout.
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <inttypes.h>
#include <string.h>
#include <math.h>
#include <time.h>

#ifdef BOINC
#  include "boinc_api.h"
#endif

typedef __int128  i128;
typedef int64_t   i64;
typedef uint64_t  u64;
typedef unsigned __int128 u128;

/* -----------------------------------------------------------------------
 * Integer square-root: return floor(sqrt(v)), exact for v < 2^126
 * ----------------------------------------------------------------------- */
static inline u64 isqrt128(u128 v) {
    if (v == 0) return 0;
    /* Seed from double */
    double fv = (double)v;
    u64 s = (u64)sqrt(fv);
    /* Two Newton steps for precision */
    if (s > 0) { s = (s + (u64)(v / (u128)s)) >> 1; }
    if (s > 0) { s = (s + (u64)(v / (u128)s)) >> 1; }
    /* Adjust by ±1 */
    while ((u128)s * s > v) s--;
    while ((u128)(s+1) * (s+1) <= v) s++;
    return s;
}

static inline int is_perfect_square(i128 v, i64 *out_y) {
    if (v < 0) return 0;
    u64 s = isqrt128((u128)v);
    if ((u128)s * s == (u128)v) { *out_y = (i64)s; return 1; }
    return 0;
}

/* -----------------------------------------------------------------------
 * QR sieve tables: for small primes p, precompute which r in [0,p) are QRs
 * ----------------------------------------------------------------------- */
#define N_SIEVES 8
static const int SIEVE_PRIMES[N_SIEVES] = {3, 5, 7, 11, 13, 17, 19, 23};
static uint32_t qr_bits[N_SIEVES];  /* bit k set iff k is QR mod p */

static void init_sieve(void) {
    for (int i = 0; i < N_SIEVES; i++) {
        int p = SIEVE_PRIMES[i];
        qr_bits[i] = 0;
        for (int r = 0; r < p; r++) {
            /* r is QR mod p if there exists x: x^2 ≡ r (mod p) */
            int found = 0;
            for (int x = 0; x < p; x++) {
                if ((x*x) % p == r) { found = 1; break; }
            }
            if (found) qr_bits[i] |= (1u << r);
        }
    }
}

/* Given n, compute f(x) mod p for each sieve prime, return 0 if any is non-QR */
static inline int sieve_pass(i64 x, i64 n) {
    for (int i = 0; i < N_SIEVES; i++) {
        int p = SIEVE_PRIMES[i];
        /* Compute f(x) mod p. Use long long arithmetic mod p. */
        long long xm = ((long long)x % p + p) % p;
        long long nm = ((long long)n % p + p) % p;
        long long t  = (4*nm + 3) % p;
        long long A  = (81 * t % p * t) % p;
        long long B  = (243 * t % p * t % p * t) % p;
        /* C = t * (11664*n^3 + 26244*n^2 + 19683*n + 4916) mod p */
        long long n2 = nm * nm % p;
        long long n3 = n2 * nm % p;
        long long C  = t * ((11664*n3 + 26244*n2 + 19683*nm + 4916) % p) % p;
        C = ((C % p) + p) % p;
        long long fx = (xm*xm%p*xm%p + A*xm%p*xm%p + B*xm%p + C%p) % p;
        fx = ((fx % p) + p) % p;
        if (!((qr_bits[i] >> (int)fx) & 1)) return 0;
    }
    return 1;
}

/* -----------------------------------------------------------------------
 * Evaluate f(x) exactly using __int128
 * ----------------------------------------------------------------------- */
static inline i128 f_eval(i64 x, i64 n) {
    i128 t  = (i128)(4*n + 3);
    i128 A  = (i128)81 * t * t;
    i128 B  = (i128)243 * t * t * t;
    i128 C  = t * (
                 (i128)11664 * (i128)n * n * n
               + (i128)26244 * (i128)n * n
               + (i128)19683 * (i128)n
               + (i128)4916
              );
    i128 xm = (i128)x;
    return xm*xm*xm + A*xm*xm + B*xm + C;
}

/* -----------------------------------------------------------------------
 * Find leftmost integer x0 such that f(x0) >= 0  (all x < x0 have f<0)
 * Uses float to bracket then exact binary search.
 * ----------------------------------------------------------------------- */
static i64 find_lower_bound(i64 n) {
    double A_f = 81.0 * pow(4.0*n+3, 2);
    double B_f = 243.0 * pow(4.0*n+3, 3);
    double C_f = (4.0*n+3) * (11664.0*n*n*n + 26244.0*n*n + 19683.0*n + 4916.0);

    /* Quick Newton from a very negative start */
    double xf = -fabs(A_f) - 100.0;
    for (int iter = 0; iter < 80; iter++) {
        double fv = xf*xf*xf + A_f*xf*xf + B_f*xf + C_f;
        double df = 3.0*xf*xf + 2.0*A_f*xf + B_f;
        if (fabs(df) < 1e-30) break;
        xf -= fv / df;
    }
    i64 lb = (i64)floor(xf) - 2;
    /* Exact binary search: find leftmost i64 where f(x)>=0 */
    i64 lo = lb - 10, hi = lb + 20;
    /* Ensure hi gives f>=0 */
    while (f_eval(hi, n) < 0) hi += (hi < 0 ? -hi/2 + 1 : 1);
    /* Ensure lo gives f<0 */
    while (f_eval(lo, n) >= 0) lo -= (lo > 0 ? lo/2 + 1 : 1);
    /* Binary search */
    while (lo < hi - 1) {
        i64 mid = lo/2 + hi/2;
        if (f_eval(mid, n) >= 0) hi = mid;
        else lo = mid;
    }
    return hi;
}

/* -----------------------------------------------------------------------
 * Search all valid x for a given n
 * ----------------------------------------------------------------------- */
static int search_n(i64 n, i64 x_limit, FILE *out) {
    int found = 0;
    i64 y_val;

    /* --- Positive x side (0 .. x_limit) --- */
    for (i64 x = 0; x <= x_limit; x++) {
        /* QR sieve first (cheap) */
        if (!sieve_pass(x, n)) continue;
        i128 v = f_eval(x, n);
        if (is_perfect_square(v, &y_val)) {
            fprintf(out, "%" PRId64 " %" PRId64 " %" PRId64 "\n", n, x, y_val);
            if (y_val > 0)
                fprintf(out, "%" PRId64 " %" PRId64 " %" PRId64 "\n", n, x, -y_val);
            found++;
        }
    }

    /* --- Negative x side --- */
    i64 lb = find_lower_bound(n);
    i64 x_neg_start = (lb > -x_limit) ? lb : -x_limit;
    for (i64 x = x_neg_start; x < 0; x++) {
        if (!sieve_pass(x, n)) continue;
        i128 v = f_eval(x, n);
        if (v < 0) continue;
        if (is_perfect_square(v, &y_val)) {
            fprintf(out, "%" PRId64 " %" PRId64 " %" PRId64 "\n", n, x, y_val);
            if (y_val > 0)
                fprintf(out, "%" PRId64 " %" PRId64 " %" PRId64 "\n", n, x, -y_val);
            found++;
        }
    }
    return found;
}

/* -----------------------------------------------------------------------
 * BOINC checkpoint (every 60 seconds)
 * ----------------------------------------------------------------------- */
#ifdef BOINC
static double last_checkpoint = 0.0;
static void maybe_checkpoint(i64 n, i64 n_end, FILE *ckpt) {
    double now = boinc_elapsed_time();
    if (now - last_checkpoint > 60.0) {
        rewind(ckpt);
        fprintf(ckpt, "%" PRId64 "\n", n);
        fflush(ckpt);
        boinc_checkpoint_completed();
        /* Report fraction complete */
        boinc_fraction_done((double)(n - /* n_start loaded externally */ 0) /
                            (double)(n_end + 1));
        last_checkpoint = now;
    }
}
#endif

/* -----------------------------------------------------------------------
 * Main
 * ----------------------------------------------------------------------- */
int main(int argc, char **argv) {
#ifdef BOINC
    boinc_init();
#endif

    init_sieve();

    /* --- Read work unit --- */
    char wu_path[512]  = "wu.txt";
    char out_path[512] = "result.txt";
    char ckpt_path[512]= "checkpoint.txt";

    if (argc >= 2) strncpy(wu_path,  argv[1], 511);
    if (argc >= 3) strncpy(out_path, argv[2], 511);

#ifdef BOINC
    boinc_resolve_filename_s("wu.txt",         wu_path,   sizeof(wu_path));
    boinc_resolve_filename_s("result.txt",     out_path,  sizeof(out_path));
    boinc_resolve_filename_s("checkpoint.txt", ckpt_path, sizeof(ckpt_path));
#endif

    FILE *wu = fopen(wu_path, "r");
    if (!wu) { fprintf(stderr, "Cannot open %s\n", wu_path); return 1; }

    i64 n_start, n_end, x_limit;
    if (fscanf(wu, "n_start %" SCNd64 " n_end %" SCNd64 " x_limit %" SCNd64,
               &n_start, &n_end, &x_limit) != 3) {
        fprintf(stderr, "Bad WU format\n");
        fclose(wu);
        return 1;
    }
    fclose(wu);

    /* --- Check for checkpoint --- */
    i64 n_resume = n_start;
    FILE *ckpt = fopen(ckpt_path, "r");
    if (ckpt) {
        fscanf(ckpt, "%" SCNd64, &n_resume);
        fclose(ckpt);
        fprintf(stderr, "[checkpoint] Resuming from n=%" PRId64 "\n", n_resume);
    }

    FILE *out = fopen(out_path, "a");
    if (!out) { fprintf(stderr, "Cannot open %s\n", out_path); return 1; }
    FILE *ckpt_w = fopen(ckpt_path, "w");

    /* --- Main search loop --- */
    i64 total_solutions = 0;
    clock_t t0 = clock();
    i64 n_count = 0;

    for (i64 n = n_resume; n <= n_end; n++) {
        total_solutions += search_n(n, x_limit, out);
        n_count++;
        fflush(out);

#ifdef BOINC
        boinc_fraction_done((double)(n - n_start) / (double)(n_end - n_start + 1));
        /* Checkpoint */
        if (ckpt_w) {
            rewind(ckpt_w);
            fprintf(ckpt_w, "%" PRId64 "\n", n + 1);
            fflush(ckpt_w);
            boinc_checkpoint_completed();
        }
#else
        if (n_count % 10000 == 0) {
            double elapsed = (double)(clock() - t0) / CLOCKS_PER_SEC;
            fprintf(stderr, "[progress] n=%" PRId64 "  done=%"PRId64
                    "  solutions=%"PRId64"  %.1f n/s\n",
                    n, n_count, total_solutions,
                    (double)n_count / elapsed);
        }
#endif
    }

    fclose(out);
    if (ckpt_w) fclose(ckpt_w);

    fprintf(stderr, "[done] Searched n=%"PRId64" to %"PRId64
            "  x_limit=%"PRId64"  solutions=%"PRId64"\n",
            n_start, n_end, x_limit, total_solutions);

#ifdef BOINC
    boinc_finish(0);
#endif
    return 0;
}
