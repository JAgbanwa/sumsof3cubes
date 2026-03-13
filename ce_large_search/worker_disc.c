/*
 * worker_disc.c  --  Discriminant-based searcher for
 *     m*y^2 = 36x^3 + 36m*x^2 + 12m^2*x + m^3 - 19
 *
 * Equivalent to: m*t*(2m+12x+t) = 36x^3 - 19   where t = y - (m+6x)
 *
 * For fixed t, this is quadratic in m:
 *   2t*m^2 + t*(12x+t)*m - (36x^3-19) = 0
 *   disc = t^2*(12x+t)^2 + 8t*(36x^3-19)
 *   m    = [ -t*(12x+t) + isqrt(disc) ] / (4t)
 *
 * We iterate x over a work unit range, and t = 1..T_MAX,
 * checking whether disc is a perfect square.
 *
 * Usage:
 *   worker_disc wu.txt result.txt [checkpoint.txt]
 *
 * wu.txt format:
 *   x_start  <integer>     (may be negative)
 *   x_end    <integer>
 *   t_max    <integer>     (default 200)
 *   m_lo     <integer>     (default 10^20)
 *   m_hi     <integer>     (default 10^30)
 *
 * result.txt: lines of "SOL m x y" for each solution found.
 *
 * Build:
 *   gcc -O3 -march=native -o worker_disc worker_disc.c -lgmp -lm
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <gmp.h>
#include <time.h>

/* ---------- checkpoint ---------- */
static const char *CKPT_FILE = NULL;
static long long last_saved_x = 0;
static time_t last_ckpt_time = 0;
#define CKPT_INTERVAL 30   /* seconds */

static void save_checkpoint(long long x) {
    if (!CKPT_FILE) return;
    time_t now = time(NULL);
    if (now - last_ckpt_time < CKPT_INTERVAL) return;
    FILE *f = fopen(CKPT_FILE, "w");
    if (f) { fprintf(f, "%lld\n", x); fclose(f); }
    last_ckpt_time = now;
    last_saved_x = x;
}

static long long load_checkpoint(void) {
    if (!CKPT_FILE) return 0;
    FILE *f = fopen(CKPT_FILE, "r");
    if (!f) return 0;
    long long v = 0;
    fscanf(f, "%lld", &v);
    fclose(f);
    return v;
}

/* ---------- verify: m*y^2 == 36x^3+36m*x^2+12m^2*x+m^3-19 ---------- */
static int verify(mpz_t m, mpz_t x, mpz_t y,
                  mpz_t tmp1, mpz_t tmp2)
{
    /* lhs = m * y^2 */
    mpz_mul(tmp1, y, y);
    mpz_mul(tmp1, tmp1, m);

    /* rhs = 36x^3 + 36m*x^2 + 12m^2*x + m^3 - 19 */
    /* x^2 */
    mpz_mul(tmp2, x, x);
    /* 36x^3 */
    mpz_mul(tmp2, tmp2, x);
    mpz_mul_ui(tmp2, tmp2, 36);
    /* + 36m*x^2 */
    mpz_t x2; mpz_init(x2);
    mpz_mul(x2, x, x);
    mpz_mul(x2, x2, m);
    mpz_mul_ui(x2, x2, 36);
    mpz_add(tmp2, tmp2, x2);
    mpz_clear(x2);
    /* + 12m^2*x */
    mpz_t m2; mpz_init(m2);
    mpz_mul(m2, m, m);
    mpz_mul(m2, m2, x);
    mpz_mul_ui(m2, m2, 12);
    mpz_add(tmp2, tmp2, m2);
    mpz_clear(m2);
    /* + m^3 */
    mpz_t m3; mpz_init(m3);
    mpz_mul(m3, m, m);
    mpz_mul(m3, m3, m);
    mpz_add(tmp2, tmp2, m3);
    mpz_clear(m3);
    /* - 19 */
    mpz_sub_ui(tmp2, tmp2, 19);

    int ok = (mpz_cmp(tmp1, tmp2) == 0);
    return ok;
}

/* ---------- main ---------- */
int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: worker_disc wu.txt result.txt [checkpoint.txt]\n");
        return 1;
    }

    /* --- parse wu.txt --- */
    mpz_t x_start_m, x_end_m, t_max_m, m_lo, m_hi;
    mpz_inits(x_start_m, x_end_m, t_max_m, m_lo, m_hi, NULL);

    /* defaults */
    mpz_set_ui(t_max_m, 200);
    /* m_lo = 10^20 */
    mpz_ui_pow_ui(m_lo, 10, 20);
    /* m_hi = 10^30 */
    mpz_ui_pow_ui(m_hi, 10, 30);

    FILE *wu = fopen(argv[1], "r");
    if (!wu) { fprintf(stderr, "cannot open %s\n", argv[1]); return 1; }
    char key[64], val[256];
    while (fscanf(wu, "%63s %255s", key, val) == 2) {
        if      (!strcmp(key, "x_start")) mpz_set_str(x_start_m, val, 10);
        else if (!strcmp(key, "x_end"))   mpz_set_str(x_end_m,   val, 10);
        else if (!strcmp(key, "t_max"))   mpz_set_str(t_max_m,   val, 10);
        else if (!strcmp(key, "m_lo"))    mpz_set_str(m_lo,       val, 10);
        else if (!strcmp(key, "m_hi"))    mpz_set_str(m_hi,       val, 10);
    }
    fclose(wu);

    if (argc >= 4) CKPT_FILE = argv[3];

    /* convert x range to long long (x fits in ~19 digits for our range) */
    long long x_start = mpz_get_si(x_start_m);
    long long x_end   = mpz_get_si(x_end_m);
    long long t_max   = mpz_get_si(t_max_m);
    mpz_clears(x_start_m, x_end_m, t_max_m, NULL);

    /* --- checkpoint resume --- */
    long long ckpt = load_checkpoint();
    long long x_resume = (ckpt > x_start) ? ckpt + 1 : x_start;
    long long step = (x_end >= x_start) ? 1 : -1;

    FILE *res = fopen(argv[2], "a");
    if (!res) { fprintf(stderr, "cannot open result %s\n", argv[2]); return 1; }

    /* --- GMP variables --- */
    mpz_t x, t, x3, N, disc, D, D2, m, B, tmp1, tmp2, y, six_x, tx_t;
    mpz_inits(x, t, x3, N, disc, D, D2, m, B, tmp1, tmp2, y, six_x, tx_t, NULL);

    long long count_x = 0;
    long long count_sols = 0;
    time_t t0 = time(NULL);

    for (long long xi = x_resume; ; xi += step) {
        if (step > 0 && xi > x_end) break;
        if (step < 0 && xi < x_end) break;

        mpz_set_si(x, xi);

        /* x^3 */
        mpz_mul(x3, x, x);
        mpz_mul(x3, x3, x);

        /* N = 36x^3 - 19 */
        mpz_mul_ui(N, x3, 36);
        mpz_sub_ui(N, N, 19);

        /* 6x for y computation later */
        mpz_mul_si(six_x, x, 6);

        for (long long ti = 1; ti <= t_max; ti++) {
            mpz_set_si(t, ti);

            /* t must divide N: check N mod t == 0 */
            if (mpz_divisible_p(N, t) == 0) continue;

            /* N_t = N / t */
            mpz_t N_t; mpz_init(N_t);
            mpz_divexact(N_t, N, t);

            /* B = 12x + t */
            mpz_mul_si(B, x, 12);
            mpz_add(B, B, t);

            /* disc = (t*B)^2 + 8*t*N = t^2*B^2 + 8*t*N */
            /* = t*(tB^2 + 8N) */
            mpz_mul(disc, t, B);
            mpz_mul(disc, disc, B);  /* disc = t*B^2 */
            /* + 8*N */
            mpz_mul_ui(tmp1, N, 8);
            mpz_add(disc, disc, tmp1); /* disc = t*B^2 + 8N */
            mpz_mul(disc, disc, t);    /* disc = t*(t*B^2+8N) = t^2*B^2+8tN */

            /* disc must be >= 0 */
            if (mpz_sgn(disc) < 0) { mpz_clear(N_t); continue; }

            /* isqrt check */
            mpz_sqrtrem(D, D2, disc);
            if (mpz_sgn(D2) != 0) { mpz_clear(N_t); continue; } /* not perfect square */

            /* m = (D - t*B) / (4t)  -- must be positive integer */
            mpz_mul(tmp1, t, B);           /* tmp1 = tB */
            mpz_sub(tmp1, D, tmp1);        /* tmp1 = D - tB */
            /* must be divisible by 4t */
            mpz_mul_ui(tmp2, t, 4);        /* tmp2 = 4t */
            if (!mpz_divisible_p(tmp1, tmp2)) { mpz_clear(N_t); continue; }
            mpz_divexact(m, tmp1, tmp2);   /* m = (D-tB)/(4t) */

            if (mpz_sgn(m) <= 0) { mpz_clear(N_t); continue; }

            /* check m in [m_lo, m_hi] */
            if (mpz_cmp(m, m_lo) < 0 || mpz_cmp(m, m_hi) > 0) {
                mpz_clear(N_t); continue;
            }

            /* y = m + 6x + t */
            mpz_add(y, m, six_x);
            mpz_add(y, y, t);

            /* verify */
            if (!verify(m, x, y, tmp1, tmp2)) {
                gmp_fprintf(stderr, "VERIFY_FAIL m=%Zd x=%lld t=%lld\n", m, xi, ti);
                mpz_clear(N_t); continue;
            }

            /* output */
            gmp_fprintf(res, "SOL %Zd %lld %Zd\n", m, xi, y);
            fflush(res);
            gmp_printf("SOLUTION m=%Zd x=%lld t=%lld y=%Zd\n", m, xi, ti, y);
            fflush(stdout);
            count_sols++;

            /* Also negative t: y' = m+6x-t (same m, same x, different y sign) */
            mpz_add(y, m, six_x);
            mpz_sub(y, y, t);
            if (mpz_sgn(y) >= 0) {
                if (verify(m, x, y, tmp1, tmp2)) {
                    gmp_fprintf(res, "SOL %Zd %lld %Zd\n", m, xi, y);
                    fflush(res);
                    count_sols++;
                }
            }

            mpz_clear(N_t);
        }

        count_x++;
        if (count_x % 100000 == 0) {
            time_t now = time(NULL);
            double rate = (double)count_x / (now - t0 + 1);
            printf("[disc] x=%lld  sols=%lld  rate=%.0f x/s\n",
                   xi, count_sols, rate);
            fflush(stdout);
            save_checkpoint(xi);
        }
    }

    save_checkpoint(x_end);
    printf("[disc] DONE x=[%lld,%lld] solutions=%lld\n", x_start, x_end, count_sols);

    mpz_clears(x, t, x3, N, disc, D, D2, m, B, tmp1, tmp2, y, six_x, tx_t, NULL);
    mpz_clears(m_lo, m_hi, NULL);
    fclose(res);
    return 0;
}
