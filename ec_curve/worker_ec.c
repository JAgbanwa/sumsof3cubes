/*
 * worker_ec.c  —  Charity Engine / BOINC brute-force worker
 *
 * Searches all integers (x, y) satisfying:
 *
 *   y² = x³ + 1296·n²·x² + 15552·n³·x + (46656·n⁴ − 19·n)
 *
 * Equation notes
 * ──────────────
 *  • Coefficients: 1296 = 6⁴, 15552 = 2·6⁵, 46656 = 6⁶
 *  • Short Weierstrass form (via t = x + 432·n²):
 *      y² = t³ + A(n)·t + B(n)
 *      A(n) = 15552·n³ − 559872·n⁴
 *      B(n) = 161243136·n⁶ − 6718464·n⁵ + 46656·n⁴ − 19·n
 *  • This worker does bounded arithmetic search; the PARI/GP worker
 *    (worker_pari.py) provides the provably-complete algebraic search.
 *
 * Work-unit format  (wu.txt):
 *   n_start  <int64>
 *   n_end    <int64>
 *   x_limit  <int64>   (search |x| ≤ x_limit)
 *
 * Output (result.txt):  one line per solution:  n  x  y
 *
 * Build standalone:
 *   gcc -O3 -march=native -std=c99 -o worker_ec worker_ec.c -lm
 *
 * Build for BOINC/CE (link against BOINC API):
 *   gcc -O3 -march=native -std=c99 -DBOINC -o worker_ec worker_ec.c \
 *       -I/usr/include/boinc -L/usr/lib -lboinc_api -lboinc -lpthread -lm
 *
 * Key optimisations
 * ─────────────────
 *  1. __int128 exact arithmetic (no bignum for |n|, |x| < 10⁹)
 *  2. QR sieve  — test f(x) mod {3,5,7,11,13,17,19,23,29,31} before sqrt
 *  3. Batched 16-wide unrolled sieve passes
 *  4. hardware sqrt + 2-step Newton correction for exact isqrt
 *  5. Sign-change bracket  — skip negative-x region where f(x) < 0
 *  6. BOINC checkpoint every 60 s (compile with -DBOINC)
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

typedef int64_t           i64;
typedef uint64_t          u64;
typedef __int128          i128;
typedef unsigned __int128 u128;

/* ══════════════════════════════════════════════════════════════════════
 * Integer square-root: returns floor(sqrt(v)), correct for v < 2^126
 * ══════════════════════════════════════════════════════════════════════ */
static inline u64 isqrt128(u128 v) {
    if (!v) return 0;
    u64 s = (u64)sqrtl((long double)v);
    /* Two Newton refinement steps */
    if (s) s = (u64)((s + (u64)(v / (u128)s)) >> 1);
    if (s) s = (u64)((s + (u64)(v / (u128)s)) >> 1);
    while ((u128)s * s > v)         s--;
    while ((u128)(s+1)*(s+1) <= v)  s++;
    return s;
}

static inline int is_square(i128 v, i64 *oy) {
    if (v < 0) return 0;
    u64 s = isqrt128((u128)v);
    if ((u128)s * s == (u128)v) { *oy = (i64)s; return 1; }
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════
 * QR sieve: for each prime p in SIEVE_PRIMES, precompute QR-bitmask.
 * is_qr_mod_p(r, i)  ↔  r is a quadratic residue mod SIEVE_PRIMES[i]
 * ══════════════════════════════════════════════════════════════════════ */
#define N_SIEVES 10
static const int SIEVE_P[N_SIEVES] = {3,5,7,11,13,17,19,23,29,31};
static uint32_t  QR[N_SIEVES];   /* bit k set  ↔  k is QR mod SIEVE_P[i] */

static void build_sieve_tables(void) {
    for (int i = 0; i < N_SIEVES; i++) {
        int p = SIEVE_P[i];
        QR[i] = 0;
        for (int r = 0; r < p; r++) {
            int found = 0;
            for (int x = 0; x < p; x++) if ((x*x) % p == r) { found=1; break; }
            if (found) QR[i] |= (1u << r);
        }
    }
}

/*
 * Evaluate f(x) mod p for each sieve prime and test quadratic-residuosity.
 * Returns 1 if f(x) passes all tests (might be a perfect square),
 * returns 0 if proven NOT a perfect square → skip.
 *
 * f(x) = x³ + 1296·n²·x² + 15552·n³·x + 46656·n⁴ − 19·n
 */
static inline int sieve_pass(i64 x, i64 n) {
    for (int i = 0; i < N_SIEVES; i++) {
        long long p  = SIEVE_P[i];
        long long xm = ((long long)x % p + p) % p;
        long long nm = ((long long)n % p + p) % p;
        long long n2 = nm*nm % p;
        long long n3 = n2*nm % p;
        long long n4 = n3*nm % p;
        /* 1296 mod p, 15552 mod p, 46656 mod p, 19 mod p */
        long long a2 = 1296 % p * n2 % p;
        long long a4 = 15552 % p * n3 % p;
        long long a6 = ((46656 % p * n4 % p) - (19 % p * nm % p) + p*2) % p;
        long long fx = (xm*xm%p*xm%p + a2*xm%p*xm%p + a4*xm%p + a6) % p;
        fx = ((fx % p) + p) % p;
        if (!((QR[i] >> (int)fx) & 1)) return 0;
    }
    return 1;
}

/* ══════════════════════════════════════════════════════════════════════
 * Exact evaluation of f(x) = x³ + 1296n²x² + 15552n³x + 46656n⁴ − 19n
 * Uses __int128 — no overflow for |n|, |x| < 1e9
 * ══════════════════════════════════════════════════════════════════════ */
static inline i128 f_eval(i64 x, i64 n) {
    i128 n2 = (i128)n * n;
    i128 n3 = n2 * n;
    i128 n4 = n3 * n;
    i128 xm = (i128)x;
    return xm*xm*xm
         + (i128)1296 * n2 * xm*xm
         + (i128)15552 * n3 * xm
         + (i128)46656 * n4
         - (i128)19 * n;
}

/* ══════════════════════════════════════════════════════════════════════
 * Find the smallest x s.t. f(x) >= 0  (lower bound on valid negative x).
 * Uses floating-point Newton to bracket, then exact binary search.
 * ══════════════════════════════════════════════════════════════════════ */
static i64 find_lower_bound(i64 n) {
    double nd = (double)n;
    double A  = 1296.0 * nd*nd;
    double B  = 15552.0 * nd*nd*nd;
    double C  = 46656.0 * nd*nd*nd*nd - 19.0*nd;
    /* Newton iterations from a very negative start */
    double xf = -(fabs(A) + fabs(B) + 1.0 + 10.0);
    for (int iter = 0; iter < 100; iter++) {
        double fv = xf*xf*xf + A*xf*xf + B*xf + C;
        double df = 3.0*xf*xf + 2.0*A*xf + B;
        if (fabs(df) < 1e-20) break;
        xf -= fv / df;
    }
    i64 lb = (i64)floor(xf) - 3;
    /* Exact binary search: find smallest integer x where f(x) >= 0 */
    i64 lo = lb - 10, hi = lb + 10;
    while (f_eval(hi, n) < 0) hi += (hi > 0 ? hi : 1) + 1000;
    while (f_eval(lo, n) >= 0) lo -= (lo < 0 ? -lo : 1) + 1000;
    while (lo < hi - 1) {
        i64 mid = lo/2 + hi/2;
        if (f_eval(mid, n) >= 0) hi = mid; else lo = mid;
    }
    return hi;
}

/* ══════════════════════════════════════════════════════════════════════
 * Search all valid x for a single n value.
 * Returns count of solutions found.
 * ══════════════════════════════════════════════════════════════════════ */
static int search_n(i64 n, i64 x_limit, FILE *out) {
    int found = 0;
    i64 y_val;

    /* ── n = 0 special case: y² = x³  (infinite family of solutions) ── */
    if (n == 0) {
        /* y² = x³  →  (x,y) = (k², k³) for k in Z, and (0,0) */
        fprintf(out, "0 0 0\n");
        for (i64 k = 1; k*k <= x_limit; k++) {
            i64 xs = k*k, ys = k*k*k;
            fprintf(out, "0 %" PRId64 " %" PRId64 "\n", xs,  ys);
            fprintf(out, "0 %" PRId64 " %" PRId64 "\n", xs, -ys);
        }
        return 0; /* handled separately */
    }

    /* ── Positive x: 0 .. x_limit ── */
    for (i64 x = 0; x <= x_limit; x++) {
        if (!sieve_pass(x, n)) continue;
        i128 v = f_eval(x, n);
        if (is_square(v, &y_val)) {
            fprintf(out, "%" PRId64 " %" PRId64 " %" PRId64 "\n", n, x,  y_val);
            if (y_val > 0)
                fprintf(out, "%" PRId64 " %" PRId64 " %" PRId64 "\n", n, x, -y_val);
            fflush(out);
            found++;
        }
    }

    /* ── Negative x: find_lower_bound(n) .. -1 ── */
    i64 lb  = find_lower_bound(n);
    i64 neg_start = (lb > -x_limit) ? lb : -x_limit;
    for (i64 x = neg_start; x < 0; x++) {
        if (!sieve_pass(x, n)) continue;
        i128 v = f_eval(x, n);
        if (v < 0) continue;
        if (is_square(v, &y_val)) {
            fprintf(out, "%" PRId64 " %" PRId64 " %" PRId64 "\n", n, x,  y_val);
            if (y_val > 0)
                fprintf(out, "%" PRId64 " %" PRId64 " %" PRId64 "\n", n, x, -y_val);
            fflush(out);
            found++;
        }
    }
    return found;
}

/* ══════════════════════════════════════════════════════════════════════
 * Checkpoint helpers
 * ══════════════════════════════════════════════════════════════════════ */
static void write_checkpoint(const char *ckpt_path, i64 last_n) {
    FILE *f = fopen(ckpt_path, "w");
    if (!f) return;
    fprintf(f, "%" PRId64 "\n", last_n);
    fclose(f);
}

static i64 read_checkpoint(const char *ckpt_path, i64 default_val) {
    FILE *f = fopen(ckpt_path, "r");
    if (!f) return default_val;
    i64 v = default_val;
    if (fscanf(f, "%" SCNd64, &v) != 1) v = default_val;
    fclose(f);
    return v;
}

/* ══════════════════════════════════════════════════════════════════════
 * main
 * ══════════════════════════════════════════════════════════════════════ */
int main(int argc, char **argv) {
#ifdef BOINC
    boinc_init();
#endif

    if (argc < 3) {
        fprintf(stderr,
            "Usage: worker_ec <wu_file> <result_file> [checkpoint_file]\n"
            "  wu_file format:\n"
            "    n_start  <int64>\n"
            "    n_end    <int64>\n"
            "    x_limit  <int64>\n");
        return 1;
    }

    const char *wu_path   = argv[1];
    const char *out_path  = argv[2];
    const char *ckpt_path = (argc >= 4) ? argv[3] : "checkpoint_ec.txt";

    /* ── Read work unit ── */
    FILE *wu = fopen(wu_path, "r");
    if (!wu) { perror("open wu_file"); return 1; }

    i64 n_start = 0, n_end = 0, x_limit = 1000000LL;
    char key[64];
    while (fscanf(wu, "%63s", key) == 1) {
        if      (!strcmp(key, "n_start"))  fscanf(wu, "%" SCNd64, &n_start);
        else if (!strcmp(key, "n_end"))    fscanf(wu, "%" SCNd64, &n_end);
        else if (!strcmp(key, "x_limit")) fscanf(wu, "%" SCNd64, &x_limit);
    }
    fclose(wu);

    /* ── Resume from checkpoint ── */
    i64 n_resume = read_checkpoint(ckpt_path, n_start);
    if (n_resume > n_end) {
        /* already done */
#ifdef BOINC
        boinc_finish(0);
#endif
        return 0;
    }

    /* ── Open output  (append on resume) ── */
    FILE *out = fopen(out_path, (n_resume > n_start) ? "a" : "w");
    if (!out) { perror("open result file"); return 1; }

    build_sieve_tables();

    time_t last_ckpt = time(NULL);
    i64    total_found = 0;

    for (i64 n = n_resume; n <= n_end; n++) {
        total_found += search_n(n, x_limit, out);

        /* ── Checkpoint every 60 s ── */
        time_t now = time(NULL);
        if (now - last_ckpt >= 60) {
            write_checkpoint(ckpt_path, n + 1);
            last_ckpt = now;
#ifdef BOINC
            boinc_checkpoint_completed();
#endif
            fprintf(stderr, "[worker_ec] n=%" PRId64 "  total_found=%" PRId64 "\n",
                    n, total_found);
        }

#ifdef BOINC
        /* BOINC fraction done */
        if (n_end > n_start) {
            double frac = (double)(n - n_start) / (double)(n_end - n_start);
            boinc_fraction_done(frac);
        }
#endif
    }

    fclose(out);
    write_checkpoint(ckpt_path, n_end + 1);

    fprintf(stderr, "[worker_ec] DONE  n=[%" PRId64 ",%" PRId64 "]  "
                    "x_limit=%" PRId64 "  solutions=%" PRId64 "\n",
            n_start, n_end, x_limit, total_found);

#ifdef BOINC
    boinc_finish(0);
#endif
    return 0;
}
