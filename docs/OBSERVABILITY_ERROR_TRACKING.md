# Observability Error Tracking

Stage 9 adds scrubbed exception grouping without requiring a third-party error service.

## Runtime Behavior

`core/error_tracking.py` captures unexpected exceptions and emits structured logs with:

- `event=error.exception.captured`
- `log_class=error`
- `error_fingerprint`
- `error_source`
- `exception_type`
- redacted `exception_message`
- project-local stack frames
- `request_id`
- `actor_id`
- `actor_role`
- `path`
- `method`
- `job_name`
- `run_id`
- `bot_event_type`
- `environment`
- `release_sha`

The fingerprint is stable for the same source, exception type, and project-local stack location. It intentionally avoids raw exception values so passwords, tokens, OTPs, mobile numbers, and user input do not affect grouping.

## Capture Points

Current capture points:

- API request middleware unexpected exceptions.
- Bot update middleware exceptions.
- Background job repeated-error logger.

Expected business validation errors should remain ordinary HTTP responses or structured warning/info logs, not captured errors.

## Optional Sentry Bridge

The code has an optional bridge if `sentry_sdk` is installed and `ERROR_TRACKING_DSN` is configured.

Environment variables:

```text
ERROR_TRACKING_DSN=
ERROR_TRACKING_SAMPLE_RATE=1.0
RELEASE_SHA=
ENVIRONMENT=production
```

Do not commit DSNs or Sentry auth tokens. `send_default_pii` is disabled when the optional SDK is initialized.

External forwarding is scrubbed in two layers:

- `capture_exception()` emits a sanitized structured Sentry event with the same redacted fields used by local JSON logs. It does not call `sentry_sdk.capture_exception(exc)` with the raw exception object.
- `configure_logging()` registers `core.error_tracking.scrub_sentry_event` as Sentry `before_send`, so SDK-generated events are recursively redacted before leaving the process. Request bodies, cookies, and request env data are replaced with `[REDACTED]`.

## Grafana

Dashboard:

```text
Trading Bot Error Tracking
```

File:

```text
observability/grafana/dashboards/error-tracking.json
```

Primary searches:

```logql
{log_class="error", event="error.exception.captured"} | json
```

Group by fingerprint:

```logql
topk(10, sum by (error_fingerprint, exception_type, error_source) (
  count_over_time({log_class="error", event="error.exception.captured"} | json [24h])
))
```

Trace by request id:

```logql
{service="api"} | json | request_id="..."
```

## Alert

### Captured exception spike

Trigger:
- More than three captured exceptions in the five-minute window for five minutes.

First checks:
- Open the Error Tracking dashboard.
- Identify the top `error_fingerprint`.
- Search all logs for the same fingerprint:

```logql
{log_class="error"} | json | error_fingerprint="..."
```

- Search by `request_id` if present.
- Compare `release_sha` against the latest deployment.

## Security Rules

Captured error events must not include:

- raw request bodies
- response bodies
- headers
- cookies
- authorization values
- passwords
- OTPs
- refresh/access tokens
- chat message text or captions
- file names
- media URLs or signed URLs
- mobile numbers

Only stable ids, roles, route paths, and redacted exception summaries are allowed.
