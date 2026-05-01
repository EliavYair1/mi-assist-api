#!/bin/bash
# ════════════════════════════════════════════════════════════
#  MI Assist API — One-Shot Deploy Script
#  Run on a fresh Ubuntu 22.04 server (as root or with sudo)
#
#  Usage:
#    chmod +x deploy.sh
#    sudo ./deploy.sh
# ════════════════════════════════════════════════════════════

set -e   # Exit on any error

APP_DIR="/var/www/mi-assist-api"
LOG_DIR="/var/log/mi-assist"
SERVICE="mi-assist"
PYTHON="python3.11"

echo "════════════════════════════════"
echo "  MI Assist API — Deploy"
echo "════════════════════════════════"

# 1. System deps
echo "[1/8] Installing system packages..."
apt-get update -q
apt-get install -y python3.11 python3.11-venv python3-pip postgresql postgresql-contrib nginx certbot python3-certbot-nginx

# 2. PostgreSQL setup
echo "[2/8] Setting up PostgreSQL..."
sudo -u postgres psql -c "CREATE USER mi_user WITH PASSWORD 'changeme_strong_password';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE mi_assist OWNER mi_user;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE mi_assist TO mi_user;" 2>/dev/null || true

# 3. App directory
echo "[3/8] Creating app directory..."
mkdir -p "$APP_DIR" "$LOG_DIR"
chown -R www-data:www-data "$LOG_DIR"

# 4. Copy app files (assumes you're running from the repo root)
echo "[4/8] Copying application files..."
cp -r app "$APP_DIR/"
cp requirements.txt "$APP_DIR/"
cp .env.example "$APP_DIR/.env"

echo ""
echo "  ⚠  IMPORTANT: Edit $APP_DIR/.env now before continuing!"
echo "     Fill in: OPENAI_API_KEY, DATABASE_URL, JWT_SECRET, WP_SITE_URL, etc."
echo ""
read -p "  Press ENTER when .env is ready..."

# 5. Python venv + deps
echo "[5/8] Installing Python dependencies..."
cd "$APP_DIR"
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
deactivate

# 6. Systemd service
echo "[6/8] Installing systemd service..."
cp "$OLDPWD/mi-assist.service" /etc/systemd/system/mi-assist.service
sed -i "s|/var/www/mi-assist-api|$APP_DIR|g" /etc/systemd/system/mi-assist.service
systemctl daemon-reload
systemctl enable mi-assist
systemctl start mi-assist

# 7. Test service
echo "[7/8] Testing service..."
sleep 3
if systemctl is-active --quiet mi-assist; then
    echo "  ✅ MI Assist API is running"
    curl -s http://127.0.0.1:8000/health | python3 -m json.tool
else
    echo "  ❌ Service failed to start — check logs:"
    journalctl -u mi-assist -n 30
    exit 1
fi

# 8. Nginx (assumes WordPress already configured)
echo "[8/8] Adding API location to Nginx..."
echo ""
echo "  Add this block inside your server {} block in Nginx:"
echo ""
cat << 'NGINX'
    location /v1/ {
        proxy_pass         http://127.0.0.1:8000/v1/;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        client_max_body_size 11M;
    }
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
    }
NGINX

echo ""
echo "  Then run:"
echo "    nginx -t && systemctl reload nginx"
echo ""
echo "════════════════════════════════"
echo "  ✅ Deploy complete!"
echo "  API: http://127.0.0.1:8000"
echo "  Health: curl http://127.0.0.1:8000/health"
echo "  Logs: journalctl -u mi-assist -f"
echo "  Restart: systemctl restart mi-assist"
echo "════════════════════════════════"
