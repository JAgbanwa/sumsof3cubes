FROM debian:bookworm-slim

# -----------------------------------------------------------------------
# Install BOINC server stack + build tools
# -----------------------------------------------------------------------
RUN apt-get update && apt-get install -y \
    boinc-server-maker \
    mysql-server \
    apache2 \
    php php-mysql \
    gcc make \
    python3 python3-pip \
    openssl curl git \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install gmpy2 sympy --break-system-packages

# -----------------------------------------------------------------------
# Copy project source
# -----------------------------------------------------------------------
WORKDIR /opt/sumsof3cubes
COPY . .

# -----------------------------------------------------------------------
# Build the C worker
# -----------------------------------------------------------------------
WORKDIR /opt/sumsof3cubes/boinc_app
RUN make all

# -----------------------------------------------------------------------
# Expose BOINC project ports
# -----------------------------------------------------------------------
EXPOSE 80 443

# -----------------------------------------------------------------------
# Entry point: run fast_search.py locally for real-time
# or setup_boinc_project.sh for full BOINC deployment
# -----------------------------------------------------------------------
WORKDIR /opt/sumsof3cubes
ENTRYPOINT ["python3", "fast_search.py"]
CMD ["--cores", "4", "--x_limit", "10000000"]
