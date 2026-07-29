
# ==========================================
# Trading Bot — Two-Server Deployment
# ==========================================
# Foreign (Germany): Bot + Sync + API
# Iran:              API + Nginx + Frontend
# ==========================================

LOCAL_COMPOSE ?= $(shell if docker compose version >/dev/null 2>&1; then printf '%s' 'docker compose'; elif command -v docker-compose >/dev/null 2>&1; then printf '%s' 'docker-compose'; else printf '%s' 'docker compose'; fi)

.PHONY: help up deploy frontend iran foreign sync-recover sync-health sync-health-iran sync-health-sample sync-health-monitor-install audit-anchor-export audit-anchor-monitor-install audit-anchor-ship audit-anchor-ship-install metrics-targets deployment-surface-guard restore-default-commodities dev-admin create-superadmin create-admin create-user list-users show-user change-password force-password-change set-role set-status set-max-sessions reset-sessions unlock-login down logs logs-api logs-bot logs-jobs logs-follow metrics logs-iran restart restart-iran status observability-up observability-down observability-logs observability-overhead observability-readiness observability-gate audit-log-export test-report test-gate test-diff-gate stage9-infrastructure-gate stage9-runtime-evidence stage9-evidence-gate stage9-mutation-gate stage9-test-matrix frontend-test-e2e frontend-test-e2e-firefox frontend-test-e2e-webkit frontend-test-e2e-matrix messenger-surface-report messenger-query-plans production-read-path-query-plans production-read-path-attribution messenger-benchmark-prepare messenger-benchmark-run messenger-benchmark-report messenger-benchmark-all production-alerts production-alerts-monitor-install production-backup-foreign production-backup-iran production-backup-all production-recoverability-report production-recoverability-drill production-deployment-restart production-release-gate production-data-hygiene production-data-hygiene-iran production-benchmark-baseline production-benchmark-quick production-benchmark-targeted production-benchmark-full production-load-runner-bootstrap production-load-fixtures production-load-realistic production-load-sampler production-load-pool-matrix production-full-matrix-manifest production-full-matrix-run production-full-matrix-plan writer-witness-real-host-matrix-plan writer-witness-real-host-matrix-preflight writer-witness-real-host-scenario-plan writer-witness-real-host-scenario-approve writer-witness-real-host-scenario-run writer-witness-real-host-scenario-recover webapp-ir-dark-standby-check three-site-topology-contract-check production-release production-online-help production-online-check production-online-bootstrap production-online-nginx production-online-cert production-online-build production-online-sync production-online-ship-images production-online-load-images production-online-deploy production-online-inspect-shared production-online-seed-shared production-online-health

help:
	@echo ""
	@echo "🚀 Available commands:"
	@echo ""
	@echo "  make up/deploy/frontend/iran/foreign - Retired legacy two-site deployment routes"
	@echo "  make sync-recover - Retired legacy two-site sync mutation route"
	@echo "  make sync-health - Show local/foreign sync backlog and lag"
	@echo "  make sync-health-iran - Show Iran sync backlog and lag through SSH"
	@echo "  make sync-health-sample - Sample local and Iran sync health from the foreign host"
	@echo "  make sync-health-monitor-install - Install the 1-minute sync health sampler on the foreign host"
	@echo "  make audit-anchor-export - Export the current durable audit head as a compact anchor"
	@echo "  make audit-anchor-monitor-install - Install the 5-minute audit anchor exporter timer on the host"
	@echo "  make audit-anchor-ship - Ship the latest compact audit anchor line to a restricted sink"
	@echo "  make audit-anchor-ship-install - Install the 10-minute audit anchor shipper timer on the host"
	@echo "  make metrics-targets - Render the explicit production metrics surface contract"
	@echo "  make deployment-surface-guard - Fail if production IP/domain identities leak into runtime/entrypoint code"
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
	@echo "  make down        - Retired legacy two-site container mutation route"
	@echo "  make logs        - Foreign server logs"
	@echo "  make logs-api    - Follow API container logs"
	@echo "  make logs-bot    - Follow bot container logs"
	@echo "  make logs-jobs   - Follow app/bot logs where background jobs emit events"
	@echo "  make logs-follow - Follow all local runtime logs with a bounded tail"
	@echo "  make metrics     - Print Prometheus metrics from the local API"
	@echo "  make logs-iran   - Iran server logs"
	@echo "  make restart     - Retired legacy two-site container mutation route"
	@echo "  make restart-iran - Retired legacy two-site container mutation route"
	@echo "  make status      - Show status of both servers"
	@echo "  make observability-up   - Start local Loki/Promtail/Grafana stack"
	@echo "  make observability-down - Stop local observability stack"
	@echo "  make observability-logs - Follow observability stack logs"
	@echo "  make observability-overhead - Measure structured logging overhead"
	@echo "  make observability-readiness - Run the production observability readiness report"
	@echo "  make observability-gate - Run the focused observability regression gate"
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
	@echo "  make production-read-path-query-plans - Run EXPLAIN ANALYZE on Stage L/RPL2 hot read surfaces"
	@echo "  make production-read-path-attribution - Compare endpoint-family latency from Stage L/RPL artifacts"
	@echo "  make messenger-benchmark-prepare - Prepare reproducible old/current benchmark builds"
	@echo "  make messenger-benchmark-run - Run the official Messenger performance benchmark"
	@echo "  make messenger-benchmark-report - Build comparison-summary and surface-status artifacts"
	@echo "  make messenger-benchmark-all - Run the full benchmark prep + measure + report pipeline"
	@echo "  make production-benchmark-baseline - Capture the Stage P0 production optimization baseline"
	@echo "  make production-benchmark-quick - Run the short full-product production benchmark smoke"
	@echo "  make production-benchmark-targeted PROFILE=<name> - Run one production benchmark profile"
	@echo "  make production-benchmark-full - Run the full production benchmark harness"
	@echo "  make production-load-runner-bootstrap LOAD_RUNNER_HOST=user@host - Bootstrap the Stage L1 k6 load-runner"
	@echo "  make production-load-fixtures LOAD_RUNNER_HOST=user@host - Run Stage L2 synthetic fixture/auth-pool setup or cleanup"
	@echo "  make production-load-realistic ARGS='--dry-run' - Run/list the Stage L3 realistic k6 harness"
	@echo "  make production-load-sampler ARGS='--dry-run --json' - Validate/run the Stage L4 runtime sampler"
	@echo "  make production-full-matrix-manifest ARGS='--prefix PFM_...' - Build the production full-matrix scenario manifest"
	@echo "  make production-full-matrix-run ARGS='--manifest /tmp/...json' - Build the manifest-driven production matrix run plan"
	@echo "  make production-full-matrix-plan ARGS='--prefix PFM_...' - Render the guarded production full-matrix command plan"
	@echo "  make writer-witness-real-host-matrix-plan - Render the read-only dark-Witness real-host matrix plan"
	@echo "  make writer-witness-real-host-matrix-preflight - Execute all read-only entry gates before that matrix"
	@echo "  make writer-witness-real-host-scenario-plan ARGS='--scenario RH-001 --expected-commit SHA' - Render one executable scenario contract"
	@echo "  make writer-witness-real-host-scenario-approve ARGS='...' - Bind observer/incident approval to one preflight and scenario"
	@echo "  make writer-witness-real-host-scenario-run ARGS='...' - Execute exactly one confirmed dark-Witness scenario"
	@echo "  make writer-witness-real-host-scenario-recover ARGS='--campaign-journal ...' - Reconcile one interrupted dirty campaign"
	@echo "  make webapp-ir-dark-standby-check MANIFEST=/secure/path - Validate the data-only WA-IR manifest"
	@echo "  make production-backup-foreign - Create an operational backup on the foreign host"
	@echo "  make production-backup-iran    - Create an operational backup on the Iran host"
	@echo "  make production-backup-all     - Create operational backups on both hosts"
	@echo "  make production-alerts         - Evaluate DB/Redis/sync/disk/backup alert thresholds"
	@echo "  make production-alerts-monitor-install - Install the 5-minute production alert sampler"
	@echo "  make production-recoverability-report - Run live recoverability health/sync checks"
	@echo "  make production-recoverability-drill  - Create Iran backup and smoke-restore DB in a temporary container"
	@echo "  make production-release       - Retired legacy live route; use the three-site production-shadow campaign path"
	@echo "  make production-deployment-restart - Retired legacy two-site mutation route"
	@echo "  make production-release-gate  - Retired legacy two-site release gate"
	@echo "  make production-data-hygiene  - Run read-only dev/test artifact guard on the foreign DB"
	@echo "  make production-data-hygiene-iran - Run read-only dev/test artifact guard on the Iran DB"
	@echo "  make production-online-help   - Show the production release helper usage"
	@echo "  make production-online-<command> - Retired; all legacy two-site commands are blocked"
	@echo ""

# --- Deploy Commands ---

up:
	@printf '%s\n' 'Legacy two-site deployment command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

deploy:
	@printf '%s\n' 'Legacy two-site deployment command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

frontend:
	@printf '%s\n' 'Legacy two-site deployment command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

iran:
	@printf '%s\n' 'Legacy two-site deployment command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

foreign:
	@printf '%s\n' 'Legacy two-site deployment command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

sync-recover:
	@printf '%s\n' 'Legacy two-site sync recovery is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

sync-health:
	@printf '%s\n' 'Legacy two-site sync health contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

sync-health-iran:
	@printf '%s\n' 'Legacy two-site sync health contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

sync-health-sample:
	@printf '%s\n' 'Legacy two-site sync health contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

sync-health-monitor-install:
	@printf '%s\n' 'Legacy two-site sync health contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

audit-anchor-export:
	@python3 scripts/export_audit_anchor.py $${ARGS}

audit-anchor-monitor-install:
	@chmod +x ./scripts/install_audit_anchor_timer.sh
	@./scripts/install_audit_anchor_timer.sh

audit-anchor-ship:
	@python3 scripts/ship_audit_anchor.py $${ARGS}

audit-anchor-ship-install:
	@chmod +x ./scripts/install_audit_anchor_shipper.sh
	@./scripts/install_audit_anchor_shipper.sh

metrics-targets:
	@python3 scripts/render_metrics_targets.py $${ARGS}

deployment-surface-guard:
	@python3 scripts/check_deployment_surface_guard.py

restore-default-commodities:
	@$(LOCAL_COMPOSE) run --rm migration python scripts/restore_default_commodities.py

dev-admin:
	@$(LOCAL_COMPOSE) exec -T app python scripts/dev_admin.py $${ARGS}

create-superadmin:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py create-superadmin

create-admin:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py create-admin

create-user:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py create-user

list-users:
	@$(LOCAL_COMPOSE) exec -T app python scripts/dev_admin.py list-users $${ARGS}

show-user:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py show-user

change-password:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py change-password

force-password-change:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py force-password-change

set-role:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py set-role

set-status:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py set-status

set-max-sessions:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py set-max-sessions

reset-sessions:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py reset-sessions

unlock-login:
	@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py unlock-login

# --- Management Commands ---

down:
	@printf '%s\n' 'Legacy two-site container mutation is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

logs:
	@printf '%s\n' 'Legacy two-site runtime contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

logs-api:
	@printf '%s\n' 'Legacy two-site runtime contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

logs-bot:
	@printf '%s\n' 'Legacy two-site runtime contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

logs-jobs:
	@printf '%s\n' 'Legacy two-site runtime contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

logs-follow:
	@printf '%s\n' 'Legacy two-site runtime contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

metrics:
	@printf '%s\n' 'Legacy two-site runtime contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

logs-iran:
	@printf '%s\n' 'Legacy two-site runtime contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

restart:
	@printf '%s\n' 'Legacy two-site container mutation is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

restart-iran:
	@printf '%s\n' 'Legacy two-site container mutation is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

status:
	@printf '%s\n' 'Legacy two-site runtime contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

observability-up:
	@docker compose -f docker-compose.observability.yml up -d

observability-down:
	@docker compose -f docker-compose.observability.yml down

observability-logs:
	@docker compose -f docker-compose.observability.yml logs -f --tail=100

observability-overhead:
	@python3 scripts/measure_logging_overhead.py

observability-readiness:
	@python3 scripts/report_observability_readiness.py $${ARGS}

observability-gate:
	@python3 scripts/run_observability_gate.py $${ARGS}

audit-log-export:
	@python3 scripts/export_audit_logs.py $${ARGS}

test-report:
	@/bin/python3 ./scripts/report_test_matrix.py

test-gate:
	@/bin/python3 ./scripts/report_test_matrix.py --check-breadth

test-diff-gate:
	@/bin/python3 ./scripts/report_test_matrix.py --check-breadth --check-diff --base-ref $${BASE:-HEAD~1}

stage9-infrastructure-gate:
	@PYTHONPATH="$(CURDIR)/tmp/stage9-site-packages:$${PYTHONPATH}" python3 -m unittest tests.test_stage9_async_checkpoints tests.test_stage9_changed_branch_closure tests.test_stage9_ci_contract tests.test_stage9_diff_coverage tests.test_stage9_mutation_contracts tests.test_stage9_mutation_runner tests.test_stage9_redis_runner tests.test_stage9_runtime_evidence tests.test_stage9_test_matrix tests.test_stage9_traceability tests.test_guarded_scratch_alembic tests.test_registration_identity_property tests.test_registration_stateful_fuzz tests.test_registration_scratch_suite
	@PYTHONPATH="$(CURDIR)/tmp/stage9-site-packages:$${PYTHONPATH}" python3 scripts/build_stage9_traceability.py --validate-only
	@PYTHONPATH="$(CURDIR)/tmp/stage9-site-packages:$${PYTHONPATH}" python3 scripts/run_stage9_test_matrix.py --preflight-only

stage9-evidence-gate:
	@PYTHONPATH="$(CURDIR)/tmp/stage9-site-packages:$${PYTHONPATH}" python3 scripts/build_stage9_traceability.py --require-runtime-evidence --results $${RESULTS:?required} --backend-coverage $${BACKEND_COVERAGE:?required} --frontend-coverage $${FRONTEND_COVERAGE:?required} --mutation $${MUTATION:?required} --output $${OUTPUT:-tmp/stage9-traceability.json}

stage9-runtime-evidence:
	@PYTHONPATH="$(CURDIR)/tmp/stage9-site-packages:$${PYTHONPATH}" python3 scripts/build_stage9_runtime_evidence.py --matrix $${MATRIX:?required} --postgres-log $${POSTGRES_LOG:?required} --redis-log $${REDIS_LOG:?required} --output $${OUTPUT:-tmp/stage9-runtime-evidence.json}

stage9-mutation-gate:
	@PYTHONPATH="$(CURDIR)/tmp/stage9-site-packages:$${PYTHONPATH}" python3 scripts/run_stage9_mutation_gate.py --fresh --max-children $${MUTATION_WORKERS:-2}
	@PYTHONPATH="$(CURDIR)/tmp/stage9-site-packages:$${PYTHONPATH}" python3 scripts/check_stage9_mutation_evidence.py --evidence tmp/stage9-mutation-evidence.json

stage9-test-matrix:
	@PYTHONPATH="$(CURDIR)/tmp/stage9-site-packages:$${PYTHONPATH}" python3 scripts/run_stage9_test_matrix.py $${ARGS}

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

production-release:
	@printf '%s\n' 'Legacy two-site production release is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-deployment-restart:
	@printf '%s\n' 'Legacy two-site deployment/restart benchmark is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-release-gate:
	@printf '%s\n' 'Legacy two-site final release gate is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-data-hygiene:
	@printf '%s\n' 'Legacy two-site production data contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-data-hygiene-iran:
	@printf '%s\n' 'Legacy two-site production data contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-backup-foreign:
	@printf '%s\n' 'Legacy two-site production backup is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-backup-iran:
	@printf '%s\n' 'Legacy two-site production backup is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-backup-all:
	@printf '%s\n' 'Legacy two-site production backup is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-alerts:
	@printf '%s\n' 'Legacy two-site production alert contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-alerts-monitor-install:
	@printf '%s\n' 'Legacy two-site production alert contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-recoverability-report:
	@printf '%s\n' 'Legacy two-site production recoverability contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-recoverability-drill:
	@printf '%s\n' 'Legacy two-site production recoverability contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-help:
	@printf '%s\n' 'Legacy two-site production helper is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-check:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-bootstrap:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-nginx:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-cert:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-build:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-sync:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-ship-images:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-load-images:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-deploy:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-inspect-shared:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-seed-shared:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-online-health:
	@printf '%s\n' 'Legacy two-site production command is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

messenger-query-plans:
	@python3 ./scripts/report_messenger_query_plans.py

production-read-path-query-plans:
	@printf '%s\n' 'Legacy two-site production read-path contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-read-path-attribution:
	@printf '%s\n' 'Legacy two-site production read-path contact is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

messenger-benchmark-prepare:
	@python3 ./scripts/prepare_messenger_benchmark_versions.py

messenger-benchmark-run:
	@cd frontend && npm run benchmark:messenger

messenger-benchmark-report:
	@python3 ./scripts/build_messenger_benchmark_report.py

messenger-benchmark-all: messenger-surface-report messenger-benchmark-prepare messenger-benchmark-run messenger-benchmark-report

production-benchmark-baseline:
	@printf '%s\n' 'Legacy two-site production benchmark is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-benchmark-quick:
	@printf '%s\n' 'Legacy two-site production benchmark is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-benchmark-targeted:
	@printf '%s\n' 'Legacy two-site production benchmark is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-benchmark-full:
	@printf '%s\n' 'Legacy two-site production benchmark is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-load-runner-bootstrap:
	@printf '%s\n' 'Legacy two-site production load route is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-load-fixtures:
	@printf '%s\n' 'Legacy two-site production load route is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-load-realistic:
	@printf '%s\n' 'Legacy two-site production load route is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-load-sampler:
	@printf '%s\n' 'Legacy two-site production load route is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-load-pool-matrix:
	@printf '%s\n' 'Legacy two-site production load route is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-full-matrix-manifest:
	@python3 ./scripts/build_production_full_matrix_manifest.py $${ARGS}

production-full-matrix-run:
	@printf '%s\n' 'Legacy two-site Full Matrix runner is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

production-full-matrix-plan:
	@printf '%s\n' 'Legacy two-site Full Matrix planner is retired and hard-disabled. Use the dedicated three-site production-shadow campaign path.' >&2; exit 2

writer-witness-real-host-matrix-plan:
	@./scripts/run_writer_witness_matrix_controller.sh preflight --mode plan $${ARGS}

writer-witness-real-host-matrix-preflight:
	@./scripts/run_writer_witness_matrix_controller.sh preflight --mode preflight $${ARGS}

writer-witness-real-host-scenario-plan:
	@./scripts/run_writer_witness_matrix_controller.sh scenario --mode plan $${ARGS}

writer-witness-real-host-scenario-approve:
	@./scripts/run_writer_witness_matrix_controller.sh scenario --mode approve $${ARGS}

writer-witness-real-host-scenario-run:
	@./scripts/run_writer_witness_matrix_controller.sh scenario --mode execute $${ARGS}

writer-witness-real-host-scenario-recover:
	@./scripts/run_writer_witness_matrix_controller.sh scenario --mode recover $${ARGS}

webapp-ir-dark-standby-check:
	@python3 ./scripts/verify_webapp_ir_dark_standby_manifest.py --manifest $${MANIFEST:?set MANIFEST to the private dark-standby env} --check-files --json

three-site-topology-contract-check:
	@python3 ./scripts/verify_three_site_topology_contract.py --json
