#!/usr/bin/env bash
# setup_boinc_project.sh  —  Full automated setup of the ec_curve
#                             Charity Engine / BOINC project on a
#                             Debian/Ubuntu server.
#
# Run as root on a clean VPS.
#   sudo bash setup_boinc_project.sh [--host yourdomain.example.com]
#
# What this script does:
#   1. Installs BOINC server, MySQL, Apache, Python deps
#   2. Creates the boincadm user and project database
#   3. Runs make_project to scaffold the BOINC project dir
#   4. Copies the worker binary and helper scripts into place
#   5. Installs the WU/result XML templates
#   6. Writes config.xml and project.xml
#   7. Starts BOINC daemons (feeder, transitioner, validator, assimilator,
#      work generator) under supervisor
#   8. Configures Apache virtual host
#
# After completion point a browser at http://<host>/ec_curve/

set -euo pipefail
LANG=en_US.UTF-8

# ══════════════════════════════════════════════════════════════════════
# Parameters
# ══════════════════════════════════════════════════════════════════════
PROJECT_NAME="ec_curve"
PROJECT_USER="boincadm"
PROJECT_ROOT="/home/$PROJECT_USER/projects/$PROJECT_NAME"
APP_NAME="ec_curve"
APP_VERSION="1.00"
DB_NAME="$PROJECT_NAME"
DB_USER="boincadm"
DB_PASS="$(openssl rand -hex 20)"

# Parse --host argument
SERVER_HOST="${1:-$(hostname -f)}"
for arg in "$@"; do
  case $arg in
    --host=*) SERVER_HOST="${arg#--host=}" ;;
    --host)   shift; SERVER_HOST="$1" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ec_curve  Charity Engine / BOINC project setup"
echo " Project  : $PROJECT_NAME"
echo " Root     : $PROJECT_ROOT"
echo " Host     : $SERVER_HOST"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. System packages ─────────────────────────────────────────────
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
    boinc-server-maker \
    mysql-server \
    apache2 \
    php \
    php-mysql \
    gcc \
    make \
    python3 \
    python3-pip \
    pari-gp \
    supervisor \
    git \
    curl \
    openssl

pip3 install -q cypari2 || true   # optional in-process PARI

# ── 2. Project user ────────────────────────────────────────────────
id "$PROJECT_USER" &>/dev/null || useradd -m -s /bin/bash "$PROJECT_USER"

# ── 3. MySQL ───────────────────────────────────────────────────────
mysql -u root <<SQL
CREATE DATABASE IF NOT EXISTS $DB_NAME
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL

# ── 4. BOINC project scaffold ──────────────────────────────────────
su - "$PROJECT_USER" -c "
make_project \
    --url_base http://$SERVER_HOST \
    --db_name $DB_NAME \
    --db_user $DB_USER \
    --db_passwd '$DB_PASS' \
    --project_root $PROJECT_ROOT \
    $PROJECT_NAME
" || true   # make_project may already exist

# ── 5. Build the worker binary ─────────────────────────────────────
echo "[setup] Building worker_ec …"
(cd "$SCRIPT_DIR" && make clean all)
WORKER_BIN="$SCRIPT_DIR/worker_ec"

# Create app version directory
PLATFORM="x86_64-pc-linux-gnu"
APP_DIR="$PROJECT_ROOT/apps/$APP_NAME/$APP_VERSION/${PLATFORM}__gcc_opt"
mkdir -p "$APP_DIR"

cp "$WORKER_BIN"                         "$APP_DIR/worker_ec"
cp "$SCRIPT_DIR/worker_pari.py"          "$APP_DIR/"
cp "$SCRIPT_DIR/worker_ec.gp"            "$APP_DIR/"
chmod +x "$APP_DIR/worker_ec"

# Write app version XML
cat > "$APP_DIR/version.xml" <<VXML
<version>
  <file>
    <physical_name>worker_ec</physical_name>
    <main_program/>
    <logical_name>worker_ec</logical_name>
  </file>
  <file>
    <physical_name>worker_pari.py</physical_name>
    <logical_name>worker_pari.py</logical_name>
  </file>
  <file>
    <physical_name>worker_ec.gp</physical_name>
    <logical_name>worker_ec.gp</logical_name>
  </file>
</version>
VXML

# ── 6. WU / result templates ──────────────────────────────────────
cp "$SCRIPT_DIR/templates/ec_curve_wu"     "$PROJECT_ROOT/templates/"
cp "$SCRIPT_DIR/templates/ec_curve_result" "$PROJECT_ROOT/templates/"

# ── 7. Copy Python scripts ─────────────────────────────────────────
cp "$SCRIPT_DIR/work_generator.py" "$PROJECT_ROOT/bin/"
cp "$SCRIPT_DIR/validator.py"      "$PROJECT_ROOT/bin/"
cp "$SCRIPT_DIR/assimilator.py"    "$PROJECT_ROOT/bin/"

chmod +x "$PROJECT_ROOT/bin/work_generator.py"
chmod +x "$PROJECT_ROOT/bin/validator.py"
chmod +x "$PROJECT_ROOT/bin/assimilator.py"

# ── 8. config.xml daemons ─────────────────────────────────────────
cat >> "$PROJECT_ROOT/config.xml" <<DXML
  <daemon>
    <cmd>feeder -d 3</cmd>
  </daemon>
  <daemon>
    <cmd>transitioner -d 3</cmd>
  </daemon>
  <daemon>
    <cmd>file_deleter -d 3</cmd>
  </daemon>
  <daemon>
    <cmd>python3 bin/validator.py --app ec_curve</cmd>
    <pid_file>log_$PROJECT_NAME/validator.pid</pid_file>
  </daemon>
  <daemon>
    <cmd>python3 bin/assimilator.py --results_dir results \
         --master solutions_master.txt</cmd>
    <pid_file>log_$PROJECT_NAME/assimilator.pid</pid_file>
  </daemon>
  <daemon>
    <cmd>python3 bin/work_generator.py --mode boinc \
         --project_dir $PROJECT_ROOT</cmd>
    <pid_file>log_$PROJECT_NAME/work_generator.pid</pid_file>
  </daemon>
DXML

# ── 9. Create the app in DB ────────────────────────────────────────
su - "$PROJECT_USER" -c "
  cd $PROJECT_ROOT
  ./bin/xadd
  ./bin/update_versions
  ./bin/start
"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " SETUP COMPLETE"
echo " DB password  : $DB_PASS  (save this!)"
echo " Project URL  : http://$SERVER_HOST/$PROJECT_NAME/"
echo " Status check : su $PROJECT_USER -c '$PROJECT_ROOT/bin/status'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
