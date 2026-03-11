#!/usr/bin/env bash
# =============================================================================
# Orbit Provisioning Platform - Install Script
# Supports: Ubuntu 20.04/22.04/24.04, Debian 11/12,
#           RHEL/CentOS/Rocky/AlmaLinux 8/9
# Run as: sudo bash install.sh
# =============================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
header()  { echo -e "\n${BOLD}${BLUE}━━━ $* ━━━${NC}\n"; }

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
PYTHON_MIN_VERSION="3.11"
DB_NAME="orbitdb"
DB_USER="orbituser"

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
        error "Cannot detect OS. /etc/os-release not found."
    fi
}

detect_os

case "${OS_ID}" in
    ubuntu|debian)        PKG_MANAGER="apt" ;;
    rhel|centos|rocky|almalinux|fedora) PKG_MANAGER="dnf" ;;
    *)
        # Fallback via ID_LIKE
        if echo "${OS_LIKE}" in *debian*; then PKG_MANAGER="apt"
        elif echo "${OS_LIKE}" in *rhel*; then PKG_MANAGER="dnf"
        else error "Unsupported OS: ${OS_ID}. Supported: Ubuntu, Debian, RHEL, CentOS, Rocky, AlmaLinux"
        fi ;;
esac

info "Package manager: ${PKG_MANAGER}"

# ── CLI Admin User ────────────────────────────────────────────────────────────
header "CLI Administrator Account Setup"

echo -e "${YELLOW}This account is for SSH/CLI access only.${NC}"
echo -e "${YELLOW}Root login will be disabled. This user cannot modify the OS.${NC}\n"

while true; do
    read -rp "Enter CLI admin username: " CLI_ADMIN_USER
    [[ -z "${CLI_ADMIN_USER}" ]] && { warn "Username cannot be empty."; continue; }
    [[ "${CLI_ADMIN_USER}" =~ ^[a-z_][a-z0-9_-]*$ ]] && break
    warn "Username must be lowercase letters, numbers, hyphens or underscores."
done

while true; do
    read -rsp "Enter CLI admin password: " CLI_ADMIN_PASS; echo
    read -rsp "Confirm CLI admin password: " CLI_ADMIN_PASS2; echo
    [[ "${CLI_ADMIN_PASS}" == "${CLI_ADMIN_PASS2}" ]] && break
    warn "Passwords do not match. Try again."
done

# ── Collect basic info ────────────────────────────────────────────────────────
header "Basic Configuration"

read -rp "Server FQDN or IP (for Nginx, e.g. orbit.company.com): " SERVER_FQDN
SERVER_FQDN="${SERVER_FQDN:-$(hostname -I | awk '{print $1}')}"

read -rp "Application port [8080]: " APP_PORT
APP_PORT="${APP_PORT:-8080}"

# ── Generate Secrets ──────────────────────────────────────────────────────────
DB_PASS=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 40)
SECRET_KEY=$(openssl rand -base64 48)
FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null \
             || openssl rand -base64 32)

info "Generated secure DB password and application secret keys."

# ── Install Packages ──────────────────────────────────────────────────────────
header "Installing System Packages"

install_apt() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq \
        python3.11 python3.11-venv python3.11-dev python3-pip \
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
    # Enable EPEL for extra packages
    dnf install -y -q epel-release 2>/dev/null || true
    dnf install -y -q \
        python3.11 python3.11-devel python3-pip \
        nginx \
        postgresql postgresql-server postgresql-contrib postgresql-devel \
        redis \
        certbot python3-certbot-nginx \
        openssl \
        openssl-devel libffi-devel \
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

# ── Python Version Check ──────────────────────────────────────────────────────
PYTHON_BIN=$(command -v python3.11 || command -v python3 || error "Python 3.11+ not found after install.")
PY_VERSION=$(${PYTHON_BIN} -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python version: ${PY_VERSION} (${PYTHON_BIN})"

# ── PostgreSQL Setup ──────────────────────────────────────────────────────────
header "Configuring PostgreSQL"

if [[ "${PKG_MANAGER}" == "dnf" ]]; then
    postgresql-setup --initdb 2>/dev/null || true
fi

systemctl enable postgresql --now
sleep 2

# Create DB user and database
sudo -u postgres psql -v ON_ERROR_STOP=1 << EOSQL
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
      CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
   END IF;
END
\$\$;
CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
EOSQL

# Ensure MD5 auth for local connections
PG_HBA=$(sudo -u postgres psql -t -c "SHOW hba_file;" | xargs)
if ! grep -q "${DB_USER}" "${PG_HBA}"; then
    echo "host    ${DB_NAME}    ${DB_USER}    127.0.0.1/32    md5" >> "${PG_HBA}"
    echo "host    ${DB_NAME}    ${DB_USER}    ::1/128         md5" >> "${PG_HBA}"
    systemctl reload postgresql
fi

success "PostgreSQL configured: database '${DB_NAME}', user '${DB_USER}'."

# ── Redis Setup ───────────────────────────────────────────────────────────────
header "Configuring Redis"

REDIS_PASS=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 30)
REDIS_CONF="/etc/redis/redis.conf"
[[ ! -f "${REDIS_CONF}" ]] && REDIS_CONF="/etc/redis.conf"

# Set password and bind to localhost only
sed -i "s/^# requirepass .*/requirepass ${REDIS_PASS}/" "${REDIS_CONF}"
sed -i "s/^requirepass .*/requirepass ${REDIS_PASS}/" "${REDIS_CONF}"
grep -q "^requirepass" "${REDIS_CONF}" || echo "requirepass ${REDIS_PASS}" >> "${REDIS_CONF}"

sed -i "s/^bind .*/bind 127.0.0.1 ::1/" "${REDIS_CONF}"

systemctl enable redis --now 2>/dev/null || systemctl enable redis-server --now 2>/dev/null
systemctl restart redis 2>/dev/null || systemctl restart redis-server 2>/dev/null
success "Redis configured with password auth, bound to localhost."

# ── System Users ──────────────────────────────────────────────────────────────
header "Creating System Users"

# orbit service user (no login shell)
if ! id "${ORBIT_USER}" &>/dev/null; then
    useradd --system --no-create-home \
            --home-dir "${ORBIT_HOME}" \
            --shell /usr/sbin/nologin \
            "${ORBIT_USER}"
    success "Created system user: ${ORBIT_USER}"
else
    warn "System user ${ORBIT_USER} already exists, skipping."
fi

# CLI Admin user (real login, restricted sudo)
if ! id "${CLI_ADMIN_USER}" &>/dev/null; then
    useradd --create-home \
            --shell /bin/bash \
            --comment "Orbit CLI Administrator" \
            "${CLI_ADMIN_USER}"
    echo "${CLI_ADMIN_USER}:${CLI_ADMIN_PASS}" | chpasswd
    success "Created CLI admin user: ${CLI_ADMIN_USER}"
else
    warn "User ${CLI_ADMIN_USER} already exists. Updating password."
    echo "${CLI_ADMIN_USER}:${CLI_ADMIN_PASS}" | chpasswd
fi

# Grant CLI admin limited sudo (service management + logs only, no OS changes)
cat > "/etc/sudoers.d/orbit-cli-admin" << SUDO_EOF
# Orbit CLI Admin - service management only
${CLI_ADMIN_USER} ALL=(ALL) NOPASSWD: /bin/systemctl start orbit-*, /bin/systemctl stop orbit-*, /bin/systemctl restart orbit-*, /bin/systemctl status orbit-*
${CLI_ADMIN_USER} ALL=(ALL) NOPASSWD: /bin/journalctl -u orbit-*
${CLI_ADMIN_USER} ALL=(ALL) NOPASSWD: /bin/tail -f /var/log/orbit/*
SUDO_EOF
chmod 0440 "/etc/sudoers.d/orbit-cli-admin"
success "Restricted sudo configured for ${CLI_ADMIN_USER}."

# Disable root SSH login
SSHD_CONF="/etc/ssh/sshd_config"
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' "${SSHD_CONF}"
grep -q "^PermitRootLogin" "${SSHD_CONF}" || echo "PermitRootLogin no" >> "${SSHD_CONF}"
systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true
success "Root SSH login disabled."

# ── Directory Structure ───────────────────────────────────────────────────────
header "Creating Directory Structure"

mkdir -p "${ORBIT_HOME}"/{app/{models,routes,services,templates/{setup,admin,portal,email},static/{uploads/{logos,audio,certs},css,js,img,fonts}},migrations,logs,certs,systemd,scripts}
mkdir -p "${ORBIT_LOG}"

chown -R "${ORBIT_USER}:${ORBIT_GROUP}" "${ORBIT_HOME}"
chown -R "${ORBIT_USER}:${ORBIT_GROUP}" "${ORBIT_LOG}"
chmod 750 "${ORBIT_HOME}"
chmod 700 "${ORBIT_HOME}/certs"
chmod 755 "${ORBIT_HOME}/app/static"

setfacl -m u:"${CLI_ADMIN_USER}":rx "${ORBIT_HOME}" 2>/dev/null || true
success "Directory structure created at ${ORBIT_HOME}"

# ── Python Virtual Environment ────────────────────────────────────────────────
header "Setting Up Python Virtual Environment"

${PYTHON_BIN} -m venv "${ORBIT_VENV}"
"${ORBIT_VENV}/bin/pip" install --upgrade pip setuptools wheel -q
success "Virtual environment created at ${ORBIT_VENV}"

# ── Install Python Dependencies ───────────────────────────────────────────────
header "Installing Python Packages (this may take a few minutes)"

"${ORBIT_VENV}/bin/pip" install -r "${ORBIT_HOME}/requirements.txt" \
    --no-cache-dir -q \
    || error "Failed to install Python packages. Check ${ORBIT_HOME}/logs/install.log"

success "All Python packages installed."

# ── Self-Signed TLS Certificate ───────────────────────────────────────────────
header "Generating Self-Signed TLS Certificate"

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "${ORBIT_HOME}/certs/orbit-selfsigned.key" \
    -out    "${ORBIT_HOME}/certs/orbit-selfsigned.crt" \
    -subj   "/C=BE/ST=Flanders/L=Server/O=Orbit/OU=IT/CN=${SERVER_FQDN}" \
    -addext "subjectAltName=DNS:${SERVER_FQDN},IP:$(hostname -I | awk '{print $1}')" \
    2>/dev/null

chmod 600 "${ORBIT_HOME}/certs/orbit-selfsigned.key"
chmod 644 "${ORBIT_HOME}/certs/orbit-selfsigned.crt"
chown -R "${ORBIT_USER}:${ORBIT_GROUP}" "${ORBIT_HOME}/certs"
success "Self-signed certificate generated for ${SERVER_FQDN}."

# ── Write .env File ───────────────────────────────────────────────────────────
header "Writing Environment Configuration"

SERVER_IP=$(hostname -I | awk '{print $1}')

cat > "${ORBIT_HOME}/.env" << ENV_EOF
# ── Orbit Environment Configuration ──────────────────────────────────────────
# AUTO-GENERATED by install.sh — DO NOT EDIT MANUALLY UNLESS YOU KNOW WHAT YOU ARE DOING
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Flask Core
FLASK_ENV=production
FLASK_APP=wsgi:app
SECRET_KEY=${SECRET_KEY}

# Encryption
FERNET_KEY=${FERNET_KEY}

# Database
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASS=${DB_PASS}

# Redis / Celery
REDIS_PASSWORD=${REDIS_PASS}
REDIS_URL=redis://:${REDIS_PASS}@127.0.0.1:6379/0
CELERY_BROKER_URL=redis://:${REDIS_PASS}@127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://:${REDIS_PASS}@127.0.0.1:6379/1

# Server
SERVER_IP=${SERVER_IP}
SERVER_FQDN=${SERVER_FQDN}
APP_PORT=${APP_PORT}

# Application State (setup_pending → setup_complete)
APP_STATE=setup_pending

# Session
SESSION_TIMEOUT_MINUTES=30
PERMANENT_SESSION_LIFETIME=1800

# Audit Log Retention
AUDIT_LOG_RETENTION_DAYS=120

# Webex (populated during first-time setup)
WEBEX_ACCESS_TOKEN=
WEBEX_ORG_ID=

# LDAP (populated during first-time setup)
LDAP_HOST=
LDAP_PORT=389
LDAP_USE_SSL=false
LDAP_BIND_DN=
LDAP_BIND_PASSWORD=
LDAP_BASE_DN=
LDAP_USER_FILTER=(mail={username})

# SSO (populated during first-time setup)
SSO_ENABLED=false
SSO_PROVIDER=

# SMTP (populated during first-time setup)
SMTP_HOST=
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=noreply@${SERVER_FQDN}

# ServiceNow (populated during first-time setup)
SNOW_INSTANCE=
SNOW_USERNAME=
SNOW_PASSWORD=
SNOW_CATALOG_ITEM_ID=

# Paths
ORBIT_HOME=${ORBIT_HOME}
ORBIT_LOG=${ORBIT_LOG}
CERT_PATH=${ORBIT_HOME}/certs/orbit-selfsigned.crt
KEY_PATH=${ORBIT_HOME}/certs/orbit-selfsigned.key
ENV_EOF

chmod 600 "${ORBIT_HOME}/.env"
chown "${ORBIT_USER}:${ORBIT_GROUP}" "${ORBIT_HOME}/.env"
success ".env file written with secure permissions (600)."

# ── Nginx Configuration (HTTP only — setup phase) ─────────────────────────────
header "Configuring Nginx (HTTP — Setup Phase)"

cat > /etc/nginx/sites-available/orbit << NGINX_EOF
# Orbit — HTTP Setup Phase
# This config is replaced by HTTPS post first-time setup completion

server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_FQDN} ${SERVER_IP};

    client_max_body_size 20M;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Static files served directly by Nginx
    location /static/ {
        alias ${ORBIT_HOME}/app/static/;
        expires 7d;
        access_log off;
    }

    # Uploads — logo etc
    location /uploads/ {
        alias ${ORBIT_HOME}/app/static/uploads/;
        expires 1d;
        access_log off;
    }

    # All other requests to Gunicorn
    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 120s;
    }

    # Deny hidden files
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }

    access_log /var/log/nginx/orbit_access.log;
    error_log  /var/log/nginx/orbit_error.log warn;
}
NGINX_EOF

# Enable site
ln -sf /etc/nginx/sites-available/orbit /etc/nginx/sites-enabled/orbit 2>/dev/null \
    || cp /etc/nginx/sites-available/orbit /etc/nginx/conf.d/orbit.conf 2>/dev/null || true

# Remove default nginx site
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

nginx -t && systemctl enable nginx && systemctl restart nginx
success "Nginx configured (HTTP, port 80)."

# ── Systemd Services ──────────────────────────────────────────────────────────
header "Installing Systemd Services"

# orbit-web (Gunicorn WSGI)
cat > /etc/systemd/system/orbit-web.service << SVC_EOF
[Unit]
Description=Orbit Provisioning Platform - Web (Gunicorn)
Documentation=https://github.com/your-org/orbit
After=network.target postgresql.service redis.service
Requires=postgresql.service redis.service

[Service]
Type=notify
User=${ORBIT_USER}
Group=${ORBIT_GROUP}
WorkingDirectory=${ORBIT_HOME}
EnvironmentFile=${ORBIT_HOME}/.env
ExecStart=${ORBIT_VENV}/bin/gunicorn \
    --bind 127.0.0.1:${APP_PORT} \
    --workers 4 \
    --worker-class gthread \
    --threads 2 \
    --worker-connections 1000 \
    --timeout 120 \
    --keepalive 5 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --log-level info \
    --access-logfile ${ORBIT_LOG}/access.log \
    --error-logfile ${ORBIT_LOG}/error.log \
    --capture-output \
    --forwarded-allow-ips="127.0.0.1" \
    wsgi:app
ExecReload=/bin/kill -s HUP \$MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=${ORBIT_HOME}/logs ${ORBIT_HOME}/app/static/uploads ${ORBIT_LOG}
Restart=on-failure
RestartSec=10
StandardOutput=append:${ORBIT_LOG}/gunicorn.log
StandardError=append:${ORBIT_LOG}/gunicorn-error.log

[Install]
WantedBy=multi-user.target
SVC_EOF

# orbit-worker (Celery background tasks)
cat > /etc/systemd/system/orbit-worker.service << SVC_EOF
[Unit]
Description=Orbit Provisioning Platform - Celery Worker
Documentation=https://github.com/your-org/orbit
After=network.target redis.service postgresql.service
Requires=redis.service postgresql.service

[Service]
Type=forking
User=${ORBIT_USER}
Group=${ORBIT_GROUP}
WorkingDirectory=${ORBIT_HOME}
EnvironmentFile=${ORBIT_HOME}/.env
ExecStart=${ORBIT_VENV}/bin/celery \
    -A wsgi.celery worker \
    --loglevel=info \
    --concurrency=4 \
    --max-tasks-per-child=500 \
    --logfile=${ORBIT_LOG}/celery-worker.log \
    --pidfile=/run/orbit/celery-worker.pid \
    --detach
ExecStop=${ORBIT_VENV}/bin/celery \
    -A wsgi.celery control shutdown \
    --timeout 10
RuntimeDirectory=orbit
KillMode=mixed
TimeoutStopSec=10
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SVC_EOF

# orbit-beat (APScheduler/Celery Beat — cron jobs)
cat > /etc/systemd/system/orbit-beat.service << SVC_EOF
[Unit]
Description=Orbit Provisioning Platform - Celery Beat Scheduler
Documentation=https://github.com/your-org/orbit
After=network.target redis.service
Requires=redis.service

[Service]
Type=forking
User=${ORBIT_USER}
Group=${ORBIT_GROUP}
WorkingDirectory=${ORBIT_HOME}
EnvironmentFile=${ORBIT_HOME}/.env
ExecStart=${ORBIT_VENV}/bin/celery \
    -A wsgi.celery beat \
    --loglevel=info \
    --logfile=${ORBIT_LOG}/celery-beat.log \
    --pidfile=/run/orbit/celery-beat.pid \
    --schedule=${ORBIT_HOME}/celerybeat-schedule \
    --detach
RuntimeDirectory=orbit
KillMode=mixed
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SVC_EOF

systemctl daemon-reload
systemctl enable orbit-web orbit-worker orbit-beat
success "Systemd services installed and enabled."

# ── Log Rotation ──────────────────────────────────────────────────────────────
cat > /etc/logrotate.d/orbit << LOGROTATE_EOF
${ORBIT_LOG}/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    sharedscripts
    postrotate
        systemctl reload orbit-web > /dev/null 2>&1 || true
    endscript
}
LOGROTATE_EOF

success "Log rotation configured (30-day retention for OS logs)."

# ── Database Initialisation ───────────────────────────────────────────────────
header "Initialising Application Database"

cd "${ORBIT_HOME}"
sudo -u "${ORBIT_USER}" "${ORBIT_VENV}/bin/flask" db upgrade 2>/dev/null \
    || info "DB migration will run on first application start."

# ── Start Services ────────────────────────────────────────────────────────────
header "Starting Orbit Services"

systemctl start orbit-web && success "orbit-web started." || warn "orbit-web start failed — check logs."
sleep 2
systemctl start orbit-worker && success "orbit-worker started." || warn "orbit-worker start failed."
systemctl start orbit-beat   && success "orbit-beat started."   || warn "orbit-beat start failed."

# ── Firewall ──────────────────────────────────────────────────────────────────
header "Configuring Firewall"

if command -v ufw &>/dev/null; then
    ufw allow 22/tcp   comment "SSH"
    ufw allow 80/tcp   comment "Orbit HTTP Setup"
    ufw allow 443/tcp  comment "Orbit HTTPS"
    ufw --force enable
    success "UFW firewall configured."
elif command -v firewall-cmd &>/dev/null; then
    firewall-cmd --permanent --add-service=ssh
    firewall-cmd --permanent --add-service=http
    firewall-cmd --permanent --add-service=https
    firewall-cmd --reload
    success "firewalld configured."
else
    warn "No firewall detected — ensure ports 22, 80, 443 are open manually."
fi

# ── Post-Install Summary ──────────────────────────────────────────────────────
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${GREEN}  ✅  Orbit Installation Complete!${NC}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}First-Time Setup URL:${NC}"
echo -e "  ${CYAN}http://${SERVER_IP}/setup${NC}"
echo -e "  ${CYAN}http://${SERVER_FQDN}/setup${NC}  (if DNS is configured)"
echo ""
echo -e "  ${BOLD}CLI Admin User:${NC}  ${CLI_ADMIN_USER}"
echo -e "  ${BOLD}Install Log:${NC}     ${ORBIT_LOG}/install.log"
echo -e "  ${BOLD}App Config:${NC}      ${ORBIT_HOME}/.env  (chmod 600)"
echo ""
echo -e "  ${YELLOW}⚠  Open the setup URL in a browser to complete configuration.${NC}"
echo -e "  ${YELLOW}⚠  HTTP is used for setup only. HTTPS is enabled post-setup.${NC}"
echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
