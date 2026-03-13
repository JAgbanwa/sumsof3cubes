#!/usr/bin/env bash
# setup_ec19n_boinc.sh  —  One-shot BOINC server setup for ec19n project.
#
# Installs BOINC server, scaffolds the ec19n project, uploads the worker
# binary, and starts the daemons.
#
# Prerequisites:
#   - Debian 11/12 or Ubuntu 22.04/24.04 server
#   - Run as root (or with sudo)
#   - Internet access for apt packages
#
# Usage:
#   sudo bash setup_ec19n_boinc.sh --host ec19n.example.com [--email admin@example.com]
#
# After setup, run the work generator:
#   python3 /home/boincadm/projects/ec19n/ec19n_work_generator.py --mode boinc --max_new 500

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────
BOINC_PROJ="ec19n"
BOINC_USER="boincadm"
DB_USER="boincadm"
DB_PASS="ec19ndbpass"
HOST=""
ADMIN_EMAIL="admin@localhost"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Parse args ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --host)  HOST="$2"; shift 2 ;;
        --email) ADMIN_EMAIL="$2"; shift 2 ;;
        *)       echo "Unknown arg: $1"; exit 1 ;;
    esac
done
[[ -z "$HOST" ]] && { echo "ERROR: --host required"; exit 1; }

echo "╔══════════════════════════════════════════════════════╗"
echo "║  ec19n BOINC Project Setup                          ║"
echo "║  host=$HOST                                         ║"
echo "╚══════════════════════════════════════════════════════╝"

# ── 1. System packages ───────────────────────────────────────────────────
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    boinc-server-maker mysql-server apache2 php \
    python3 python3-pip gcc make git curl wget \
    libmysqlclient-dev m4 pkg-config

# ── 2. BOINC server + MySQL ──────────────────────────────────────────────
echo "[2/8] Setting up MySQL..."
mysql -u root <<SQL
CREATE DATABASE IF NOT EXISTS ${BOINC_PROJ};
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON ${BOINC_PROJ}.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

# ── 3. Create BOINC user and project ────────────────────────────────────
echo "[3/8] Creating BOINC user and project..."
id "$BOINC_USER" &>/dev/null || useradd -m -s /bin/bash "$BOINC_USER"
PROJ_DIR="/home/${BOINC_USER}/projects/${BOINC_PROJ}"

sudo -u "$BOINC_USER" bash -c "
    /usr/lib/boinc-server-maker/make_project \\
        --project_host ${HOST} \\
        --project_name 'ec19n: Integer Points on Elliptic Curves' \\
        --db_name ${BOINC_PROJ} \\
        --db_user ${DB_USER} \\
        --db_passwd ${DB_PASS} \\
        --user_name Admin \\
        --admin_email ${ADMIN_EMAIL} \\
        ${BOINC_PROJ} \\
    || true
"

# ── 4. Build worker binary ───────────────────────────────────────────────
echo "[4/8] Building ec19n_worker (BOINC variant)..."
BOINC_INC=""
BOINC_LIB=""
# Attempt to locate BOINC headers
for d in /usr/include/boinc /usr/local/include/boinc; do
    [[ -f "$d/boinc_api.h" ]] && BOINC_INC="$d" && break
done
for d in /usr/lib /usr/local/lib; do
    [[ -f "$d/libboinc_api.a" ]] && BOINC_LIB="$d" && break
done

if [[ -n "$BOINC_INC" && -n "$BOINC_LIB" ]]; then
    gcc -O3 -march=native -std=c99 -DBOINC \
        -I"$BOINC_INC" \
        -o "${SRC_DIR}/ec19n_worker_boinc" \
        "${SRC_DIR}/ec19n_worker.c" \
        -L"$BOINC_LIB" -lm -lboinc_api -lboinc
    WORKER_BIN="${SRC_DIR}/ec19n_worker_boinc"
    echo "    Built with BOINC API."
else
    # Fallback: standalone binary works as BOINC wrapper too
    gcc -O3 -march=native -std=c99 \
        -o "${SRC_DIR}/ec19n_worker_boinc" \
        "${SRC_DIR}/ec19n_worker.c" -lm
    WORKER_BIN="${SRC_DIR}/ec19n_worker_boinc"
    echo "    WARNING: BOINC headers not found, built without BOINC API."
fi

# ── 5. Install app into BOINC project ───────────────────────────────────
echo "[5/8] Installing app and templates..."
APP_DIR="${PROJ_DIR}/apps/ec19n/1.00_x86_64-pc-linux-gnu"
sudo -u "$BOINC_USER" mkdir -p "$APP_DIR"
sudo -u "$BOINC_USER" cp "$WORKER_BIN" "$APP_DIR/ec19n_worker"
sudo -u "$BOINC_USER" chmod 755 "$APP_DIR/ec19n_worker"

# Register app in DB
mysql -u "$DB_USER" -p"$DB_PASS" "$BOINC_PROJ" <<SQL || true
INSERT IGNORE INTO app (name, user_friendly_name, create_time, weight, min_version)
VALUES ('ec19n','ec19n: y^2=x^3+...+46656n^4-19n  [y/(6n) integer search]',UNIX_TIMESTAMP(),1,0);
INSERT IGNORE INTO app_version (app_id, version_num, platform, xml_doc, create_time, min_core_version, max_core_version)
SELECT id, 100, 'x86_64-pc-linux-gnu',
    '<app_version><file_ref><file_name>ec19n_worker</file_name><main_program/></file_ref></app_version>',
    UNIX_TIMESTAMP(), 60000, 99999
FROM app WHERE name='ec19n';
SQL

# Templates
sudo -u "$BOINC_USER" cp "${SRC_DIR}/templates/ec19n_wu"     "${PROJ_DIR}/templates/"
sudo -u "$BOINC_USER" cp "${SRC_DIR}/templates/ec19n_result" "${PROJ_DIR}/templates/"

# Copy management scripts
sudo -u "$BOINC_USER" cp "${SRC_DIR}/ec19n_work_generator.py" "$PROJ_DIR/"
sudo -u "$BOINC_USER" cp "${SRC_DIR}/ec19n_validator.py"      "$PROJ_DIR/"
sudo -u "$BOINC_USER" cp "${SRC_DIR}/ec19n_assimilator.py"    "$PROJ_DIR/"

# ── 6. Configure daemons in config.xml ───────────────────────────────────
echo "[6/8] Writing daemon config..."
CONFIG="${PROJ_DIR}/config.xml"
# Inject daemons block if not present
if ! grep -q "ec19n_work_generator" "$CONFIG"; then
sudo -u "$BOINC_USER" python3 - << PYEOF
import re
cfg = open('${CONFIG}').read()
daemons = """
    <!-- ec19n daemons -->
    <daemon>
      <!-- Frontier-based WU queue manager: fills BOINC queue from ec19n_wuqueue.db -->
      <cmd>python3 $PROJ_DIR/ec19n_boinc_queue.py submit --project_dir $PROJ_DIR</cmd>
      <period>90</period>
    </daemon>
    <daemon>
      <cmd>python3 $PROJ_DIR/ec19n_assimilator.py --mode boinc</cmd>
      <period>30</period>
    </daemon>
    <daemon>
      <cmd>sample_validate_program --app ec19n --validate_cmd "python3 $PROJ_DIR/ec19n_validator.py" --credit_from_wu 100</cmd>
    </daemon>
    <daemon>
      <cmd>file_deleter</cmd>
    </daemon>
"""
cfg = cfg.replace('</daemons>', daemons + '</daemons>')
open('${CONFIG}', 'w').write(cfg)
print("Daemon config written.")
PYEOF
fi

# ── 7. Apache virtual host ───────────────────────────────────────────────
echo "[7/8] Configuring Apache..."
cat > "/etc/apache2/sites-available/${BOINC_PROJ}.conf" <<APACHE
<VirtualHost *:80>
    ServerName ${HOST}
    DocumentRoot ${PROJ_DIR}/html/user
    Alias /download/ ${PROJ_DIR}/download/
    Alias /results/  ${PROJ_DIR}/upload/
    <Directory ${PROJ_DIR}/html>
        AllowOverride All
        Options FollowSymLinks
        Require all granted
    </Directory>
</VirtualHost>
APACHE
a2ensite "${BOINC_PROJ}.conf"
a2enmod rewrite
systemctl reload apache2

# ── 8. Initialise queue DB, start BOINC project ─────────────────────────
echo "[8/8] Initialising WU queue DB and starting daemons..."
sudo -u "$BOINC_USER" bash -c "
    cd ${PROJ_DIR}
    python3 ec19n_boinc_queue.py init
    ./bin/start
"

echo ""
echo "══════════════════════════════════════════════════════"
echo "  ec19n BOINC project is RUNNING."
echo ""
echo "  Admin URL:   http://${HOST}/ec19n"
echo "  Key file:    ${PROJ_DIR}/keys/master.pub"
echo ""
echo "  WU queue DB: ${PROJ_DIR}/ec19n_wuqueue.db"
echo "  The submit daemon fills BOINC automatically."
echo ""
echo "  Monitor progress:"
echo "    python3 ${PROJ_DIR}/ec19n_boinc_queue.py status"
echo ""
echo "  Solutions:"
echo "    ${PROJ_DIR}/output/solutions_ec19n.txt"
echo ""
echo "  If WUs get stuck after server downtime:"
echo "    python3 ${PROJ_DIR}/ec19n_boinc_queue.py reset_stuck --stuck_hours 48"
echo "══════════════════════════════════════════════════════"
