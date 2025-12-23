#!/bin/bash

# ==========================================
# تنظیمات سرور Nginx (مقصد)
# ==========================================
NGINX_SERVER_IP="83.147.19.226"
NGINX_USER="root"
REMOTE_DIR="/var/www/telegram_bot/dist/"

# ==========================================
# تنظیمات پروژه (مبدا - سرور اپلیکیشن)
# ==========================================
PROJECT_DIR="/root/trading-bot/trading_bot"
FRONTEND_DIR="$PROJECT_DIR/frontend"
DIST_DIR="$PROJECT_DIR/mini_app_dist"

echo "🚀 Starting Deployment Process..."

# 1. رفتن به پوشه فرانت‌‌اند و بیلد گرفتن
echo "📦 Building Frontend..."
cd $FRONTEND_DIR
npm install
npm run build

# بررسی موفقیت بیلد
if [ $? -ne 0 ]; then
    echo "❌ Build failed! Aborting deployment."
    exit 1
fi

echo "✅ Build successful!"

# 2. بررسی وجود دایرکتوری بیلد
if [ ! -d "$DIST_DIR" ]; then
    echo "❌ Build directory ($DIST_DIR) not found!"
    exit 1
fi

# 3. ارسال فایل‌ها به سرور Nginx
echo "📡 Syncing files to Nginx Server ($NGINX_SERVER_IP)..."

# استفاده از rsync برای کپی هوشمند (فقط فایل‌های تغییر کرده)
# -a: archive mode (حفظ پرمیشن‌ها)
# -v: verbose
# -z: compression
# --delete: حذف فایل‌هایی در مقصد که در مبدا دیگر نیستند
rsync -avz --delete -e "ssh -o StrictHostKeyChecking=no" "$DIST_DIR/" "$NGINX_USER@$NGINX_SERVER_IP:$REMOTE_DIR"

if [ $? -eq 0 ]; then
    echo "✅ Deployment completed successfully!"
    echo "🌐 Static files are now live on Nginx Server."
else
    echo "❌ Deployment failed! Please check SSH connection."
    echo "💡 Hint: Make sure you have added your SSH public key to the Nginx Server:"
    echo "   ssh-copy-id $NGINX_USER@$NGINX_SERVER_IP"
fi
