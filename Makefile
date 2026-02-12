
# ==========================================
# Trading Bot — Two-Server Deployment
# ==========================================
# Foreign (Germany): Bot + Sync + API
# Iran:              API + Nginx + Frontend
# ==========================================

IRAN_HOST = root@87.107.110.68
IRAN_DIR  = /root/trading-bot/trading_bot

.PHONY: help up deploy frontend iran foreign down logs logs-iran restart restart-iran status

help:
	@echo ""
	@echo "🚀 Available commands:"
	@echo ""
	@echo "  make up         - Full deploy: build frontend + deploy both servers"
	@echo "  make frontend   - Build frontend + deploy to Iran only"
	@echo "  make iran       - Build frontend + full Iran deploy"
	@echo "  make foreign    - Rebuild Docker on foreign server only"
	@echo ""
	@echo "  make down        - Stop foreign containers"
	@echo "  make logs        - Foreign server logs"
	@echo "  make logs-iran   - Iran server logs"
	@echo "  make restart     - Restart foreign containers"
	@echo "  make restart-iran - Restart Iran containers"
	@echo "  make status      - Show status of both servers"
	@echo ""

# --- Deploy Commands ---

up: deploy
deploy:
	@chmod +x ./deploy.sh
	@./deploy.sh all

frontend:
	@chmod +x ./deploy.sh
	@./deploy.sh frontend

iran:
	@chmod +x ./deploy.sh
	@./deploy.sh iran

foreign:
	@chmod +x ./deploy.sh
	@./deploy.sh foreign

# --- Management Commands ---

down:
	@docker compose down

logs:
	@docker compose logs -f

logs-iran:
	@ssh -o StrictHostKeyChecking=no $(IRAN_HOST) 'cd $(IRAN_DIR) && docker compose -f docker-compose.iran.yml logs -f --tail=50'

restart:
	@docker compose restart

restart-iran:
	@ssh -o StrictHostKeyChecking=no $(IRAN_HOST) 'cd $(IRAN_DIR) && docker compose -f docker-compose.iran.yml restart'

status:
	@echo ""
	@echo "🌍 Foreign Server (local):"
	@docker compose ps
	@echo ""
	@echo "🇮🇷 Iran Server (87.107.110.68):"
	@ssh -o StrictHostKeyChecking=no $(IRAN_HOST) 'cd $(IRAN_DIR) && docker compose -f docker-compose.iran.yml ps'
