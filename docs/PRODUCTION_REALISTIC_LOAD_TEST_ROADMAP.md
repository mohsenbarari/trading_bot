# Production Realistic Load Test Roadmap

Status: Draft. This roadmap defines the post-release-readiness realistic load
test stage for validating mixed production behavior above `500 req/s`.

Last updated: 2026-06-12

This roadmap is intentionally separate from the existing focused P0-P11
optimization stages. P0-P11 proved individual production surfaces and release
recoverability. Stage L validates the real-world combined workload: market,
trading, Messenger, media, notifications, profile/customer/accountant reads,
database pressure, Redis queues, Nginx, and cross-server sync running at the
same time.

## Objective

Prove how the current production profile behaves when hundreds of users are
active at the same time and the system receives more than `500` HTTP requests
per second from a third load-runner host.

The test must answer these questions:

- Can Iran production handle a realistic mixed workload at `500 req/s` without
  API, DB, Redis, Nginx, or sync instability?
- Which surface becomes the first bottleneck: API workers, PostgreSQL
  connections/queries, Redis/AOF, Nginx, file upload path, sync-worker, or
  network?
- Does the system recover cleanly after pressure stops?
- Are synthetic writes fully cleaned up on both foreign and Iran hosts?

## Current Production Baseline

| Surface | Current known profile |
| --- | --- |
| Target server | Iran production profile |
| API workers | `API_WORKERS=8` |
| DB pool | `DB_POOL_SIZE=8`, `DB_MAX_OVERFLOW=6` |
| PostgreSQL | Direct Postgres, tuned P2 profile |
| Redis | AOF `everysec`, `noeviction` |
| Static assets | Nginx direct immutable delivery |
| Sync | Bidirectional foreign/Iran sync-health must be clean before and after |
| Existing benchmark root | `tmp/production-benchmark/<timestamp>/` |

## Load Generator Choice

Primary tool: `k6`.

Reason:

- Supports `constant-arrival-rate` for precise `500+ req/s` targets.
- Supports multiple simultaneous scenarios/personas.
- Supports authenticated HTTP, multipart/file uploads, thresholds, and JSON
  summaries.
- Is lighter and more suitable for request pressure than Playwright.
- Can run on a third server so app/DB resources are not consumed by the load
  generator itself.

Supporting tools on the load-runner:

- `curl`
- `jq`
- `k6`
- optional `node` only if helper scripts need JSON fixture generation outside
  k6.

## Load-Runner Host

The load test must run from a third host, not from the Iran app/DB server.

Required when Stage L1 starts:

- SSH host/IP
- SSH user
- authentication method: key or password
- OS family/version
- expected outbound access to the Iran domain

The load-runner will not store production secrets in repo files. Runtime secrets
and synthetic account credentials must be written to a local, ignored env file
on the load-runner.

## Artifact Layout

Every run writes:

```text
tmp/production-benchmark/<timestamp>/load-realistic/
  metadata.json
  results.json
  summary.md
  k6-summary.json
  k6-stdout.log
  k6-stderr.log
  health-before.json
  health-after.json
  db-before.json
  db-after.json
  redis-before.json
  redis-after.json
  docker-before.json
  docker-after.json
  sync-before-foreign.json
  sync-before-iran.json
  sync-after-foreign.json
  sync-after-iran.json
  cleanup-report.json
```

Artifacts must be redacted. They must not include passwords, OTPs, tokens,
phone numbers, captions, raw message text, media URLs with tokens, or Telegram
bot credentials.

## Workload Model

The load test is persona-based. It is not an endpoint-only loop.

| Persona | Share | Behavior |
| --- | ---: | --- |
| `market_watcher` | 25% | Reads active offers, prices, recent trades, dashboard market widgets |
| `offer_maker` | 10% | Creates synthetic offers, edits/cancels a subset, reads own offer state |
| `trade_taker` | 10% | Requests/takes synthetic offers, triggers trade validation and notifications |
| `chat_texter` | 20% | Opens conversation lists, opens direct/group rooms, sends text, reads messages, marks seen |
| `chat_media_sender` | 8% | Sends small synthetic image/voice/video fixtures through the real upload/session path |
| `profile_browser` | 15% | Reads own profile, public profile, customer/accountant profile, relationship lists |
| `notification_user` | 7% | Lists notifications, marks read/unread, deletes synthetic notifications where safe |
| `admin_light_read` | 5% | Read-only admin/system/config/status surfaces with admin synthetic users |

The persona mix can be tuned by env, but the `500 req/s` release test must keep
market, trading, Messenger, profile, notification, and sync pressure active at
the same time.

## Safety Rules

1. Do not use real user accounts for mutating actions.
2. Do not call real OTP/SMS delivery during the load test.
3. Do not send real Telegram messages from the test.
4. All mutating fixture data must use a deterministic prefix, for example
   `loadtest_<timestamp>_`.
5. Every mutating scenario must have cleanup on both servers.
6. Cleanup failure fails the benchmark, even if request latency looked good.
7. Take a production backup before the first mutating load run.
8. The test must refuse to start if foreign/Iran sync-health is not clean.
9. The test must refuse to start if the repo worktree is dirty unless an
   explicit override is provided for development-only dry runs.
10. Media fixtures must be small and synthetic. Large upload stress is a
    separate test.

## Acceptance Gates

For the official `500 req/s` target run:

| Signal | Gate |
| --- | --- |
| HTTP 5xx | `< 0.1%` |
| HTTP 429 | must be explained; acceptable only if deliberate rate-limit behavior is being tested |
| Read p95 | `< 500ms` for common read surfaces |
| Read p99 | `< 1500ms` |
| Mutating p95 | `< 1000ms` unless a specific endpoint has a documented heavier path |
| Nginx 502/504 | `0` |
| App container restarts | `0` |
| PostgreSQL connections | warning above `200`, fail above `300` unless explicitly justified |
| PostgreSQL idle in transaction | no long-lived sessions |
| Redis AOF | `aof_last_write_status=ok` |
| Redis memory | no rapid unbounded growth; warning above current operational thresholds |
| Sync backlog | clean before run; drains to clean after run within the configured recovery window |
| Cleanup | all synthetic fixtures removed from both servers |
| Production health | passes before and after |

## Stage L0 - Contract and Safety Inventory

Goal: lock the test contract before installing tools or generating pressure.

Changes:

- Add the roadmap and define the workload/persona mix.
- Define artifact layout, redaction rules, target RPS, and failure gates.
- Map existing endpoints to persona actions.
- Identify write endpoints that need synthetic fixtures and cleanup.

Acceptance:

- Roadmap exists and is reviewed.
- No production load is generated in this stage.
- The next stage can request the third server credentials with a clear purpose.

## Stage L1 - Load-Runner Bootstrap

Goal: prepare the third server as the only host that generates load.

Changes:

- SSH into the load-runner host.
- Install `k6`, `curl`, `jq`, and minimal helper dependencies.
- Verify DNS/TLS/network path to the Iran domain.
- Create a non-repo runtime env file for load-test secrets.
- Record load-runner CPU/RAM/network baseline.

Acceptance:

- `k6 version` works on the load-runner.
- `curl` to Iran public health endpoint succeeds.
- The load-runner can write local artifacts.
- No production mutations are performed.

Needs from operator:

- load-runner SSH host/IP
- SSH user/auth

## Stage L2 - Synthetic Fixture and Auth Pool

Goal: create enough safe synthetic identities and relations to avoid one-account
hotspots and make the workload realistic.

Changes:

- Add fixture generator for synthetic normal users, middle admins, accountants,
  customers, groups, and market participants.
- Pre-authenticate users and store only runtime tokens on the load-runner.
- Create synthetic group/direct/channel rooms for chat pressure.
- Create synthetic market fixtures for offer/trade paths.
- Add cleanup script that removes fixture rows and matching sync residue on
  both servers.

Acceptance:

- Fixture creation is idempotent.
- Cleanup is idempotent.
- Sync-health is clean after fixture creation and after cleanup.
- No real user data is modified.

## Stage L3 - k6 Mixed Scenario Harness

Goal: implement the combined persona workload.

Changes:

- Add `scripts/load/k6_realistic_mix.js`.
- Add `scripts/report_production_realistic_load.py`.
- Add `make production-load-realistic`.
- Support env:
  - `TARGET_RPS`
  - `DURATION`
  - `LOAD_PROFILE`
  - `LOAD_RUNNER_HOST`
  - `LOAD_ARTIFACT_ROOT`
  - `INCLUDE_MEDIA`
  - `INCLUDE_MUTATIONS`
- Emit k6 JSON summary and project summary.

Acceptance:

- Local dry-run can list scenarios without hitting production.
- Smoke profile can run at low RPS.
- k6 thresholds reflect the official gates.

## Stage L4 - Observability Sampler During Load

Goal: capture system behavior while pressure is active.

Changes:

- Start a sampler before k6 begins and stop it after recovery.
- Sample:
  - Docker stats
  - PostgreSQL connections and slow/active queries
  - Redis memory/AOF/client state
  - Nginx 5xx/access summary
  - sync-health/backlog
  - host CPU/RAM/disk/network
- Write before/during/after snapshots to the artifact directory.

Acceptance:

- Sampler overhead is low.
- If k6 fails, sampler still writes final snapshots.
- Sensitive values are redacted.

## Stage L5 - Smoke Run

Goal: prove correctness before high pressure.

Run:

```bash
TARGET_RPS=50 DURATION=2m make production-load-realistic
```

Acceptance:

- Request error rate is near zero.
- All personas execute at least once.
- Cleanup succeeds.
- Sync-health is clean after recovery.

## Stage L6 - Warmup Ramp

Goal: expose obvious bottlenecks before the official target.

Run:

```bash
TARGET_RPS=100 DURATION=3m make production-load-realistic
TARGET_RPS=250 DURATION=5m make production-load-realistic
```

Acceptance:

- No container restart.
- PostgreSQL connection growth stays within expected range.
- Redis and sync remain stable.
- No cleanup residue remains.

## Stage L7 - Official Target Run

Goal: validate realistic `500 req/s`.

Run:

```bash
TARGET_RPS=500 DURATION=10m make production-load-realistic
```

Acceptance:

- All official gates pass.
- Summary identifies the slowest endpoints/personas.
- Final production health and sync-health are clean.
- Cleanup is complete on both servers.

## Stage L8 - Spike Run

Goal: understand behavior above the target without calling it release capacity.

Run:

```bash
TARGET_RPS=750 DURATION=5m make production-load-realistic
```

Optional:

```bash
TARGET_RPS=1000 DURATION=2m make production-load-realistic
```

Acceptance:

- If failures occur, they must be classified:
  - graceful rate limit
  - API worker saturation
  - DB connection pressure
  - slow query pressure
  - Redis/AOF pressure
  - Nginx/network pressure
- The system must recover to clean health after pressure stops.

## Stage L9 - Soak Run

Goal: detect memory leaks, slow queue growth, and gradual DB/Redis pressure.

Run:

```bash
TARGET_RPS=500 DURATION=30m make production-load-realistic
```

If stable and release needs stronger evidence:

```bash
TARGET_RPS=500 DURATION=60m make production-load-realistic
```

Acceptance:

- Memory does not grow without returning to baseline trend.
- Redis memory/AOF stays healthy.
- Sync backlog does not grow permanently.
- No delayed cleanup failure.

## Stage L10 - Analysis and Bottleneck Classification

Goal: convert raw load results into engineering decisions.

Changes:

- Generate `summary.md` with:
  - achieved RPS
  - per-persona latency/error rate
  - endpoint hot spots
  - DB/Redis/Nginx/API bottleneck classification
  - recovery time
  - cleanup status
- Compare smoke/warmup/target/spike/soak artifacts.
- Record whether tuning is required before release.

Acceptance:

- Every gate failure has an owner and next action.
- No broad worker/pool tuning is accepted without artifact evidence.

## Stage L11 - Release Capacity Decision

Goal: decide whether the first release can proceed with the current production
profile.

Possible outcomes:

| Outcome | Meaning |
| --- | --- |
| `release_ready_at_500_rps` | Target run passed, spike/soak behavior acceptable, no blocking residue |
| `release_ready_with_limits` | Target mostly passed but needs documented rate limits or operational watchpoints |
| `not_release_ready` | Target failed due to instability, data residue, unrecovered sync backlog, or repeated 5xx |

Acceptance:

- Decision is documented in a release load-test report.
- If not ready, the next roadmap is limited to the measured bottleneck.

## Initial Implementation Files

Expected files when implementation starts:

```text
scripts/load/k6_realistic_mix.js
scripts/report_production_realistic_load.py
scripts/bootstrap_load_runner.sh
scripts/cleanup_load_test_fixtures.py
docs/PRODUCTION_REALISTIC_LOAD_TEST_ROADMAP.md
```

Expected Make target:

```bash
make production-load-realistic
```

## Execution Notes

- The official test target is Iran, because users currently connect to the Iran
  server for the app path being validated.
- The foreign server must still be monitored because cross-server sync can be
  pressured by write-heavy personas.
- Playwright remains useful for UX validation, but it is not the primary load
  generator.
- Large media upload capacity is intentionally separate from the mixed `500
  req/s` target. Stage L includes small synthetic media to keep the real upload
  path active without turning the test into a pure bandwidth benchmark.
- Any follow-up tuning must be scoped to the measured bottleneck. Do not increase
  API workers, DB pools, or Redis settings based on theory alone.
