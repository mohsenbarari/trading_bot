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

## Audit Log Export

Audit logs are operationally sensitive. Keep short local retention and export required windows to restricted storage.

Export from local Loki:

```bash
make audit-log-export
```

Customize the window:

```bash
make audit-log-export ARGS="--hours 72 --limit 10000"
```

The exporter writes JSONL files under:

```text
tmp/audit-log-exports/
```

Export files must be moved to restricted storage if they are needed beyond local retention. Do not commit exported audit logs.

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
