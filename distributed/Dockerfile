# Volunteer worker Docker image for the sums-of-cubes distributed search.
# Based on the official SageMath image – no local Sage install needed.
#
# Build:
#   docker build -t cubes-worker .
#
# Run (replace SERVER and KEY):
#   docker run --rm -e SERVER=http://HOST:5555 -e KEY=volunteer_key_change_me \
#              -e WORKERS=4 cubes-worker
#
# Or with docker-compose:
#   docker-compose up --scale worker=8

FROM sagemath/sagemath:10.5

LABEL maintainer="sumsof3cubes contributor"
LABEL description="Volunteer worker for m∈[10²⁰,10³⁰] search"

# Install Python requests (Sage's Python already has pip)
RUN sage -pip install requests --quiet

# Copy worker scripts
WORKDIR /opt/cubes
COPY worker.py      .
COPY worker_sage.sage .

# Environment (overridden at runtime)
ENV SERVER=http://localhost:5555
ENV KEY=volunteer_key_change_me
ENV WORKERS=1
ENV WORKER_ID=""

# Health-check: ping server status
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python3 -c "import requests,os; requests.get(os.environ['SERVER']+'/api/status', timeout=5)"

ENTRYPOINT ["sh", "-c", \
    "exec sage -python worker.py \
        --server \"$SERVER\" \
        --key \"$KEY\" \
        --workers \"$WORKERS\" \
        ${WORKER_ID:+--worker-id \"$WORKER_ID\"}"]
