/*
 * worker_ec.c  —  BOINC/Charity Engine worker for integral points on E_n
 *
 *   E_n : y² = X³ + a4(n)·X + a6(n)
 *   a4(n) = -45349632·n⁴ + 419904·n³
 *   a6(n) = 3·(39182082048·n⁶ - 544195584·n⁵ + 1259712·n⁴ - 19·n)
 *   excluding  X = -3888·n²
 *
 * Uses __int128 throughout to handle large intermediate values.
 * QR sieve over 10 small primes for ~85% rejection of non-square RHS values.
 * Checkpoint support for BOINC fault tolerance.
 *
 * Build (standalone):
 *   gcc -O3 -march=native -std=c11 -o worker_ec worker_ec.c -lm
 *
 * Build (BOINC):
 *   gcc -O3 -march=native -std=c11 -DBOINC -o worker_ec worker_ec.c \
 *       -lm -lboinc_api -lboinc
 *
 * WU file format (wu.txt):
 *   n       <int64>     # positive integer
 *   x_start <int64>     # inclusive start of X range
 *   x_end   <int64>     # inclusive end of X range
 *
 * Output lines:   n X y
 *   Both signs of y are printed when y != 0.
 *
 * CLI usage (standalone, no WU file):
 *   ./worker_ec <n_start> <n_end> <x_bound>
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <inttypes.h>
#include <string.h>
#include <math.h>
#include <time.h>

#ifdef BOINC
#include "boinc_api.h"
#endif

typedef int64_t           i64;
typedef uint64_t          u64;
typedef __int128          i128;
typedef unsigned __int128 u128;

/* ── __int128 printing (decimal) ─────────────────────────────────── */
static void print128(FILE *f, i128 v)
{
    if (v < 0) { fputc('-', f); v = -v; }
    if (v > 9) print128(f, v / 10);
    fputc('0' + (int)(v % 10), f);
}

/* ── Integer square root (Newton, exact) ───────────────────────── */
static inline u64 isqrt128(u128 v)
{
    if (!v) return 0;
    u64 s = (u64)sqrtl((long double)v);
    if (s) s = (u64)((s + (u64)(v / (u128)s)) >> 1);
    if (s) s = (u64)((s + (u64)(v / (u128)s)) >> 1);
    while ((u128)s * s > v)           s--;
    while ((u128)(s+1) * (s+1) <= v)  s++;
    return s;
}

static inline int is_perfect_square(i128 v, i128 *out_y)
{
    if (v < 0) return 0;
    u64 s = isqrt128((u128)v);
    if ((u128)s * s != (u128)v) return 0;
    *out_y = (i128)s;
    return 1;
}

/* ── Curve coefficients in __int128 ─────────────────────────────── */
static i128 compute_a4(i64 n)
{
    i128 N = (i128)n;
    return (i128)(-45349632) * N*N*N*N
         + (i128)( 419904)   * N*N*N;
}

static i128 compute_a6(i64 n)
{
    i128 N  = (i128)n;
    i128 N2 = N*N, N3 = N2*N, N4 = N3*N, N5 = N4*N, N6 = N5*N;
    return (i128)3 * (
          (i128)(39182082048LL) * N6
        - (i128)(544195584LL)   * N5
        + (i128)(1259712LL)     * N4
        - (i128)(19LL)          * N
    );
}

/* RHS: X³ + a4·X + a6 */
static inline i128 feval(i128 X, i128 a4, i128 a6)
{
    return X*X*X + a4*X + a6;
}

/* ── QR sieve (10 small primes) ─────────────────────────────────── */
#define NPRIMES 10
static const int PRIMES[NPRIMES] = {3,5,7,11,13,17,19,23,29,31};
static unsigned int QR_MASK[NPRIMES];

static void init_qr_sieve(void)
{
    for (int i = 0; i < NPRIMES; i++) {
        int p = PRIMES[i]; QR_MASK[i] = 0;
        for (int r = 0; r < p; r++)
            QR_MASK[i] |= 1u << ((r*r) % p);
    }
}

static inline int sieve_pass(i64 X, i64 a4r, i64 a6r)
{
    for (int i = 0; i < NPRIMES; i++) {
        int p = PRIMES[i];
        long long xm  = ((long long)X   % p + p) % p;
        long long am4 = ((long long)a4r % p + p) % p;
        long long am6 = ((long long)a6r % p + p) % p;
        long long fx  = (xm*xm%p*xm%p + am4*xm%p + am6) % p;
        if (!((QR_MASK[i] >> (int)fx) & 1)) return 0;
    }
    return 1;
}

/* ── Lower bound on X where RHS ≥ 0 ────────────────────────────── */
static i64 lower_bound(i64 n)
{
    i128 a4 = compute_a4(n);
    i128 a6 = compute_a6(n);
    double a4f = (double)a4, a6f = (double)a6;

    /* Newton start at -cbrt(|a6|) */
    double xf = (a6f > 0.0) ? (-cbrt(a6f) - 100.0) :
                (a6f < 0.0) ? ( cbrt(-a6f) + 100.0) : -1e6;
    for (int i = 0; i < 200; i++) {
        double fv = xf*xf*xf + a4f*xf + a6f;
        double dv = 3.0*xf*xf + a4f;
        if (fabs(dv) < 1e-30) break;
        double dx = fv / dv; xf -= dx;
        if (fabs(dx) < 0.5) break;
    }
    i64 lb = (i64)floor(xf) - 10;
    while (feval((i128)lb,       a4, a6) >= 0) lb -= 1000;
    while (feval((i128)(lb + 1), a4, a6) < 0)  lb++;
    return lb + 1;
}

/* ── Search [x_start, x_end] for a single n ─────────────────────── */
static i64 search_range(i64 n, i64 x_start, i64 x_end, FILE *out)
{
    i128 a4   = compute_a4(n);
    i128 a6   = compute_a6(n);
    i128 excl = (i128)(-3888LL) * (i128)n * (i128)n;

    /* Sieve uses small residues of a4, a6 mod product-of-primes */
    i64 a4r = (i64)(a4 % (i128)9699690);   /* = product of first 10 primes */
    i64 a6r = (i64)(a6 % (i128)9699690);
    if (a4r < 0) a4r += 9699690;
    if (a6r < 0) a6r += 9699690;

    /* Clamp start to lower bound */
    i64 xlb = lower_bound(n);
    i64 x0  = (x_start > xlb) ? x_start : xlb;

    i64 found = 0;

    for (i64 X = x0; X <= x_end; X++) {
        if (!sieve_pass(X, a4r, a6r)) continue;

        i128 rhs = feval((i128)X, a4, a6);
        if (rhs < 0) continue;

        i128 y;
        if (!is_perfect_square(rhs, &y)) continue;

        if ((i128)X == excl) continue;   /* skip excluded point */

        fprintf(out, "% " PRId64 " ", n);
        print128(out, (i128)X);
        fputc(' ', out);
        print128(out, y);
        fputc('\n', out);
        fflush(out);
        if (y > 0) {
            fprintf(out, "% " PRId64 " ", n);
            print128(out, (i128)X);
            fputs(" -", out);
            print128(out, y);
            fputc('\n', out);
            fflush(out);
        }
        found++;
    }
    return found;
}

/* ═══════════════════════════════════════════════════════════════════ */

int main(int argc, char **argv)
{
#ifdef BOINC
    BOINC_OPTIONS opts;
    boinc_options_defaults(opts);
    opts.normal_thread_priority = 1;
    boinc_init_options(&opts);
#endif

    init_qr_sieve();

    /* ── WU mode: 1st arg is a filename (contains letters or '.') ─── */
    int wu_mode = (argc >= 2 &&
                   (strchr(argv[1], '.') || strchr(argv[1], '/') ||
                    atoll(argv[1]) == 0));

    if (!wu_mode && argc >= 4) {
        /* ── Standalone CLI: ./worker_ec <n_start> <n_end> <x_bound> ─ */
        i64 n_start = (i64)atoll(argv[1]);
        i64 n_end   = (i64)atoll(argv[2]);
        i64 x_bound = (i64)atoll(argv[3]);
        for (i64 n = n_start; n <= n_end; n++) {
            if (n == 0) continue;
            i64 x_lo = lower_bound(n);
            if (x_lo < -x_bound) x_lo = -x_bound;
            search_range(n, x_lo, x_bound, stdout);
            fflush(stdout);
        }
        return 0;
    }

    /* ── WU / BOINC mode ──────────────────────────────────────────── */
    char wu_path[512]   = "wu.txt";
    char out_path[512]  = "result.txt";
    char ckpt_path[512] = "checkpoint.txt";
    if (argc >= 2) strncpy(wu_path,   argv[1], 511);
    if (argc >= 3) strncpy(out_path,  argv[2], 511);
    if (argc >= 4) strncpy(ckpt_path, argv[3], 511);

    /* Read WU file */
    FILE *wf = fopen(wu_path, "r");
    if (!wf) {
        fprintf(stderr, "Cannot open WU file: %s\n", wu_path);
#ifdef BOINC
        boinc_finish(1);
#endif
        return 1;
    }
    i64 wu_n = 0, x_start = 0, x_end = 0;
    if (fscanf(wf, "n %" SCNd64 " x_start %" SCNd64 " x_end %" SCNd64,
               &wu_n, &x_start, &x_end) != 3) {
        fprintf(stderr, "Bad WU format. Expected: n <n> x_start <lo> x_end <hi>\n");
        fclose(wf);
#ifdef BOINC
        boinc_finish(1);
#endif
        return 1;
    }
    fclose(wf);
    fprintf(stderr, "[ec_nf] n=%" PRId64 "  x=[%" PRId64 ",%" PRId64 "]\n",
            wu_n, x_start, x_end);

    /* Checkpoint resume */
    i64 x_resume = x_start;
    {
        FILE *cf = fopen(ckpt_path, "r");
        if (cf) {
            if (fscanf(cf, "%" SCNd64, &x_resume) == 1)
                fprintf(stderr, "[resume] x_resume=%" PRId64 "\n", x_resume);
            fclose(cf);
        }
    }

    FILE *out = fopen(out_path, "a");
    if (!out) {
        fprintf(stderr, "Cannot open output: %s\n", out_path);
#ifdef BOINC
        boinc_finish(1);
#endif
        return 1;
    }

    const i64 BLOCK = 1000000000LL;   /* checkpoint every 10^9 x values */
    i64 total_found = 0;
    clock_t t0 = clock();

    for (i64 xb = x_resume; xb <= x_end; xb += BLOCK) {
        i64 xb_end = xb + BLOCK - 1;
        if (xb_end > x_end) xb_end = x_end;

        i64 found = search_range(wu_n, xb, xb_end, out);
        total_found += found;

        /* Write checkpoint */
        {
            FILE *cf = fopen(ckpt_path, "w");
            if (cf) { fprintf(cf, "%" PRId64 "\n", xb_end + 1); fclose(cf); }
        }
#ifdef BOINC
        boinc_checkpoint_completed();
        boinc_fraction_done((double)(xb_end - x_start + 1) /
                            (double)(x_end   - x_start + 1));
#endif
        double elapsed = (double)(clock() - t0) / CLOCKS_PER_SEC;
        fprintf(stderr,
            "[progress] x=[%" PRId64 ",%" PRId64 "]  found=%" PRId64
            "  total=%" PRId64 "  %.0fs\n",
            xb, xb_end, found, total_found, elapsed);
    }

    fclose(out);
    fprintf(stderr, "[done] n=%" PRId64 "  x=[%" PRId64 ",%" PRId64 "]"
            "  solutions=%" PRId64 "\n",
            wu_n, x_start, x_end, total_found);
#ifdef BOINC
    boinc_finish(0);
#endif
    return 0;
}
