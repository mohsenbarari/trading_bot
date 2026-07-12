# Dual-Platform Registration Stage 8: Observability And Audit

Date: 2026-07-12

Branch: `candidate/bot-webapp-integration`

Parent commit: `cf1d728ae03df94a4f79dba952718b7f1300236b`

Controlling roadmap:
`docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`

## Scope

Stage 8 adds PII-free observability to the registration reconciliation and synchronized OTP paths
implemented in Stages 4 through 6. It reuses the existing metrics registry, `/metrics`, signed sync
health endpoint, structured logging, durable audit sink, production alert evaluator, background
leader, Loki, and Grafana alert provisioning.

This stage does not add a dashboard, monitoring service, worker service, database table, migration,
admin/support action, intent browser, retry endpoint, frontend behavior, invitation lifecycle, sync
transport, CDN, or object-storage path. It does not enable a feature flag or deploy any source.

## Runtime Design

### Job snapshots

Each existing job writes one local Redis snapshot after a cycle:

- `telegram_registration_reconciliation` on foreign;
- `otp_sms_fallback` on Iran.

The key is `observability:registration_job:<bounded-job-name>` with a one-hour TTL. Its schema is
restricted to job/server identity, heartbeat, last success/error timestamps, a bounded error code,
pending count, oldest age, last batch size/duration, connectivity classification, and lag. It has no
mobile, Telegram ID, name, address, invitation token, OTP code, delivery target, command body, or
provider response. Invalid JSON, invalid UTF-8, arbitrary error text, malformed numbers, NaN, and
infinity fail closed to a missing/sanitized snapshot.

The application runs multiple Uvicorn workers while the background leader runs in only one process.
Because the current Prometheus registry defaults to process memory, `/metrics` rehydrates gauges
from the Redis snapshots before rendering. Hydration has a 250 ms bound, is fail-open for metrics
availability, and does not increment cycle counters. The worker that actually completed a cycle is
the only owner of the cycle counter.

Event counters and histograms retain the existing registry's process-local production semantics.
They are diagnostic samples rather than cross-worker exact totals. Stage 8 does not reintroduce the
previous shared-SQLite hot-path writes or add Redis writes to every auth/audit event. Operational
alerts use the shared job snapshots and structured logs, not these process-local totals. Whether the
repository-wide metrics architecture needs a separate low-overhead multi-process aggregation design
is intentionally outside this registration stage and is called out for independent risk review.

### Queue summaries

The foreign reconciliation summary performs one aggregate PostgreSQL query over nonterminal intent
statuses. It returns count and minimum `created_at`; it never loads command payloads or identity
fields. Connectivity health comes from the current reconciliation cycle's transport responses and
retains the previous observation only when no transport attempt occurs. Historical row error codes
therefore cannot suppress the healthy-connectivity pending alert after a successful peer response.

The Iran OTP summary reads only `ZCARD` and the oldest score from the existing fallback due sorted
set. It does not load OTP state, mobile ciphertext, Telegram identity, or code. Lag is the positive
difference between the current UTC time and the oldest due score.

### Health contract and thresholds

`/api/sync/health` now includes `registration_jobs` with role-aware `disabled`, `not_expected`,
`missing`, `stale`, or `healthy` status and these locked thresholds:

- job heartbeat age: at most 60 seconds;
- registration oldest pending age: at most 300 seconds while connectivity is healthy;
- automatic SMS fallback lag: at most 2 seconds.

The existing production alert report consumes this contract. Four new Grafana/Loki rules consume
the existing redacted `sync.health` log: both heartbeat alerts, healthy-connectivity registration
pending age, and SMS fallback lag. The rules use the existing contact policy and do not create a new
dashboard or alert system. The existing market p95 alert remains unchanged.

### Metrics

The existing metrics registry now exposes bounded-label counters, gauges, and histograms for:

- first terminal registration completion by `telegram` or `webapp` and bounded outcome;
- reconciliation result/status counts;
- authoritative Iran invitation completion to foreign projection visibility latency;
- job heartbeat, last success/error, pending, oldest age, batch size/duration, lag, and connectivity;
- OTP request, Telegram delivery result, fallback scheduling/claim, SMS delivery result, and
  verification before or after fallback;
- fallback processing delay from its scheduled deadline.

Labels are fixed surface, outcome, status, event, job, and server values. No natural identity or
request payload is a metric label.

### Audit events

The stage preserves existing registration events and adds the missing direct-flow contact/open and
OTP lifecycle audit calls through `core.audit_logger.audit_log`. First-terminal authoritative
registration completion remains guarded by `first_terminal_transition`, so replay cannot duplicate
the terminal success/rejection event. OTP provider outcomes are converted to the audit sink's strict
`success`, `failure`, or `denied` vocabulary. `otp.sms_fallback_scheduled` records `result=success`
and keeps `scheduled` only as a bounded lifecycle field, preventing a false audit-failure alert.

`otp.expired` is not fabricated in this source change. Natural expiration is an atomic passive Redis
TTL event: the OTP code, pointer, and short-lived state expire together, so there is no reliable
first-terminal application hook after expiry. Retaining identity state longer, adding a second expiry
index/worker, or accepting a client-supplied request ID as expiration evidence would alter Stage 6
retention/authority or create verify/expiry races. This is an explicit independent-review question,
not a hidden claim of full event coverage. Stage 9 must either prove that passive TTL evidence is an
approved exception or define a race-safe design change before the roadmap's `otp.expired` event is
marked closed.

## Safety And Compatibility

- Iran remains authoritative for Web registration, invitation/User mutation, OTP creation,
  verification, and SMS delivery.
- Foreign remains owner of Telegram bot delivery and registration reconciliation.
- Existing job placement and leader ownership are unchanged.
- Existing pending-invitation Web UI and all bot onboarding copy/order are unchanged.
- Existing database backup, staging reset, and fixture ownership remain unchanged because Stage 8
  adds no durable table or schema.
- Feature-disabled and wrong-role health states are explicit and do not produce job alerts.
- Healthy-connectivity pending alerts are suppressed for the existing transport-outage reason.
- No exception text is persisted as an error code; only a bounded safe code is accepted.
- Snapshot corruption or Redis hydration delay cannot make `/metrics` unavailable.

## Verification Evidence

The following evidence was generated without a deployment, migration, feature enablement, Telegram
provider call, SMS provider call, or production action:

- focused registration/OTP/metrics/health/alerts matrix: 241 tests passed;
- full backend suite: 2,930 passed, 56 skipped;
- sandbox-blocked deploy-smoke tests rerun with required filesystem access: 2 passed;
- disposable real PostgreSQL registration suite: 12 passed and the scratch database was dropped;
- isolated real Redis DB 15 OTP suite: 9 passed and DB 15 was empty after teardown;
- full frontend unit suite: 128 files and 1,117 tests passed;
- production frontend build: passed;
- Python compile and `git diff --check`: passed;
- Grafana YAML: 4 groups, 18 rules, and 18 unique UIDs.

Raw evidence is retained under `tmp/stage8/` and copied into the independent-review ZIP. The first
sandboxed full-backend run had three permission-only deploy-smoke failures; the permission-specific
rerun passed and a clean full-backend rerun then passed. `ruff` was unavailable and is not claimed.

## Exit Assessment

Implemented and verified:

- terminal intent outcomes remain explicit and first-terminal metrics/audit are replay-safe;
- pending/oldest age, heartbeat, healthy-connectivity, OTP lag, and existing market alert paths use
  the current monitoring stack;
- logs, metrics, health snapshots, and alert labels are PII/secret bounded;
- no manual-review path or administrative registration action was introduced;
- existing pending-invitation UI remains unchanged.

Open for independent review before Stage 8 is declared fully closed:

- disposition of the roadmap's passive `otp.expired` event requirement without extending retention
  or introducing a conflicting expiry owner;
- acceptance of the existing process-local semantics for event counters/histograms; only job gauges
  are rehydrated across API workers and operational alerts do not depend on process-local totals;
- operational validity of the four Loki expressions against the deployed Loki/Grafana versions,
  which belongs to real staging validation rather than this source-only stage.

## Operational State

All registration and synchronized OTP feature flags remain off. No push, staging deploy, production
deploy, database migration, Redis mutation outside the disposable test DB, or provider action is
authorized by this implementation record.
