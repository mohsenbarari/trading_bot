# Observability Roadmap

Last updated: 2026-06-08

This document defines the phased roadmap for adding a complete logging and monitoring system to the project. The goal is not only to print more logs, but to make API, bot, workers, realtime flows, security events, and business-critical actions traceable without leaking passwords, tokens, OTP codes, cookies, or personal data.

## Current State

- API and bot logging are currently basic and scattered.
- `main.py`, `run_bot.py`, and `core/sync_worker.py` still rely on plain `logging.basicConfig(level=logging.INFO)`.
- Many modules already use `logging.getLogger(__name__)`, but there is no unified formatter, request correlation, redaction policy, metrics endpoint, alerting, or searchable log stack.
- Docker healthchecks exist for core services, but there is no production-grade monitoring layer.
- The remote `loger` / logging branch should be kept as a reference for now. It must not be merged directly until its foundation is complete, integrated, tested, and security-reviewed.

## Non-Negotiable Security Rules

1. Never log raw passwords, refresh tokens, access tokens, Telegram bot tokens, API keys, cookies, OTP codes, authorization headers, or password reset secrets.
2. Never log raw request or response bodies for auth, sessions, OTP, password, token refresh, file upload, media upload, or recovery endpoints.
3. Mobile numbers and account identifiers must be masked or logged only when operationally necessary.
4. Audit logs must record the fact of a sensitive action, not the secret values involved in that action.
5. Every new logging sink must pass redaction tests before production deploy.
6. Log labels and metric labels must avoid high-cardinality raw values such as free-form message text, file names, tokens, full URLs with query strings, or arbitrary user input.
7. Error reporting integrations must scrub local variables, headers, cookies, and request bodies by default.

## Stage 0: Discovery and Baseline

Purpose: define what must be observable before implementing the technical foundation.

Scope:
- Inventory runtime services: API, bot, Redis listeners, sync worker, market schedule loop, session expiry loop, offer expiry loop, user account status loop, and deployment scripts.
- Inventory sensitive user flows: login, OTP, session recovery, password reset, role change, customer/accountant management, trade lifecycle, chat upload/download, websocket connect/disconnect, and admin actions.
- Define log classes: application logs, access logs, audit logs, security logs, job logs, realtime logs, integration logs, and deployment logs.
- Define severity rules: debug, info, warning, error, critical.
- Define data retention expectations for each log class.

Deliverables:
- Event taxonomy table.
- Severity and retention matrix.
- Initial list of dashboards and alerts.

Acceptance:
- Every critical flow has an assigned log class and required context fields.
- Every sensitive field has a masking or omission rule.

### Runtime Service Inventory

| Runtime surface | Entrypoint / owner | Primary risks | Required observability |
| --- | --- | --- | --- |
| API service | `main.py` / FastAPI routers | request failures, auth/session regressions, slow DB paths, upload failures, websocket auth failures | access logs, request ids, error logs, route latency metrics, security/audit events |
| Telegram bot | `run_bot.py`, `bot/handlers/*` | polling failures, handler exceptions, Telegram API failures, accidental secret/message logging | service logs, handler context, Telegram failure metrics, redacted update metadata |
| Realtime websocket | `api/routers/realtime.py`, Redis pub/sub | dropped connections, listener loop failures, publish failures, auth token failures | realtime logs, active connection metrics, publish failure counters, disconnect reason taxonomy |
| Sync worker | `core/sync_worker.py`, `core/events.py`, `api/routers/sync.py` | cross-server drift, invalid signatures, retry storms, leaked sync API keys | job logs, integration logs, HMAC failure security logs, retry/backoff metrics |
| Offer expiry loop | `core/offer_expiry.py` | missed expirations, repeated DB errors, Telegram notification failures | job run logs, counts, duration, failure metrics |
| Market schedule loop | `core/market_schedule_loop.py` | wrong market state, missed transitions, timezone errors | job run logs, transition audit logs, duration/failure metrics |
| Session expiry loop | `core/session_expiry.py` | stale sessions, missed revocation, user lockout issues | job run logs, revoked-session counts, security event metrics |
| User account status loop | `core/user_account_status_loop.py` | account status drift, notification errors | job run logs, status-change audit/security logs |
| Redis/cache helpers | `core/redis.py`, `core/cache.py` | connection loss, stale data, pub/sub interruption | reconnect logs, cache failure metrics, degraded-mode warnings |
| Database layer | `core/db.py`, SQLAlchemy sessions | connection failures, transaction rollbacks, slow queries | error logs, later slow-query metrics, transaction failure counters |
| Deployment scripts | `Makefile`, deploy scripts, Docker Compose | failed build/deploy, unhealthy containers, disk pressure | deployment logs, healthcheck events, disk/memory alerts |

### Event Taxonomy

| Event family | Examples | Log class | Default severity | Required fields | Sensitive-data policy |
| --- | --- | --- | --- | --- | --- |
| API request lifecycle | request completed, request failed | access/app | info/error | `request_id`, `method`, `path`, `status_code`, `duration_ms`, `service` | no raw body, no query secrets, no auth headers |
| Authentication | login success/failure, OTP request, token refresh | security/app | info/warning | `request_id`, `actor_id` when known, `result`, `reason_code`, `client_ip` | never log OTP, password, access/refresh token, cookie |
| Session management | session create, revoke, reset, recovery approve/reject | security/audit | info/warning | `request_id`, `actor_id`, `target_id`, `session_id`, `result` | log session id only if opaque/internal; never log refresh token |
| User/admin management | create user, set role/status, force password change | audit/security | info/warning | `actor_id`, `actor_role`, `target_id`, `action`, `result` | no password values; summarize changed fields only |
| Customer/accountant management | link/unlink, status change, limit update | audit/app | info/warning | `actor_id`, `target_id`, `relation_id`, `action`, `result` | mask mobile/name where not operationally required |
| Trade lifecycle | offer create, trade execute/reject/cancel, sync trade | audit/app/integration | info/error | `actor_id`, `trade_id`, `offer_id`, `commodity_id`, `action`, `result` | no free-form message text; quantities/status only |
| Chat message flow | send message, upload finalize, download failure, moderation | app/audit | info/warning/error | `request_id`, `actor_id`, `room_id`, `message_id`, `media_type`, `result` | never log message body, captions, filenames with PII, media URLs/tokens |
| Realtime/websocket | connect, disconnect, publish, listener error | realtime/app | info/warning/error | `user_id`, `connection_id`, `event`, `reason_code`, `duration_ms` | never log websocket token or raw event payload when sensitive |
| Sync/integration | outbound push, inbound receive, signature failure, retry | integration/security | info/warning/error | `sync_item_id`, `table`, `operation`, `target_server`, `result` | never log sync API key/signature; payload summaries only |
| Background job | start, finish, skip, retry, fail | job/app | info/warning/error | `job_name`, `run_id`, `duration_ms`, `success_count`, `error_count` | no raw records; counts and ids only |
| Deployment/runtime | service start, service stop, healthcheck, config missing | deployment/app | info/warning/error | `service`, `environment`, `server_mode`, `release_sha`, `result` | never log `.env` values or full config dumps |
| Security anomaly | invalid token, invalid signature, brute-force hints, forbidden access | security | warning/error | `request_id`, `actor_id` when known, `client_ip`, `reason_code`, `path` | no supplied secrets; sanitize identifiers |

### Log Class Definitions

| Log class | Purpose | Sink phase | Retention target | Access level |
| --- | --- | --- | --- | --- |
| `app` | normal application behavior and operational errors | stdout JSON, later Loki | 14-30 days | developers/operators |
| `access` | sanitized API request completion/failure | stdout JSON, later Loki/metrics | 14-30 days | developers/operators |
| `security` | auth/session/security anomalies | stdout JSON, later separate Loki stream | 90-180 days | restricted admins/operators |
| `audit` | business/security-sensitive user actions | stdout JSON first, later dedicated audit sink | 180-365 days | restricted admins only |
| `job` | recurring workers and background loops | stdout JSON, later Loki/metrics | 30-60 days | developers/operators |
| `realtime` | websocket and Redis pub/sub behavior | stdout JSON, later Loki/metrics | 14-30 days | developers/operators |
| `integration` | cross-server sync and external providers | stdout JSON, later Loki/metrics | 30-90 days | developers/operators |
| `deployment` | builds, migrations, healthchecks, deploy events | deploy logs, later Loki | 30-90 days | operators |

### Severity Matrix

| Severity | Use for | Do not use for |
| --- | --- | --- |
| `debug` | local-only detail, temporary diagnostics behind an explicit flag | production secrets, raw payloads, high-volume default logs |
| `info` | expected successful lifecycle events and summarized business operations | repeated tight-loop noise |
| `warning` | expected but undesirable outcomes: invalid login, forbidden action, retry, degraded dependency | unexpected exceptions with stack traces |
| `error` | failed requests/jobs/integrations that need investigation but do not stop the process | normal validation errors |
| `critical` | service cannot continue, data integrity risk, security incident requiring immediate action | ordinary 5xx without systemic impact |

### Sensitive Data Handling Matrix

| Data type | Logging policy | Allowed replacement |
| --- | --- | --- |
| Passwords and password hashes | never log | `[REDACTED]` |
| Access/refresh/JWT tokens | never log | `[REDACTED]` / `[REDACTED_JWT]` |
| OTP and recovery codes | never log | `[REDACTED]` |
| Cookies and authorization headers | never log | `[REDACTED]` |
| Telegram bot token | never log | `[REDACTED]` |
| Sync API key and HMAC signature | never log | `[REDACTED]` |
| Mobile numbers | mask unless explicitly needed for an audit export | `0912****789` |
| Account names and display names | allowed only when operationally necessary; prefer ids | user id, role, masked display |
| Chat message text and captions | never log in app/access/realtime logs | message id, room id, media type |
| File names, media URLs, signed URLs | avoid by default; signed URLs never log | file id, media type, size bucket |
| Request/response body | never log for sensitive endpoints; summarize only for safe internal events | route, status, reason code |

### Baseline Dashboard and Alert Candidates

| Area | Dashboard panels | Initial alerts |
| --- | --- | --- |
| API | request rate, p95 latency, 4xx/5xx rate, slow routes | service down, 5xx spike, p95 latency spike |
| Auth/sessions | login success/failure, OTP failure rate, session revoke/recovery counts | auth failure spike, OTP provider failure, recovery abuse hint |
| Bot | polling status, handler failures, Telegram API failures | bot down, Telegram API failure spike |
| Realtime | active websocket connections, disconnect reasons, publish failures | websocket publish failure spike, Redis listener repeated failure |
| Jobs | run duration, success/failure count, missed cycles | repeated job failure, missed expiry loop |
| Sync | outbound queue length, retries, signature failures | retry storm, invalid signature spike, peer unreachable |
| Chat/upload | send failure, upload finalize failure, download failure | upload failure spike, media storage failure |
| Infra | CPU, memory, disk, DB/Redis health | disk high, memory pressure, DB/Redis unhealthy |

### Stage 0 Completion Criteria

- Runtime surfaces are inventoried.
- Critical event families are mapped to log classes.
- Severity usage is defined.
- Retention and access expectations are defined by log class.
- Sensitive fields have explicit omit/mask/replacement rules.
- Dashboard and alert candidates are ready for metrics and collection stages.

## Stage 1: Central Logging Foundation

Purpose: replace scattered logging setup with one safe, predictable logging foundation.

Scope:
- Add `core/request_context.py` based on `contextvars`.
- Add `core/log_redaction.py` for recursive redaction.
- Add `core/logging_config.py` for structured logging setup.
- Add optional `core/log_schemas.py` for standard event names and allowed extra fields.
- Support two formats:
  - JSON for production and Docker logs.
  - text for local development.
- Replace direct `logging.basicConfig(...)` setup in API, bot, and worker entrypoints.
- Keep stdout as the default sink so Docker can collect logs.

Required context fields:
- `service`
- `environment`
- `server_mode`
- `release_sha`
- `request_id`
- `actor_id`
- `actor_role`
- `session_id`
- `path`
- `method`
- `job_name`
- `run_id`

Security requirements:
- Redact sensitive keys recursively in dict/list/tuple/set payloads.
- Redact token-like strings inside messages.
- Redact OTP/code patterns.
- Mask mobile numbers.
- Add tests for every redaction class.

Acceptance:
- API, bot, and workers can emit structured logs with the same formatter.
- Redaction tests prove secrets do not appear in formatted output.
- Existing log calls keep working without requiring a full rewrite.

## Stage 2: API Request Logging and Correlation

Purpose: every API request must be traceable from entrypoint to error.

Scope:
- Add FastAPI middleware for request context.
- Accept incoming `X-Request-ID` or generate a UUID.
- Return `X-Request-ID` in responses.
- Log request completion with method, path, status code, duration, client IP, and actor context when available.
- Log unexpected request failures with structured exception data.
- Suppress or sanitize logs for sensitive routes.

Sensitive route policy:
- Auth, sessions, recovery, OTP, password, token refresh, and upload endpoints must never log raw body, query secrets, cookies, or authorization headers.
- Query strings must be omitted or sanitized by default.

Acceptance:
- A failing API request can be traced by `request_id`.
- Business exceptions are not incorrectly logged as critical system failures.
- No secret is emitted in access or error logs.

## Stage 3: Bot and Background Worker Logging

Purpose: bot handlers and loops must produce structured, searchable operational logs.

Scope:
- Wire central logging into `run_bot.py`.
- Wire central logging into background worker entrypoints.
- Add job run context:
  - `job_name`
  - `run_id`
  - `iteration`
  - `target_count`
  - `success_count`
  - `error_count`
  - `duration_ms`
- Standardize start, finish, retry, skip, and failure logs.
- Add rate-limited logging for repeated loop failures.

Security requirements:
- Never log Telegram tokens.
- Never log raw message text when it may contain OTP, credentials, customer personal data, or trade secrets.
- For bot user identity, prefer stable IDs and masked display values.

Acceptance:
- Each recurring job has a clear start/finish/failure trail.
- Bot failures can be connected to user/action context without leaking message content.

## Stage 4: Audit Logging

Status: Completed on 2026-06-08.

Purpose: security-sensitive and business-critical actions must have an independent audit trail.

Scope:
- Add `core/audit_logger.py`.
- Define a strict audit event schema:
  - `event`
  - `actor_id`
  - `actor_role`
  - `target_type`
  - `target_id`
  - `action`
  - `result`
  - `request_id`
  - `client_ip`
  - `before_summary`
  - `after_summary`
  - `reason`
- Cover key actions:
  - user creation/status/role changes
  - password changes and forced password changes
  - session revoke/reset/unlock
  - customer/accountant link and unlink
  - block/unblock
  - trade create/approve/reject/execute/cancel
  - admin broadcasts
  - channel/group permission changes
  - sensitive chat moderation actions

Security requirements:
- Audit logs must record changed fields, not raw secret values.
- `before_summary` and `after_summary` must use safe summaries for PII and secrets.
- Audit logs must be append-only in behavior even if the first implementation writes to stdout.

Acceptance:
- An admin/security incident can be reconstructed from audit logs.
- Audit logs are clearly distinguishable from normal app logs.

Completion notes:
- Added `core/audit_logger.py` with a strict stdout JSON audit schema, request-context inheritance, result normalization, and recursive redaction for summaries and reasons.
- Added unit coverage in `tests/test_audit_logger.py` for schema fields, actor/request context, result normalization, and secret redaction.
- Instrumented successful audit events for block/unblock, user update/delete/session reset, customer/accountant link/update/unlink/session termination, direct session termination/logout-all, login-request approve/reject, and admin broadcasts.
- Kept audit payloads summary-only: no passwords, tokens, OTPs, raw broadcast content, raw request bodies, mobile numbers, message text, or file names are emitted.

## Stage 5: Metrics Foundation

Status: Completed on 2026-06-08.

Purpose: production health must be measurable with numeric time-series data, not inferred only from logs.

Scope:
- Add a `/metrics` endpoint for Prometheus.
- Add metrics for API:
  - request count
  - request latency
  - 4xx/5xx count
  - active websocket connections
  - websocket publish failures
- Add metrics for bot:
  - handler count
  - handler latency
  - Telegram API failures
- Add metrics for jobs:
  - run count
  - duration
  - failures
- Add business metrics:
  - login success/failure
  - OTP send success/failure
  - trade actions
  - chat send/upload/download failures

Security requirements:
- Metric labels must be low-cardinality.
- Do not use account names, message text, phone numbers, file names, or raw paths with dynamic IDs as metric labels.

Acceptance:
- Prometheus can scrape metrics.
- Metrics remain bounded and do not create cardinality explosions.

Completion notes:
- Added `core/metrics.py`, a dependency-free Prometheus text-format registry for counters, gauges, and fixed-bucket histograms, backed by a shared SQLite file under `/tmp` so API/job counters aggregate across the current multi-worker Uvicorn setup.
- Added `/metrics` in `main.py` with Prometheus text output and process uptime.
- Instrumented API request count, duration, and 4xx/5xx counts through the request logging middleware using route templates/normalized paths instead of raw URLs.
- Instrumented active websocket connections and websocket/Redis publish failures.
- Instrumented bot update count/duration, job run count/duration/failure, and audit/business action counts.
- Added `tests/test_metrics.py` for label normalization, text rendering, bot/job/audit metrics, and secret-free output.
- Kept labels low-cardinality: method, route template, status class, event type, job name, result, and fixed action names only.

## Stage 6: Log Collection and Search

Status: Completed on 2026-06-08.

Purpose: logs must be searchable across app, bot, workers, and deployments.

Recommended stack:
- Loki for log storage.
- Promtail or Docker log driver for collection.
- Grafana for search and dashboards.

Scope:
- Keep application logs on stdout.
- Add service labels in container logs.
- Ensure JSON logs can be parsed by the collector.
- Configure retention based on log class.
- Add search examples:
  - by `request_id`
  - by `actor_id`
  - by `event`
  - by `service`
  - by `status_code`

Security requirements:
- Restrict dashboard/log access.
- Do not expose log UI publicly without authentication.
- Apply retention limits for PII-bearing operational logs.

Acceptance:
- A production error can be found by request id or timestamp.
- API, bot, and worker logs can be filtered independently.

Completion notes:
- Added `docker-compose.observability.yml` as an optional local-only Loki, Promtail, and Grafana stack.
- Added Loki retention/storage config in `observability/loki/loki-config.yml`.
- Added Promtail Docker log discovery in `observability/promtail/promtail-config.yml`, restricted to `trading_bot_*` containers and low-cardinality labels.
- Added Grafana Loki datasource provisioning in `observability/grafana/provisioning/datasources/datasources.yml`.
- Added `docs/OBSERVABILITY_LOG_SEARCH.md` with run commands, security notes, label policy, and LogQL search examples for `request_id`, `actor_id`, `event`, `service`, `status_code`, jobs, bot logs, and audit logs.
- Added `make observability-up`, `make observability-down`, and `make observability-logs`.

## Stage 7: Dashboards

Status: Completed on 2026-06-08.

Purpose: operations must be visible without manual log inspection.

Dashboards:
- API overview: latency, throughput, status codes, top failing routes.
- Bot overview: handler rate, Telegram failures, active flow errors.
- Realtime overview: websocket connections, disconnects, publish failures, Redis listener errors.
- Auth and sessions: login success/failure, OTP failures, recovery actions, session revokes.
- Chat and upload: send failures, upload finalize failures, media failures.
- Jobs overview: job runs, durations, failures, missed cycles.
- Infrastructure: container health, CPU, memory, disk, DB, Redis.

Acceptance:
- Each critical runtime surface has a dashboard.
- Dashboards use stable labels and do not expose secrets.

Completion notes:
- Added Grafana dashboard provisioning in `observability/grafana/provisioning/dashboards/dashboards.yml`.
- Added dashboard JSON files under `observability/grafana/dashboards/` for API overview, runtime/jobs/realtime, security/audit, business/chat/upload, and infrastructure log health.
- Wired dashboards into `docker-compose.observability.yml` through read-only provisioning volumes.
- Added `docs/OBSERVABILITY_DASHBOARDS.md` with dashboard coverage, local loading instructions, label policy, sensitive-data rules, and current log-based infrastructure limitations.
- Kept dashboards Loki/log based for this stage so no new production runtime dependency or exporter is required.

## Stage 8: Alerting

Status: Completed on 2026-06-08.

Purpose: actionable issues must be reported before manual user complaints.

Initial alerts:
- API service down.
- Bot service down.
- DB or Redis unhealthy.
- 5xx spike.
- auth failure spike.
- OTP provider failure spike.
- websocket publish failure spike.
- background job repeated failures.
- disk usage high.
- memory pressure.
- deployment healthcheck failure.

Alert channels:
- Telegram admin channel.
- Optional email/webhook.

Security requirements:
- Alerts must not include tokens, OTPs, passwords, cookies, or raw request bodies.
- Alerts should include request ids and event ids instead of sensitive payloads.

Acceptance:
- Alerts are actionable and not noisy.
- Every alert has a runbook link or clear remediation hint.

Completion notes:
- Added Grafana alert provisioning under `observability/grafana/provisioning/alerting/`.
- Added an inert local webhook contact point and notification policy so cloned repos do not accidentally send alerts.
- Added Loki-backed alert rules for API log silence, bot log silence, API 5xx spike, auth/session failure spike, audit failure/denied events, repeated job failures, realtime publish/listener failures, and chat upload/media failures.
- Added `docs/OBSERVABILITY_ALERTS.md` with receiver setup guidance, per-alert first checks, LogQL examples, and alert payload security rules.
- Deferred disk/memory/CPU/DB-pool/Redis-memory alerts to Stage 11 because they require node/container/exporter metrics, not only application logs.

## Stage 9: Error Tracking

Status: Completed on 2026-06-08.

Purpose: unexpected exceptions must be grouped and traceable across releases.

Options:
- Sentry.
- Self-hosted equivalent.
- OpenTelemetry-compatible backend.

Scope:
- Tag errors with service, environment, release, request id, and actor role.
- Scrub headers, cookies, request bodies, local variables, and secrets.
- Record only unexpected exceptions.
- Keep expected business validation errors as structured warnings or info logs.

Acceptance:
- Exceptions are grouped by root cause.
- Error tracking does not collect secrets.
- Release regressions are visible.

Completion notes:
- Added `core/error_tracking.py` for scrubbed exception capture, stable `error_fingerprint` grouping, request/actor/job/bot context tags, project-local frames, and redacted messages/extras.
- Added optional Sentry bridge guarded by `ERROR_TRACKING_DSN`; no Sentry dependency or DSN is required by default.
- Wired unexpected API request exceptions, bot update exceptions, and background job repeated errors into error capture.
- Added `tests/test_error_tracking.py` for grouping stability and secret redaction.
- Added `Trading Bot Error Tracking` Grafana dashboard and a captured-exception-spike alert rule.
- Added `docs/OBSERVABILITY_ERROR_TRACKING.md` with capture points, Sentry setup, LogQL searches, alert response, and security rules.

## Stage 10: DevEx and Runbooks

Status: Completed on 2026-06-08.

Purpose: developers and operators must be able to diagnose issues quickly.

Scope:
- Update `DEV_TOOLS.md`.
- Add `docs/OBSERVABILITY_RUNBOOK.md`.
- Add make targets:
  - `make logs-api`
  - `make logs-bot`
  - `make logs-jobs`
  - `make logs-follow`
  - `make metrics`
  - `make observability-up`
  - `make observability-down`
- Add runbooks for:
  - tracing a failed login
  - tracing a websocket disconnect
  - tracing a failed media upload
  - tracing a trade action
  - investigating worker failures
  - checking alert causes

Acceptance:
- A developer can find relevant logs and metrics without knowing internal container names.
- Incident investigation steps are documented.

Completion notes:
- Added `make logs-api`, `make logs-bot`, `make logs-jobs`, `make logs-follow`, and `make metrics` shortcuts.
- Kept `make observability-up`, `make observability-down`, and `make observability-logs` as the local Loki/Promtail/Grafana entrypoints.
- Added `docs/OBSERVABILITY_RUNBOOK.md` for failed login tracing, websocket disconnects, failed media uploads, trade actions, worker failures, and alert cause checks.
- Updated `DEV_TOOLS.md` with the observability shortcuts and runbook map.

## Stage 11: Production Hardening

Purpose: finalize observability for production operations.

Scope:
- Retention policy.
- Dashboard access control.
- Backup/export policy for audit logs.
- Log volume budget.
- Sampling policy for high-volume logs.
- Rate limiting repeated exceptions.
- Load test for logging overhead.

Acceptance:
- Logging overhead is measured and acceptable.
- Storage growth is bounded.
- Sensitive logs are protected by access control and retention policy.

## Recommended Execution Order

1. Stage 0: Discovery and Baseline.
2. Stage 1: Central Logging Foundation.
3. Stage 2: API Request Logging and Correlation.
4. Stage 3: Bot and Background Worker Logging.
5. Stage 4: Audit Logging.
6. Stage 5: Metrics Foundation.
7. Stage 6: Log Collection and Search.
8. Stage 7: Dashboards.
9. Stage 8: Alerting.
10. Stage 9: Error Tracking.
11. Stage 10: DevEx and Runbooks.
12. Stage 11: Production Hardening.

## Branch Policy

- Keep the `loger` / logging branch for reference.
- Do not merge it directly unless it includes:
  - request context implementation,
  - entrypoint integration,
  - API middleware wiring,
  - security redaction tests,
  - runtime smoke validation.
- The preferred path is to implement this roadmap incrementally on top of `main`, using the branch only to avoid losing useful ideas.

## First Implementation Slice

The first safe implementation slice should include:

1. `core/request_context.py`
2. `core/log_redaction.py`
3. `core/logging_config.py`
4. API and bot entrypoint wiring.
5. FastAPI request id middleware.
6. Redaction unit tests.
7. Smoke validation that API and bot still boot.

This slice must not introduce Loki, Prometheus, dashboards, or alerting yet. Those belong after the logging foundation is stable.

## Execution Tracker

| Stage | Status | Notes |
| --- | --- | --- |
| Stage 0: Discovery and Baseline | Completed | Added runtime service inventory, event taxonomy, log class definitions, severity matrix, sensitive-data handling matrix, baseline dashboard/alert candidates, and completion criteria. |
| Stage 1: Central Logging Foundation | Completed | Added request context, redaction helpers, central logging configuration, API/bot/sync-worker entrypoint wiring, focused redaction tests, and deploy smoke validation with JSON logs from healthy `app` and `bot` containers. |
| Stage 2: API Request Logging and Correlation | Completed | Added FastAPI request logging middleware, `X-Request-ID` propagation, sanitized access logs, sensitive/static route policy, actor context attachment after authentication, focused request logging tests, and deploy smoke validation confirming query secrets stay out of access logs. |
| Stage 3: Bot and Background Worker Logging | Completed | Added job context helpers, run ids, iteration fields, duration helpers, repeated-error suppression, structured context for offer/session/market/user-status loops and sync worker, and a bot update logging-context middleware that avoids raw message payloads. |
| Stage 4: Audit Logging | Completed | Added strict audit event schema and summary-only audit events for sensitive user/session/relation/block/broadcast actions with redaction coverage. |
| Stage 5: Metrics Foundation | Completed | Added `/metrics`, shared SQLite-backed Prometheus text metrics for multi-worker API/job aggregation, low-cardinality request/job/realtime/bot/audit metrics, and focused metrics tests/smoke validation. |
| Stage 6: Log Collection and Search | Completed | Added optional local Loki/Promtail/Grafana stack, low-cardinality Docker log labels, Grafana datasource provisioning, LogQL search examples, and observability make targets. |
| Stage 7: Dashboards | Completed | Added provisioned Grafana dashboards for API, jobs/bot/realtime, security/audit, business/chat/upload, and infrastructure log health with stable Loki labels and dashboard documentation. |
| Stage 8: Alerting | Completed | Added Grafana alert provisioning, inert local contact point, Loki-backed core alert rules, notification policy, and alert runbook with security constraints. |
| Stage 9: Error Tracking | Completed | Added scrubbed grouped error capture, optional Sentry bridge, API/bot/job hooks, error tracking dashboard, alert rule, focused tests, and runbook documentation. |
| Stage 10: DevEx and Runbooks | Completed | Added developer/operator make shortcuts for API/bot/job/all logs and metrics, plus the observability incident runbook and DEV_TOOLS command map. |
| Stage 11: Production Hardening | Planned | Add retention, access control, sampling, and log overhead validation. |
