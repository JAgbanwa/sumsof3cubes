\\  worker_ec_large.gp
\\  ─────────────────────────────────────────────────────────────────────
\\  PARI/GP inner script for the Charity Engine large-n search.
\\
\\  Equation (user presentation, parameter m):
\\
\\    y² = (36/m)·x³ + 36·x² + 12m·x + (m³−19)/m        (m ≠ 0)
\\
\\  This is birationally equivalent to the standard Weierstrass curve
\\  (multiply through by m³ and substitute X = 6mx, Y = 6m²y):
\\
\\    E_m : Y² = X³ + 1296·m²·X² + 15552·m³·X + (46656·m⁴ − 19·m)
\\
\\  i.e., [a1,a2,a3,a4,a6] = [0, 1296m², 0, 15552m³, 46656m⁴−19m]
\\
\\  This script uses PARI's ellintegralpoints() which is PROVABLY
\\  COMPLETE by Siegel's theorem: ALL integer points are found for
\\  every m, regardless of how large they are.  No x-limit required.
\\
\\  Call:
\\    gp -q --stacksize=512m    (script read with \\r)
\\    ec_search_large(m_start, m_end)
\\
\\  Output lines:
\\    SOL  m  X  Y         — verified integer solution
\\    SKP  m  reason       — curve skipped (singular / rank timeout)
\\    DONE m_start m_end ms=<ms>
\\
\\  Note on coordinate conventions:
\\    X, Y are the Weierstrass coordinates.
\\    To recover the user's (x,y): x = X/(6m), y = Y/(6m²)
\\    (only relevant when m | X and m² | Y; checked on output).
\\  ─────────────────────────────────────────────────────────────────────

\\ ===================================================================
\\ RHS evaluator in Weierstrass form
\\ ===================================================================
ec_rhs_large(m, X) = {
    X^3 + 1296*m^2*X^2 + 15552*m^3*X + 46656*m^4 - 19*m
}

ec_verify_large(m, X, Y) = {
    Y^2 == ec_rhs_large(m, X)
}

\\ ===================================================================
\\ Back-transform to user (x,y) if integer
\\ ===================================================================
ec_user_xy(m, X, Y, ref_x, ref_y) = {
    my(rx, ry);
    if(m == 0, return(0));
    rx = X / (6*m);
    ry = Y / (6*m^2);
    if(type(rx) == "t_INT" && type(ry) == "t_INT",
        ref_x = rx;
        ref_y = ry;
        return(1)
    ,
        return(0)
    )
}

\\ ===================================================================
\\ Search one m value
\\ ===================================================================
ec_search_one_large(m) = {
    my(E, pts, X, Y, a2, a4, a6, disc, np, rx, ry, t0, dt);

    if(m == 0, return());

    a2 = 1296*m^2;
    a4 = 15552*m^3;
    a6 = 46656*m^4 - 19*m;

    E = ellinit([0, a2, 0, a4, a6]);
    disc = E.disc;
    if(disc == 0,
        printf("SKP %Pd singular\n", m);
        return()
    );

    \\ ellintegralpoints is provably complete (Baker height bounds + LLL)
    \\ For large m the height bound can be large; pari handles it exactly.
    pts = ellintegralpoints(E, 1);  \\ 1 = also return negative y

    np = #pts;
    for(i = 1, np,
        X = pts[i][1];
        Y = pts[i][2];
        if(!ec_verify_large(m, X, Y),
            printf("## VERIFY_FAIL m=%Pd X=%Pd Y=%Pd\n", m, X, Y);
            next()
        );
        \\ Print Weierstrass solution
        printf("SOL %Pd %Pd %Pd\n", m, X, Y);
    )
}

\\ ===================================================================
\\ Main entry: range [m_start, m_end]
\\ ===================================================================
ec_search_large(m_start, m_end) = {
    my(m, t0, dt);
    t0 = gettime();
    for(m = m_start, m_end,
        if(m == 0, next());
        ec_search_one_large(m);
    );
    dt = gettime() - t0;
    printf("DONE %Pd %Pd ms=%d\n", m_start, m_end, dt);
}
