# Production Realistic Load Test Roadmap

Status: Stage L1 complete. Stage L2 is next.

Last updated: 2026-06-12

This roadmap is intentionally separate from the existing focused P0-P11
optimization stages. P0-P11 proved individual production surfaces and release
recoverability. Stage L validates the real-world combined workload: market,
trading, Messenger, media, notifications, profile/customer/accountant reads,
database pressure, Redis queues, Nginx, and cross-server sync running at the
same time.

## Execution Status

| Stage | Status | Evidence |
| --- | --- | --- |
| `L0` | Complete on 2026-06-12 | Contract, safety inventory, persona endpoint map, restricted endpoint list, synthetic mutation inventory, and Stage L1 credential requirements are recorded in this document. No production load was generated. |
| `L1` | Complete on 2026-06-12 | Load-runner `root@45.129.39.182` was bootstrapped through jump host `root@87.107.3.22`, wrote artifacts under `tmp/production-benchmark/20260612T190146Z/load-runner-bootstrap/`, verified `k6 v0.49.0`, `curl`, `jq`, UTC baseline, and HTTP 200 from `https://coin.gold-trade.ir/api/config`. |
| `L2` | In progress | `scripts/load_fixture_worker.py`, `scripts/report_production_load_fixtures.py`, and `make production-load-fixtures` are available for synthetic fixture/auth-pool setup and cleanup. A live Stage L2 prepare/cleanup run still requires the new scripts to be synced/deployed to the Iran app container. |
| `L3`-`L11` | Pending | k6 mixed harness, observability sampler, smoke, warmup, target, spike, soak, analysis, and release-capacity decision remain pending. |

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

## Stage L0 Endpoint Map

All paths below are under the `/api` prefix. This map is the initial contract
for the mixed k6 workload. Stage L3 may refine exact weights after fixture
generation, but it must keep the same real-world mix.

| Persona | Endpoint set | Notes |
| --- | --- | --- |
| `market_watcher` | `GET /trading-settings`, `GET /trading-settings/market-state`, `GET /commodities`, `GET /offers`, `GET /trades/my`, `GET /notifications/unread-count` | Read-heavy market browsing. Use authenticated synthetic users. |
| `offer_maker` | `POST /offers`, `GET /offers/my`, `DELETE /offers/{offer_id}`, `POST /offers/cancel-all`, `POST /offers/parse` | Mutates only synthetic offers. `cancel-all` must run only for synthetic accounts created for the load test. |
| `trade_taker` | `POST /trades`, `GET /trades/my`, `GET /trades/{trade_id}`, `GET /offers`, `GET /notifications/unread-count` | Uses synthetic maker/taker accounts and synthetic offers only. Do not call `/trades/internal/execute` from k6. |
| `chat_texter` | `GET /chat/conversations`, `GET /chat/messages/{user_id}`, `GET /chat/rooms/{chat_id}/messages`, `POST /chat/send`, `POST /chat/rooms/{chat_id}/send`, `POST /chat/read/{user_id}`, `POST /chat/rooms/{chat_id}/read`, `POST /chat/messages/{message_id}/reaction`, `GET /chat/poll` | Uses synthetic direct/group rooms. Message bodies must be deterministic synthetic text, never copied from real users. |
| `chat_media_sender` | `POST /chat/upload-batches`, `POST /chat/upload-sessions`, `PATCH /chat/upload-sessions/{session_id}/chunk`, `POST /chat/upload-sessions/{session_id}/finalize`, `POST /chat/upload-batches/{batch_id}/commit`, `GET /chat/files/{file_id}` | Uses small synthetic image/voice/video fixtures. Legacy `POST /chat/upload-media` may be used only for a small channel-specific slice if Stage L3 explicitly enables it. |
| `profile_browser` | `GET /auth/me`, `GET /users-public/search`, `GET /users-public/{user_id}`, `GET /users-public/{user_id}/project-users`, `GET /customers/owner-relations`, `GET /customers/owner-relations/{relation_id}/trade-stats`, `GET /accountants/owner-relations` | Read-heavy dashboard/profile/customer/accountant behavior. Use synthetic relationships for high-frequency relationship reads. |
| `notification_user` | `GET /notifications`, `GET /notifications/unread`, `GET /notifications/unread-count`, `PATCH /notifications/{notification_id}/read`, `POST /notifications/mark-all-read`, `DELETE /notifications/{notification_id}` | Mutating notification actions must target synthetic notifications generated by synthetic offers/trades/messages. |
| `admin_light_read` | `GET /users`, `GET /admin-messages/market/current`, `GET /admin-messages/market/history`, `GET /admin-messages/broadcasts/history`, `GET /trading-settings/market-overrides` | Read-only admin persona. Requires a synthetic admin account. No admin writes in the official 500 RPS run. |

## Stage L0 Restricted Endpoint Inventory

These routes must not be part of the default realistic load test because they
either touch external providers, operate on production control planes, or can
damage real data if fixture isolation is imperfect.

| Route family | Decision | Reason |
| --- | --- | --- |
| `POST /auth/request-otp`, `POST /auth/resend-otp-sms`, `POST /auth/register-otp-request`, `POST /auth/register-otp-verify`, `POST /auth/verify-otp` | Excluded | Would call or simulate real login/OTP behavior and can affect user sessions/SMS provider state. Stage L2 must pre-create tokens instead. |
| `POST /auth/register-complete`, `POST /auth/setup-password` | Excluded by default | User lifecycle writes belong to fixture setup, not the high-RPS loop. |
| `POST /auth/dev-login`, `GET /auth/dev-switch/users`, `POST /auth/dev-switch/{target_user_id}` | Excluded from production load | Development-only routes must not be a production capacity dependency. |
| `POST /sync/receive`, `POST /sync/resync` | Excluded | Sync-worker and operator tools own these paths. k6 must observe sync health, not directly mutate sync internals. |
| `PUT /trading-settings`, `POST /trading-settings/reset`, market override writes | Excluded | Production control-plane settings. |
| Commodity writes and alias writes | Excluded | Static/reference data should not be churned by load tests. |
| `POST /admin-messages/market`, `DELETE /admin-messages/market/current`, `POST /admin-messages/broadcasts` | Excluded | Broadcast/system-visible writes can affect real users. |
| User update/delete/session-terminate admin routes | Excluded | Real account safety risk. |
| Session recovery approval/reject/identity routes | Excluded | Security workflow, not throughput workload. |
| Block/unblock routes | Excluded from default | Relationship side effects are high risk and not central to release capacity. |
| Group/channel management writes | Fixture setup only | Creating rooms/members is allowed in Stage L2 setup, not inside the 500 RPS loop unless a separate management-load test is requested. |

## Stage L0 Synthetic Mutation Inventory

The official mixed test may mutate only synthetic data with a run prefix such as
`loadtest_<timestamp>_`. Cleanup must run on both foreign and Iran hosts.

| Data surface | Created by | Cleanup requirement |
| --- | --- | --- |
| Synthetic users/accounts | Stage L2 fixture generator | Hard-delete only rows matching the exact run prefix and synthetic phone/account markers; remove related sessions and auth artifacts. |
| Customer/accountant relations | Stage L2 fixture generator | Remove relation rows, invitations, relation sessions, and matching notification/change-log residue. |
| Group/direct/channel chat fixtures | Stage L2 fixture generator and chat personas | Remove synthetic chats when created by the run, chat members, messages, reactions, pins/mutes/unread state, upload activity, and related notifications. |
| Chat media fixtures | `chat_media_sender` | Remove `chat_files`, upload batches/sessions, physical uploaded files, file-cache residue if server-side, and messages referencing those files. |
| Offers | `offer_maker` | Cancel/delete active synthetic offers; remove expired/cancelled synthetic offers after trade cleanup where safe. |
| Trades | `trade_taker` | Remove synthetic trade rows and dependent notifications/change-log rows only when both sides match the run prefix. |
| Notifications | Trading/chat personas | Delete notifications whose source objects are synthetic and run-scoped. |
| Redis queues | App/sync side effects | After DB cleanup, verify `sync:outbound` and `sync:retry` are empty or contain no run prefix residue. |
| `change_log` / sync residue | Any mutating persona | Remove or neutralize only run-scoped residue after both servers converge; final `sync-health` must be clean. |

## Stage L0 Workload Contract

Default official target:

```text
TARGET_RPS=500
DURATION=10m
LOAD_PROFILE=realistic-mixed
INCLUDE_MEDIA=1
INCLUDE_MUTATIONS=1
```

Required preflight:

1. local repo clean
2. production manifest present
3. production health passes
4. `make sync-health` clean
5. `make sync-health-iran` clean
6. load-runner reachable over SSH
7. load-runner clock roughly synchronized with UTC
8. fixture pool exists and has enough accounts for each persona
9. backup exists before first mutating run

Required postflight:

1. k6 summary captured
2. production health passes
3. `make sync-health` clean or drains clean within recovery window
4. `make sync-health-iran` clean or drains clean within recovery window
5. cleanup report confirms no synthetic run residue
6. bottleneck classification recorded

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

- SSH into the load-runner host with `scripts/bootstrap_load_runner.sh`.
- Install or verify `k6`, `curl`, `jq`, `gnupg`, and minimal apt dependencies.
- Set the load-runner timezone to UTC when the host allows it.
- Verify DNS/TLS/network path from the load-runner to the Iran health URL.
- Create a remote load-test workspace under `/srv/trading-bot-loadtest` by default.
- Record load-runner CPU/RAM/disk/route/tool baseline.
- Copy remote bootstrap artifacts back into
  `tmp/production-benchmark/<timestamp>/load-runner-bootstrap/`.

Command:

```bash
LOAD_RUNNER_HOST=root@<load-runner-ip> make production-load-runner-bootstrap
```

Optional overrides:

```bash
LOAD_RUNNER_HOST=root@<load-runner-ip> \
LOAD_RUNNER_SSH_PORT=22 \
ARGS="--remote-dir /srv/trading-bot-loadtest" \
make production-load-runner-bootstrap
```

If the load-runner is reachable only through the Iran host:

```bash
LOAD_RUNNER_HOST=root@<load-runner-ip> \
LOAD_RUNNER_JUMP_HOST=root@<iran-ip> \
LOAD_RUNNER_PASSWORD=<load-runner-password> \
make production-load-runner-bootstrap
```

Do not commit or write the load-runner password to repo files. Pass it only as
runtime environment input when the bootstrap command is executed.

If the load-runner cannot reach the k6 apt/GitHub endpoints directly, the
bootstrap script seeds a pinned k6 archive from the current host through the SSH
jump path. Repeated runs skip that seed upload when `k6` already exists on the
load-runner. Set `LOAD_RUNNER_SEED_K6_ARCHIVE=0` only when the load-runner has
reliable direct access to the k6 package source.

Acceptance:

- `k6 version` works on the load-runner.
- `curl` to Iran public health endpoint succeeds.
- The load-runner can write local artifacts.
- No production mutations are performed.

Needs from operator:

- load-runner SSH host/IP
- SSH user/auth

Current implementation status:

- Completed against `root@45.129.39.182` through `root@87.107.3.22`.
- Final artifact: `tmp/production-benchmark/20260612T190146Z/load-runner-bootstrap/`.
- Result: `passed`, Iran health HTTP `200`, `k6 v0.49.0`, `jq-1.7`, `curl 8.5.0`.

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

Command:

```bash
LOAD_RUNNER_HOST=root@<load-runner-ip> \
LOAD_RUNNER_JUMP_HOST=root@<iran-ip> \
LOAD_RUNNER_PASSWORD=<load-runner-password> \
make production-load-fixtures
```

Default action is `prepare-and-cleanup`: it proves fixture creation, uploads the
auth pool to the load-runner, waits for sync-health, then removes synthetic data
from both hosts and deletes the load-runner auth-pool file.

For Stage L3 setup, keep fixtures by running:

```bash
LOAD_RUNNER_HOST=root@<load-runner-ip> \
LOAD_RUNNER_JUMP_HOST=root@<iran-ip> \
LOAD_RUNNER_PASSWORD=<load-runner-password> \
ARGS="--action prepare" \
make production-load-fixtures
```

For cleanup of a known prefix:

```bash
LOAD_RUNNER_HOST=root@<load-runner-ip> \
LOAD_RUNNER_JUMP_HOST=root@<iran-ip> \
LOAD_RUNNER_PASSWORD=<load-runner-password> \
ARGS="--action cleanup --prefix loadtest_<timestamp>_" \
make production-load-fixtures
```

Current implementation status:

- `scripts/load_fixture_worker.py` runs inside the app container and creates or
  cleans only rows matching the exact `loadtest_*` prefix.
- `scripts/report_production_load_fixtures.py` orchestrates Iran prepare,
  load-runner auth-pool upload, sync-health gates, Iran/foreign cleanup, and
  redacted artifacts.
- `make production-load-fixtures` is the operator entrypoint.
- Auth tokens are not written to repo artifacts; local logs/results redact
  tokens, and the raw auth pool is written only to
  `/srv/trading-bot-loadtest/auth/<prefix>auth-pool.json` on the load-runner
  with restricted permissions.
- Live execution should happen after the new scripts are present on the Iran
  server through `make production-release` or the appropriate production sync
  path.

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
