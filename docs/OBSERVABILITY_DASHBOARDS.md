# Observability Dashboards

Stage 7 adds Grafana dashboard provisioning for the optional local observability stack.

## How To Load

Start the Stage 6 stack:

```bash
export GRAFANA_ADMIN_USER=admin
export GRAFANA_ADMIN_PASSWORD='use-a-long-random-password'
make observability-up
```

Open Grafana locally:

```text
http://127.0.0.1:3000
```

The dashboards are provisioned under the `Trading Bot` folder.

Default local URL:

```text
http://127.0.0.1:3000
```

## Dashboards

### Trading Bot API Overview

File: `observability/grafana/dashboards/api-overview.json`

Purpose:
- API request rate.
- Status-code distribution.
- p95 request duration from access logs.
- Top API paths.
- Recent API errors.

Primary labels:
- `service`
- `log_class`
- `status_code`
- parsed `path`

### Trading Bot Runtime, Jobs, and Realtime

File: `observability/grafana/dashboards/runtime-jobs-realtime.json`

Purpose:
- Background job run rate.
- Job error rate.
- Job p95 duration.
- Bot log activity.
- Realtime/WebSocket/Redis listener errors.

Primary labels:
- `log_class`
- `job_name`
- `service`
- `level`

### Trading Bot Security and Audit

File: `observability/grafana/dashboards/security-audit.json`

Purpose:
- Audit events by action.
- Audit event results.
- Auth/session failures.
- Unauthorized/forbidden API activity.
- Recent audit and security-relevant logs.

Sensitive-data rule:
- Panels must show audit summaries and ids only. Do not add panels that expose request bodies, tokens, OTPs, chat text, captions, mobile numbers, or raw secret-bearing fields.

### Trading Bot Business, Chat, and Upload

File: `observability/grafana/dashboards/business-chat-upload.json`

Purpose:
- Trade/offer/market event activity.
- Chat API errors.
- Upload/media failure logs.
- Recent trade/offer/chat logs.

Sensitive-data rule:
- Do not add message body, caption, file name, media URL, signed URL, or mobile-number fields to labels or table columns.

### Trading Bot Infrastructure Log Health

File: `observability/grafana/dashboards/infrastructure-log-health.json`

Purpose:
- Container log rate.
- Error rate by service/container.
- Startup and health logs.
- DB and Redis logs.

Current limitation:
- This is log-based. CPU, memory, disk, DB connection pool, and Redis memory require node/container exporters and are expected in later production hardening.

### Trading Bot Cross Server Sync

File: `observability/grafana/dashboards/cross-server-sync.json`

Purpose:
- Foreign/Iran sync backlog.
- Oldest unsynced change age.
- Redis outbound and retry queue state.
- Recent `sync.health` samples.
- Sync worker failures.
- Direct push cooldown/failure logs.

How to feed it:
- Run `make sync-health` on the foreign/local server.
- Run `make sync-health-iran` to sample the Iran server through SSH.
- For production, run both checks periodically through cron, systemd timer, or a small monitor container.

Runbook:
- `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md`

## Dashboard Policy

- Use bounded labels only: `service`, `level`, `log_class`, `event`, `logger`, `container`, stable status codes, and fixed action names.
- Use `| json` for high-cardinality fields such as `request_id`, `actor_id`, and `path`; do not promote them to labels.
- Keep Grafana and Loki bound to `127.0.0.1` unless production access control, TLS, and authentication are explicitly configured.
- Do not enable anonymous Grafana access.
