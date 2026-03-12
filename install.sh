#!/usr/bin/env bash
# =============================================================================
# Orbit Provisioning Platform - Install Script
# Supports: Ubuntu 20.04/22.04/24.04, Debian 11/12,
#           RHEL/CentOS/Rocky/AlmaLinux 8/9
#
# IMPORTANT: Copy all project files to /opt/orbit BEFORE running this script.
# Run as: sudo bash install.sh
# =============================================================================

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
header()  { echo -e "\n${BOLD}${BLUE}━━━ $* ━━━${NC}\n"; }
step()    { echo -e "${BOLD}  ➜  $*${NC}"; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "This script must be run as root: sudo bash install.sh"

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo -e "${BOLD}${BLUE}"
cat << 'EOF'
   ____       _     _ _
  / __ \     | |   (_) |
 | |  | |_ __| |__  _| |_
 | |  | | '__| '_ \| | __|
 | |__| | |  | |_) | | |_
  \____/|_|  |_.__/|_|\__|

  Webex Calling Provisioning Platform
  Installer v1.0.0
EOF
echo -e "${NC}"

# ── Constants ─────────────────────────────────────────────────────────────────
ORBIT_HOME="/opt/orbit"
ORBIT_USER="orbit"
ORBIT_GROUP="orbit"
ORBIT_VENV="${ORBIT_HOME}/venv"
ORBIT_LOG="/var/log/orbit"
DB_NAME="orbitdb"
DB_USER="orbituser"
INSTALL_LOG="${ORBIT_LOG}/install.log"

# ── Redirect all output to install log as well ────────────────────────────────
mkdir -p "${ORBIT_LOG}"
exec > >(tee -a "${INSTALL_LOG}") 2>&1
info "Install log: ${INSTALL_LOG}"

# ── Verify project files exist ────────────────────────────────────────────────
header "Verifying Project Files"

[[ ! -f "${ORBIT_HOME}/wsgi.py" ]] && \
    error "Project files not found in ${ORBIT_HOME}. Copy your Orbit project files there first."
[[ ! -f "${ORBIT_HOME}/requirements.txt" ]] && \
    error "requirements.txt not found in ${ORBIT_HOME}."
[[ ! -f "${ORBIT_HOME}/celery_worker.py" ]] && \
    error "celery_worker.py not found in ${ORBIT_HOME}."

success "Project files verified in ${ORBIT_HOME}."

# ── OS Detection ──────────────────────────────────────────────────────────────
header "Detecting Operating System"

detect_os() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS_ID="${ID}"
        OS_VERSION="${VERSION_ID%%.*}"
        OS_LIKE="${ID_LIKE:-}"
        success "Detected: ${PRETTY_NAME}"
    else
        error "Cannot detect OS — /etc/os-release not found."
    fi
}

detect_os

case "${OS_ID}" in
    ubuntu|debian)
        PKG_MANAGER="apt"
        ;;
    rhel|centos|rocky|almalinux|fedora)
        PKG_MANAGER="dnf"
        ;;
    *)
        if [[ "${OS_LIKE}" == *debian* ]]; then
            PKG_MANAGER="apt"
        elif [[ "${OS_LIKE}" == *rhel* ]]; then
            PKG_MANAGER="dnf"
        else
            error "Unsupported OS: ${OS_ID}."
        fi
        ;;
esac

info "Package manager: ${PKG_MANAGER}"

# ── Collect Configuration ─────────────────────────────────────────────────────
header "Configuration"

# ── CLI Admin User ────────────────────────────────────────────────────────────
echo -e "${YELLOW}Step 1/4 — CLI Administrator Account (SSH access only)${NC}"
echo -e "${YELLOW}Root SSH login will be disabled after setup.${NC}\n"

while true; do
    read -rp "  Enter CLI admin username: " CLI_ADMIN_USER
    [[ -z "${CLI_ADMIN_USER}" ]] && { warn "Username cannot be empty."; continue; }
    [[ "${CLI_ADMIN_USER}" =~ ^[a-z_][a-z0-9_-]*$ ]] && break
    warn "Username must be lowercase letters, numbers, hyphens or underscores."
done

while true; do
    read -rsp "  Enter CLI admin password: " CLI_ADMIN_PASS; echo
    read -rsp "  Confirm CLI admin password: " CLI_ADMIN_PASS2; echo
    [[ "${CLI_ADMIN_PASS}" == "${CLI_ADMIN_PASS2}" ]] && break
    warn "Passwords do not match. Try again."
done

echo ""

# ── Orbit Web Admin ───────────────────────────────────────────────────────────
echo -e "${YELLOW}Step 2/4 — Orbit Web Superadmin Account${NC}"
echo -e "${YELLOW}This is the login for the Orbit web interface.${NC}\n"

while true; do
    read -rp "  Orbit admin username [admin]: " ORBIT_ADMIN_USER
    ORBIT_ADMIN_USER="${ORBIT_ADMIN_USER:-admin}"
    [[ "${ORBIT_ADMIN_USER}" =~ ^[a-z_][a-z0-9_-]*$ ]] && break
    warn "Username must be lowercase letters, numbers, hyphens or underscores."
done

while true; do
    read -rp "  Orbit admin email: " ORBIT_ADMIN_EMAIL
    [[ "${ORBIT_ADMIN_EMAIL}" =~ ^[^@]+@[^@]+\.[^@]+$ ]] && break
    warn "Please enter a valid email address."
done

read -rp "  Orbit admin full name [Admin User]: " ORBIT_ADMIN_NAME
ORBIT_ADMIN_NAME="${ORBIT_ADMIN_NAME:-Admin User}"

while true; do
    read -rsp "  Orbit admin password (min 10 chars): " ORBIT_ADMIN_PASS; echo
    [[ ${#ORBIT_ADMIN_PASS} -ge 10 ]] && break
    warn "Password must be at least 10 characters."
done

echo ""

# ── Server & Port ─────────────────────────────────────────────────────────────
echo -e "${YELLOW}Step 3/4 — Server Configuration${NC}\n"

read -rp "  Server FQDN or IP [$(hostname -I | awk '{print $1}')]: " SERVER_FQDN
SERVER_FQDN="${SERVER_FQDN:-$(hostname -I | awk '{print $1}')}"

read -rp "  Application internal port [8000]: " APP_PORT
APP_PORT="${APP_PORT:-8000}"

echo ""

# ── Gunicorn Workers ──────────────────────────────────────────────────────────
echo -e "${YELLOW}Step 4/4 — Worker Configuration${NC}\n"

CPU_COUNT=$(nproc)
RECOMMENDED_WORKERS=$(( CPU_COUNT * 2 + 1 ))
read -rp "  Gunicorn workers [${RECOMMENDED_WORKERS}]: " GUNICORN_WORKERS
GUNICORN_WORKERS="${GUNICORN_WORKERS:-${RECOMMENDED_WORKERS}}"

RECOMMENDED_CELERY=$(( CPU_COUNT > 4 ? 4 : CPU_COUNT ))
read -rp "  Celery worker concurrency [${RECOMMENDED_CELERY}]: " CELERY_CONCURRENCY
CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-${RECOMMENDED_CELERY}}"

echo ""
info "Configuration collected. Starting installation…"

# ── Generate Secrets ──────────────────────────────────────────────────────────
header "Generating Secrets"

DB_PASS=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 40)
SECRET_KEY=$(openssl rand -hex 64)
FERNET_KEY=$(python3 -c \
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
    2>/dev/null || openssl rand -base64 32)
SNOW_WEBHOOK_SECRET=$(openssl rand -hex 32)
REDIS_PASS=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 30)

success "Secrets generated."

# ── Install System Packages ───────────────────────────────────────────────────
header "Installing System Packages"

install_apt() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq \
        python3.11 python3.11-venv python3.11-dev \
        python3-pip \
        nginx \
        postgresql postgresql-contrib \
        redis-server \
        certbot python3-certbot-nginx \
        openssl \
        libpq-dev libssl-dev libffi-dev \
        build-essential gcc \
        git curl wget \
        logrotate \
        acl \
        net-tools
    success "APT packages installed."
}

install_dnf() {
    dnf update -y -q
    dnf install -y -q epel-release 2>/dev/null || true
    dnf install -y -q \
        python3.11 python3.11-devel python3-pip \
        nginx \
        postgresql postgresql-server postgresql-contrib postgresql-devel \
        redis \
        certbot python3-certbot-nginx \
        openssl openssl-devel \
        libffi-devel \
        gcc gcc-c++ make \
        git curl wget \
        logrotate \
        acl \
        net-tools
    success "DNF packages installed."
}

case "${PKG_MANAGER}" in
    apt) install_apt ;;
    dnf) install_dnf ;;
esac

# ── Verify Python ─────────────────────────────────────────────────────────────
PYTHON_BIN=$(command -v python3.11 || command -v python3 \
    || error "Python 3.11+ not found after install.")
PY_VERSION=$(${PYTHON_BIN} -c \
    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python: ${PY_VERSION} (${PYTHON_BIN})"

# ── PostgreSQL Setup ──────────────────────────────────────────────────────────
header "Configuring PostgreSQL"

if [[ "${PKG_MANAGER}" == "dnf" ]]; then
    postgresql-setup --initdb 2>/dev/null || true
fi

systemctl enable postgresql --now
sleep 3

sudo -u postgres psql -v ON_ERROR_STOP=1 << EOSQL
DO \$\$
BEGIN
   IF NOT EXISTS (
       SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}'
   ) THEN
      CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
   END IF;
END
\$\$;

DROP DATABASE IF EXISTS ${DB_NAME};
CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
EOSQL

# Ensure local MD5 auth
PG_HBA=$(sudo -u postgres psql -t -c "SHOW hba_file;" | xargs)
if ! grep -q "${DB_USER}" "${PG_HBA}"; then
    echo "host  ${DB_NAME}  ${DB_USER}  127.0.0.1/32  md5" >> "${PG_HBA}"
    echo "host  ${DB_NAME}  ${DB_USER}  ::1/128       md5" >> "${PG_HBA}"
    systemctl reload postgresql
fi

success "PostgreSQL: database '${DB_NAME}', user '${DB_USER}'."

# ── Redis Setup ───────────────────────────────────────────────────────────────
header "Configuring Redis"

REDIS_CONF="/etc/redis/redis.conf"
[[ ! -f "${REDIS_CONF}" ]] && REDIS_CONF="/etc/redis.conf"

# Set password and bind to localhost
sed -i "s/^# requirepass .*/requirepass ${REDIS_PASS}/" "${REDIS_CONF}"
sed -i "s/^requirepass .*/requirepass ${REDIS_PASS}/" "${REDIS_CONF}"
grep -q "^requirepass" "${REDIS_CONF}" || \
    echo "requirepass ${REDIS_PASS}" >> "${REDIS_CONF}"
sed -i "s/^bind .*/bind 127.0.0.1 ::1/" "${REDIS_CONF}"

# Set max memory
grep -q "^maxmemory " "${REDIS_CONF}" || \
    echo "maxmemory 256mb" >> "${REDIS_CONF}"
grep -q "^maxmemory-policy" "${REDIS_CONF}" || \
    echo "maxmemory-policy allkeys-lru" >> "${REDIS_CONF}"

systemctl enable redis-server --now 2>/dev/null || \
    systemctl enable redis --now 2>/dev/null
systemctl restart redis-server 2>/dev/null || \
    systemctl restart redis 2>/dev/null

success "Redis configured with password auth, bound to 127.0.0.1."

# ── System Users ──────────────────────────────────────────────────────────────
header "Creating System Users"

# orbit service user (no shell)
if ! id "${ORBIT_USER}" &>/dev/null; then
    useradd --system \
            --no-create-home \
            --home-dir "${ORBIT_HOME}" \
            --shell /usr/sbin/nologin \
            "${ORBIT_USER}"
    success "Created system user: ${ORBIT_USER}"
else
    warn "System user ${ORBIT_USER} already exists — skipping."
fi

# CLI admin user
if ! id "${CLI_ADMIN_USER}" &>/dev/null; then
    useradd --create-home \
            --shell /bin/bash \
            --comment "Orbit CLI Administrator" \
            "${CLI_ADMIN_USER}"
    success "Created CLI admin user: ${CLI_ADMIN_USER}"
else
    warn "User ${CLI_ADMIN_USER} already exists — updating password."
fi
echo "${CLI_ADMIN_USER}:${CLI_ADMIN_PASS}" | chpasswd

# Restricted sudo for CLI admin — service management only
cat > "/etc/sudoers.d/orbit-cli-admin" << SUDO_EOF
# Orbit CLI Admin — systemd service control and log access only
${CLI_ADMIN_USER} ALL=(ALL) NOPASSWD: \
    /bin/systemctl start orbit-*, \
    /bin/systemctl stop orbit-*, \
    /bin/systemctl restart orbit-*, \
    /bin/systemctl status orbit-*, \
    /bin/systemctl reload orbit-*
${CLI_ADMIN_USER} ALL=(ALL) NOPASSWD: \
    /usr/bin/journalctl -u orbit-*
${CLI_ADMIN_USER} ALL=(ALL) NOPASSWD: \
    /usr/bin/tail -f /var/log/orbit/*
SUDO_EOF
chmod 0440 "/etc/sudoers.d/orbit-cli-admin"
success "Restricted sudo rules written for ${CLI_ADMIN_USER}."

# Disable root SSH login
SSHD_CONF="/etc/ssh/sshd_config"
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' "${SSHD_CONF}"
grep -q "^PermitRootLogin" "${SSHD_CONF}" || \
    echo "PermitRootLogin no" >> "${SSHD_CONF}"
systemctl reload sshd 2>/dev/null || \
    systemctl reload ssh 2>/dev/null || true
success "Root SSH login disabled."

# ── Directory Structure ───────────────────────────────────────────────────────
header "Creating Directory Structure"

mkdir -p \
    "${ORBIT_HOME}/app/static/uploads/"{logos,audio,certs} \
    "${ORBIT_HOME}/migrations/versions" \
    "${ORBIT_HOME}/logs" \
    "${ORBIT_HOME}/certs" \
    "${ORBIT_LOG}"

# Ensure upload directories are writable by orbit user
chown -R "${ORBIT_USER}:${ORBIT_GROUP}" "${ORBIT_HOME}"
chown -R "${ORBIT_USER}:${ORBIT_GROUP}" "${ORBIT_LOG}"
chmod 750 "${ORBIT_HOME}"
chmod 700 "${ORBIT_HOME}/certs"
chmod 755 "${ORBIT_HOME}/app/static"
chmod 775 "${ORBIT_HOME}/app/static/uploads"

# Allow CLI admin read access to app directory
setfacl -m u:"${CLI_ADMIN_USER}":rx "${ORBIT_HOME}" 2>/dev/null || true
setfacl -m u:"${CLI_ADMIN_USER}":rx "${ORBIT_LOG}"  2>/dev/null || true

success "Directory structure ready."

# ── Python Virtual Environment ────────────────────────────────────────────────
header "Setting Up Python Virtual Environment"

${PYTHON_BIN} -m venv "${ORBIT_VENV}"
"${ORBIT_VENV}/bin/pip" install --upgrade pip setuptools wheel -q

success "Virtual environment created: ${ORBIT_VENV}"

# ── Install Python Packages ───────────────────────────────────────────────────
header "Installing Python Packages"
info "This may take a few minutes…"

"${ORBIT_VENV}/bin/pip" install \
    -r "${ORBIT_HOME}/requirements.txt" \
    --no-cache-dir -q \
    || error "Failed to install Python packages. Check ${INSTALL_LOG}."

# Ensure gevent is installed (Gunicorn worker class)
"${ORBIT_VENV}/bin/pip" install gevent -q

success "All Python packages installed."

# ── Self-Signed TLS Certificate ───────────────────────────────────────────────
header "Generating Self-Signed TLS Certificate"

SERVER_IP=$(hostname -I | awk '{print $1}')

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "${ORBIT_HOME}/certs/orbit.key" \
    -out    "${ORBIT_HOME}/certs/orbit.crt" \
    -subj   "/C=BE/ST=Flanders/L=Server/O=Orbit/OU=IT/CN=${SERVER_FQDN}" \
    -addext "subjectAltName=DNS:${SERVER_FQDN},IP:${SERVER_IP}" \
    2>/dev/null

chmod 600 "${ORBIT_HOME}/certs/orbit.key"
chmod 644 "${ORBIT_HOME}/certs/orbit.crt"
chown -R "${ORBIT_USER}:${ORBIT_GROUP}" "${ORBIT_HOME}/certs"

success "Self-signed certificate generated for ${SERVER_FQDN}."

# ── Write .env File ───────────────────────────────────────────────────────────
header "Writing Environment Configuration"

cat > "${ORBIT_HOME}/.env" << ENV_EOF
# ════════════════════════════════════════════════════════════════
# Orbit Environment Configuration
# AUTO-GENERATED by install.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# File permissions: 600 (orbit:orbit)
# ════════════════════════════════════════════════════════════════

# ── Flask ──────────────────────────────────────────────────────
FLASK_ENV=production
FLASK_APP=wsgi:app
SECRET_KEY=${SECRET_KEY}

# ── Encryption (Fernet) ────────────────────────────────────────
FERNET_KEY=${FERNET_KEY}

# ── Database ───────────────────────────────────────────────────
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASS=${DB_PASS}

# ── Redis ──────────────────────────────────────────────────────
REDIS_PASSWORD=${REDIS_PASS}
REDIS_URL=redis://:${REDIS_PASS}@127.0.0.1:6379/2
CELERY_BROKER_URL=redis://:${REDIS_PASS}@127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://:${REDIS_PASS}@127.0.0.1:6379/1

# ── Gunicorn ───────────────────────────────────────────────────
GUNICORN_WORKERS=${GUNICORN_WORKERS}
GUNICORN_THREADS=2
GUNICORN_BIND=127.0.0.1:${APP_PORT}
GUNICORN_TIMEOUT=120

# ── Celery ─────────────────────────────────────────────────────
CELERY_CONCURRENCY=${CELERY_CONCURRENCY}

# ── Server ─────────────────────────────────────────────────────
SERVER_IP=${SERVER_IP}
SERVER_FQDN=${SERVER_FQDN}
APP_PORT=${APP_PORT}

# ── TLS Certs ──────────────────────────────────────────────────
CERT_PATH=${ORBIT_HOME}/certs/orbit.crt
KEY_PATH=${ORBIT_HOME}/certs/orbit.key

# ── Logging ────────────────────────────────────────────────────
LOG_LEVEL=INFO
LOG_TO_STDOUT=true
LOG_FILE=${ORBIT_LOG}/orbit.log

# ── ServiceNow Webhook ─────────────────────────────────────────
# Auto-generated — paste this into your SNOW catalog webhook header
SNOW_WEBHOOK_SECRET=${SNOW_WEBHOOK_SECRET}

# ── The following are configured via Settings UI after first login ──
WEBEX_ACCESS_TOKEN=
WEBEX_ORG_ID=
SNOW_INSTANCE=
SNOW_USERNAME=
SNOW_PASSWORD=
SMTP_HOST=
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_SENDER_EMAIL=orbit@${SERVER_FQDN}
SMTP_SENDER_NAME=Orbit
ENV_EOF

chmod 600 "${ORBIT_HOME}/.env"
chown "${ORBIT_USER}:${ORBIT_GROUP}" "${ORBIT_HOME}/.env"
success ".env written with secure permissions (600)."

# ── Database Migration & Seed ─────────────────────────────────────────────────
header "Initialising Database"

# Export env vars for flask CLI calls
set -a
# shellcheck source=/dev/null
source "${ORBIT_HOME}/.env"
set +a

export FLASK_APP=wsgi:app
export FLASK_ENV=production
export PYTHONPATH="${ORBIT_HOME}"

cd "${ORBIT_HOME}"

step "Running database migrations…"
sudo -u "${ORBIT_USER}" \
    env \
        FLASK_APP=wsgi:app \
        FLASK_ENV=production \
        DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}" \
        SECRET_KEY="${SECRET_KEY}" \
        FERNET_KEY="${FERNET_KEY}" \
        REDIS_URL="redis://:${REDIS_PASS}@127.0.0.1:6379/2" \
        CELERY_BROKER_URL="redis://:${REDIS_PASS}@127.0.0.1:6379/0" \
        CELERY_RESULT_BACKEND="redis://:${REDIS_PASS}@127.0.0.1:6379/1" \
    "${ORBIT_VENV}/bin/flask" db upgrade \
    || error "Database migration failed. Check ${INSTALL_LOG}."
success "Database migrations applied."

step "Seeding default application config…"
sudo -u "${ORBIT_USER}" \
    env \
        FLASK_APP=wsgi:app \
        FLASK_ENV=production \
        DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}" \
        SECRET_KEY="${SECRET_KEY}" \
        FERNET_KEY="${FERNET_KEY}" \
        REDIS_URL="redis://:${REDIS_PASS}@127.0.0.1:6379/2" \
        CELERY_BROKER_URL="redis://:${REDIS_PASS}@127.0.0.1:6379/0" \
        CELERY_RESULT_BACKEND="redis://:${REDIS_PASS}@127.0.0.1:6379/1" \
    "${ORBIT_VENV}/bin/flask" admin seed-config \
    || warn "seed-config returned non-zero — may already be seeded."
success "Default config seeded."

step "Creating Orbit superadmin account…"
sudo -u "${ORBIT_USER}" \
    env \
        FLASK_APP=wsgi:app \
        FLASK_ENV=production \
        DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}" \
        SECRET_KEY="${SECRET_KEY}" \
        FERNET_KEY="${FERNET_KEY}" \
        REDIS_URL="redis://:${REDIS_PASS}@127.0.0.1:6379/2" \
        CELERY_BROKER_URL="redis://:${REDIS_PASS}@127.0.0.1:6379/0" \
        CELERY_RESULT_BACKEND="redis://:${REDIS_PASS}@127.0.0.1:6379/1" \
        ORBIT_ADMIN_USER="${ORBIT_ADMIN_USER}" \
        ORBIT_ADMIN_EMAIL="${ORBIT_ADMIN_EMAIL}" \
        ORBIT_ADMIN_NAME="${ORBIT_ADMIN_NAME}" \
        ORBIT_ADMIN_PASS="${ORBIT_ADMIN_PASS}" \
    "${ORBIT_VENV}/bin/flask" admin create-admin \
        --username  "${ORBIT_ADMIN_USER}" \
        --email     "${ORBIT_ADMIN_EMAIL}" \
        --full-name "${ORBIT_ADMIN_NAME}" \
        --password  "${ORBIT_ADMIN_PASS}" \
        --role      superadmin \
    || warn "create-admin returned non-zero — account may already exist."
success "Superadmin account created: ${ORBIT_ADMIN_USER}"

# ── Nginx Configuration ───────────────────────────────────────────────────────
header "Configuring Nginx"

# ── HTTP config (used during setup / before Let's Encrypt) ────────────────────
cat > /etc/nginx/sites-available/orbit << NGINX_EOF
# Orbit — Nginx Configuration
# HTTP block: active immediately
# HTTPS block: activate after running certbot

upstream orbit_app {
    server 127.0.0.1:${APP_PORT};
    keepalive 32;
}

# ── HTTP ────────────────────────────────────────────────────────
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_FQDN} ${SERVER_IP};

    client_max_body_size 20M;

    # Security headers
    add_header X-Frame-Options        "SAMEORIGIN"                     always;
    add_header X-Content-Type-Options "nosniff"                        always;
    add_header X-XSS-Protection       "1; mode=block"                  always;
    add_header Referrer-Policy        "strict-origin-when-cross-origin" always;

    # Static files served by Nginx directly
    location /static/ {
        alias ${ORBIT_HOME}/app/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    # Favicon
    location = /favicon.ico {
        alias ${ORBIT_HOME}/app/static/img/favicon.ico;
        access_log    off;
        log_not_found off;
    }

    # Health check — no auth, no logging
    location = /health {
        proxy_pass http://orbit_app;
        access_log off;
    }

    # Webhook endpoint — allow larger SNOW payloads
    location /api/webhook/ {
        proxy_pass         http://orbit_app;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        client_max_body_size 4M;
    }

    # All other traffic → Gunicorn
    location / {
        proxy_pass         http://orbit_app;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_set_header   Connection        "";
        proxy_read_timeout    120s;
        proxy_connect_timeout 10s;
        proxy_send_timeout    120s;
        proxy_redirect off;
    }

    # Block hidden files
    location ~ /\. {
        deny all;
        access_log    off;
        log_not_found off;
    }

    access_log /var/log/nginx/orbit_access.log;
    error_log  /var/log/nginx/orbit_error.log warn;
}

# ── HTTPS ───────────────────────────────────────────────────────
# Uncomment this block after running:
#   sudo certbot --nginx -d ${SERVER_FQDN}
#
# server {
#     listen 443 ssl http2;
#     listen [::]:443 ssl http2;
#     server_name ${SERVER_FQDN};
#
#     ssl_certificate     /etc/letsencrypt/live/${SERVER_FQDN}/fullchain.pem;
#     ssl_certificate_key /etc/letsencrypt/live/${SERVER_FQDN}/privkey.pem;
#     ssl_protocols       TLSv1.2 TLSv1.3;
#     ssl_ciphers         HIGH:!aNULL:!MD5;
#     ssl_prefer_server_ciphers on;
#     ssl_session_cache   shared:SSL:10m;
#     ssl_session_timeout 10m;
#
#     add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
#     add_header X-Frame-Options        "SAMEORIGIN"                             always;
#     add_header X-Content-Type-Options "nosniff"                                always;
#     add_header Referrer-Policy        "strict-origin-when-cross-origin"        always;
#
#     location /static/ {
#         alias ${ORBIT_HOME}/app/static/;
#         expires 30d;
#         add_header Cache-Control "public, immutable";
#         access_log off;
#     }
#
#     location = /health {
#         proxy_pass http://orbit_app;
#         access_log off;
#     }
#
#     location /api/webhook/ {
#         proxy_pass http://orbit_app;
#         proxy_set_header Host              \$host;
#         proxy_set_header X-Real-IP         \$remote_addr;
#         proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
#         proxy_set_header X-Forwarded-Proto \$scheme;
#         client_max_body_size 4M;
#     }
#
#     location / {
#         proxy_pass         http://orbit_app;
#         proxy_http_version 1.1;
#         proxy_set_header   Host              \$host;
#         proxy_set_header   X-Real-IP         \$remote_addr;
#         proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
#         proxy_set_header   X-Forwarded-Proto \$scheme;
#         proxy_set_header   Connection        "";
#         proxy_read_timeout    120s;
#         proxy_connect_timeout 10s;
#         proxy_redirect off;
#         client_max_body_size 20M;
#     }
#
#     location ~ /\. { deny all; }
#
#     access_log /var/log/nginx/orbit_access.log;
#     error_log  /var/log/nginx/orbit_error.log warn;
# }
NGINX_EOF

# Enable site, remove default
ln -sf /etc/nginx/sites-available/orbit \
       /etc/nginx/sites-enabled/orbit 2>/dev/null || \
cp     /etc/nginx/sites-available/orbit \
       /etc/nginx/conf.d/orbit.conf 2>/dev/null || true

rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

nginx -t || error "Nginx config test failed."
systemctl enable nginx
systemctl restart nginx
success "Nginx configured and running."

# ── Systemd Services ──────────────────────────────────────────────────────────
header "Installing Systemd Services"

# ── orbit-web (Gunicorn) ──────────────────────────────────────────────────────
cat > /etc/systemd/system/orbit-web.service << SVC_EOF
[Unit]
Description=Orbit — Gunicorn Web Server
Documentation=https://github.com/your-org/orbit
After=network.target postgresql.service redis.service
Requires=postgresql.service

[Service]
Type=notify
User=${ORBIT_USER}
Group=${ORBIT_GROUP}
WorkingDirectory=${ORBIT_HOME}
EnvironmentFile=${ORBIT_HOME}/.env

ExecStart=${ORBIT_VENV}/bin/gunicorn wsgi:app \\
    --bind          127.0.0.1:${APP_PORT} \\
    --workers       ${GUNICORN_WORKERS} \\
    --worker-class  gevent \\
    --threads       2 \\
    --worker-connections 1000 \\
    --timeout       120 \\
    --keepalive     5 \\
    --max-requests  1000 \\
    --max-requests-jitter 100 \\
    --log-level     info \\
    --access-logfile  ${ORBIT_LOG}/access.log \\
    --error-logfile   ${ORBIT_LOG}/error.log \\
    --capture-output \\
    --forwarded-allow-ips="127.0.0.1"

ExecReload=/bin/kill -s HUP \$MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=${ORBIT_HOME}/logs ${ORBIT_HOME}/app/static/uploads ${ORBIT_LOG}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SVC_EOF

# ── orbit-worker (Celery worker) ──────────────────────────────────────────────
cat > /etc/systemd/system/orbit-worker.service << SVC_EOF
[Unit]
Description=Orbit — Celery Worker
Documentation=https://github.com/your-org/orbit
After=network.target redis.service postgresql.service orbit-web.service
Requires=redis.service postgresql.service

[Service]
Type=forking
User=${ORBIT_USER}
Group=${ORBIT_GROUP}
WorkingDirectory=${ORBIT_HOME}
EnvironmentFile=${ORBIT_HOME}/.env
RuntimeDirectory=orbit

ExecStart=${ORBIT_VENV}/bin/celery -A celery_worker.celery worker \\
    --loglevel=info \\
    --queues=default,snow,webex_sync,call_forward,maintenance,notifications \\
    --concurrency=${CELERY_CONCURRENCY} \\
    --max-tasks-per-child=500 \\
    --logfile=${ORBIT_LOG}/celery-worker.log \\
    --pidfile=/run/orbit/celery-worker.pid \\
    --detach

ExecStop=${ORBIT_VENV}/bin/celery -A celery_worker.celery control shutdown

KillMode=mixed
TimeoutStopSec=15
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SVC_EOF

# ── orbit-beat (Celery beat scheduler) ───────────────────────────────────────
cat > /etc/systemd/system/orbit-beat.service << SVC_EOF
[Unit]
Description=Orbit — Celery Beat Scheduler
Documentation=https://github.com/your-org/orbit
After=network.target redis.service orbit-worker.service
Requires=redis.service

[Service]
Type=forking
User=${ORBIT_USER}
Group=${ORBIT_GROUP}
WorkingDirectory=${ORBIT_HOME}
EnvironmentFile=${ORBIT_HOME}/.env
RuntimeDirectory=orbit

ExecStart=${ORBIT_VENV}/bin/celery -A celery_worker.celery beat \\
    --loglevel=info \\
    --scheduler celery.beat:PersistentScheduler \\
    --schedule=${ORBIT_HOME}/celerybeat-schedule \\
    --logfile=${ORBIT_LOG}/celery-beat.log \\
    --pidfile=/run/orbit/celery-beat.pid \\
    --detach

KillMode=mixed
TimeoutStopSec=10
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SVC_EOF

systemctl daemon-reload
systemctl enable orbit-web orbit-worker orbit-beat
success "Systemd services installed and enabled."

# ── Log Rotation ──────────────────────────────────────────────────────────────
header "Configuring Log Rotation"

cat > /etc/logrotate.d/orbit << LOGROTATE_EOF
${ORBIT_LOG}/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 640 ${ORBIT_USER} ${ORBIT_GROUP}
    sharedscripts
    postrotate
        systemctl reload orbit-web > /dev/null 2>&1 || true
    endscript
}
LOGROTATE_EOF

success "Log rotation configured (30-day retention)."

# ── Firewall ──────────────────────────────────────────────────────────────────
header "Configuring Firewall"

if command -v ufw &>/dev/null; then
    ufw allow 22/tcp   comment "SSH"
    ufw allow 80/tcp   comment "Orbit HTTP"
    ufw allow 443/tcp  comment "Orbit HTTPS"
    ufw --force enable
    success "UFW configured — ports 22, 80, 443 open."
elif command -v firewall-cmd &>/dev/null; then
    firewall-cmd --permanent --add-service=ssh
    firewall-cmd --permanent --add-service=http
    firewall-cmd --permanent --add-service=https
    firewall-cmd --reload
    success "firewalld configured — ports 22, 80, 443 open."
else
    warn "No firewall detected — ensure ports 22, 80, 443 are open manually."
fi

# ── Start Services ────────────────────────────────────────────────────────────
header "Starting Orbit Services"

step "Starting orbit-web…"
systemctl start orbit-web   && success "orbit-web started." \
                             || warn   "orbit-web failed to start — check: journalctl -u orbit-web"

sleep 3

step "Starting orbit-worker…"
systemctl start orbit-worker && success "orbit-worker started." \
                              || warn   "orbit-worker failed to start — check: journalctl -u orbit-worker"

step "Starting orbit-beat…"
systemctl start orbit-beat   && success "orbit-beat started." \
                              || warn   "orbit-beat failed to start — check: journalctl -u orbit-beat"

# ── Health Check ──────────────────────────────────────────────────────────────
header "Verifying Installation"

sleep 5
step "Checking /health endpoint…"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://127.0.0.1:${APP_PORT}/health" 2>/dev/null || echo "000")

if [[ "${HTTP_CODE}" == "200" ]]; then
    success "Health check passed (HTTP ${HTTP_CODE})."
else
    warn "Health check returned HTTP ${HTTP_CODE}. Check logs:"
    warn "  journalctl -u orbit-web -n 50"
fi

# ── Write Credentials Summary ─────────────────────────────────────────────────
CREDS_FILE="${ORBIT_HOME}/INSTALL_CREDENTIALS.txt"

cat > "${CREDS_FILE}" << CREDS_EOF
# ════════════════════════════════════════════════════════════════
# Orbit — Installation Credentials
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
#
# ⚠  KEEP THIS FILE SECURE — DELETE AFTER NOTING CREDENTIALS
# ════════════════════════════════════════════════════════════════

Web Interface URL:     http://${SERVER_FQDN}/admin/login
                       http://${SERVER_IP}/admin/login

Orbit Admin Username:  ${ORBIT_ADMIN_USER}
Orbit Admin Password:  ${ORBIT_ADMIN_PASS}
Orbit Admin Email:     ${ORBIT_ADMIN_EMAIL}

CLI Admin Username:    ${CLI_ADMIN_USER}
CLI Admin Password:    ${CLI_ADMIN_PASS}

PostgreSQL DB:         ${DB_NAME}
PostgreSQL User:       ${DB_USER}
PostgreSQL Password:   ${DB_PASS}

Redis Password:        ${REDIS_PASS}

SNOW Webhook Secret:   ${SNOW_WEBHOOK_SECRET}
(Use as X-API-Key header in your ServiceNow catalog webhook)

Secret Key:            ${SECRET_KEY}
Fernet Key:            ${FERNET_KEY}

Install Log:           ${INSTALL_LOG}
App Config:            ${ORBIT_HOME}/.env
CREDS_EOF

chmod 600 "${CREDS_FILE}"
chown root:root "${CREDS_FILE}"
success "Credentials saved to ${CREDS_FILE} (root-only, chmod 600)."

# ── Post-Install Summary ──────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${GREEN}  ✅  Orbit Installation Complete!${NC}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Web Interface:${NC}"
echo -e "  ${CYAN}http://${SERVER_IP}/admin/login${NC}"
echo -e "  ${CYAN}http://${SERVER_FQDN}/admin/login${NC}"
echo ""
echo -e "  ${BOLD}Orbit Login:${NC}"
echo -e "  Username: ${YELLOW}${ORBIT_ADMIN_USER}${NC}"
echo -e "  Password: ${YELLOW}${ORBIT_ADMIN_PASS}${NC}"
echo ""
echo -e "  ${BOLD}CLI Admin:${NC}        ${CLI_ADMIN_USER}"
echo -e "  ${BOLD}SNOW Webhook:${NC}     ${SNOW_WEBHOOK_SECRET}"
echo -e "  ${BOLD}Credentials file:${NC} ${CREDS_FILE}"
echo -e "  ${BOLD}Install log:${NC}      ${INSTALL_LOG}"
echo ""
echo -e "  ${BOLD}${YELLOW}Next Steps:${NC}"
echo -e "  ${YELLOW}1. Log in to the web interface and complete Settings${NC}"
echo -e "  ${YELLOW}2. Settings → Webex — paste your Webex access token${NC}"
echo -e "  ${YELLOW}3. Settings → ServiceNow — configure instance + webhook${NC}"
echo -e "  ${YELLOW}4. Settings → Email — configure SMTP${NC}"
echo -e "  ${YELLOW}5. Task Monitor — verify workers and beat are online${NC}"
echo -e "  ${YELLOW}6. Enable HTTPS: sudo certbot --nginx -d ${SERVER_FQDN}${NC}"
echo -e "  ${YELLOW}7. Delete credentials file after noting them:${NC}"
echo -e "  ${YELLOW}   sudo rm ${CREDS_FILE}${NC}"
echo ""
echo -e "  ${BOLD}Service Commands:${NC}"
echo -e "  sudo systemctl status  orbit-web orbit-worker orbit-beat"
echo -e "  sudo systemctl restart orbit-web"
echo -e "  sudo journalctl -u orbit-web -f"
echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
