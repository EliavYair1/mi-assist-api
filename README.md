# MI Assist API — Setup Guide
MetroIntegrity · Safety First. Integrity Always.

## Project Structure

```
mi_assist_api/
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # All settings (reads .env)
│   ├── database.py          # Async SQLAlchemy engine
│   ├── models.py            # All DB models (User, Usage, Conversation, Message)
│   ├── auth.py              # JWT create/decode/dependency
│   ├── routers/
│   │   ├── auth.py          # POST /v1/auth/exchange
│   │   ├── chat.py          # POST /v1/chat  ← main AI endpoint
│   │   ├── usage.py         # GET  /v1/usage
│   │   ├── billing.py       # POST /v1/billing/subscribe + /webhook
│   │   └── upload.py        # POST /v1/upload  (Pro+ only)
│   └── services/
│       ├── openai_service.py  # GPT-4o call + System Prompt v5
│       └── usage.py           # Daily counter logic
├── scripts/
│   └── reset_usage.py       # Cron: cleanup old usage rows
├── requirements.txt
├── .env.example             # Copy to .env and fill in values
├── nginx.conf.example       # Nginx block to add to your WP config
├── mi-assist.service        # Systemd service file
├── deploy.sh                # One-shot deploy script
└── wordpress-integration.php  # Add to your WP theme functions.php
```

---

## Step-by-Step Setup on Your Server

### 1. PostgreSQL database

```bash
sudo -u postgres psql
CREATE USER mi_user WITH PASSWORD 'strong_password_here';
CREATE DATABASE mi_assist OWNER mi_user;
GRANT ALL PRIVILEGES ON DATABASE mi_assist TO mi_user;
\q
```

### 2. Create app directory and venv

```bash
mkdir -p /var/www/mi-assist-api /var/log/mi-assist
cd /var/www/mi-assist-api

# Copy all files here, then:
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

### 3. Configure environment

```bash
cp .env.example .env
nano .env
# Fill in every value — especially:
#   OPENAI_API_KEY, DATABASE_URL, JWT_SECRET, WP_SITE_URL, WP_API_SECRET
```

### 4. Start the API (test)

```bash
source venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
# Visit http://127.0.0.1:8000/health  → should return {"status":"ok"}
# Ctrl+C to stop
```

### 5. Install systemd service (production)

```bash
cp mi-assist.service /etc/systemd/system/
# Edit paths if your app is not in /var/www/mi-assist-api
nano /etc/systemd/system/mi-assist.service

systemctl daemon-reload
systemctl enable mi-assist
systemctl start mi-assist
systemctl status mi-assist

# View logs live:
journalctl -u mi-assist -f
```

### 6. Add Nginx block

Open your existing Nginx config (the one serving WordPress):
```bash
nano /etc/nginx/sites-available/yourdomain.com
```

Add inside the `server { ... }` block (before the WordPress `location /` block):
```nginx
location /v1/ {
    proxy_pass         http://127.0.0.1:8000/v1/;
    proxy_http_version 1.1;
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
```

Test and reload:
```bash
nginx -t && systemctl reload nginx
```

### 7. WordPress integration

```bash
# Add to wp-config.php:
define('MI_ASSIST_API_URL',    'https://yourdomain.com');
define('MI_ASSIST_API_SECRET', 'same-as-WP_API_SECRET-in-.env');

# Add to functions.php:
require_once get_template_directory() . '/mi-assist-integration.php';
# Copy wordpress-integration.php to your theme directory
```

### 8. Cron — daily usage cleanup

```bash
crontab -e
# Add:
0 0 * * * /var/www/mi-assist-api/venv/bin/python /var/www/mi-assist-api/scripts/reset_usage.py >> /var/log/mi-assist/cron.log 2>&1
```

---

## Quick Test Commands

```bash
# Health check
curl https://yourdomain.com/health

# Get a token (replace with real values after WP integration)
curl -X POST https://yourdomain.com/v1/auth/exchange \
  -H "Content-Type: application/json" \
  -d '{"wp_user_id": 1, "wp_nonce": "abc123", "email": "test@test.com"}'

# Send a chat message (replace TOKEN)
curl -X POST https://yourdomain.com/v1/chat \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What PPE is required for confined space entry?"}'

# Check usage
curl https://yourdomain.com/v1/usage \
  -H "Authorization: Bearer TOKEN"
```

---

## Stripe Webhook Setup

1. Go to dashboard.stripe.com → Developers → Webhooks
2. Add endpoint: `https://yourdomain.com/v1/billing/webhook`
3. Select events:
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
   - `checkout.session.completed`
4. Copy the signing secret → paste as `STRIPE_WEBHOOK_SECRET` in `.env`
5. Restart service: `systemctl restart mi-assist`

## PayPal Webhook Setup

1. Go to developer.paypal.com → My Apps → Your App → Webhooks
2. Add URL: `https://yourdomain.com/v1/billing/webhook`
3. Add header: `X-Provider: paypal` (configure in PayPal dashboard or handle in your WP frontend)
4. Select events: `BILLING.SUBSCRIPTION.ACTIVATED`, `BILLING.SUBSCRIPTION.CANCELLED`, `PAYMENT.SALE.DENIED`

---

## Common Commands

```bash
# Restart after code update
systemctl restart mi-assist

# View live logs
journalctl -u mi-assist -f

# View last 50 error lines
tail -50 /var/log/mi-assist/error.log

# Check DB tables were created
sudo -u postgres psql -d mi_assist -c "\dt"

# Manual usage reset (if cron fails)
/var/www/mi-assist-api/venv/bin/python /var/www/mi-assist-api/scripts/reset_usage.py
```

---

## Flow Summary

```
User opens /mi-assist/ on WordPress
    ↓
WordPress: user is logged in → generate nonce → localize to JS
    ↓
JS: POST /v1/auth/exchange { wp_user_id, nonce, email }
    ↓
API: validates nonce with WP REST → issues JWT
    ↓
JS: stores JWT in memory (not localStorage)
    ↓
User sends message → JS: POST /v1/chat { message } + Bearer JWT
    ↓
API: checks daily limit → calls OpenAI GPT-4o → saves to DB → returns reply
    ↓
Limit reached → 429 response → frontend shows upgrade modal
    ↓
User upgrades → POST /v1/billing/subscribe → Stripe/PayPal checkout
    ↓
Webhook → /v1/billing/webhook → plan updated in DB
    ↓
Next JWT refresh → new token carries updated plan
```
