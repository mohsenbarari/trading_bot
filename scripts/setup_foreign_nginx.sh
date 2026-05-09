#!/bin/bash

# ==========================================
# Setup Nginx and SSL for the foreign server
# Domain: coin.362514.ir
# ==========================================

set -euo pipefail

PROJECT_DIR="/root/trading-bot/trading_bot"
DIST_DIR="$PROJECT_DIR/mini_app_dist"
UPLOADS_DIR="$PROJECT_DIR/uploads"
SITE_PATH="/etc/nginx/sites-available/coin.362514.ir"

echo "🚀 Setting up foreign Nginx for coin.362514.ir..."

echo "📦 Installing Nginx and Certbot..."
apt update
apt install -y nginx certbot python3-certbot-nginx

echo "📁 Ensuring required directories exist..."
mkdir -p "$DIST_DIR"
mkdir -p "$UPLOADS_DIR"

echo "⚙️ Writing Nginx site config..."
cat > "$SITE_PATH" <<'EOF'
server {
    server_name coin.362514.ir;
    client_max_body_size 50M;

    # API and realtime traffic continue to flow through the app container.
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Large hashed JS chunks can be truncated for Electron/Chromium clients if
    # Nginx spills proxied asset responses to temp files. Keep this branch fully
    # streamed so login boot stays stable in the VS Code integrated browser too.
    location /assets/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_max_temp_file_size 0;
        gzip off;

        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    location /uploads/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Keep the default SPA/app serving path proxied to the FastAPI container.
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/coin.362514.ir/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/coin.362514.ir/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}

server {
    if ($host = coin.362514.ir) {
        return 301 https://$host$request_uri;
    }

    listen 80;
    server_name coin.362514.ir;
    client_max_body_size 50M;
    return 404;
}
EOF

echo "🔗 Enabling site..."
ln -sf "$SITE_PATH" /etc/nginx/sites-enabled/coin.362514.ir
rm -f /etc/nginx/sites-enabled/default

echo "🧪 Validating Nginx config..."
nginx -t

echo "🔄 Reloading Nginx..."
systemctl reload nginx
systemctl enable nginx

echo "🔐 Ensuring SSL certificate exists..."
certbot --nginx -d coin.362514.ir --non-interactive --agree-tos --email mohsenbarari235@gmail.com --redirect || true

echo "✅ Foreign Nginx setup complete."