
# ==========================================
# Trading Bot — Two-Server Deployment
# ==========================================
# Foreign (Germany): Bot + Sync + API
# Iran:              API + Nginx + Frontend
# ==========================================

IRAN_HOST = root@87.107.110.68
IRAN_DIR  = /root/trading-bot/trading_bot

.PHONY: help up deploy frontend iran foreign sync-recover sync-health sync-health-iran restore-default-commodities dev-admin create-superadmin create-admin create-user list-users show-user change-password force-password-change set-role set-status set-max-sessions reset-sessions unlock-login down logs logs-api logs-bot logs-jobs logs-follow metrics logs-iran restart restart-iran status observability-up observability-down observability-logs observability-overhead audit-log-export test-report test-gate test-diff-gate frontend-test-e2e frontend-test-e2e-firefox frontend-test-e2e-webkit frontend-test-e2e-matrix messenger-surface-report messenger-query-plans messenger-benchmark-prepare messenger-benchmark-run messenger-benchmark-report messenger-benchmark-all production-online-help production-online-check production-online-bootstrap production-online-nginx production-online-cert production-online-build production-online-sync production-online-deploy production-online-health production-online-full

help:
	@echo ""
	@echo "🚀 Available commands:"
	@echo ""
	@echo "  make up         - Full deploy: build frontend + deploy both servers + auto sync recovery"
	@echo "  make frontend   - Build frontend + deploy to Iran only"
	@echo "  make iran       - Build frontend + full Iran deploy"
	@echo "  make foreign    - Rebuild Docker on foreign server only"
	@echo "  make sync-recover - Manual fallback to catch up both servers after Iran reconnects"
	@echo "  make sync-health - Show local/foreign sync backlog and lag"
	@echo "  make sync-health-iran - Show Iran sync backlog and lag through SSH"
	@echo "  make restore-default-commodities - Restore canonical default commodities on the current DB"
	@echo "  make dev-admin ARGS=\"...\" - Run the developer admin CLI inside the app container"
	@echo "  make create-superadmin - Interactive super admin creation"
	@echo "  make create-admin      - Interactive middle admin creation"
	@echo "  make create-user       - Interactive normal user creation"
	@echo "  make list-users        - List users"
	@echo "  make show-user         - Interactive user lookup"
	@echo "  make change-password   - Interactive admin password change"
	@echo "  make force-password-change - Force an admin to rotate password"
	@echo "  make set-role          - Interactive role change"
	@echo "  make set-status        - Interactive account activation/deactivation"
	@echo "  make set-max-sessions  - Interactive session limit change"
	@echo "  make reset-sessions    - Interactive session reset"
	@echo "  make unlock-login      - Interactive login throttle unlock"
	@echo ""
	@echo "  make down        - Stop foreign containers"
	@echo "  make logs        - Foreign server logs"
	@echo "  make logs-api    - Follow API container logs"
	@echo "  make logs-bot    - Follow bot container logs"
	@echo "  make logs-jobs   - Follow app/bot logs where background jobs emit events"
	@echo "  make logs-follow - Follow all local runtime logs with a bounded tail"
	@echo "  make metrics     - Print Prometheus metrics from the local API"
	@echo "  make logs-iran   - Iran server logs"
	@echo "  make restart     - Restart foreign containers"
	@echo "  make restart-iran - Restart Iran containers"
	@echo "  make status      - Show status of both servers"
	@echo "  make observability-up   - Start local Loki/Promtail/Grafana stack"
	@echo "  make observability-down - Stop local observability stack"
	@echo "  make observability-logs - Follow observability stack logs"
	@echo "  make observability-overhead - Measure structured logging overhead"
	@echo "  make audit-log-export - Export audit logs from local Loki to JSONL"
	@echo "  make test-report - Show repository test breadth summary"
	@echo "  make test-gate   - Enforce repository test breadth baseline"
	@echo "  make test-diff-gate BASE=<ref> - Enforce test changes alongside product changes"
	@echo "  make frontend-test-e2e - Run frontend Playwright on Chromium"
	@echo "  make frontend-test-e2e-firefox - Run frontend Playwright on Firefox"
	@echo "  make frontend-test-e2e-webkit - Run frontend Playwright on WebKit"
	@echo "  make frontend-test-e2e-matrix - Run frontend Playwright on Chromium + Firefox + WebKit"
	@echo "  make messenger-surface-report - Generate docs/messenger-surface-report.md from the manifest"
	@echo "  make messenger-query-plans - Run EXPLAIN ANALYZE on the core Messenger query surfaces"
	@echo "  make messenger-benchmark-prepare - Prepare reproducible old/current benchmark builds"
	@echo "  make messenger-benchmark-run - Run the official Messenger performance benchmark"
	@echo "  make messenger-benchmark-report - Build comparison-summary and surface-status artifacts"
	@echo "  make messenger-benchmark-all - Run the full benchmark prep + measure + report pipeline"
	@echo "  make production-online-help   - Show the Iran-online production deploy helper usage"
	@echo "  make production-online-check  - Validate the production deploy manifest and SSH access"
	@echo "  make production-online-bootstrap - Install Iran host prerequisites over SSH"
	@echo "  make production-online-nginx  - Render and install the Iran Nginx config"
	@echo "  make production-online-cert   - Request/renew SSL on the Iran host"
	@echo "  make production-online-build  - Build frontend locally and prepare wheel cache"
	@echo "  make production-online-sync   - Rsync the production payload to the Iran host"
	@echo "  make production-online-deploy - Build/start the Iran Docker stack"
	@echo "  make production-online-health - Run post-deploy health checks"
	@echo "  make production-online-full   - Run the full Iran-online deployment flow"
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

sync-recover:
	@chmod +x ./scripts/recover_cross_server_sync.sh
	@./scripts/recover_cross_server_sync.sh

sync-health:
	@set -a; [ ! -f .env ] || . ./.env; set +a; curl -fsS -H "X-Dev-Api-Key: $${DEV_API_KEY}" http://127.0.0.1:8000/api/sync/health

sync-health-iran:
	@ssh -o StrictHostKeyChecking=no $(IRAN_HOST) 'cd $(IRAN_DIR) && set -a; [ ! -f .env ] || . ./.env; set +a; curl -fsS -H "X-Dev-Api-Key: $$DEV_API_KEY" http://127.0.0.1:8000/api/sync/health'

restore-default-commodities:
	@docker compose run --rm migration python scripts/restore_default_commodities.py

dev-admin:
	@docker compose exec -T app python scripts/dev_admin.py $${ARGS}

create-superadmin:
	@docker compose exec app python scripts/dev_admin.py create-superadmin

create-admin:
	@docker compose exec app python scripts/dev_admin.py create-admin

create-user:
	@docker compose exec app python scripts/dev_admin.py create-user

list-users:
	@docker compose exec -T app python scripts/dev_admin.py list-users $${ARGS}

show-user:
	@docker compose exec app python scripts/dev_admin.py show-user

change-password:
	@docker compose exec app python scripts/dev_admin.py change-password

force-password-change:
	@docker compose exec app python scripts/dev_admin.py force-password-change

set-role:
	@docker compose exec app python scripts/dev_admin.py set-role

set-status:
	@docker compose exec app python scripts/dev_admin.py set-status

set-max-sessions:
	@docker compose exec app python scripts/dev_admin.py set-max-sessions

reset-sessions:
	@docker compose exec app python scripts/dev_admin.py reset-sessions

unlock-login:
	@docker compose exec app python scripts/dev_admin.py unlock-login

# --- Management Commands ---

down:
	@docker compose down

logs:
	@docker compose logs -f

logs-api:
	@docker compose logs -f --tail=150 app

logs-bot:
	@docker compose logs -f --tail=150 bot

logs-jobs:
	@docker compose logs -f --tail=200 app bot

logs-follow:
	@docker compose logs -f --tail=100 app bot redis db

metrics:
	@curl -fsS http://127.0.0.1:8000/metrics

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

observability-up:
	@docker compose -f docker-compose.observability.yml up -d

observability-down:
	@docker compose -f docker-compose.observability.yml down

observability-logs:
	@docker compose -f docker-compose.observability.yml logs -f --tail=100

observability-overhead:
	@python3 scripts/measure_logging_overhead.py

audit-log-export:
	@python3 scripts/export_audit_logs.py $${ARGS}

test-report:
	@/bin/python3 ./scripts/report_test_matrix.py

test-gate:
	@/bin/python3 ./scripts/report_test_matrix.py --check-breadth

test-diff-gate:
	@/bin/python3 ./scripts/report_test_matrix.py --check-breadth --check-diff --base-ref $${BASE:-HEAD~1}

frontend-test-e2e:
	@cd frontend && PLAYWRIGHT_HTML_OPEN=never npm run test:e2e

frontend-test-e2e-firefox:
	@cd frontend && PLAYWRIGHT_HTML_OPEN=never npm run test:e2e:firefox

frontend-test-e2e-webkit:
	@cd frontend && PLAYWRIGHT_HTML_OPEN=never npm run test:e2e:webkit

frontend-test-e2e-matrix:
	@cd frontend && PLAYWRIGHT_HTML_OPEN=never npm run test:e2e:matrix

messenger-surface-report:
	@python3 ./scripts/build_messenger_surface_report.py

production-online-help:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} help

production-online-check:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} check-local

production-online-bootstrap:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} bootstrap-iran

production-online-nginx:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} configure-nginx

production-online-cert:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} issue-cert

production-online-build:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} build-release

production-online-sync:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} sync-project

production-online-deploy:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} deploy-iran

production-online-health:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} healthcheck

production-online-full:
	@bash ./scripts/production_deploy_online.sh --manifest $${MANIFEST:-./deploy/production/online.env} full

messenger-query-plans:
	@python3 ./scripts/report_messenger_query_plans.py

messenger-benchmark-prepare:
	@python3 ./scripts/prepare_messenger_benchmark_versions.py

messenger-benchmark-run:
	@cd frontend && npm run benchmark:messenger

messenger-benchmark-report:
	@python3 ./scripts/build_messenger_benchmark_report.py

messenger-benchmark-all: messenger-surface-report messenger-benchmark-prepare messenger-benchmark-run messenger-benchmark-report
