#!/usr/bin/env bash
# setup_boinc_project.sh
# Full automated setup of a BOINC project on a Debian/Ubuntu server.
# Run as root on a clean VPS (Charity Engine requires you to submit a
# registered BOINC project URL; this script builds the server side).
#
# Usage:
#   sudo bash setup_boinc_project.sh

set -euo pipefail

PROJECT_NAME="sumsof3cubes"
PROJECT_USER="boincadm"
PROJECT_ROOT="/home/$PROJECT_USER/projects/$PROJECT_NAME"
DB_NAME="$PROJECT_NAME"
DB_USER="boincadm"
DB_PASS="$(openssl rand -hex 16)"
SERVER_HOST="$(hostname -f)"
APP_NAME="sumsof3cubes"
APP_VERSION="1.00"

echo "=== Sum-of-Three-Cubes BOINC Project Setup ==="
echo "Project:  $PROJECT_NAME"
echo "Root:     $PROJECT_ROOT"
echo "Host:     $SERVER_HOST"

# -----------------------------------------------------------------------
# 1. Install dependencies
# -----------------------------------------------------------------------
apt-get update -q
apt-get install -y -q \
    boinc-server-maker \
    mysql-server \
    apache2 \
    php \
    php-mysql \
    gcc \
    make \
    python3 \
    python3-pip \
    openssl \
    curl \
    git

pip3 install gmpy2 sympy -q

# -----------------------------------------------------------------------
# 2. Create BOINC project user
# -----------------------------------------------------------------------
id "$PROJECT_USER" &>/dev/null || useradd -m -s /bin/bash "$PROJECT_USER"

# -----------------------------------------------------------------------
# 3. MySQL setup
# -----------------------------------------------------------------------
mysql -u root <<SQL
CREATE DATABASE IF NOT EXISTS $DB_NAME CHARACTER SET utf8;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL

# -----------------------------------------------------------------------
# 4. Create BOINC project
# -----------------------------------------------------------------------
su - "$PROJECT_USER" -c "
make_project \
    --url_base http://$SERVER_HOST \
    --db_name $DB_NAME \
    --db_user $DB_USER \
    --db_passwd '$DB_PASS' \
    --project_root $PROJECT_ROOT \
    $PROJECT_NAME
"

# -----------------------------------------------------------------------
# 5. Copy application binary and templates
# -----------------------------------------------------------------------
APP_DIR="$PROJECT_ROOT/apps/$APP_NAME/$APP_VERSION"
mkdir -p "$APP_DIR/x86_64-pc-linux-gnu__cuda"

# Build the worker binary
cd "$(dirname "$0")"
make -C boinc_app -f Makefile all 2>&1 || true
if [ -f "boinc_app/worker" ]; then
    # Build BOINC version if API available
    make -C boinc_app -f Makefile boinc 2>&1 || cp boinc_app/worker boinc_app/worker_boinc
    cp boinc_app/worker_boinc "$APP_DIR/x86_64-pc-linux-gnu__cuda/${APP_NAME}__x86_64-pc-linux-gnu"
    chmod +x "$APP_DIR/x86_64-pc-linux-gnu__cuda/${APP_NAME}__x86_64-pc-linux-gnu"
fi

# Templates
cp boinc_app/templates/* "$PROJECT_ROOT/templates/"

# -----------------------------------------------------------------------
# 6. Create app version XML
# -----------------------------------------------------------------------
cat > "$APP_DIR/x86_64-pc-linux-gnu__cuda/version.xml" <<XML
<version>
    <file>
        <physical_name>${APP_NAME}__x86_64-pc-linux-gnu</physical_name>
        <main_program/>
    </file>
</version>
XML

# -----------------------------------------------------------------------
# 7. Register app in BOINC DB
# -----------------------------------------------------------------------
su - "$PROJECT_USER" -c "
cd $PROJECT_ROOT && \
./bin/xadd && \
./bin/update_versions
"

# -----------------------------------------------------------------------
# 8. Install daemon config
# -----------------------------------------------------------------------
CONFIG="$PROJECT_ROOT/config.xml"
python3 - <<PYEOF
import xml.etree.ElementTree as ET

tree = ET.parse("$CONFIG")
root = tree.getroot()
daemons = root.find("daemons")
if daemons is None:
    daemons = ET.SubElement(root, "daemons")

# Work generator
wg = ET.SubElement(daemons, "daemon")
ET.SubElement(wg, "cmd").text = "python3 $(pwd)/boinc_app/work_generator.py --boinc_project_dir $PROJECT_ROOT"
ET.SubElement(wg, "output").text = "work_generator.out"
ET.SubElement(wg, "pid_file").text = "work_generator.pid"

# Validator
va = ET.SubElement(daemons, "daemon")
ET.SubElement(va, "cmd").text = "python3 $(pwd)/boinc_app/validator.py"
ET.SubElement(va, "output").text = "validator.out"

# Assimilator
asi = ET.SubElement(daemons, "daemon")
ET.SubElement(asi, "cmd").text = "python3 $(pwd)/boinc_app/assimilator.py --results_dir $PROJECT_ROOT/upload --master $(pwd)/solutions_master.txt"
ET.SubElement(asi, "output").text = "assimilator.out"

tree.write("$CONFIG")
print("[setup] config.xml updated")
PYEOF

# -----------------------------------------------------------------------
# 9. Start project
# -----------------------------------------------------------------------
su - "$PROJECT_USER" -c "cd $PROJECT_ROOT && ./bin/start"

echo ""
echo "=== Setup complete ==="
echo "DB password stored in: db_password.txt"
echo "$DB_PASS" > db_password.txt
chmod 600 db_password.txt
echo "Project URL:  http://$SERVER_HOST/$PROJECT_NAME/"
echo "Admin URL:    http://$SERVER_HOST/${PROJECT_NAME}_ops/"
echo ""
echo "Next steps:"
echo "  1. Register your project at https://charityengine.net/project/"
echo "  2. Point Charity Engine volunteers to: http://$SERVER_HOST/$PROJECT_NAME/"
echo "  3. Monitor: cd $PROJECT_ROOT && ./bin/status"
