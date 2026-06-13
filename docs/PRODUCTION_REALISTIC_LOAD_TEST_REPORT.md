# Production Realistic Load Test Report

Last updated: 2026-06-13

## Current Decision

The current Iran production profile is proven for the official short target:
`500 RPS / 10m`.

It is not proven for sustained `500 RPS / 30m` soak. The L9 soak completed
with clean recovery and no lasting sync residue, but it failed latency,
throughput, dropped-iteration, and sampler gates. Treat this as a capacity
boundary, not as an application crash.

L11 outcome: `release_ready_with_limits`.

The first release can proceed only if the release notes/runbook document that
the current validated capacity is the L7 target shape, and that sustained
30-minute `500 RPS` pressure requires follow-up optimization before being
advertised as supported capacity.

## L11 Release Capacity Decision

Decision: `release_ready_with_limits`.

Reason:

- `500 RPS / 10m` passed with clean health, clean sync recovery, and acceptable
  latency.
- `750 RPS / 5m` degraded gracefully without request failures or new Nginx 5xx,
  proving the system does not collapse immediately above target.
- `500 RPS / 30m` did not pass sustained latency/throughput gates, so the
  release cannot honestly claim sustained 30-minute `500 RPS` capacity.
- Post-run cleanup and sync recovery were clean, so this is a capacity/UX
  limitation, not a data-integrity or recoverability blocker.

Release condition:

```text
The first release may proceed with operational limits:
- validated mixed-load capacity: 500 RPS for 10 minutes;
- sustained 500 RPS for 30 minutes is not yet accepted;
- alerting must watch p95/p99 latency, Nginx 5xx delta, DB connections, and sync backlog;
- follow-up optimization must target broad read-path latency before another L9 run.
```

## Key Artifacts

| Stage | Artifact | Result |
| --- | --- | --- |
| L5 smoke | `tmp/production-benchmark/20260613T065328Z/load-realistic/` | Passed at `50 RPS / 2m`. |
| L6 warmup | `tmp/production-benchmark/20260613T071052Z/load-realistic/` | Passed at `250 RPS / 5m`. |
| L7 target | `tmp/production-benchmark/20260613T111706Z/load-pool-matrix/` | Passed at `500 RPS / 10m` with `workers=24 pool=10:4`. |
| L8 spike | `tmp/production-benchmark/20260613T115351Z/load-pool-matrix/` | Graceful saturation at `750 RPS / 5m`; not accepted release capacity. |
| L9 soak | `tmp/production-benchmark/20260613T123638Z/load-pool-matrix/` | Failed at `500 RPS / 30m`; recovered cleanly. |

## L9 Soak Summary

Run profile:

```text
LOAD_RUNNER_SHARDS=2
API_WORKERS=24
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=4
TARGET_RPS=500
DURATION=30m
INCLUDE_MEDIA=0
INCLUDE_MUTATIONS=0
```

Result:

| Metric | Value |
| --- | ---: |
| HTTP requests | `856083` |
| Effective RPS | `475.46` |
| Request failure rate | `0.004%` |
| Checks pass rate | `99.996%` |
| Dropped iterations | `43919` |
| Dropped-iteration ratio | `4.88%` |
| Overall p95 | `5594.85ms` |
| Overall p99 | `11504.99ms` |
| Max PostgreSQL connections | `344` |
| Max Redis memory | `7.21MB` |
| Max sync backlog during test | `3147` |
| Nginx 5xx delta | `5` |

The k6 shards reported `Insufficient VUs` early in the run and later crossed
latency thresholds for `market_watcher`, `chat_texter`, and
`profile_browser`. This means response time grew high enough that the
load-runner needed more concurrency than the configured `1000` VUs per shard
to maintain a clean `500 RPS` arrival rate.

## Endpoint Hot Spots

The slow endpoints were broad, not isolated to one route:

| Endpoint | P95 | P99 | Avg |
| --- | ---: | ---: | ---: |
| `market_state` | `6046.04ms` | `12379.66ms` | `1030.54ms` |
| `chat_poll` | `5943.24ms` | `11790.44ms` | `1086.61ms` |
| `admin_market_current` | `5804.73ms` | `12411.36ms` | `934.54ms` |
| `project_users` | `5792.50ms` | `11588.92ms` | `920.02ms` |
| `users_public_search` | `5785.73ms` | `11744.09ms` | `1004.94ms` |
| `accountant_relations` | `5781.70ms` | `11818.96ms` | `958.75ms` |
| `offers_list` | `5773.88ms` | `11566.84ms` | `970.54ms` |
| `chat_conversations` | `5765.80ms` | `11732.72ms` | `1041.27ms` |
| `admin_users` | `5750.55ms` | `11684.79ms` | `938.10ms` |
| `offers_my` | `5745.78ms` | `11369.41ms` | `1007.06ms` |

Interpretation: the bottleneck is broad API/session/query latency under
sustained concurrency. It is not a single bad endpoint.

## Resource Classification

| Surface | Classification | Evidence |
| --- | --- | --- |
| API | Primary bottleneck candidate | Iran app CPU peaked near `2448%` across 56 logical CPUs, and latency grew across many endpoint families. |
| PostgreSQL | Pressure contributor, not hard exhaustion | Connections peaked at `344`, below the safe `400` budget and below `max_connections=500`. DB CPU peaked near `629%`, and no QueuePool timeout was found in L9 artifacts. |
| Redis | Not a bottleneck | Redis memory stayed below `8MB`; AOF status stayed healthy. |
| Nginx | Minor symptom, not primary cause | Only `5` new 5xx lines were observed during the sampled window; most requests did not fail. |
| Sync | Temporary backlog, recovered | Backlog rose during fixture/load activity, but post-run `sync-health` on both servers returned `unsynced=0` and `outbound=0`. |
| Memory | No leak evidence from L9 | Iran host memory stayed below `11%`; app memory rose from about `5.96GiB` to `6.46GiB`, then production was restored. |
| Load runner | Concurrency ceiling observed | k6 reached `1000` active VUs per shard after latency grew; this is a symptom of target response time, not the root cause by itself. |

## Recovery

The L9 run restored Iran production env after the test:

```text
API_WORKERS=8
DB_POOL_SIZE=8
DB_MAX_OVERFLOW=6
```

Post-run health:

```text
foreign sync-health: status=ok, unsynced=0, outbound=0
iran sync-health:    status=ok, unsynced=0, outbound=0
```

The failed soak did not leave durable test data residue or persistent sync
backlog.

## Gate Failures and Owners

| Gate failure | Owner | Next action |
| --- | --- | --- |
| Sustained p95/p99 latency at `500 RPS / 30m` | API + DB query owners | Profile hot read paths first: market state, offers list/my, chat poll/conversations/messages, public user search/detail, project users, customer/accountant relations. |
| Dropped-iteration ratio `4.88%` | API capacity/load-test owner | Do not raise worker/pool again blindly. First reduce long-tail latency; then rerun L9. |
| Nginx 5xx delta `5` | Nginx/API ops owner | Track whether 5xx recurs after query/API optimization. It is too small to classify as primary. |
| Sync backlog during load | Sync owner | No release blocker because cleanup recovered; keep alerting on persistent backlog. |

## Release Interpretation

Do not market the first release as capable of sustained `500 RPS` for 30
minutes. The system is stable enough to recover, but the UX under sustained
pressure would be poor due to multi-second p95/p99 latency.

The next optimization roadmap should not start with larger worker or DB pool
counts. The measured bottleneck is broad endpoint/session latency. The next
work should be limited to:

- query-plan and index review for the hot read surfaces listed above;
- reducing polling/read amplification in Messenger and market screens;
- short-lived caching for high-frequency read endpoints where correctness
  permits it;
- targeted rerun of L9 after those changes.
