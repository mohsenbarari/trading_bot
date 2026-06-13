#!/bin/bash

# ==========================================
# اسکریپت نصب Nginx و SSL برای سرور ایران
# Domain is read from IRAN_APP_DOMAIN or deploy/production/online.env.
# ==========================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_CONFIG_SCRIPT="${DEPLOY_CONFIG_SCRIPT:-$SCRIPT_DIR/deploy_config.py}"

if [[ -f "$DEPLOY_CONFIG_SCRIPT" ]]; then
    IRAN_APP_DOMAIN="${IRAN_APP_DOMAIN:-$(python3 "$DEPLOY_CONFIG_SCRIPT" --key IRAN_APP_DOMAIN 2>/dev/null || true)}"
    IRAN_CERTBOT_EMAIL="${IRAN_CERTBOT_EMAIL:-$(python3 "$DEPLOY_CONFIG_SCRIPT" --key IRAN_CERTBOT_EMAIL 2>/dev/null || true)}"
    IRAN_PROJECT_DIR="${IRAN_PROJECT_DIR:-$(python3 "$DEPLOY_CONFIG_SCRIPT" --key IRAN_PROJECT_DIR 2>/dev/null || true)}"
fi

: "${IRAN_APP_DOMAIN:?IRAN_APP_DOMAIN is required. Define it in DEPLOY_MANIFEST or environment.}"
: "${IRAN_CERTBOT_EMAIL:?IRAN_CERTBOT_EMAIL is required. Define it in DEPLOY_MANIFEST or environment.}"
: "${IRAN_PROJECT_DIR:?IRAN_PROJECT_DIR is required. Define it in DEPLOY_MANIFEST or environment.}"
PROJECT_DIR="$IRAN_PROJECT_DIR"
DIST_DIR="${IRAN_DIST_DIR:-$PROJECT_DIR/mini_app_dist}"
UPLOADS_DIR="${IRAN_UPLOADS_DIR:-$PROJECT_DIR/uploads}"

echo "🚀 شروع نصب Nginx و SSL برای سرور ایران..."

# 1. آپدیت و نصب پکیج‌ها
echo "📦 نصب Nginx و Certbot..."
apt update
apt install -y nginx certbot python3-certbot-nginx

# 2. ایجاد دایرکتوری برای frontend
echo "📁 ایجاد دایرکتوری‌ها..."
mkdir -p "$DIST_DIR"
mkdir -p "$UPLOADS_DIR"

# 3. ایجاد کانفیگ Nginx
echo "⚙️ ایجاد کانفیگ Nginx..."
cat > /etc/nginx/sites-available/trading-bot <<EOF
upstream trading_bot_api {
    server 127.0.0.1:8000;
    keepalive 256;
}

server {
    listen 80;
    server_name ${IRAN_APP_DOMAIN};
    client_max_body_size 50M;
    root ${DIST_DIR};
    index index.html;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    location = /metrics {
        deny all;
        return 404;
    }

    # API proxying to Docker container
    location /api/ {
        proxy_pass http://trading_bot_api;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    # WebSocket endpoint
    location /api/realtime/ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
        proxy_buffering off;
    }

    # Frontend static assets served directly by Nginx
    location ~ ^/assets/.*\.js$ {
        try_files \$uri @stale_js_chunk;
        access_log off;
        add_header Cache-Control "public, max-age=31536000, immutable" always;
        add_header X-Static-Delivery "nginx" always;
    }

    location /assets/ {
        try_files \$uri =404;
        access_log off;
        add_header Cache-Control "public, max-age=31536000, immutable" always;
        add_header X-Static-Delivery "nginx" always;
    }

    location = /manifest.webmanifest {
        try_files \$uri =404;
        default_type application/manifest+json;
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        add_header X-Static-Delivery "nginx" always;
    }

    location = /sw.js {
        try_files \$uri =404;
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        add_header Service-Worker-Allowed "/" always;
        add_header X-Static-Delivery "nginx" always;
    }

    location = /share-target-sw.js {
        try_files \$uri =404;
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        add_header Service-Worker-Allowed "/" always;
        add_header X-Static-Delivery "nginx" always;
    }

    location ~ ^/workbox-[A-Za-z0-9_-]+\.js$ {
        try_files \$uri =404;
        access_log off;
        add_header Cache-Control "public, max-age=31536000, immutable" always;
        add_header X-Static-Delivery "nginx" always;
    }

    # Raw uploads are not public. Chat media stays behind /api/chat/files auth.
    location /uploads/ {
        access_log off;
        return 404;
    }

    location @stale_js_chunk {
        internal;
        default_type application/javascript;
        add_header Cache-Control "no-store, no-cache, must-revalidate" always;
        add_header X-Static-Delivery "nginx" always;
        return 200 "console.warn('Stale PWA chunk requested. Forcing hard reload...'); window.location.reload(true);";
    }

    # Favicon
    location /favicon.ico {
        try_files \$uri =404;
        access_log off;
    }

    # Frontend routing (Vue SPA)
    location / {
        try_files \$uri \$uri/ /index.html;
        add_header Cache-Control "no-store, no-cache, must-revalidate" always;
        add_header X-Static-Delivery "nginx" always;
    }
}
EOF

# 4. فعال کردن سایت
echo "🔗 فعال کردن سایت..."
ln -sf /etc/nginx/sites-available/trading-bot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# 5. تست کانفیگ
echo "🧪 تست کانفیگ Nginx..."
nginx -t

# 6. تنظیم مجوزها
echo "🔧 تنظیم مجوزها..."
chmod 755 /root
chmod -R 755 "$(dirname "$PROJECT_DIR")"

# 7. ریستارت Nginx
echo "🔄 ریستارت Nginx..."
systemctl restart nginx
systemctl enable nginx

# 8. دریافت SSL
echo "🔐 دریافت SSL Certificate..."
certbot --nginx -d "$IRAN_APP_DOMAIN" --non-interactive --agree-tos --email "$IRAN_CERTBOT_EMAIL" --redirect

# 9. تست نهایی
echo ""
echo "✅ نصب کامل شد!"
echo ""
echo "📋 خلاصه:"
echo "   • Nginx: نصب شد ✅"
echo "   • SSL: فعال شد ✅"
echo "   • Domain: https://$IRAN_APP_DOMAIN"
echo ""
echo "📌 مرحله بعد:"
echo "   1. پروژه را clone کنید یا فایل‌ها را منتقل کنید"
echo "   2. Docker را نصب کنید"
echo "   3. docker compose up -d را اجرا کنید"
echo ""
