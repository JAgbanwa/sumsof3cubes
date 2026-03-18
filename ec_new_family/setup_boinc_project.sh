#!/usr/bin/env bash
# setup_boinc_project.sh  —  One-shot BOINC server setup for ec_new_family
#
# Tested on Debian 11/12 and Ubuntu 22.04/24.04.
# Run as root (or with sudo) on the BOINC server:
#
#   sudo bash setup_boinc_project.sh --host ec-nf.yourdomain.com
#
# What this script does:
#   1. Installs BOINC server dependencies
#   2. Creates the boincadm user
#   3. Clones + builds the BOINC server software
#   4. Creates the ec_nf project with make_project
#   5. Copies application files (worker binary + templates)
#   6. Registers the app version in the BOINC database
#   7. Initialises the work-queue DB
#   8. Writes cron jobs for the assimilator, validator, and queue daemon
#   9. Starts BOINC daemons

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_NAME="ec_nf"
PROJECT_SHORTNAME="ec_nf"
BOINC_USER="boincadm"
BOINC_HOME="/home/${BOINC_USER}"
PROJECT_DIR="${BOINC_HOME}/projects/${PROJECT_NAME}"
BOINC_SRC="${BOINC_HOME}/boinc"
APP_VERSION="1.00"
PLATFORM="x86_64-pc-linux-gnu"   # built on the server itself

HOST="${1:-}"
if [[ -z "$HOST" ]]; then
    read -rp "Enter your server hostname (e.g. ec-nf.yourdomain.com): " HOST
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Install dependencies ───────────────────────────────────────────────────
echo "==> Installing dependencies..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    build-essential git autoconf automake libtool pkg-config \
    libssl-dev libcurl4-openssl-dev \
    mysql-server python3 python3-pip \
    apache2 php libapache2-mod-php \
    m4 curl wget

# ── 2. Create boincadm user ───────────────────────────────────────────────────
id "${BOINC_USER}" 2>/dev/null || useradd -m -s /bin/bash "${BOINC_USER}"

# ── 3. Clone + build BOINC server ────────────────────────────────────────────
if [[ ! -d "${BOINC_SRC}" ]]; then
    echo "==> Cloning BOINC source..."
    sudo -u "${BOINC_USER}" git clone --depth 1 \
        https://github.com/BOINC/boinc.git "${BOINC_SRC}"
fi
echo "==> Building BOINC server..."
cd "${BOINC_SRC}"
sudo -u "${BOINC_USER}" bash -c \
    "./_autosetup && ./configure --disable-client --disable-manager --enable-server && make -j$(nproc)"

# ── 4. Create the project ─────────────────────────────────────────────────────
echo "==> Creating BOINC project ${PROJECT_NAME}..."
cd "${BOINC_SRC}/tools"
sudo -u "${BOINC_USER}" ./make_project \
    --url_base "http://${HOST}" \
    --db_name "${PROJECT_NAME}" \
    --project_root "${PROJECT_DIR}" \
    --no_query \
    "${PROJECT_NAME}" "${PROJECT_SHORTNAME}" || true

# ── 5. Build and install the worker ──────────────────────────────────────────
echo "==> Building worker binary..."
cd "${SRC_DIR}"
gcc -O3 -march=native -std=c11 -DBOINC \
    -I"${BOINC_SRC}/api" -I"${BOINC_SRC}/lib" \
    -o worker_ec_boinc worker_ec.c \
    -L"${BOINC_SRC}/api" -L"${BOINC_SRC}/lib" \
    -lm -lboinc_api -lboinc

APP_VERSION_DIR="${PROJECT_DIR}/apps/${PROJECT_NAME}/${APP_VERSION}/${PLATFORM}__${PLATFORM}"
mkdir -p "${APP_VERSION_DIR}"
cp worker_ec_boinc "${APP_VERSION_DIR}/${PROJECT_NAME}"

# ── 6. Register the app version ───────────────────────────────────────────────
echo "==> Registering app version in BOINC DB..."
cd "${PROJECT_DIR}"
sudo -u "${BOINC_USER}" bin/update_versions

# ── 7. Copy templates ─────────────────────────────────────────────────────────
echo "==> Installing WU templates..."
cp "${SRC_DIR}/templates/ec_nf_wu"     "${PROJECT_DIR}/templates/"
cp "${SRC_DIR}/templates/ec_nf_result" "${PROJECT_DIR}/templates/"

# ── 8. Copy project scripts ───────────────────────────────────────────────────
for f in boinc_queue.py assimilator.py validator.py local_search.py; do
    cp "${SRC_DIR}/${f}" "${PROJECT_DIR}/${f}"
done
mkdir -p "${PROJECT_DIR}/output"
touch "${PROJECT_DIR}/output/solutions.txt"
mkdir -p "${PROJECT_DIR}/results"

# ── 9. Initialise work-queue DB ───────────────────────────────────────────────
echo "==> Initialising work-queue DB (n=1..500)..."
cd "${PROJECT_DIR}"
sudo -u "${BOINC_USER}" python3 boinc_queue.py init --n-hi 500

# ── 10. Write BOINC daemon config ─────────────────────────────────────────────
echo "==> Writing config.xml additions..."
cat >> "${PROJECT_DIR}/config.xml" << 'XMLEOF'
<!-- ec_nf daemons — added by setup_boinc_project.sh -->
<daemon>
    <cmd>python3 boinc_queue.py submit --project_dir __PROJECT_DIR__</cmd>
    <output>boinc_queue_log.txt</output>
</daemon>
<daemon>
    <cmd>python3 assimilator.py --results_dir results --master output/solutions.txt --verbose</cmd>
    <output>assimilator_log.txt</output>
    <period>60</period>
</daemon>
<daemon>
    <cmd>python3 validator.py</cmd>
    <output>validator_log.txt</output>
    <period>300</period>
</daemon>
XMLEOF
# Replace placeholder with actual path
sed -i "s|__PROJECT_DIR__|${PROJECT_DIR}|g" "${PROJECT_DIR}/config.xml"

# ── 11. Start daemons ─────────────────────────────────────────────────────────
echo "==> Starting BOINC project daemons..."
cd "${PROJECT_DIR}"
sudo -u "${BOINC_USER}" bin/start

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ec_nf BOINC project is running at: http://${HOST}"
echo ""
echo "  Monitor:  python3 ${PROJECT_DIR}/boinc_queue.py status"
echo "  Solutions: cat ${PROJECT_DIR}/output/solutions.txt"
echo "════════════════════════════════════════════════════════════"
