\\  worker_ec.gp  —  PARI/GP inner script
\\
\\  For each n in [n_start, n_end], find ALL integer points on:
\\
\\    E_n : y² = x³ + 1296·n²·x² + 15552·n³·x + (46656·n⁴ − 19·n)
\\
\\  Strategy:
\\    1. Initialise E_n in generalised Weierstrass form
\\       [a1,a2,a3,a4,a6] = [0, 1296·n², 0, 15552·n³, 46656·n⁴−19·n]
\\    2. Skip singular curves (disc = 0, which occurs only at n = 0)
\\    3. ellintegralpoints(E) uses:
\\         • Nagell-Tate torsion enumeration
\\         • Mordell-Weil rank via 2-descent
\\         • Elliptic-logarithm height bound (Baker–Wüstholz)
\\         • LLL + sieving to enumerate all integral points
\\       This is PROVABLY COMPLETE: all integer points are found.
\\    4. Print: n  x  y   (one solution per line)
\\
\\  Called from worker_pari.py via:
\\     gp -q --stacksize=256m worker_ec.gp <<< "ec_search(n_start, n_end)"
\\  or:
\\     echo "ec_search(n_start, n_end)" | gp -q worker_ec.gp
\\
\\  Output format:  n x y
\\                  (y = 0 is omitted from the ±y duplication)

\\  ================================================================
\\  Equation evaluator – used for self-verification
\\  ================================================================
ec_rhs(n, x) = {
    x^3 + 1296*n^2*x^2 + 15552*n^3*x + 46656*n^4 - 19*n
}

ec_verify(n, x, y) = {
    y^2 == ec_rhs(n, x)
}

\\  ================================================================
\\  Handle n = 0 separately (degenerate: y² = x³)
\\  ================================================================
ec_n0(x_lim) = {
    print("0 0 0");
    k = 1;
    while(k^2 <= x_lim,
        xs = k^2;
        ys = k^3;
        printf("%d %d %d\n", 0, xs,  ys);
        printf("%d %d %d\n", 0, xs, -ys);
        k++;
    );
}

\\  ================================================================
\\  Core: find all integral points for a single n ≠ 0
\\  ================================================================
ec_search_one(n) = {
    my(E, pts, x, y, np, disc, a2, a4, a6);

    \\ Generalised Weierstrass form [a1,a2,a3,a4,a6]
    \\ y² + a1·xy + a3·y = x³ + a2·x² + a4·x + a6
    \\ Here a1=a3=0, so  y² = x³ + a2·x² + a4·x + a6
    a2 = 1296*n^2;
    a4 = 15552*n^3;
    a6 = 46656*n^4 - 19*n;

    E = ellinit([0, a2, 0, a4, a6]);

    \\ Discriminant check (singular curve has no integral points structure)
    disc = E.disc;
    if(disc == 0, return());          \\ singular — skip

    \\ Find ALL integral points (PARI certificate-level completeness)
    pts = ellintegralpoints(E);

    np = #pts;
    if(np == 0, return());

    for(i = 1, np,
        x = pts[i][1];
        y = pts[i][2];
        \\ Self-verify before printing
        if(ec_verify(n, x, y),
            printf("%d %d %d\n", n, x,  y);
            if(y != 0,
                printf("%d %d %d\n", n, x, -y)
            )
        ,
            printf("## VERIFY_FAIL n=%d x=%d y=%d\n", n, x, y)
        )
    );
}

\\  ================================================================
\\  Main entry: process range [n_start, n_end]
\\  ================================================================
ec_search(n_start, n_end) = {
    my(n, t0, dt);
    t0 = gettime();
    if(n_start <= 0 && 0 <= n_end,
        \\ n=0: degenerate case, bounded by x_lim=10^6
        ec_n0(10^6)
    );
    for(n = n_start, n_end,
        if(n == 0, next());
        ec_search_one(n);
    );
    dt = gettime() - t0;
    printf("## DONE n_start=%d n_end=%d ms=%d\n", n_start, n_end, dt);
}
