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

## Stage 5: Metrics Foundation

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

## Stage 6: Log Collection and Search

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

## Stage 7: Dashboards

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

## Stage 8: Alerting

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

## Stage 9: Error Tracking

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

## Stage 10: DevEx and Runbooks

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
| Stage 0: Discovery and Baseline | Planned | Event taxonomy and retention matrix still need to be written before audit/metrics expansion. |
| Stage 1: Central Logging Foundation | Completed | Added request context, redaction helpers, central logging configuration, API/bot/sync-worker entrypoint wiring, focused redaction tests, and deploy smoke validation with JSON logs from healthy `app` and `bot` containers. |
| Stage 2: API Request Logging and Correlation | Planned | Add FastAPI middleware for `X-Request-ID`, access logs, sanitized route policy, and request failure logs. |
| Stage 3: Bot and Background Worker Logging | Planned | Add handler/job context, run ids, and repeated failure rate limiting. |
| Stage 4: Audit Logging | Planned | Add strict audit event schema for security-sensitive and business-critical actions. |
| Stage 5: Metrics Foundation | Planned | Add Prometheus metrics with low-cardinality labels. |
| Stage 6: Log Collection and Search | Planned | Add Loki/Promtail or equivalent collection after JSON logs are stable. |
| Stage 7: Dashboards | Planned | Add Grafana dashboards for API, bot, realtime, auth, chat/upload, jobs, and infra. |
| Stage 8: Alerting | Planned | Add actionable alerts with no secret-bearing payloads. |
| Stage 9: Error Tracking | Planned | Add scrubbed exception grouping after logging/metrics baseline stabilizes. |
| Stage 10: DevEx and Runbooks | Planned | Add make targets and incident investigation runbooks. |
| Stage 11: Production Hardening | Planned | Add retention, access control, sampling, and log overhead validation. |
