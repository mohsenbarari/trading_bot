# Observability Log Search

This document covers the Stage 6 local log collection/search setup.

## Local Stack

The optional stack lives in `docker-compose.observability.yml`:

- Loki: `127.0.0.1:3100`
- Grafana: `127.0.0.1:3000`
- Promtail: reads Docker JSON logs for containers named `trading_bot_*`

Start it only on trusted operator machines:

```bash
docker compose -f docker-compose.observability.yml up -d
```

Stop it:

```bash
docker compose -f docker-compose.observability.yml down
```

Grafana anonymous access is disabled. Set these environment variables before starting the stack:

```bash
export GRAFANA_ADMIN_USER=admin
export GRAFANA_ADMIN_PASSWORD='use-a-long-random-password'
```

Do not expose ports `3000` or `3100` publicly. The compose file binds both to `127.0.0.1`.

## Labels

Promtail keeps labels intentionally bounded:

- `container`
- `compose_project`
- `compose_service`
- `service`
- `level`
- `log_class`
- `event`
- `logger`

High-cardinality fields such as `request_id`, `actor_id`, `path`, and `status_code` are parsed from JSON but are not labels. Search them with `| json`.

## Search Examples

All API logs:

```logql
{service="api"}
```

Request by id:

```logql
{service="api"} | json | request_id="req-123"
```

HTTP 5xx responses:

```logql
{service="api", log_class="access"} | json | status_code >= 500
```

Audit events for one actor:

```logql
{log_class="audit"} | json | actor_id=42
```

Background job failures:

```logql
{log_class="job"} | json | result="failure"
```

Bot errors:

```logql
{service="bot"} |= "ERROR"
```

Realtime publish failures:

```logql
{service="api"} | json | event="realtime.publish.failure"
```

## Security Notes

- The application logging formatter already redacts passwords, tokens, OTPs, authorization headers, cookies, and common secret patterns.
- Do not add raw request bodies, chat text, captions, mobile numbers, file names, signed URLs, or tokens as labels.
- Keep Loki/Grafana behind private access. Public exposure requires authentication, TLS, and access review.
- Current local retention is `168h` in Loki config. Production retention should be finalized in Stage 11.

