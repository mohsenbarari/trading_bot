# Observability Runbook

Stage 10 gives developers and operators a short path from a reported symptom to the relevant logs, metrics, dashboard, and next check. The commands here avoid container-name knowledge where possible.

## Quick Commands

```bash
make logs-api
make logs-bot
make logs-jobs
make logs-follow
make metrics
make observability-up
make observability-down
make observability-logs
```

Use `make observability-up` only on a trusted operator machine. Grafana and Loki are bound to `127.0.0.1` by default.

## First Triage

1. Check runtime health:

```bash
docker compose ps
```

2. Check application metrics:

```bash
make metrics
```

3. Follow service logs:

```bash
make logs-follow
```

4. If the local observability stack is running, open Grafana:

```text
http://127.0.0.1:3000
```

Use the dashboards under the `Trading Bot` folder.

## Trace a Failed Login

Goal: identify whether the failure is an expected auth rejection, throttling, session issue, or unexpected exception.

1. Check API access logs around the user report time:

```bash
make logs-api
```

2. In Grafana/Loki, search auth routes and failures:

```logql
{service="api", log_class="access"} | json | path =~ "/api/auth.*|/api/sessions.*" | status_code >= 400
```

3. If a `request_id` appears, pivot to all logs for that request:

```logql
{service="api"} | json | request_id="req-..."
```

4. Check auth/session security or audit events:

```logql
{log_class=~"security|audit"} | json | event =~ ".*auth.*|.*login.*|.*session.*"
```

5. If an `error_fingerprint` exists, open the Error Tracking dashboard and search:

```logql
{log_class="error", event="error.exception.captured"} | json | error_fingerprint="..."
```

Never add OTPs, passwords, cookies, authorization headers, or raw request bodies to logs while investigating.

## Trace a WebSocket Disconnect

Goal: separate normal client navigation from auth failures, Redis publish/listener failures, or server errors.

1. Search realtime events:

```logql
{service="api"} | json | event =~ "realtime.*|websocket.*"
```

2. Check publish/listener failures:

```logql
{service="api"} | json | event =~ "realtime.publish.failure|realtime.listener.failure"
```

3. Check Redis container logs if publish/listener errors cluster:

```bash
docker compose logs --tail=100 redis
```

4. Check related metrics:

```bash
make metrics
```

Look for realtime connection gauges/counters and HTTP 5xx growth around the same time.

## Trace a Failed Media Upload

Goal: identify client upload failures, API finalize failures, background upload-session failures, or media processing failures.

1. Search upload/media events:

```logql
{service="api"} | json | event =~ ".*upload.*|.*media.*|.*file.*"
```

2. Search failed API requests on chat/upload routes:

```logql
{service="api", log_class="access"} | json | path =~ "/api/chat.*|/api/files.*|/api/upload.*" | status_code >= 400
```

3. If a room/message/upload identifier appears, search by the stable id only. Do not log or search raw file names, captions, signed URLs, media URLs, or message text.

4. Check the Business Chat Upload dashboard for failure rate and recent logs.

## Trace a Trade Action

Goal: reconstruct what happened without exposing financial secrets or user-private text.

1. Search audit events:

```logql
{log_class="audit"} | json | event =~ ".*trade.*|.*offer.*|.*market.*"
```

2. If an `actor_id` is known:

```logql
{log_class="audit"} | json | actor_id=123
```

3. Search API requests around the action:

```logql
{service="api", log_class="access"} | json | path =~ "/api/.*trade.*|/api/.*offer.*|/api/.*market.*"
```

4. Check repeated job failures if the action depends on scheduled processing:

```logql
{log_class="job"} | json | result="failure"
```

## Investigate Worker Failures

Goal: find the failing recurring job and distinguish one-off exceptions from repeated failures.

1. Follow app and bot logs:

```bash
make logs-jobs
```

2. Search job failures:

```logql
{log_class="job"} | json | result="failure"
```

3. Group by job name:

```logql
sum by (job_name) (count_over_time({log_class="job"} | json | result="failure" [15m]))
```

4. If `error_fingerprint` is present, pivot to captured exceptions:

```logql
{log_class="error", event="error.exception.captured"} | json | error_fingerprint="..."
```

5. Check metrics:

```bash
make metrics
```

Look for job run counters and failure counters changing during the incident window.

## Check Alert Causes

Goal: move from a Grafana alert to the first useful evidence quickly.

1. Open the alert details in Grafana and note:

- rule name
- evaluation time
- labels
- runbook URL
- any `error_fingerprint`, `event`, `service`, `log_class`, or `job_name`

2. Open the documented runbook:

- `docs/OBSERVABILITY_ALERTS.md`
- `docs/OBSERVABILITY_ERROR_TRACKING.md`
- this file

3. Search the exact alert dimensions in Loki. Examples:

```logql
{service="api", log_class="access"} | json | status_code >= 500
```

```logql
{log_class="error", event="error.exception.captured"} | json
```

```logql
{log_class="job"} | json | result="failure"
```

4. Check whether the problem is still active:

```bash
docker compose ps
make metrics
```

5. Record the final incident note with request ids, stable object ids, fingerprints, timestamps, and remediation. Do not include secrets, OTPs, tokens, cookies, full mobile numbers, chat text, captions, file names, or signed URLs.

## Related Documents

- `docs/OBSERVABILITY_LOG_SEARCH.md`
- `docs/OBSERVABILITY_DASHBOARDS.md`
- `docs/OBSERVABILITY_ALERTS.md`
- `docs/OBSERVABILITY_ERROR_TRACKING.md`
