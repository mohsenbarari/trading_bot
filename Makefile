
# ==========================================
# Trading Bot Deployment Makefile
# ==========================================

.PHONY: help deploy up down logs restart

help:
	@echo "Available commands:"
	@echo "  make deploy   - Build frontend, sync to Nginx, and rebuild Docker containers"
	@echo "  make up       - Same as 'deploy'"
	@echo "  make down     - Stop all containers"
	@echo "  make logs     - Show logs for all containers"
	@echo "  make restart  - Restart containers without rebuilding"

deploy:
	@echo "🔄 Starting automated deployment..."
	@chmod +x ./deploy.sh
	@./deploy.sh
	@echo "🐳 Rebuilding and starting Docker containers..."
	@docker compose up -d --build
	@echo "✅ Deployment finished!"

up: deploy

down:
	@docker compose down

logs:
	@docker compose logs -f

restart:
	@docker compose restart
