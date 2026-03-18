\\ worker_ec.gp  —  PARI/GP inner script
\\
\\ Finds ALL integral points on the short-Weierstrass curve family:
\\
\\   E_n : y² = X³ + a4(n)·X + a6(n)
\\
\\ where
\\   a4(n) = -45349632·n⁴ + 419904·n³
\\   a6(n) = 3·(39182082048·n⁶ − 544195584·n⁵ + 1259712·n⁴ − 19·n)
\\
\\ The point X = −3888·n² is excluded (trivial locus).
\\
\\ Strategy (provably complete):
\\   1. Build E_n in short Weierstrass form  [0,0,0,a4,a6]
\\   2. Skip n=0 (singular) and any other degenerate n (disc=0)
\\   3. ellintegralpoints(E) — Baker bound + MW saturation + LLL
\\      → ALL integral points, certified.
\\   4. Filter out X = −3888·n²
\\   5. Self-verify each output point
\\
\\ Entry point:
\\   ec_search(n_start, n_end)
\\ e.g.  echo "ec_search(1,500)" | gp -q worker_ec.gp
\\       gp -q --stacksize=256m worker_ec.gp <<< "ec_search(-200,200)"

\\ ================================================================
\\ Equation RHS evaluator
\\ ================================================================
ec_rhs(n, X) = {
    my(a4, a6);
    a4 = -45349632*n^4 + 419904*n^3;
    a6 = 3*(39182082048*n^6 - 544195584*n^5 + 1259712*n^4 - 19*n);
    X^3 + a4*X + a6
}

ec_verify(n, X, y) = { y^2 == ec_rhs(n, X) }

\\ ================================================================
\\ Core: integral points for a single n ≠ 0
\\ ================================================================
ec_search_one(n) = {
    my(E, pts, X, y, np, disc, a4, a6, excl);

    a4   = -45349632*n^4 + 419904*n^3;
    a6   = 3*(39182082048*n^6 - 544195584*n^5 + 1259712*n^4 - 19*n);
    excl = -3888*n^2;

    \\ Short Weierstrass: [a1,a2,a3,a4,a6] = [0,0,0,a4,a6]
    E = ellinit([0, 0, 0, a4, a6]);

    disc = E.disc;
    if(disc == 0,
        printf("## SINGULAR n=%d\n", n);
        return()
    );

    \\ Provably-complete integral-point enumeration
    pts = ellintegralpoints(E);

    np = #pts;
    if(np == 0, return());

    for(i = 1, np,
        X = pts[i][1];
        y = pts[i][2];

        \\ Skip the excluded locus
        if(X == excl, next());

        \\ Self-verify
        if(ec_verify(n, X, y),
            printf("%d %d %d\n", n, X,  y);
            if(y != 0,
                printf("%d %d %d\n", n, X, -y)
            )
        ,
            printf("## VERIFY_FAIL n=%d X=%d y=%d\n", n, X, y)
        )
    );
}

\\ ================================================================
\\ Main entry: process range [n_start, n_end]
\\ ================================================================
ec_search(n_start, n_end) = {
    my(n, t0, dt);
    t0 = gettime();
    for(n = n_start, n_end,
        if(n == 0, next());
        ec_search_one(n);
    );
    dt = gettime() - t0;
    printf("## DONE n_start=%d n_end=%d ms=%d\n", n_start, n_end, dt);
}
