# Observability Production Hardening

Stage 11 closes the observability rollout with production controls for retention, access, audit export, log volume, exception storms, and logging overhead.

## Retention Policy

Current local Loki retention is configured in `observability/loki/loki-config.yml`:

```yaml
limits_config:
  retention_period: 168h
  reject_old_samples_max_age: 168h
```

Baseline policy:

| Log class | Default retention | Notes |
| --- | --- | --- |
| `access` | 7 days local, 14-30 days production | Keep request ids, routes, status classes, and durations only. |
| `application` | 7 days local, 14-30 days production | No raw payloads. |
| `job` | 7 days local, 30-60 days production | Required for recurring worker diagnosis. |
| `realtime` | 7 days local, 14-30 days production | Keep event/reason ids, not message content. |
| `audit` | 7 days local, 90+ days exported archive | Export to restricted storage before retention expiry. |
| `security` | 7 days local, 90+ days exported archive | Treat as restricted operational data. |
| `error` | 7 days local, 30-90 days production | Group by fingerprint and release. |

Production deployments should use deployment-specific storage, backup, and access policies before increasing retention above the local default.

## Dashboard Access Control

The optional Grafana stack binds ports to `127.0.0.1` and disables anonymous access:

```yaml
GF_AUTH_ANONYMOUS_ENABLED: "false"
GF_USERS_ALLOW_SIGN_UP: "false"
```

Production requirements:

- Use a strong `GRAFANA_ADMIN_PASSWORD`.
- Do not expose Grafana or Loki directly to the public internet.
- Put Grafana behind trusted VPN, SSH tunnel, or authenticated reverse proxy with TLS.
- Restrict dashboard access to operators and developers who need incident visibility.
- Do not create dashboard variables from high-cardinality or secret-bearing fields.
- Rotate Grafana credentials when team access changes.

Promtail currently discovers Docker containers through a read-only Docker socket mount. That is acceptable only for the local operator stack on a trusted host. If the observability stack is moved to a shared or less-trusted environment, replace direct socket access with a socket proxy or file-based scrape path before rollout.

## Production Alert Delivery

Provisioned alert delivery is intentionally env-driven. The repo ships three receiver names:

- `Trading Bot Local Webhook`
- `Trading Bot Production Webhook`
- `Trading Bot Production Email`

The Grafana container reads these environment variables:

```text
GRAFANA_ALERT_DEFAULT_RECEIVER
GRAFANA_ALERT_CRITICAL_RECEIVER
GRAFANA_ALERT_WARNING_RECEIVER
GRAFANA_ALERT_WEBHOOK_URL
GRAFANA_ALERT_EMAIL_ADDRESSES
GF_SMTP_ENABLED
GF_SMTP_HOST
GF_SMTP_USER
GF_SMTP_PASSWORD
GF_SMTP_FROM_ADDRESS
GF_SMTP_FROM_NAME
```

Policy:

- keep the default local receiver inert in clones and developer machines
- use a private webhook bridge for Telegram admin-channel delivery instead of committing bot tokens into Grafana provisioning
- keep SMTP credentials in deployment secrets only
- rotate webhook URLs, SMTP passwords, and admin access when operators change
- never put secret receiver values into git-tracked env files

Baseline alert thresholds and observed ranges are recorded in `docs/OBSERVABILITY_ALERTS.md`.

## Audit Log Export

Audit logs are operationally sensitive. Keep short local retention and export required windows to restricted storage.

Runtime audit policy:

- `core.audit_logger.audit_log()` still emits searchable stdout/Loki audit events.
- Production Compose also enables a durable append-only JSONL trail through `AUDIT_TRAIL_PATH=/app/audit_trail/audit.jsonl`.
- The API container mounts the named `audit_data` Docker volume at `/app/audit_trail`.
- Each durable record includes `audit_event_id`, `audit_recorded_at`, `previous_hash`, `event_hash`, and the redacted audit payload.
- The hash chain is tamper-evident, not encryption. Keep volume and exported files restricted.

Export from local Loki:

```bash
make audit-log-export
```

Customize the window:

```bash
make audit-log-export ARGS="--hours 72 --limit 10000"
```

Export and verify the durable local trail:

```bash
python3 scripts/export_audit_logs.py \
  --source=file \
  --input=/app/audit_trail/audit.jsonl \
  --output=tmp/audit-log-exports/audit-trail.jsonl
```

The exporter writes JSONL files under:

```text
tmp/audit-log-exports/
```

Each export writes a `.manifest.json` sidecar with record count, source, output SHA-256, and integrity metadata. Loki exports are paged with `--page-size` so windows larger than 5000 records do not silently truncate at the first page.

Export files must be moved to restricted storage if they are needed beyond local retention. Do not commit exported audit logs.

## External Audit Integrity Anchor

The local append-only audit trail is durable and hash chained, but it still lives on the same host as the application. Production should add an external anchor so host compromise cannot silently rewrite both the trail and its latest known head hash.

Recommended path:

1. Keep `AUDIT_TRAIL_PATH` enabled on every production API container.
2. On a fixed interval, export only the latest durable head metadata:
   - `audit_event_id`
   - `audit_recorded_at`
   - `event_hash`
   - `previous_hash`
   - release identifier / host identifier
3. Push that compact head record to an external append-only destination that is not writable by the app container runtime. Acceptable options:
   - foreign-host restricted object storage bucket with versioning and immutable retention
   - operator-controlled Git repository or signed manifest store
   - separate SIEM / audit archive endpoint
4. Store export manifests with monotonic timestamps so missing anchors are also visible.
5. Verify periodically that:
   - the local trail hash-chain is intact,
   - the latest exported anchor matches the local head for the same window,
   - the external destination has stricter access controls than the app host.

This stage adds `audit_durable` signaling into runtime audit logs. That signal answers whether a record was durably appended locally; it does not replace the external anchor requirement above.

R12 adds a concrete export step so this is no longer design-only:

```bash
make audit-anchor-export ARGS="--input /app/audit_trail/audit.jsonl"
```

Export behavior:

- verifies the full local audit hash chain before emitting anything
- emits only compact head metadata plus trail digest and record count
- fails closed on malformed JSON, `previous_hash` drift, or `event_hash` mismatch
- can print to stdout (`--output -`) so a host-level timer can append outside the app container write surface

Recommended production installation:

```bash
make audit-anchor-monitor-install
```

The installed timer runs every 5 minutes, executes the exporter inside the API container, and appends the compact anchor line to a host-level path such as:

```text
/var/lib/trading-bot-observability/audit-anchor.jsonl
```

That host file is intentionally outside the app container writable path. It is still not the final remote sink; operators should replicate or back up that file to the foreign restricted destination described above.

R13 adds that replication path directly:

```bash
make audit-anchor-ship ARGS="--input /var/lib/trading-bot-observability/audit-anchor.jsonl --remote ops@foreign-audit.example:/srv/trading-bot-audit/audit-anchor.jsonl"
```

Behavior:

- reads only the latest non-empty compact anchor line
- validates required anchor fields before shipping
- appends only that compact line to the remote or relay destination
- fails closed on malformed local anchor JSON or partial payloads

Recommended production installation:

```bash
make audit-anchor-ship-install
```

The shipper timer runs every 10 minutes and is intended to move the compact host-level anchor off-host to a restricted append-only destination. This is the first point where the evidence leaves the app host entirely.

## Log Volume Budget

Default production budget:

| Surface | Budget |
| --- | --- |
| API access logs | One completion/failure log per request. |
| Bot logs | One start/failure summary per update class; no raw Telegram payloads. |
| Jobs | One success/failure summary per iteration; repeated identical failures use suppression. |
| Realtime | State transitions and failures only; no per-message noisy logs. |
| Chat upload/media | Lifecycle milestone logs only; no filenames, captions, signed URLs, or message text. |
| Debug diagnostics | Disabled by default and gated by explicit flags. |

Any new high-frequency log path must define:

- event name
- log class
- expected maximum rate
- labels and parsed fields
- retention impact
- redaction behavior

## Sampling and Repeated Exceptions

Unexpected exceptions are captured by `core/error_tracking.py` and grouped with `error_fingerprint`.

Repeated capture is rate-limited by:

```text
ERROR_TRACKING_RATE_LIMIT_WINDOW_SECONDS=60
ERROR_TRACKING_MAX_EVENTS_PER_FINGERPRINT=10
ERROR_TRACKING_RATE_LIMIT_MAX_FINGERPRINTS=2048
```

Behavior:

- first repeated errors in the window are logged normally
- additional identical fingerprints are suppressed
- the limiter map is capped to avoid long-lived browser/server memory growth patterns in the Python process
- a later emitted event includes `suppressed_repeats` when suppressed events existed in the previous window

Expected business validation errors should remain structured warnings or normal HTTP responses, not captured exceptions.

## Logging Overhead Check

Measure local structured logging overhead:

```bash
make observability-overhead
```

Default acceptance budget:

```text
per_event_overhead_us <= 1000
```

If the budget fails:

1. Check whether a recent change added large `extra` payloads.
2. Remove raw object dumps and replace them with stable ids.
3. Keep stack traces only for captured errors.
4. Re-run the overhead check before deploying.

## Production Observability Readiness

Stage P9 adds a single production readiness report:

```bash
make observability-readiness
```

The report is also wired into the production benchmark runner through
`PROFILE=observability`. It verifies:

- structured logging overhead against the configured budget
- the explicit memory-backend metrics contract
- durable audit-trail anchor export
- audit-anchor shipper behavior through a local relay artifact
- required sync-health sampler timers on both foreign and Iran hosts
- benchmark artifact hygiene for blocked sensitive patterns
- clean foreign and Iran sync-health

Audit anchor export/shipper timers are reported as optional until a production
anchor sink is configured. Once `AUDIT_ANCHOR_*` deployment values point to a
real restricted sink, operators should install and monitor those timers as part
of the release checklist.

## Metrics Backend Policy

R2 chooses the conservative production-safe default:

```text
TRADING_BOT_METRICS_BACKEND=memory
```

This keeps request, bot, job, websocket, sync, and audit metric updates in process memory and avoids SQLite file locks or disk I/O in hot paths. The `/metrics` response includes explicit backend metadata:

```text
trading_bot_metrics_backend_info{backend="memory",service="api",shared="false"} 1
```

Service semantics:

- `api` metrics are exposed by the API container process that receives the scrape.
- `bot` and `sync_worker` maintain their own in-process metrics. They are not automatically merged into the API `/metrics` response when the backend is `memory`.
- Docker Compose sets `TRADING_BOT_SERVICE` for `api`, `bot`, and `sync_worker` so each process can identify its metrics surface.

The legacy SQLite aggregation path is now explicit opt-in only:

```text
TRADING_BOT_METRICS_BACKEND=shared_sqlite
TRADING_BOT_METRICS_DB=/tmp/trading_bot_metrics.sqlite3
```

Use `shared_sqlite` only for local development or short diagnostic windows. It restores cross-process aggregation through a shared file, but it reintroduces SQLite write cost into high-frequency metric paths.

R2 validation result:

```text
make observability-overhead
per_event_overhead_us=377.33
budget_us=1000.0
acceptable=true
```

## Production Metrics Architecture

The current `memory` backend is the correct local default, but it is not the final production architecture.

Production interpretation rules:

- `api`:
  - Local `/metrics` on an API worker exposes only the in-process view of that worker.
  - Because the API runs with multiple Uvicorn workers, a single scrape of one worker is not a full application aggregate.
- `bot`:
  - Bot metrics are produced in the bot process and are not exported through the API `/metrics` endpoint when the backend is `memory`.
- `sync_worker`:
  - Sync-worker metrics are produced in the worker process and are not exported through the API `/metrics` endpoint when the backend is `memory`.

Production path for this project:

1. Keep `TRADING_BOT_METRICS_BACKEND=memory` as the local/runtime-safe default during development and current release hardening.
2. Treat API, bot, and sync-worker as separate metric surfaces unless an explicit aggregation/export layer is enabled.
3. Before relying on metrics for production SLOs or alert thresholds, deploy one of these explicit aggregation strategies:
   - per-service scrape endpoints with operator-visible separation, or
   - a dedicated exporter/aggregation layer that merges API multi-worker, bot, and sync-worker metrics deliberately.
4. Do not assume the API `/metrics` endpoint is a full-system aggregate while the backend remains `memory`.

Until that production aggregation layer exists, Grafana dashboards and alerting should treat logs/Loki as the authoritative cross-service source and use metrics as bounded local process telemetry.

R12 also adds an explicit operator manifest for this contract:

```bash
make metrics-targets
```

Current interpretation:

- `api`: scrapeable over HTTP with `X-Observability-Api-Key`, but only authoritative for the single API process that answered.
- `bot`: no direct metrics scrape surface under the `memory` backend; production should treat Loki/logs as authoritative for bot alerts until a dedicated exporter or sidecar exists.
- `sync_worker`: same as bot; rely on Loki plus the sync-health sampler until a dedicated exporter or sidecar exists.

This is deliberate. The repo should not imply a false full-system aggregate while the runtime remains on the low-risk `memory` backend.

## Security Constraints

Never log or export:

- passwords
- OTPs
- access or refresh tokens
- cookies
- authorization headers
- raw request bodies
- chat message text or captions
- media filenames
- signed URLs
- full mobile numbers

Allowed operational identifiers:

- request ids
- actor ids
- room/message/upload ids
- route templates
- status classes/codes
- error fingerprints
- job names
- release/environment metadata

## Stage 11 Validation Checklist

```bash
make -n observability-overhead audit-log-export
python3 scripts/measure_logging_overhead.py --iterations 1000
python3 scripts/export_audit_logs.py --help
docker compose -f docker-compose.observability.yml config
```

`audit-log-export` requires Loki to be running. Do not treat a stopped local Loki as an application failure.
