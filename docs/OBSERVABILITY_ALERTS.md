# Observability Alerts

Stage 8 adds Grafana alert provisioning for the optional local observability stack.

## How Alerts Are Loaded

Alerting files:

- `observability/grafana/provisioning/alerting/contact-points.yml`
- `observability/grafana/provisioning/alerting/notification-policies.yml`
- `observability/grafana/provisioning/alerting/rules.yml`

Start the stack:

```bash
make observability-up
```

Grafana loads the rules into the `Trading Bot Alerts` folder.

## Contact Point

The local default receiver remains intentionally inert:

```text
http://127.0.0.1:9/trading-bot-alerts-disabled
```

This prevents accidental alert delivery from a cloned repository.

Provisioned receiver templates now exist for:

- `Trading Bot Local Webhook`
- `Trading Bot Production Webhook`
- `Trading Bot Production Email`

Notification routing is env-driven through the Grafana container environment:

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

Production guidance:

- Use `Trading Bot Production Webhook` when alerts must reach a Telegram admin channel through a private webhook bridge, Alertmanager relay, or internal notification gateway.
- Use `Trading Bot Production Email` only when Grafana SMTP is configured through env vars and the SMTP secret stays outside git.
- Keep local clones on `Trading Bot Local Webhook`.

Do not commit Telegram bot tokens, webhook secrets, email passwords, API keys, or private receiver URLs.

Example production shell exports before `make observability-up`:

```bash
export GRAFANA_ALERT_DEFAULT_RECEIVER='Trading Bot Production Webhook'
export GRAFANA_ALERT_CRITICAL_RECEIVER='Trading Bot Production Webhook'
export GRAFANA_ALERT_WARNING_RECEIVER='Trading Bot Production Email'
export GRAFANA_ALERT_WEBHOOK_URL='https://alerts-bridge.example.internal/trading-bot'
export GRAFANA_ALERT_EMAIL_ADDRESSES='ops@example.ir,dev@example.ir'
export GF_SMTP_ENABLED=true
export GF_SMTP_HOST='smtp.example.ir:587'
export GF_SMTP_USER='alerts@example.ir'
export GF_SMTP_PASSWORD='set-from-secret-store'
export GF_SMTP_FROM_ADDRESS='alerts@example.ir'
export GF_SMTP_FROM_NAME='Trading Bot Alerts'
```

## Alert Rules

### API service log stream silent

Trigger:
- No API logs for 10 minutes.

First checks:
- `docker compose ps app`
- `docker compose logs --tail=100 app`
- `curl -sS http://127.0.0.1:8000/api/config`

Likely causes:
- App container stopped or stuck during startup.
- Promtail cannot read Docker logs.
- Loki is unavailable.

### Bot service log stream silent

Trigger:
- No bot logs for 15 minutes.

First checks:
- `docker compose ps bot`
- `docker compose logs --tail=100 bot`
- Check Telegram connectivity and `BOT_TOKEN` availability without printing token values.

### API 5xx response spike

Trigger:
- 5xx access log rate remains above the initial threshold.

First checks:
- Search by recent `request_id` in Grafana:

```logql
{service="api", log_class="access"} | json | status_code >= 500
```

- Then use the `request_id` against app logs:

```logql
{service="api"} | json | request_id="..."
```

### Auth/session failure spike

Trigger:
- Auth/session 4xx/5xx failures increase.

First checks:
- Confirm whether failures are expected user mistakes or a real outage.
- Review only status codes, reason codes, and request ids. Do not inspect or add raw passwords, OTPs, cookies, authorization headers, or request bodies to logs.

### Audit failure or denied event detected

Trigger:
- Audit event with `result=failure` or `result=denied`.

First checks:
- Search audit events:

```logql
{log_class="audit"} | json | result=~"failure|denied"
```

- Use `actor_id`, `target_type`, `target_id`, and `action` to reconstruct the incident. Do not rely on names or mobile numbers.

### Background job repeated failures

Trigger:
- More than two job error logs in the evaluation window.

First checks:
- Search job errors:

```logql
{log_class="job"} | json | level=~"ERROR|CRITICAL"
```

- Check `job_name`, `run_id`, and `repeat_count`.

### WebSocket or Redis publish failures

Trigger:
- Realtime publish/listener warning or error logs.

First checks:
- Check Redis health:

```bash
docker compose ps redis
docker compose logs --tail=100 redis
```

- Search API realtime failures:

```logql
{service="api"} |~ "Error publishing|Error broadcasting|Redis listener"
```

### Chat upload or media failures

Trigger:
- Upload/media/file warning or error logs.

First checks:
- Check app logs around upload finalization.
- Check storage paths and disk pressure manually.
- Do not add file names, media URLs, captions, signed URLs, or message text to alert annotations.

### Captured exception spike

Trigger:
- Captured exception events are elevated.

First checks:
- Open `Trading Bot Error Tracking`.
- Follow `docs/OBSERVABILITY_ERROR_TRACKING.md#captured-exception-spike`.
- Use `error_fingerprint`, `request_id`, and `release_sha` for investigation.

### Cross-server sync backlog high

Trigger:
- `sync.health` samples show `unsynced_change_log_count > 100`.

First checks:
- `make sync-health`
- `make sync-health-iran`
- `make logs-jobs`
- Follow `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md#sync-backlog-high`.

### Cross-server sync lag high

Trigger:
- `sync.health` samples show `oldest_unsynced_age_seconds > 900`.

First checks:
- Confirm whether Iran was recently disconnected.
- If connectivity is restored, run `make sync-recover`.
- Follow `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md#sync-lag-high`.

### Cross-server sync retry queue non-empty

Trigger:
- `sync.health` samples show `sync_retry_queue_length > 0`.

First checks:
- `make sync-health`
- `make sync-health-iran`
- `make sync-recover`
- Follow `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md#sync-retry-queue-non-empty`.

## Not Fully Covered Yet

The following initial-alert goals require infrastructure metrics exporters and are intentionally deferred to Stage 11 production hardening:

- Disk usage high.
- Memory pressure.
- CPU saturation.
- DB connection pool pressure.
- Redis memory pressure.

The current Stage 8 coverage can still surface DB/Redis/app failures through logs, but it is not a substitute for node/container exporters.

## Alert Payload Rules

Alert summaries and annotations must include only:

- service/component
- severity
- bounded event/action name
- runbook link
- request id or event id when available

Alert payloads must not include:

- passwords
- OTPs
- access or refresh tokens
- cookies
- authorization headers
- raw request/response bodies
- chat message text or captions
- mobile numbers
- file names
- media URLs or signed URLs

The provisioned webhook/email templates are intentionally limited to:

- `alertname`
- `severity`
- `component`
- safe `summary`
- `runbook_url`
- `request_id`
- `event_id`

They do not include raw bodies, chat text, path tokens, filenames, URLs, or identifiers outside explicit request/event ids.

## Baseline Snapshot

Baseline date:

- `2026-06-09`

Observed environment:

- current foreign runtime with live sync backlog present
- low-traffic operator smoke window for API/bot/chat paths
- no intentional public traffic replay

Initial observed ranges and tuned thresholds:

| Alert family | Observed range on 2026-06-09 | Threshold kept/tuned |
| --- | --- | --- |
| API 5xx spike | `0` observed during smoke window | keep `rate > 0.05` over `5m` for `5m` |
| Auth/session failure spike | `0` observed during smoke window | keep `rate > 0.2` over `5m` for `5m` |
| Chat upload/media failures | `0` observed during smoke window | keep `count > 0` over `5m` for `2m` |
| Captured exception spike | `0` observed on clean runtime; test-induced failures exceed threshold by design | keep `count > 3` over `5m` for `5m` |
| Sync backlog high | live sample showed `3513` unsynced rows | keep `backlog > 100` for `10m`; this should alert until recovery |
| Sync lag high | live sample showed `287238s` oldest unsynced age | keep `age > 900s` for `10m`; this should alert until recovery |
| Sync retry queue non-empty | live sample showed `3504` retry items | keep `retry queue > 0` for `5m`; this should alert until recovery |

Interpretation:

- non-sync alert families are still in a low-traffic baseline phase, so thresholds remain conservative and intentionally low
- sync alerts are already proving useful because the current foreign runtime is not in steady state
- after the first stable post-recovery week, refresh this table with a real 24h production sample and adjust only if alert noise is confirmed
