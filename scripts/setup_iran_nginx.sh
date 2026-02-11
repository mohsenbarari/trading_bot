#!/bin/bash

# ==========================================
# اسکریپت نصب Nginx و SSL برای سرور ایران
# Domain: coin.gold-trade.ir
# ==========================================

set -e

echo "🚀 شروع نصب Nginx و SSL برای سرور ایران..."

# 1. آپدیت و نصب پکیج‌ها
echo "📦 نصب Nginx و Certbot..."
apt update
apt install -y nginx certbot python3-certbot-nginx

# 2. ایجاد دایرکتوری برای frontend
echo "📁 ایجاد دایرکتوری‌ها..."
mkdir -p /root/trading-bot/trading_bot/mini_app_dist
mkdir -p /root/trading-bot/trading_bot/uploads

# 3. ایجاد کانفیگ Nginx
echo "⚙️ ایجاد کانفیگ Nginx..."
cat > /etc/nginx/sites-available/trading-bot << 'EOF'
server {
    listen 80;
    server_name coin.gold-trade.ir;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Frontend (Vue.js static files)
    location / {
        root /root/trading-bot/trading_bot/mini_app_dist;
        try_files $uri $uri/ /index.html;
        
        # Cache static assets
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
        }
    }

    # API proxying to Docker container
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # WebSocket endpoint
    location /api/realtime/ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
        proxy_buffering off;
    }

    # Assets served by FastAPI
    location /assets/ {
        proxy_pass http://127.0.0.1:8000;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Uploads
    location /uploads/ {
        proxy_pass http://127.0.0.1:8000;
    }

    # Favicon
    location /favicon.ico {
        root /root/trading-bot/trading_bot/mini_app_dist;
        access_log off;
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
chmod -R 755 /root/trading-bot

# 7. ریستارت Nginx
echo "🔄 ریستارت Nginx..."
systemctl restart nginx
systemctl enable nginx

# 8. دریافت SSL
echo "🔐 دریافت SSL Certificate..."
certbot --nginx -d coin.gold-trade.ir --non-interactive --agree-tos --email mohsenbarari235@gmail.com --redirect

# 9. تست نهایی
echo ""
echo "✅ نصب کامل شد!"
echo ""
echo "📋 خلاصه:"
echo "   • Nginx: نصب شد ✅"
echo "   • SSL: فعال شد ✅"
echo "   • Domain: https://coin.gold-trade.ir"
echo ""
echo "📌 مرحله بعد:"
echo "   1. پروژه را clone کنید یا فایل‌ها را منتقل کنید"
echo "   2. Docker را نصب کنید"
echo "   3. docker compose up -d را اجرا کنید"
echo ""
