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

The provisioned contact point is intentionally inert:

```text
http://127.0.0.1:9/trading-bot-alerts-disabled
```

This prevents accidental alert delivery from a cloned repository. Before production use, configure a real receiver in Grafana UI or replace the local webhook through deployment-specific secret management.

Do not commit Telegram bot tokens, webhook secrets, email passwords, API keys, or private receiver URLs.

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

