# ══════════════════════════════════════════════════════════════════════
# Dockerfile  —  Charity Engine / BOINC worker image
#
# Equation: y² = (36/m)·x³ + 36·x² + 12m·x + (m³−19)/m
# Provably-complete search via PARI/GP ellintegralpoints()
#
# Build:
#   docker build -t ec_large_worker .
#
# Test standalone:
#   docker run --rm -v $PWD/test_wus:/work ec_large_worker \
#       python3 /app/worker_pari_large.py /work/wu.txt /work/result.txt
#
# Charity Engine deployment:
#   docker push <registry>/ec_large_worker:latest
#   Configure the CE project to pull this image.
# ══════════════════════════════════════════════════════════════════════

FROM ubuntu:24.04

# ── system packages ────────────────────────────────────────────────
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        pari-gp \
        git curl wget ca-certificates \
        libpari-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Python packages ────────────────────────────────────────────────
RUN pip3 install --no-cache-dir \
        cypari2==2.1.5 \
        requests

# ── Copy application ───────────────────────────────────────────────
WORKDIR /app
COPY worker_pari_large.py   .
COPY worker_ec_large.gp     .
COPY assimilator_github.py  .
COPY validator_large.py     .

# ── Working directory for WU I/O ──────────────────────────────────
WORKDIR /work

# ── BOINC environment defaults ────────────────────────────────────
ENV BOINC=1
ENV GP_BIN=gp

# ── Default command: read wu.txt, write result.txt ─────────────────
ENTRYPOINT ["python3", "/app/worker_pari_large.py"]
CMD ["wu.txt", "result.txt", "checkpoint.json"]

# ── Health-check: write fraction_done every 60 s ──────────────────
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD test -f /work/fraction_done && \
        [ $(( $(date +%s) - $(date +%s -r /work/fraction_done) )) -lt 120 ]
