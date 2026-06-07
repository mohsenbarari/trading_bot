# Logging Setup

This project keeps runtime secrets and environment-specific values in gitignored `.env` files. Do not commit `.env`, API keys, JWT secrets, SMS credentials, sync keys, or provider tokens.

## Phase 1 configuration

The committed logging foundation only needs setting names, not secret values. These settings can be added to each server's local `.env` file if the defaults are not enough:

```env
# Optional; defaults shown here are safe examples, not production secrets.
ENVIRONMENT=production
LOG_LEVEL=INFO
LOG_FORMAT=json
RELEASE_SHA=
DOCKER_LOG_MAX_SIZE=50m
DOCKER_LOG_MAX_FILE=5
```

`LOG_FORMAT=json` is intended for production because every log line becomes machine-searchable. `LOG_FORMAT=text` is only for local debugging.

## What is logged

Application services emit compact structured logs to stdout:

- `api` logs startup/shutdown, request summaries, stale frontend chunk handling, and unhandled request failures.
- `bot` logs startup/shutdown and polling failures.
- `sync_worker` logs configuration issues, retry/failure summaries, invalid queue payloads, and debug-level per-item success details.
- `api.request` logs `request_id`, HTTP method, path, status code, duration, and client IP when available.

Docker remains the log transport. Compose files rotate container logs with defaults that can be overridden per server through local environment variables.

## What must not be logged

Never log full values for:

- JWT access/refresh tokens
- Authorization headers
- OTP codes
- API keys, dev keys, sync keys, SMS keys
- passwords
- cookies
- full mobile numbers
- raw chat message text or uploaded file content

The formatter applies best-effort redaction for common key names and text patterns, but callers should still avoid passing sensitive payloads to log statements.

## External information needed later

For the current Phase 1 logging foundation, no private value is required beyond optional `.env` overrides listed above.

For later phases, provide these decisions/values outside git:

1. Central log sink choice, if any: none, Grafana Loki, ELK/OpenSearch, Sentry, or another provider.
2. If using an external provider: endpoint URL, tenant/project name, token/DSN, and retention policy.
3. Preferred retention: app logs, Nginx logs, Docker logs, and audit logs.
4. Alert destinations: Telegram admin chat, email, SMS, Grafana contact point, or another channel.
5. PII policy: whether mobile numbers should be fully hidden, partially masked, or only stored in audit logs.
6. Audit-log retention and access rules for trade/admin/security actions.

Keep provider tokens, DSNs, and webhook URLs only in the server `.env` files or secret manager.
