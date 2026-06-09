# Observability Review Remediation Roadmap

Last updated: 2026-06-09

Source review file: `tmp/log`

This document records the second-pass review of the logging and monitoring critique. The current observability foundation is useful and directionally correct, but it should not be treated as production-complete until the P0 and P1 items below are closed.

## Review Verdict

Most of the critique is valid. The highest-risk findings are not cosmetic: the current Promtail pipeline can make Loki dashboards and alerts unable to parse JSON fields, several sensitive identifiers can still reach logs, Sentry can receive raw exceptions when enabled, and job/sync failure metrics can be counted as successful runs.

Some critique items are duplicated or overstated:

- Critique 8 is duplicated in `tmp/log`; it is one metrics-backend issue.
- Critique 12 is duplicated in `tmp/log`; it is one redaction-coverage issue.
- Critiques 18, 19, 20, 27, 29, 31, and 32 are mostly production-hardening or quality issues, not immediate breakages in the local-only stack.
- Critique 21 is already documented as a gap in `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md`, but it is still not implemented.

## Decision Matrix

| # | Decision | Exact Evidence | Remediation |
| --- | --- | --- | --- |
| 1 | Accepted, P0 | `observability/promtail/promtail-config.yml:30-53` promotes a few fields, then `output.source: message`; dashboards and alerts use `| json`, e.g. `observability/grafana/dashboards/api-overview.json:37`, `:50`, `:63`, `:82`, and `observability/grafana/provisioning/alerting/rules.yml:130`, `:189`. | Preserve the full JSON log line in Loki or explicitly store all required structured fields. |
| 2 | Accepted, P0 | `core/request_logging.py:95`, `:122`, `:139`, `:151` store raw path while only adding `sensitive_route`. | Log route template/redacted path for sensitive and dynamic routes. |
| 3 | Accepted, P0 | `api/deps.py:129-133` adds `session_id` to request context; `core/logging_config.py:69-71` injects context into every record; `core/log_redaction.py:19-46` does not classify `session_id` as sensitive. | Redact or hash session ids before log emission. |
| 4 | Accepted, P0 when Sentry is enabled | `core/error_tracking.py:151-160` forwards the raw exception to Sentry; `core/logging_config.py:175-182` initializes Sentry without a `before_send` scrubber. | Add a Sentry scrubber or forward sanitized events only. |
| 5 | Accepted, P0 | `core/sync_worker.py:85-88` logs raw invalid Redis payload; `core/sync_worker.py:129-132` logs raw peer response body. | Log only hashes, status, table/operation/id summaries, and bounded sanitized error classes. |
| 6 | Accepted, P0 | `core/job_logging.py:19-33` records success unless an exception escapes; loops catch inside the context in `core/offer_expiry.py:138-156`, `core/market_schedule_loop.py:58-74`, `core/session_expiry.py:65-81`, `core/user_account_status_loop.py:39-55`. | Add explicit failure marking or move catch outside `job_context`. |
| 7 | Accepted, P0 | `core/sync_worker.py:111-143` handles non-200 inside `job_context` without raising or marking failure. | Non-200 sync delivery must record job failure/retry result. |
| 8 | Accepted, P1 | `core/metrics.py:66`, `:100-114`, `:163-182` writes SQLite in request/job hot paths; `docker-compose.yml:10-24`, `:56-74`, `:147-150` does not share a metrics volume across app/bot/sync_worker. | Replace SQLite hot-path aggregation or make it bounded/async/shared deliberately. |
| 9 | Accepted, P0/P1 | `main.py:166-174` exposes `/metrics` without auth. App port is local-bound in `docker-compose.yml:23-24`, but `scripts/setup_foreign_nginx.sh:72-83` proxies all `/` to FastAPI, so `/metrics` can become public on the foreign server. | Block `/metrics` at Nginx and/or protect it with a narrow metrics key. |
| 10 | Accepted, P1 | `core/audit_logger.py:40-81` emits audit events only to stdout; Loki retention is 7 days in `observability/loki/loki-config.yml:35-43`; export tooling is pull-based. | Add durable append-only/tamper-evident audit storage. |
| 11 | Accepted, P1 | `core/audit_logger.py:14-16` supports `failure` and `denied`, but current router instrumentation is mostly success-path calls, e.g. `api/routers/users.py`, `blocks.py`, `customers.py`, `accountants.py`, `sessions.py`. | Add denied/failure audit events around security-sensitive rejection paths. |
| 12 | Accepted, P0/P1 | `core/log_redaction.py:56` only masks `09...` mobile numbers; `core/logging_config.py:108` uses `json.dumps(..., default=str)`. | Expand Iranian PII/signed URL/file-name redaction and avoid unsafe object stringification. |
| 13 | Accepted, P2 | `core/log_redaction.py:30-46`, `:59-69` treats any key containing `access` as sensitive. | Narrow exact/semantic secret-key matching to reduce unnecessary redaction. |
| 14 | Accepted, P2 | `core/request_logging.py:55-59` trims/truncates `X-Request-ID` but does not validate charset. | Restrict request ids to a safe charset or generate a replacement. |
| 15 | Accepted, P1/P2 | `core/request_logging.py:62-68` trusts any `X-Forwarded-For`; Nginx forwards it in `scripts/setup_foreign_nginx.sh:36`, `:51`, `:68`, `:77` and Iran templates. | Trust forwarded IPs only from configured proxy ranges or use `X-Real-IP` from trusted Nginx. |
| 16 | Accepted, P2 | `bot/middlewares/logging_context.py:45-49` generates a UUID instead of using Telegram `Update.update_id`. | Store both internal correlation id and Telegram update id. |
| 17 | Accepted with policy decision, P2 | `bot/middlewares/logging_context.py:49` logs raw Telegram user id. It is not promoted as a Loki label, so cardinality risk is limited; privacy policy remains open. | Decide raw vs hashed Telegram id for production. |
| 18 | Partially accepted, P2 | `docker-compose.observability.yml:17-20` mounts Docker socket read-only. Stack ports are local-bound. | Keep acceptable for local operator stack; consider file-based Docker log scraping or socket proxy for production/shared hosts. |
| 19 | Partially accepted, P2 | `observability/loki/loki-config.yml:1` has `auth_enabled: false`; `docker-compose.observability.yml:6-7`, `:28-29` binds Loki/Grafana to `127.0.0.1`. | Accept local-only use; require auth/reverse-proxy controls if ever exposed beyond localhost. |
| 20 | Accepted as intentional limitation, P1 | `observability/grafana/provisioning/alerting/contact-points.yml:3-12` points to an inert webhook. | Configure real Telegram/email/webhook receiver for production. |
| 21 | Accepted, P1 | `api/routers/sync.py:725-802` logs `sync.health` only when called; `Makefile:106-110` exposes manual calls; `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md:220-228` already says no always-on sampler exists. | Add systemd timer/cron/monitor container to sample both servers. |
| 22 | Accepted, P1 | `api/routers/sync.py:20-23` uses broad `DEV_API_KEY` for health. | Add `OBSERVABILITY_API_KEY` or `SYNC_HEALTH_API_KEY`. |
| 23 | Accepted, P2 | `core/sync_worker.py:77-84` calls `BLPOP([queue_name, retry_queue])`, so a busy outbound queue can delay retry work. | Add fair queue selection or alternating priority. |
| 24 | Accepted, P2 | `core/sync_worker.py:114-127` logs success on HTTP 200 while TODO says DB status is not marked verified/synced. | Rename event to delivered/accepted or add DB verification before success. |
| 25 | Accepted, P0 | `core/job_logging.py:53-68` records failure metrics only when the repeated error is emitted. Suppressed failures are not counted. | Record every failure metric; rate-limit only logs/error-tracking events. |
| 26 | Accepted, P2 | `core/request_logging.py:163-177` records metrics before static access log suppression. | Align static route metric policy with access-log policy or separate static metrics. |
| 27 | Partially accepted, P2 | `core/logging_config.py:153-164` clears root and selected handlers. Current entrypoint use is intentional, but future APM/OpenTelemetry/Sentry integrations could be affected. | Make logging setup idempotent and integration-aware. |
| 28 | Accepted, P2 | `core/logging_config.py:166-172` checks `os.environ` before reading `settings.error_tracking_dsn`; local `.env` loaded by settings may not initialize Sentry. | Read settings first, then decide. |
| 29 | Partially accepted, P2 | `core/error_tracking.py:96-100` excludes line number from fingerprint. This is privacy-safe and stable, but can group separate errors in the same function. | Consider optional line-bucket or top-frame line in fingerprint after noise testing. |
| 30 | Accepted, P2 | `core/error_tracking.py:80-93` stores only basename, function, line. | Use project-relative paths without leaking host paths. |
| 31 | Accepted as current-stage tradeoff, P2 | Dashboards under `observability/grafana/dashboards/*.json` are Loki/log based. | Add Prometheus/metric-based SLO panels after metrics backend is production-grade. |
| 32 | Accepted, P1 | `observability/grafana/provisioning/alerting/rules.yml:157-158`, `:216-217`, `:550-551`, `:604-605`, `:657-659` use initial hard-coded thresholds. | Baseline with real traffic and tune thresholds. |
| 33 | Partially accepted, P1 | `.github/workflows/merge-gate.yml` and `pre-release-gate.yml` run broad gates, but no observability-specific config/dashboard/redaction gate exists. | Add a focused observability CI job. |
| 34 | Accepted positive | Central logging, contextvars, request id propagation, datasource UID consistency, local-bound observability ports, and sync health concept are good foundations. | Preserve these properties while fixing the issues above. |

## Remediation Stages

### Stage R1: P0 Log Integrity and Secret Hygiene

Goal: make logs parseable and prevent high-risk sensitive data leakage.

Files:

- `observability/promtail/promtail-config.yml`
- `observability/grafana/dashboards/*.json`
- `observability/grafana/provisioning/alerting/rules.yml`
- `core/request_logging.py`
- `api/deps.py`
- `core/log_redaction.py`
- `core/logging_config.py`
- `core/error_tracking.py`
- `core/sync_worker.py`
- `core/job_logging.py`
- `main.py`
- `scripts/setup_foreign_nginx.sh`
- `deploy/production/nginx-iran-online.conf.template`
- tests under `tests/test_logging_foundation.py`, `tests/test_request_logging.py`, `tests/test_error_tracking.py`, `tests/test_sync_worker.py`, `tests/test_job_logging.py`

Sub-stages:

#### Stage R1.1: Loki JSON Preservation

Status: Completed on 2026-06-09.

Purpose: keep the structured JSON log line intact after Promtail processing.

Work:

1. Remove or replace `output.source: message` so Loki keeps the full application JSON log line.
2. Add a static regression test proving the Promtail config does not collapse the line to plain `message` while dashboards/alerts still depend on `| json`.
3. Validate that dashboard/alert query assumptions remain coherent: `event`, `request_id`, `path` or `route`, `status_code`, `duration_ms`, and job fields must be parseable from JSON log lines, not only labels.

Acceptance:

- `observability/promtail/promtail-config.yml` keeps the original JSON payload as the Loki line.
- The focused observability config test fails if `output.source: message` returns.
- Existing Grafana queries using `| json` remain compatible.

Completion notes:

- Removed the Promtail `output.source: message` stage so the original structured JSON app log remains the Loki line.
- Added `tests/test_observability_config.py` to guard Promtail JSON preservation and validate the dashboard/alert JSON-field query assumptions.
- Validated with `python3 -m unittest tests.test_observability_config`.

#### Stage R1.2: Request Path and Session Identifier Safety

Status: Completed on 2026-06-09.

Purpose: prevent sensitive path segments and session identifiers from reaching logs.

Work:

1. Replace raw access-log `path` with a safe route template or redacted path for sensitive/dynamic routes.
2. Do not store raw `session_id` in formatted logs. Use `[REDACTED]` or `session_id_hash` if correlation is needed.
3. Add request-logging tests for token-bearing paths, upload-session paths, and authenticated context logs.

Acceptance:

- Access/error logs do not contain raw token-like path segments on sensitive routes.
- `session_id` is never emitted raw by `JsonLogFormatter`.

Completion notes:

- Added `safe_request_log_path()` and sensitive segment redaction in `core/request_logging.py`, so access/error logs use route templates such as `/api/invitations/accept/{token}`, `/api/chat/upload-sessions/{session_id}/chunk`, and `/api/recovery/{code}` instead of raw token/session/code path segments; unmatched sensitive paths also redact token-like or marker-adjacent secret segments before logging.
- Added `/invitations` to the sensitive route policy because invitation accept links can carry token-like path segments.
- Added `session_id` / `sid` redaction coverage in `core/log_redaction.py`; `JsonLogFormatter` now emits `[REDACTED]` for request-context session ids.
- Migrated `tests/test_request_logging.py` from `TestClient` to `httpx.ASGITransport` because `TestClient` hangs in the current sandbox environment, then added route-template regressions for token-bearing invitation paths, upload-session paths, and exception logging paths.
- Validated with `timeout 30 python3 -m unittest tests.test_request_logging tests.test_logging_foundation tests.test_observability_config`, `python3 -m unittest tests.test_error_tracking`, and `python3 -m py_compile core/request_logging.py core/log_redaction.py`.

#### Stage R1.3: Redaction Coverage and Object Safety

Status: Completed on 2026-06-09.

Purpose: strengthen redaction against Iranian PII and unsafe object stringification.

Work:

1. Add `session_id`, `sid`, upload session ids, signed-url keys, Iran mobile variants, email, card number, IBAN/sheba, and national-id patterns to redaction coverage.
2. Stop relying on `json.dumps(default=str)` for unknown objects that may expose PII through `__str__`; convert unknown objects to safe type metadata unless explicitly allowed.
3. Add focused redaction tests for every new pattern.

Acceptance:

- No raw OTP, mobile, email, card, sheba, national id, signed URL, file name, or secret-bearing object string appears in formatted log output.

Completion notes:

- Expanded `core/log_redaction.py` coverage for email, Iranian mobile variants, bank card numbers, sheba/IBAN values, national ids, signed URL query secrets, file-name keys, upload-session ids, and `sid`/session-style keys.
- Replaced unsafe unknown-object passthrough with safe object metadata, so arbitrary objects in structured log extras no longer rely on `__str__`/`__repr__` output.
- Removed `json.dumps(default=str)` from `JsonLogFormatter`; formatted log payloads must now be JSON-safe after redaction/coercion instead of silently stringifying unknown objects.
- Added focused redaction regressions in `tests/test_logging_foundation.py` for Iranian PII, signed URLs, filenames, upload/session keys, and secret-bearing unknown objects.
- Validated with `timeout 30 python3 -m unittest tests.test_request_logging tests.test_logging_foundation tests.test_observability_config tests.test_error_tracking`, `python3 -m unittest tests.test_audit_logger`, and `python3 -m py_compile core/log_redaction.py core/logging_config.py`.

#### Stage R1.4: Error Tracking Scrubbing

Status: Completed on 2026-06-09.

Purpose: prevent optional external error tracking from bypassing local redaction.

Work:

1. Add Sentry `before_send` scrubbing or disable raw `sentry_sdk.capture_exception(exc)` forwarding in favor of sanitized structured events.
2. Add tests/mocks proving raw exception messages and extras are scrubbed before external forwarding.

Acceptance:

- Enabling Sentry cannot send raw tokens, OTPs, mobile numbers, signed URLs, request bodies, or raw exception text containing secrets.

Completion notes:

- Added `scrub_sentry_event()` in `core/error_tracking.py` to recursively apply log redaction to Sentry events and replace request bodies, cookies, and request environment payloads with `[REDACTED]`.
- Changed `capture_exception()` to forward a sanitized structured Sentry event via `capture_event()` instead of passing the raw exception object to `sentry_sdk.capture_exception(exc)`.
- Registered the same scrubber as Sentry `before_send` in `core/logging_config.py` so SDK-generated events are scrubbed before external forwarding.
- Updated `docs/OBSERVABILITY_ERROR_TRACKING.md` to document the two-layer external scrubbing model.
- Added focused tests proving raw Sentry event payloads are scrubbed and `capture_exception()` does not forward raw exception objects.
- Validated with `timeout 30 python3 -m unittest tests.test_error_tracking tests.test_logging_foundation tests.test_request_logging tests.test_observability_config tests.test_audit_logger` and `python3 -m py_compile core/error_tracking.py core/logging_config.py`.

#### Stage R1.5: Sync Worker Log and Failure Semantics

Status: Completed on 2026-06-09.

Purpose: make sync logs safe and sync metrics truthful.

Work:

1. Remove raw Redis sync payload and peer `response.text` from sync worker logs.
2. Treat sync non-200 responses as failure/retry in metrics.
3. Rename HTTP-200-only success to a precise delivered/accepted event unless DB verification is implemented.

Acceptance:

- Sync worker tests prove invalid payloads and peer response bodies are not emitted raw.
- Non-200 sync attempts increment failure/retry metrics.

Completion notes:

- Added sync payload and peer response summarizers in `core/sync_worker.py`; invalid Redis payload logs now include only payload size and SHA-256 prefix, not raw queue contents.
- Removed raw peer `response.text` from non-200 logs; failure logs now include status, response size, response hash, and content type.
- Changed HTTP 200 success event from `job.item.completed` to `job.item.delivered` to avoid claiming DB-level verified/synced state before that TODO is implemented.
- Added `SyncDeliveryError` so non-200 peer responses leave `job_context` through a controlled exception path and record `result="failure"` in job metrics before the item is requeued.
- Added focused sync worker tests proving invalid payloads and peer response bodies are not logged raw, and proving non-200 delivery attempts record failure metrics.
- Validated with `python3 -m unittest tests.test_sync_worker`, `python3 -m unittest tests.test_job_logging tests.test_metrics tests.test_sync_worker`, and `python3 -m py_compile core/sync_worker.py`.

#### Stage R1.6: Job Failure Metric Semantics

Status: Completed on 2026-06-09.

Purpose: make background job success/failure metrics trustworthy.

Work:

1. Change `job_context` so handled failures can be explicitly marked as failure.
2. Count every repeated job failure in metrics even when the log line is suppressed.
3. Update loops that catch exceptions inside `job_context`.

Acceptance:

- Failed job iterations and suppressed repeated failures increment failure metrics exactly once per failure.

Completion notes:

- Added `mark_current_job_failed()` in `core/job_logging.py`, allowing handled failures inside `job_context` to explicitly mark the current iteration as failed without re-raising.
- `job_context()` now reads the final `job_result` from request context before recording metrics, so loops that catch exceptions internally no longer produce false `success` metrics.
- `RepeatedErrorLogger.log()` now counts every failure attempt in metrics even when repeated logs/error tracking events are suppressed; when called inside an active `job_context`, it marks the current iteration failed and lets the context record exactly one failure metric.
- Added `metric_recorded=True` support for cases where a failure metric has already been recorded by an exited `job_context`, and applied it to sync worker network-error logging to avoid double-counting.
- Added focused tests for manually marked handled failures, active-job repeated errors, suppressed repeated failure metrics, and sync worker network-error single-count behavior.
- Validated with `python3 -m unittest tests.test_job_logging tests.test_sync_worker tests.test_metrics` and `python3 -m py_compile core/job_logging.py core/sync_worker.py`.

#### Stage R1.7: Metrics Endpoint Exposure Guard

Status: Completed on 2026-06-09.

Purpose: prevent public exposure of operational metrics.

Work:

1. Protect `/metrics`: add Nginx deny/allow for public configs and add optional app-level auth or local-only guard.
2. Add deployment config tests or static checks for public Nginx templates.

Acceptance:

- Public deployment-facing Nginx configs do not expose `/metrics` accidentally.
- If app-level access is enabled, the key is narrow and separate from broad dev access.

Completion notes:

- Added `OBSERVABILITY_API_KEY` / `settings.observability_api_key` as a narrow metrics-access key, separate from broad `DEV_API_KEY`.
- Added app-level `/metrics` guard in `main.py`: loopback clients may scrape locally; non-loopback clients must provide `X-Observability-Api-Key`; unauthorized remote requests receive `404`.
- Added `location = /metrics { deny all; return 404; }` to the foreign Nginx setup script and Iran production Nginx template so public reverse proxies do not expose metrics.
- Updated production env generation to prompt/write `OBSERVABILITY_API_KEY` for both foreign and Iran env files.
- Added tests proving app-level metrics access allows loopback, rejects remote requests without the observability key, does not accept `X-DEV-API-KEY`, and static-checks public Nginx configs for metrics blocking.
- Validated with `python3 -m unittest tests.test_main_metrics_guard tests.test_observability_config tests.test_metrics` and `python3 -m py_compile main.py core/config.py`.

Acceptance:

- Loki dashboard and alert queries that use `| json` parse fields again.
- No raw session id, token-like path segment, invalid sync payload, peer response body, OTP, mobile, signed URL, or secret-bearing object string appears in unit-test log output.
- Failed job iterations and sync non-200 attempts increment failure metrics.
- Public `/metrics` is blocked or authenticated in deployment-facing Nginx config.

### Stage R2: P1 Metrics Backend and Runtime Cost Hardening

Status: Completed on 2026-06-09.

Goal: make metrics production-safe under traffic and multi-container deployment.

Files:

- `core/metrics.py`
- `main.py`
- `docker-compose.yml`
- `docker-compose.iran.yml`
- deployment env generation in `scripts/production_deploy_online.sh`
- `docs/OBSERVABILITY_PRODUCTION_HARDENING.md`

Work:

1. Decide metrics backend:
   - Preferred: `prometheus_client` with process-aware/multiprocess support and one scrape endpoint per service or a dedicated exporter.
   - Conservative fallback: keep SQLite only as opt-in development aggregation, disable it by default in hot paths, and expose per-process memory metrics clearly.
2. Make API, bot, and sync worker metrics visible deliberately. The current `/metrics` endpoint only exposes the app container state unless all services share a backend.
3. Add bounded queue/buffer behavior if writes are async.
4. Document expected overhead and run `make observability-overhead` before/after.
5. Add a smoke check that metrics output does not include raw routes, ids, phone numbers, names, filenames, or tokens.

Acceptance:

- Request hot path has no unbounded SQLite lock/file I/O risk.
- Metrics semantics are clear across app workers, bot, and sync worker.
- `/metrics` output is low-cardinality and secret-free.

Completion notes:

- Chose the conservative backend path for this release: `TRADING_BOT_METRICS_BACKEND=memory` is now the default, so request/job/bot/audit/realtime metric updates no longer write SQLite in hot paths unless explicitly opted in.
- Kept the prior shared SQLite implementation available only through `TRADING_BOT_METRICS_BACKEND=shared_sqlite` plus `TRADING_BOT_METRICS_DB`, for local diagnostics or deliberate short windows.
- Added `trading_bot_metrics_backend_info{backend=...,service=...,shared=...}` to `/metrics` output so operators can see whether they are looking at per-process memory metrics or an explicitly shared backend.
- Added `TRADING_BOT_SERVICE` and explicit metrics backend env defaults for app/API, bot, and sync worker in `docker-compose.yml` and `docker-compose.iran.yml`; production env generation also writes `TRADING_BOT_METRICS_BACKEND=memory`.
- Hardened metric label sanitization with existing redaction rules and filename-like label removal, then added a smoke test proving metrics output does not contain raw ids, tokens, mobile numbers, emails, or filenames.
- Updated `docs/OBSERVABILITY_PRODUCTION_HARDENING.md` with backend policy, service semantics, and the R2 overhead result.
- Validated with `python3 -m unittest tests.test_metrics tests.test_main_metrics_guard`, `python3 -m py_compile core/metrics.py main.py core/config.py`, and `make observability-overhead` (`per_event_overhead_us=377.33`, budget `1000.0`, acceptable `true`).

### Stage R3: P1 Audit Trail Hardening `[completed 2026-06-09]`

Goal: separate incident/debug logs from security-grade audit evidence.

Files:

- `core/audit_logger.py`
- new durable audit sink module, e.g. `core/audit_sink.py`
- new migration/model if using Postgres audit table
- `api/routers/users.py`
- `api/routers/blocks.py`
- `api/routers/customers.py`
- `api/routers/accountants.py`
- `api/routers/sessions.py`
- `api/routers/sync.py`
- `scripts/export_audit_logs.py`
- `docs/OBSERVABILITY_PRODUCTION_HARDENING.md`

Work:

1. Add durable audit storage with event id, timestamp, actor, target, action, result, request id, client ip, and redacted summaries.
2. Add tamper-evidence: hash chain or signed export manifest. If Postgres is used, do not rely on ordinary mutable rows as the only evidence.
3. Keep stdout/Loki audit events as searchable mirrors, not the only audit store.
4. Add denied/failure audit events for:
   - forbidden role/status/session changes,
   - accountant/customer attempts to use owner-only actions,
   - block/unblock denials,
   - login approval/recovery failures,
   - invalid sync API key/signature/timestamp,
   - wrong dev/observability key usage.
5. Update export tooling to page safely beyond 5000 records and include integrity metadata.

Acceptance:

- Security-relevant denied/failure actions can be reconstructed even if Loki retention expires.
- Audit export proves completeness/integrity for the exported range.

Completion notes:

- Added `core/audit_sink.py` as a durable append-only JSONL sink with `audit_event_id`, millisecond UTC timestamp, `previous_hash`, `event_hash`, and fully redacted payloads.
- Wired `audit_log()` to write the durable sink first and mirror audit metadata back into stdout/Loki events, preserving existing search workflows while making Loki non-authoritative.
- Added `AUDIT_TRAIL_PATH` config, production env generation, and `audit_data:/app/audit_trail` API mounts in both foreign and Iran Compose files.
- Added denied/failure audit events for block-management denials, invalid sync API key/signature/timestamp/missing headers, sync verification failures, and wrong observability-key metrics access.
- Updated `scripts/export_audit_logs.py` with Loki pagination, durable-file export, hash-chain verification, output SHA-256, and `.manifest.json` sidecars.
- Added focused tests in `tests/test_audit_logger.py` and `tests/test_audit_export.py` for durable hash chaining, redaction, manifest generation, and tamper detection.

### Stage R4: P1 Cross-Server Sync Observability `[completed 2026-06-09]`

Goal: make Iran/foreign reconnect monitoring active instead of manual.

Files:

- `api/routers/sync.py`
- `core/sync_worker.py`
- `core/config.py`
- `Makefile`
- new systemd timer/cron/monitor script under `scripts/`
- `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md`
- `observability/grafana/provisioning/alerting/rules.yml`

Work:

1. Add `OBSERVABILITY_API_KEY` or `SYNC_HEALTH_API_KEY`; stop using broad `DEV_API_KEY` for read-only health.
2. Add a production sampler that calls local and Iran `/api/sync/health` every minute from the foreign server.
3. Make sync alerts treat missing samples as suspicious for production, not always `OK`.
4. Add retry queue fairness so `sync:retry` is not starved by a constantly non-empty `sync:outbound`.
5. Rename HTTP-200-only success to `delivered` or implement DB verification before emitting `success`.

Acceptance:

- Sync backlog, lag, and retry queue panels update without manual `make sync-health`.
- Alerts detect both bad values and missing health samples.
- Retry work makes progress under continuous outbound traffic.

Completion notes:

- Changed `GET /api/sync/health` to use the narrow observability access path: non-loopback callers must use `X-Observability-Api-Key` backed by `settings.observability_api_key`, and the broad `X-Dev-Api-Key` is no longer accepted for read-only sync health.
- Added `scripts/sample_sync_health.py` to sample both local and Iran sync health from the foreign host, and `scripts/install_sync_health_monitor.sh` to install a one-minute systemd timer or print a cron fallback.
- Added `make sync-health-sample` and `make sync-health-monitor-install`, and switched `make sync-health` / `make sync-health-iran` to the observability key header.
- Added queue polling fairness in `core/sync_worker.py` by alternating `sync:outbound` and `sync:retry` priority per iteration so retry work cannot starve under sustained outbound load.
- Kept worker success semantics on `job.item.delivered` and updated sync alerting so missing `sync.health` samples alert explicitly for both `server_mode=foreign` and `server_mode=iran`.
- Updated `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md` with the new auth header, sampler installation, and missing-sample incident path.

### Stage R5: P1 Production Alert Delivery and Baseline `[completed 2026-06-09]`

Goal: make alerting operational without causing noise.

Files:

- `observability/grafana/provisioning/alerting/contact-points.yml`
- `observability/grafana/provisioning/alerting/rules.yml`
- `docs/OBSERVABILITY_ALERTS.md`
- `docs/OBSERVABILITY_PRODUCTION_HARDENING.md`
- production env/manifest docs

Work:

1. Keep inert contact point for local clones, but add production contact point template for Telegram admin channel/email/webhook.
2. Make alert receiver secrets env-driven and never committed.
3. Collect a baseline from real staging/production traffic.
4. Tune thresholds for 5xx, auth failures, sync lag, retry queue, upload/media failures, and captured exceptions.
5. Add alert payload policy: request ids and event ids only, no raw message/body/path tokens.

Acceptance:

- Production alerts can be delivered to the intended channel.
- Alert thresholds are documented with a baseline date and observed traffic range.

Completion notes:

- Added env-driven Grafana receiver wiring in `docker-compose.observability.yml` for webhook/email delivery and SMTP settings, while keeping the local default receiver inert for clones.
- Expanded `observability/grafana/provisioning/alerting/contact-points.yml` with safe-template production webhook and production email receivers, and switched `notification-policies.yml` to env-driven receiver selection for default, warning, and critical routes.
- Documented the production receiver contract, Telegram-through-webhook-bridge guidance, SMTP secret handling, and alert payload restrictions in `docs/OBSERVABILITY_ALERTS.md` and `docs/OBSERVABILITY_PRODUCTION_HARDENING.md`.
- Added a dated baseline snapshot (`2026-06-09`) with observed ranges and the currently kept thresholds for API 5xx, auth/session failures, upload/media failures, captured exceptions, and sync backlog/lag/retry alerts.
- Added optional observability receiver hints to `deploy/production/online.env.example` without introducing any real secret-bearing values.
- Added config regressions proving alert provisioning stays env-driven and limited to safe template fields (`request_id`, `event_id`, bounded labels/annotations only).

### Stage R6: P2 Collector and Privacy Hardening

Goal: reduce operational risk and improve debugging quality.

Files:

- `docker-compose.observability.yml`
- `observability/loki/loki-config.yml`
- `core/request_logging.py`
- `bot/middlewares/logging_context.py`
- `core/error_tracking.py`
- `core/log_redaction.py`
- `core/logging_config.py`

Work:

1. Validate `X-Request-ID` charset and length; generate a replacement for invalid values.
2. Trust `X-Forwarded-For` only from configured trusted proxy ranges.
3. Store Telegram `Update.update_id` alongside an internal UUID correlation id.
4. Decide raw vs hashed `telegram_user_id`; implement the policy.
5. Replace basename-only exception frames with project-relative paths.
6. Consider adding top-frame line number to error fingerprint only after checking grouping noise.
7. Make `configure_logging()` idempotent and safe for future APM/OpenTelemetry handlers.
8. Narrow redaction key matching so operational fields like `access_level` are not unnecessarily removed.
9. Keep Docker socket based Promtail only for local trusted use; document or implement socket-proxy/file-scrape alternative for production/shared hosts.
10. Keep Loki unauthenticated only while bound to localhost; require auth if exposed.

Acceptance:

- Request/audit IPs are not spoofable through client-supplied forwarded headers.
- Bot/update correlation supports Telegram-side debugging.
- Error frames are useful without exposing host paths.

Status: Completed on 2026-06-09.

Completion notes:

- Hardened `core/request_logging.py` so `X-Request-ID` only accepts a bounded safe charset and length; invalid values are replaced with a generated UUID instead of being echoed into logs or responses.
- Added trusted-proxy handling for `X-Forwarded-For` / `X-Real-IP`: forwarded client IPs are now accepted only when the direct peer belongs to configured `TRUSTED_PROXY_CIDRS`; otherwise logs keep the direct socket peer.
- Added `trusted_proxy_cidrs` and `observability_telegram_user_hash_salt` settings in `core/config.py`.
- Updated `bot/middlewares/logging_context.py` to record both a stable internal `bot_correlation_id` and the real Telegram `Update.update_id` (as `bot_update_id` when present), while hashing `telegram_user_id` before it enters structured logs.
- Reworked `core/error_tracking.py` stack frames to emit project-relative file paths instead of basename-only entries or absolute host paths; kept fingerprint grouping stable without adding line-number noise at this stage.
- Made `configure_logging()` idempotent in `core/logging_config.py` by replacing only Codex-managed handlers and preserving unrelated future handlers/integrations.
- Narrowed secret-key matching in `core/log_redaction.py` so operational fields such as `access_level` remain visible while actual token fields stay redacted.
- Documented the current Promtail Docker socket model as local-trusted-only in `docs/OBSERVABILITY_PRODUCTION_HARDENING.md`; Loki remains acceptable without auth only while bound to `127.0.0.1`.
- Added focused regressions for invalid request ids, trusted/untrusted proxy IP extraction, bot update correlation, hashed Telegram ids, project-relative error frames, `access_level` visibility, and idempotent logging setup.

### Stage R7: Observability CI Gate

Goal: prevent future observability regressions.

Files:

- `.github/workflows/merge-gate.yml`
- `.github/workflows/pre-release-gate.yml`
- new or existing tests under `tests/`
- optional scripts under `scripts/`

Work:

1. Add a focused observability test command that runs:
   - logging/redaction tests,
   - request path/session id safety tests,
   - job metric success/failure tests,
   - sync worker no-raw-payload tests,
   - audit schema tests,
   - dashboard/alert JSON/YAML syntax checks,
   - static check that Promtail does not replace the log line with plain `message` while dashboards depend on `| json`.
2. Wire that command into merge/pre-release workflows or make it part of `make test-gate`.
3. Add diff-aware trigger notes so observability changes cannot bypass the focused gate.

Acceptance:

- A PR that breaks redaction, Promtail JSON parsing, alert/dashboard syntax, or job failure metrics fails CI.

Status: Completed on 2026-06-09.

Completion notes:

- Added `scripts/run_observability_gate.py` as the focused observability regression entrypoint. It runs the core logging/redaction, request path/session safety, error tracking, job metric semantics, sync worker, audit trail/export, metrics guard, sync health, and observability config test modules in one place.
- Added `make observability-gate` so the focused gate can run locally and in CI with the same command.
- The gate supports diff-aware notes through `--base-ref/--head-ref` and prints observability-related changed paths when present, so PR runs show when the diff touched logging/metrics/collector/dashboard surfaces.
- Added a dedicated `observability-gate` job to both `.github/workflows/merge-gate.yml` and `.github/workflows/pre-release-gate.yml`; the heavier frontend browser matrix now waits on this job.
- Added static workflow regressions in `tests/test_observability_workflows.py` so future CI edits cannot silently remove the focused observability gate.
- Added script regressions in `tests/test_observability_gate.py` and compile-smoke coverage for `scripts/run_observability_gate.py`.

## Immediate Execution Order

1. Stage R1 first. This is the only stage that blocks trust in the existing dashboards/alerts.
2. Stage R2 and R4 next. They affect production monitoring accuracy and Iran/foreign recovery.
3. Stage R3 before official production release if audit evidence matters beyond short-term incident debugging.
4. Stage R5 before enabling real alert delivery.
5. Stage R6 and R7 as hardening and regression prevention.

## Do Not Change Yet

- Do not delete the current observability stack. It is a useful local operator stack.
- Do not expose Grafana/Loki publicly to compensate for missing alert delivery.
- Do not increase Loki labels with high-cardinality fields such as `request_id`, `actor_id`, `path`, `telegram_user_id`, message ids, filenames, or raw user input.
- Do not treat HTTP 200 from sync peer as final business convergence unless DB/change-log verification is added.

## Third-Pass Review After R1-R7

Last reviewed: 2026-06-09
Review source: updated `tmp/log`

The R1-R7 remediation pass materially improved the observability foundation, but a third review surfaced several real remaining gaps. Not every new critique is accepted:

- Accepted:
  - `api/routers/sync.py` still allows loopback fallback through `request.url.hostname` in `_is_loopback_sync_request()`.
  - `core/request_logging.py` still records metrics with `route_template`, not the already-sanitized `safe_path`.
  - `api/routers/sync.py` still logs raw `response.text[:200]` in the manual resync path.
  - `core/logging_config.py` still gates Sentry init on `os.environ.get("ERROR_TRACKING_DSN")` before reading `settings.error_tracking_dsn`.
  - `core/job_logging.py` still groups repeated errors by `f"{type(exc).__name__}:{exc}"`, which is too message-sensitive.
  - The default `memory` metrics backend is intentionally faster, but it is not production-complete for multi-worker API visibility or bot/sync-worker scraping.
- Accepted as lower-priority hardening:
  - `api/deps.py` still keeps raw `session_id` in runtime request context before formatter redaction.
  - `core/log_redaction.py` still treats `sid` as a broad substring match instead of exact-key-only matching.
  - `core.audit_logger.py` does not expose whether a durable write actually happened.
  - The durable audit hash-chain still lacks an external anchor.
  - Production deployment still needs explicit checks for sync sampler installation, non-default alert receivers, `TRUSTED_PROXY_CIDRS`, and a dedicated Telegram observability hash salt.
- Rejected or already-covered:
  - Docker socket exposure in Promtail is already accepted and documented as local-trusted-only.
  - Loki `auth_enabled: false` is already accepted only because the stack stays bound to `127.0.0.1`.
  - The lack of a visible GitHub workflow run for the current head is an execution-status gap, not a repository-code defect.

## Extension Stages

### Stage R8: Sync Health Access and Metrics Route Sanitization

Goal: close the remaining high-risk access and data-leak paths left after R7.

Files:

- `api/routers/sync.py`
- `core/request_logging.py`
- `tests/test_sync_health_endpoint.py`
- `tests/test_request_logging.py`
- `tests/test_observability_gate.py`

Work:

1. Remove the `request.url.hostname` fallback from `_is_loopback_sync_request()`; only the direct client peer may qualify as loopback.
2. Decide whether loopback bypass should remain at all for `/api/sync/health`; if retained, keep it strictly peer-based.
3. Change `record_http_request()` call sites to use the same sanitized route/path that logs use, or collapse unmatched sensitive paths to a constant safe route.
4. Add regressions for:
   - spoofed `Host` header not bypassing `/api/sync/health`,
   - unmatched sensitive routes not leaking token-like segments into metrics labels.

Acceptance:

- `/api/sync/health` cannot be treated as loopback through a spoofed host name.
- Sensitive unmatched routes do not leak token-like path segments into Prometheus route labels.

Status: Completed on 2026-06-09.

Completion notes:

- Removed the `request.url.hostname` fallback from `api/routers/sync.py::_is_loopback_sync_request()`. Loopback bypass for `/api/sync/health` is now based only on the direct client peer address.
- Changed `core/request_logging.py` so `record_http_request()` uses the same sanitized path/route that the access and error logs use, instead of the rawer `route_template`.
- This closes the remaining unmatched-sensitive-route leak path in Prometheus route labels. Matched sensitive routes still record their safe route template; unmatched sensitive routes now record the redacted safe path.
- Added regressions proving a spoofed loopback host name does not bypass sync-health authentication and proving metrics route labels stay sanitized for both matched and unmatched sensitive routes.

### Stage R9: Remaining Raw Payload and Error-Tracking Gate Cleanup

Goal: remove the last raw-body/logging escapes and finish the deferred Sentry init hardening.

Files:

- `api/routers/sync.py`
- `core/logging_config.py`
- `core/sync_worker.py` (shared response-summary helper if needed)
- `tests/test_sync_worker.py`
- `tests/test_error_tracking.py`

Work:

1. Replace manual resync warning logs that use `response.text[:200]` with the same safe response summary pattern already used in `core/sync_worker.py`.
2. Move Sentry initialization gating fully to `settings.error_tracking_dsn`; do not require a parallel raw process env check first.
3. Add regressions proving:
   - manual resync logs do not emit peer response bodies,
   - Sentry init honors `settings.error_tracking_dsn` even when the raw process env check would previously skip it.

Acceptance:

- No sync path logs raw peer response bodies.
- Error tracking initialization depends on configured settings, not a separate preflight env shortcut.

Status: Completed on 2026-06-09.

Completion notes:

- Added `_summarize_peer_response()` in `api/routers/sync.py` and replaced the manual resync warning that previously logged `response.text[:200]`.
- Manual resync failures now emit structured summaries only: status, bounded response size, SHA-256 prefix, and content type.
- Removed the `os.environ.get("ERROR_TRACKING_DSN")` pre-gate from `core/logging_config.py`; Sentry initialization now depends only on `settings.error_tracking_dsn`.
- Added regressions proving manual resync warnings do not emit raw peer response bodies and proving `configure_logging()` initializes Sentry from settings even when the raw process environment does not carry `ERROR_TRACKING_DSN`.

### Stage R10: Production-Grade Metrics and Repeated-Error Stability

Goal: convert the current fast local metrics posture into a production-monitorable architecture.

Files:

- `core/metrics.py`
- `core/job_logging.py`
- `docker-compose.yml`
- `docker-compose.iran.yml`
- observability docs and dashboards as needed
- deployment docs/scripts as needed

Work:

1. Define the production metrics architecture explicitly:
   - API multi-worker aggregation,
   - bot metrics scrape/export path,
   - sync-worker metrics scrape/export path.
2. Keep the `memory` backend as the local-safe default, but document and implement the production aggregation path rather than treating current output as complete.
3. Rework `RepeatedErrorLogger` grouping so suppression keys use a normalized/fingerprinted error identity instead of raw stringified exception messages.
4. Add a regression or design-time validation proving repeated errors with different incidental values still collapse into the same suppression group when appropriate.

Acceptance:

- Production metrics behavior is explicitly defined for API, bot, and sync-worker surfaces.
- Repeated-error suppression is no longer excessively sensitive to changing message text.

Status: Completed on 2026-06-09.

Completion notes:

- Reworked `core/job_logging.py::RepeatedErrorLogger` so repeat suppression keys no longer depend on the raw stringified exception message. The key now uses job name plus a stable exception fingerprint derived from error site/type.
- Added `repeat_key` to repeated job-error payloads for debugging suppression behavior without leaking raw incidental message values.
- Added a focused regression proving repeated errors from the same site with different per-message values still collapse into the same suppression group.
- Added an explicit `Production Metrics Architecture` section to `docs/OBSERVABILITY_PRODUCTION_HARDENING.md`, clarifying that:
  - API `/metrics` is per-worker under the current `memory` backend,
  - bot and sync-worker metrics are not aggregated into API `/metrics`,
  - production must treat those surfaces as separate until an explicit aggregation/export layer is deployed.

### Stage R11: Durable Audit Evidence and Deployment Enforcement

Goal: harden operational trust signals around audit durability and observability deployment completeness.

Files:

- `core/audit_logger.py`
- `core/audit_sink.py`
- `scripts/production_deploy_online.sh`
- observability production docs
- deployment env examples/docs

Work:

1. Emit whether each audit event was durably persisted or fell back to non-durable record generation.
2. Design an external audit-anchor path for periodic head-hash export outside the local host.
3. Add production deployment checks for:
   - sync sampler timer/service installation,
   - non-default alert receivers,
   - explicit `TRUSTED_PROXY_CIDRS`,
   - explicit `OBSERVABILITY_TELEGRAM_USER_HASH_SALT`.
4. Optionally move raw `session_id` out of request context in favor of a pre-hashed/opaque correlation field.
5. Narrow `sid` matching from broad substring behavior to exact-key semantics unless a real redaction corpus shows the current behavior is still needed.

Acceptance:

- Operators can tell whether audit events were durably written.
- Production deployment has explicit observability readiness checks instead of relying on default values.
- Audit evidence design includes an external integrity anchor plan.

Status: Completed on 2026-06-09.

Completion notes:

- `core.audit_sink.py` now records `audit_durable`, optional `audit_durable_reason`, optional `audit_trail_path`, and optional `audit_durable_error_type` in every generated audit record. `core.audit_logger.py` forwards those fields into the structured audit log line so operators can immediately see whether the durable append succeeded or the event fell back to non-durable generation.
- `api/deps.py` no longer places raw JWT `sid` values into request logging context. It now stores a short opaque `session_id_hash`, which preserves correlation value without exposing the session identifier itself in runtime logs.
- `core.log_redaction.py` narrowed sensitive-key matching from broad substring behavior to boundary-aware matching. Exact/compound `sid` and session-style keys still redact correctly, while incidental strings such as `outside_reference` and `residency_status` no longer get caught by accident.
- `scripts/production_deploy_online.sh` now blocks a release when runtime env files still rely on observability placeholders or are missing production-only inputs. It explicitly validates:
  - `TRUSTED_PROXY_CIDRS`
  - `OBSERVABILITY_TELEGRAM_USER_HASH_SALT`
  - non-local Grafana alert receivers
  - non-placeholder Grafana webhook/email targets
- The production deploy flow now installs and verifies the `trading-bot-sync-health-sampler` timer on both foreign and Iran hosts as part of the release path.
- `docs/OBSERVABILITY_PRODUCTION_HARDENING.md` now includes an explicit external audit-integrity anchor design so local durable trails can be periodically anchored outside the app host.
- Added focused regressions in `tests/test_audit_logger.py`, `tests/test_logging_foundation.py`, and `tests/test_observability_config.py` covering durable/fallback audit signaling, opaque session correlation, exact-key redaction semantics, and production deploy observability guards.
