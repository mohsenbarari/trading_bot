#!/bin/bash
set -e

# ==========================================
# 🚀 Deploy Script — Two-Server Architecture
# ==========================================
# Foreign Server (Germany): Bot + Sync + API
# Iran Server:              API + Nginx + Frontend
# ==========================================

PROJECT_DIR="/root/trading-bot/trading_bot"
FRONTEND_DIR="$PROJECT_DIR/frontend"
DIST_DIR="$PROJECT_DIR/mini_app_dist"

IRAN_HOST="87.107.110.68"
IRAN_USER="root"
IRAN_PROJECT_DIR="/root/trading-bot/trading_bot"

# ==========================================
# Helper Functions
# ==========================================
ssh_iran() {
    ssh -o StrictHostKeyChecking=no "$IRAN_USER@$IRAN_HOST" "$@"
}

scp_iran() {
    scp -r -o StrictHostKeyChecking=no "$@"
}

print_header() {
    echo ""
    echo "============================================"
    echo "  $1"
    echo "============================================"
}

# ==========================================
# Parse Arguments
# ==========================================
TARGET="${1:-all}"  # all | frontend | foreign | iran

print_header "🚀 Deploy: $TARGET"

# ==========================================
# 1. Frontend Build (shared step)
# ==========================================
build_frontend() {
    print_header "📦 Building Frontend"
    cd "$FRONTEND_DIR"
    npm install --silent
    npm run build

    if [ ! -d "$DIST_DIR" ]; then
        echo "❌ Build directory ($DIST_DIR) not found!"
        exit 1
    fi

    chmod -R 755 "$DIST_DIR"
    echo "✅ Frontend build successful!"
    cd "$PROJECT_DIR"
}

# ==========================================
# 1.5. Prepare Pip Packages (Germany only)
# ==========================================
prepare_pip_packages() {
    print_header "📦 Checking pip dependencies"
    
    HASH_FILE="$PROJECT_DIR/pip_packages/.requirements_hash"
    CURRENT_HASH=$(md5sum "$PROJECT_DIR/requirements.txt" | cut -d' ' -f1)
    
    if [ ! -f "$HASH_FILE" ] || [ "$(cat "$HASH_FILE")" != "$CURRENT_HASH" ] || [ ! -d "$PROJECT_DIR/pip_packages" ]; then
        echo "🔄 requirements.txt changed or packages missing. Downloading..."
        mkdir -p "$PROJECT_DIR/pip_packages"
        
        # Download for Python 3.11 (Docker image version)
        pip download -r "$PROJECT_DIR/requirements.txt" \
            -d "$PROJECT_DIR/pip_packages/" \
            --python-version 311 \
            --implementation cp \
            --abi cp311 \
            --platform manylinux2014_x86_64 \
            --platform manylinux_2_17_x86_64 \
            --platform manylinux_2_28_x86_64 \
            --platform linux_x86_64 \
            --platform any \
            --only-binary=:all:
            
        echo "$CURRENT_HASH" > "$HASH_FILE"
        echo "✅ Pip packages updated successfully!"
    else
        echo "✅ Pip packages are up to date (hash: $CURRENT_HASH)."
    fi
}

# ==========================================
# 2. Deploy to Iran Server
# ==========================================
deploy_iran() {
    print_header "🇮🇷 Deploying to Iran Server ($IRAN_HOST)"

    cd "$PROJECT_DIR"

    # 2a. Check for uncommitted changes & push to GitHub
    echo "📤 Syncing code via git..."
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "⚠️  Uncommitted changes detected!"
        echo "   Please commit your changes with a proper message first:"
        echo "   git add -A && git commit -m \"your message here\""
        exit 1
    fi
    git push 2>/dev/null || echo "  (nothing to push)"

    # 2b. Sync backend code to Iran via rsync
    echo "📥 Syncing code to Iran server via rsync..."
    rsync -avz --delete \
        --exclude '.git' \
        --exclude 'frontend' \
        --exclude 'mini_app_dist' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.env' \
        --exclude 'node_modules' \
        -e "ssh -o StrictHostKeyChecking=no" \
        "$PROJECT_DIR/" "$IRAN_USER@$IRAN_HOST:$IRAN_PROJECT_DIR/"

    # 2c. Upload built frontend assets
    echo "📤 Uploading frontend assets..."
    rsync -avz --delete \
        -e "ssh -o StrictHostKeyChecking=no" \
        "$DIST_DIR/" "$IRAN_USER@$IRAN_HOST:$IRAN_PROJECT_DIR/mini_app_dist/"

    # 2d. Rebuild Docker containers on Iran
    echo "🐳 Rebuilding Docker on Iran..."
    echo "⏳ Waiting for Iran services to become ready..."
    ssh_iran "cd $IRAN_PROJECT_DIR && docker compose -f docker-compose.iran.yml down && docker compose -f docker-compose.iran.yml up -d --build --wait --wait-timeout 180"

    echo "✅ Iran deployment complete!"
    ssh_iran "cd $IRAN_PROJECT_DIR && docker compose -f docker-compose.iran.yml ps"
}

# ==========================================
# 3. Deploy to Foreign Server (this machine)
# ==========================================
deploy_foreign() {
    print_header "🌍 Deploying Foreign Server (local)"

    cd "$PROJECT_DIR"
    docker compose down
    echo "⏳ Waiting for foreign services to become ready..."
    docker compose up -d --build --wait --wait-timeout 180

    echo "✅ Foreign deployment complete!"
    docker compose ps
}

# ==========================================
# Execute based on target
# ==========================================
case "$TARGET" in
    frontend)
        build_frontend
        deploy_iran  # frontend only goes to Iran
        ;;
    iran)
        prepare_pip_packages
        build_frontend
        deploy_iran
        ;;
    foreign)
        prepare_pip_packages
        build_frontend
        deploy_foreign
        ;;
    all)
        prepare_pip_packages
        build_frontend
        deploy_iran
        deploy_foreign
        ;;
    *)
        echo "Usage: ./deploy.sh [all|frontend|iran|foreign]"
        echo ""
        echo "  all       - Build frontend + deploy to both servers (default)"
        echo "  frontend  - Build frontend + deploy to Iran only"
        echo "  iran      - Build frontend + deploy Iran server"
        echo "  foreign   - Rebuild Docker on foreign server only"
        exit 1
        ;;
esac

print_header "🎉 Deployment Complete!"
