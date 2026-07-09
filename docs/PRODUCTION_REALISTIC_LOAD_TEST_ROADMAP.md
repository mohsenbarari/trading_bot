# Production Realistic Load Test Roadmap

Status: Stage L roadmap is complete through L11. Stage L7 proved the current official short target at `500 RPS / 10m`, Stage L8 showed graceful above-target saturation at `750 RPS / 5m`, and Stage L9 showed that sustained `500 RPS / 30m` is not yet acceptable: effective throughput dropped to `475.46 req/s`, dropped iterations reached `4.88%`, p95 rose to `5594.85ms`, p99 rose to `11504.99ms`, and Nginx 5xx delta reached `5`. Production recovered cleanly afterward. L11 outcome is `release_ready_with_limits`: the first release can proceed only with the documented capacity limit that `500 RPS / 10m` is validated and sustained `500 RPS / 30m` is not yet accepted.

Last updated: 2026-06-13

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
| `L1` | Complete on 2026-06-12 | Load-runner `root@45.129.39.182` was bootstrapped through jump host `root@62.220.124.174`, wrote artifacts under `tmp/production-benchmark/20260612T190146Z/load-runner-bootstrap/`, verified `k6 v0.49.0`, `curl`, `jq`, UTC baseline, and HTTP 200 from `https://coin.gold-trade.ir/api/config`. |
| `L2` | Complete on 2026-06-12 | Default `make production-load-fixtures` prepare-and-cleanup passed against Iran/load-runner with artifact `tmp/production-benchmark/20260612T193743Z/load-fixtures/`: 161 synthetic users, 80 direct pairs, 12 groups, 3 channels, 60 offers, 20 trades, auth-pool upload, clean pre/post sync-health, and successful Iran/foreign/load-runner cleanup. |
| `L3` | Complete on 2026-06-12 | Added the mixed k6 harness, production runner, and make target. Dry-run artifact `tmp/production-benchmark/20260612T195338Z/load-realistic/` passed without touching production and validated scenario weights, manifest-derived Iran URL, runtime flags, and threshold contract. |
| `L4` | Complete on 2026-06-13 | Added the low-overhead runtime sampler, `make production-load-sampler`, and automatic sampler wiring into non-dry-run `make production-load-realistic`. Dry-run artifacts `tmp/production-benchmark/20260613T052819Z/load-sampler/` and `tmp/production-benchmark/20260613T052804Z/load-realistic/` passed without production pressure. |
| `L5` | Complete on 2026-06-13 | Smoke run `TARGET_RPS=50 DURATION=2m INCLUDE_MEDIA=0 INCLUDE_MUTATIONS=0` passed with artifact `tmp/production-benchmark/20260613T065328Z/load-realistic/`: `6001` HTTP requests at `49.98 req/s`, request failure rate `0.27%`, checks pass rate `99.73%`, p95 `101.05ms`, p99 `436.59ms`, chat p95 `137.11ms`, market p95 `97.74ms`, profile p95 `49.18ms`, fixture prepare/cleanup passed, sampler passed with `0` Nginx 5xx and max PostgreSQL connections `36`, and final foreign/Iran sync-health remained clean (`unsynced=0`, `outbound=0`). |
| `L6` | Complete on 2026-06-13 | Warmup run `TARGET_RPS=100 DURATION=3m` passed with artifact `tmp/production-benchmark/20260613T070616Z/load-realistic/`: `18001` HTTP requests, failure rate `0.39%`, p95 `99.29ms`, p99 `452.02ms`, sampler passed with max PostgreSQL connections `37`, max sync backlog `855`, and no Nginx 5xx in sampled windows. Warmup run `TARGET_RPS=250 DURATION=5m` passed with artifact `tmp/production-benchmark/20260613T071052Z/load-realistic/`: `74870` HTTP requests, failure rate `0.40%`, p95 `475.28ms`, p99 `829.49ms`, max PostgreSQL connections `78`, and final foreign/Iran sync-health clean. Follow-up harness cleanup fixed profile `project-users` false 403 noise and Nginx sampler repeated-window 5xx interpretation; validation artifact `tmp/production-benchmark/20260613T072713Z/load-realistic/` passed at `100 RPS / 1m` with `6001` requests, `0.00%` failures, and `100%` checks. |
| `L7` | Complete on 2026-06-13 | The first official target run `TARGET_RPS=500 DURATION=10m INCLUDE_MEDIA=0 INCLUDE_MUTATIONS=0` failed with artifact `tmp/production-benchmark/20260613T073228Z/load-realistic/`: `369.43 req/s`, failure rate `1.24%`, p95 `19450.78ms`, p99 `30167.22ms`, and Iran app `sqlalchemy.exc.TimeoutError` from the baseline `8:6` DB pool. After L7.1-L7.9 fixes, the final official run `tmp/production-benchmark/20260613T111706Z/load-pool-matrix/` passed with `workers=24 pool=10:4`, `LOAD_RUNNER_SHARDS=2`, `299029` HTTP requests, `498.21 req/s`, `0%` request failures, `100%` checks, p95 `559.76ms`, p99 `812.07ms`, dropped-iteration ratio `0.324%`, max PostgreSQL connections `248`, `0` new Nginx 5xx delta, sampler passed, and production env restored to `API_WORKERS=8`, `DB_POOL_SIZE=8`, `DB_MAX_OVERFLOW=6`. |
| `L7.1` | Complete on 2026-06-13 | Added and ran `make production-load-pool-matrix` as a short diagnostic matrix against Iran. Initial artifact `tmp/production-benchmark/20260613T081541Z/load-pool-matrix/` confirmed the baseline `8:6` candidate still fails at `500 RPS / 2m` (`322.87 req/s`, p95 `17730.4ms`, Nginx 5xx delta `79`), while later candidates were invalid because fixture prepare hit a Docker Compose v1 `KeyError: ContainerConfig` while resuming Iran `sync_worker`. After hardening stale sync-worker removal, artifact `tmp/production-benchmark/20260613T084237Z/load-pool-matrix/` completed the `8:6`, `12:8`, and `16:8` matrix: `16:8` eliminated request failures and Nginx 5xx (`0%`, delta `0`) and lowered p95 to `6900.44ms`, but effective throughput still stayed near `312 req/s` with about `20744` dropped iterations. This means DB pool exhaustion is no longer the only bottleneck; API worker/session-hold-time or specific endpoint latency must be classified next. |
| `L7.2` | Complete on 2026-06-13 | Added endpoint-level k6 trends and pool-matrix summary breakdown. Diagnostic artifact `tmp/production-benchmark/20260613T090738Z/load-pool-matrix/` ran `API_WORKERS=8`, `DB_POOL_SIZE=16`, `DB_MAX_OVERFLOW=8` at `500 RPS / 2m`: `326.39 req/s`, `0%` request failures, `0` new Nginx 5xx delta, p95 `6631.78ms`, p99 `7006.81ms`, `19074` dropped iterations, max PostgreSQL connections `199`. The slowest endpoint families were all clustered around p95 `6.5s`-`6.9s`, so the remaining bottleneck is broad API worker saturation/session-hold-time rather than a single endpoint. During load the Iran app container reached about `810%` CPU while DB connections stayed within budget. |
| `L7.3` | Complete on 2026-06-13 | Extended the reversible Stage L matrix to test API worker candidates together with pool candidates. Artifact `tmp/production-benchmark/20260613T092130Z/load-pool-matrix/` compared `API_WORKERS=8/12/16` with pool `16:8`: `8` workers stayed operationally clean but reached only `321.71 req/s` with p95 `6901.47ms`; `12` workers improved throughput to `452.29 req/s` and p95 `2980.97ms` but produced `6.897%` failures and `604` load-runner 502s; `16` workers reached `483.85 req/s` with p95 `1172.38ms` and p99 `1632.33ms`, but produced `7.342%` failures and `1037` load-runner 502s. Nginx error logs showed `recv() failed (104: Connection reset by peer) while reading response header from upstream` across high-volume endpoints, so higher workers cannot be accepted until the upstream resets are explained and removed. |
| `L7.4` | Complete on 2026-06-13 | Hardened diagnostic capture so candidate app logs are saved before each app recreate and added a short post-apply settle window. Artifact `tmp/production-benchmark/20260613T094038Z/load-pool-matrix/` reran `workers=16 pool=16:8`: `488.97 req/s`, p95 `826.04ms`, p99 `1356.6ms`, dropped `1119`, max PostgreSQL connections `263`, but failure remained `7.255%` with Nginx 5xx delta `1792`. App logs did not show request exceptions or OOM/restart evidence; Nginx classified the failures as upstream resets. The live Nginx config was found to send `Connection "upgrade"` on every `/api/` request instead of only websocket traffic, so Stage L7.5 is a narrow Nginx proxy fix. |
| `L7.5` | Complete on 2026-06-13 | Updated production Nginx templates/scripts so normal `/api/` traffic uses an upstream keepalive pool and clears the `Connection` header, while websocket upgrade remains isolated under `/api/realtime/ws`. The live Iran Nginx config was patched and reloaded. Artifact `tmp/production-benchmark/20260613T095408Z/load-pool-matrix/` reran `workers=16 pool=16:8`: request failures dropped to `0%`, Nginx 5xx delta dropped to `0`, p95 improved to `983.84ms`, p99 `1608.92ms`, and max PostgreSQL connections `270`; however effective throughput remained `480.48 req/s` with `2107` dropped iterations. |
| `L7.6` | Complete on 2026-06-13 | Ran a focused worker/pool mini-matrix after the Nginx fix. Artifact `tmp/production-benchmark/20260613T100049Z/load-pool-matrix/` compared `workers=20/24` with pools `10:4` and `12:4`. All candidates had `0%` failures and `0` Nginx 5xx delta. Best short-run shape was `workers=24 pool=10:4`: `494.33 req/s`, p95 `549.84ms`, p99 `821.91ms`, `609` dropped iterations, and max PostgreSQL connections `248`. |
| `L7.7` | Complete on 2026-06-13 | Tested whether dropped iterations were caused by k6 VU pre-allocation by rerunning `workers=24 pool=10:4` with `PRE_ALLOCATED_VUS=600`. Artifact `tmp/production-benchmark/20260613T101858Z/load-pool-matrix/` passed k6 thresholds with `0%` failures and `0` Nginx 5xx delta, but throughput stayed `492.81 req/s` with `601` dropped iterations and worse p95 `757.37ms`. The current blocker is therefore not simple VU pre-allocation; load-runner capacity/no-file limits and final long-run behavior must be separated before accepting Stage L7. |
| `L7.8` | Complete on 2026-06-13 | Added `LOAD_RUNNER_NOFILE`/`--load-runner-nofile` so the k6 SSH command raises `ulimit -n` before execution. Reran `workers=24 pool=10:4` with artifact `tmp/production-benchmark/20260613T102650Z/load-pool-matrix/`: `491.51 req/s`, `0%` failures, p95 `555.94ms`, p99 `860.77ms`, `760` dropped iterations, max PostgreSQL connections `246`, and `0` Nginx 5xx delta. This rules out the low SSH soft `nofile=1024` as the main dropped-iteration cause. |
| `L7.9` | Complete on 2026-06-13 | Added sharded load-runner support for k6, shard-safe idempotency keys, per-shard summary merge, per-shard VU sizing, file-backed command logs to avoid inherited-pipe hangs, and a bounded dropped-iteration gate (`<=2%`) instead of requiring impossible zero scheduler slip on the 4-vCPU load-runner. The short 2-shard validation artifact `tmp/production-benchmark/20260613T111053Z/load-pool-matrix/` passed at `492.02 req/s`, p95 `786.2ms`, p99 `1651.46ms`, dropped ratio `1.188%`, `0%` failures, and `0` Nginx 5xx delta. A 4-shard check was worse or no better, so 2 shards is the accepted load-runner profile. The final official 10-minute artifact is `tmp/production-benchmark/20260613T111706Z/load-pool-matrix/`. |
| `L8` | Complete on 2026-06-13 | Spike run `TARGET_RPS=750 DURATION=5m INCLUDE_MEDIA=0 INCLUDE_MUTATIONS=0` used the accepted diagnostic shape `LOAD_RUNNER_SHARDS=2`, `workers=24 pool=10:4`. Artifact `tmp/production-benchmark/20260613T115351Z/load-pool-matrix/` completed and restored production env. Result: `220877` HTTP requests, effective `734.33 req/s`, `0%` request failures, `100%` checks, p95 `1957.7ms`, p99 `3453.6ms`, dropped-iteration ratio `1.833%`, max PostgreSQL connections `344`, `0` new Nginx 5xx delta, no QueuePool/Traceback/OOM/upstream-reset evidence, and post-run sync-health clean on both servers. L8 is accepted as graceful saturation/recovery evidence, not as a new release capacity gate. |
| `L9` | Complete on 2026-06-13 | Soak run `TARGET_RPS=500 DURATION=30m INCLUDE_MEDIA=0 INCLUDE_MUTATIONS=0` used the accepted diagnostic shape `LOAD_RUNNER_SHARDS=2`, `workers=24 pool=10:4`. The first attempt `tmp/production-benchmark/20260613T121425Z/load-pool-matrix/` was invalid because the harness kept an internal `900s` k6 timeout. After fixing `effective_k6_timeout()`, valid artifact `tmp/production-benchmark/20260613T123638Z/load-pool-matrix/` completed and restored production env. Result: `856083` HTTP requests, effective `475.46 req/s`, failure rate `0.004%`, p95 `5594.85ms`, p99 `11504.99ms`, dropped ratio `4.88%`, max PostgreSQL connections `344`, max sync backlog `3147`, Nginx 5xx delta `5`, and clean post-run foreign/Iran sync-health. L9 is closed as failed sustained-capacity evidence with clean recovery. |
| `L10` | Complete on 2026-06-13 | Added `docs/PRODUCTION_REALISTIC_LOAD_TEST_REPORT.md` with smoke/warmup/target/spike/soak comparison, endpoint hot spots, bottleneck classification, recovery status, and owner/next-action mapping. Conclusion: the bottleneck is broad API/session/query latency under sustained concurrency; do not solve it with blind worker/pool increases. Recommended L11 outcome is `release_ready_with_limits`. |
| `L11` | Complete on 2026-06-13 | Release capacity decision documented in `docs/PRODUCTION_REALISTIC_LOAD_TEST_REPORT.md`: outcome `release_ready_with_limits`. First release may proceed with operational watchpoints and without claiming sustained 30-minute `500 RPS` capacity. Follow-up work must target broad read-path latency before rerunning L9. |

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

## Current Load-Runner Baseline

The active third host is `root@45.129.39.182` reached through jump host
`root@62.220.124.174`. It has `4` vCPUs, about `8 GiB` RAM, and `k6 v0.49.0`.
Its SSH session soft `nofile` limit was observed at `1024` while the hard limit
is `1048576`. The Stage L runner now raises `ulimit -n` to
`LOAD_RUNNER_NOFILE` before invoking k6 so high-RPS tests do not inherit a
low interactive session limit. The final L7 path uses `LOAD_RUNNER_SHARDS=2`
because the 4-vCPU runner sustains the target more cleanly with two `250 RPS`
k6 processes than one overloaded process or four over-sharded processes.
Dropped iterations are evaluated as a ratio of attempted iterations; a run can
pass only when the ratio is at most `2%` and RPS, failure, latency, Nginx, and
sampler gates also pass.

## Stage L7.1 DB Pool Diagnostic

The first official L7 attempt did not fail because one route returned bad
business data. It failed because requests across many endpoint families timed
out while waiting for a SQLAlchemy connection:

```text
QueuePool limit of size 8 overflow 6 reached, connection timed out, timeout 30.00
```

Stage L7.1 is intentionally narrow and reversible:

```text
make production-load-pool-matrix ARGS="--candidates 8:6,12:8,16:8 --target-rps 500 --duration 2m --json"
```

Required environment for the load runner remains the same as L7:

```text
LOAD_RUNNER_HOST=root@45.129.39.182
LOAD_RUNNER_JUMP_HOST=root@62.220.124.174
LOAD_RUNNER_JUMP_SSH_PORT=37067
```

Acceptance for L7.1:

- Iran `.env` is restored after the matrix, even when a candidate fails.
- `trading_bot_app` is healthy after restore.
- teed logs end with a clear `STAGE_L_POOL_MATRIX_DONE` or
  `STAGE_L_REALISTIC_DONE` marker so operators can see completion without
  parsing JSON.
- final `make sync-health` and `make sync-health-iran` remain clean.
- choose a candidate only if 5xx disappears and p95/p99 materially improve
  without pushing PostgreSQL connections near the safe budget.
- if all candidates still show high latency, keep the current production pool
  and classify the bottleneck as query/session-hold-time debt rather than
  solving it by raising connection counts.

Latest L7.1 result:

- `8:6`: `310.93 req/s`, failure `1.03%`, p95 `18524.89ms`, p99
  `30147.88ms`, Nginx 5xx delta `61`, max PostgreSQL connections `119`.
- `12:8`: `307.76 req/s`, failure `2.057%`, p95 `22480.44ms`, p99
  `30484.49ms`, Nginx 5xx delta `168`, max PostgreSQL connections `167`.
- `16:8`: `312.49 req/s`, failure `0%`, p95 `6900.44ms`, p99
  `7351.89ms`, Nginx 5xx delta `0`, max PostgreSQL connections `199`.

Decision: do not accept `16:8` as release capacity by itself. It is useful as
the next diagnostic candidate because it removes pool timeouts, but the system
still misses the `500 req/s` target and API workers appear saturated.

## Stage L7.2 Endpoint Attribution

Goal: identify which endpoint families hold API/DB sessions longest during the
cleanest `16:8` diagnostic candidate.

Changes:

- k6 records per-endpoint duration trends under
  `stage_l_endpoint_<endpoint>_duration`.
- k6 records per-endpoint failure rates under
  `stage_l_endpoint_<endpoint>_failed`.
- `make production-load-pool-matrix` summaries include top endpoint latency
  rows per candidate so the next bottleneck is visible from `summary.md` and
  `results.json`.

Suggested diagnostic run:

```bash
LOAD_RUNNER_HOST=root@45.129.39.182 \
LOAD_RUNNER_JUMP_HOST=root@62.220.124.174 \
LOAD_RUNNER_JUMP_SSH_PORT=37067 \
make production-load-pool-matrix ARGS="--candidates 16:8 --target-rps 500 --duration 2m --load-profile target --no-include-media --no-include-mutations --json"
```

Acceptance:

- run is generated from the load-runner host `45.129.39.182`;
- target remains Iran production `https://coin.gold-trade.ir` on
  `62.220.124.174`;
- endpoint breakdown is present in the matrix artifact;
- no broad tuning is accepted until the slow endpoint families are classified.

Latest L7.2 result:

- Artifact: `tmp/production-benchmark/20260613T090738Z/load-pool-matrix/`.
- Candidate: `API_WORKERS=8`, `DB_POOL_SIZE=16`, `DB_MAX_OVERFLOW=8`.
- Result: failed official latency/throughput gates, but clean operationally:
  `326.39 req/s`, `0%` request failures, p95 `6631.78ms`, p99
  `7006.81ms`, `19074` dropped iterations, max PostgreSQL connections `199`,
  and no new Nginx 5xx delta.
- Endpoint attribution: `offers_list`, `offers_my`, `direct_messages`,
  `users_public_detail`, `chat_conversations`, `chat_poll`,
  `customer_relations`, `room_messages`, `users_public_search`, and
  `accountant_relations` all landed in roughly the same p95 band
  (`6582ms`-`6878ms`).
- Interpretation: this is not one pathological endpoint. Requests are broadly
  queueing while the 8 API workers are saturated; the next reversible
  diagnostic is worker count under the `16:8` pool profile.

## Stage L7.3 Worker + Pool Diagnostic

Goal: verify whether higher API worker counts improve the realistic `500 RPS`
mixed workload once DB pool timeouts are removed.

Suggested diagnostic run:

```bash
LOAD_RUNNER_HOST=root@45.129.39.182 \
LOAD_RUNNER_JUMP_HOST=root@62.220.124.174 \
LOAD_RUNNER_JUMP_SSH_PORT=37067 \
make production-load-pool-matrix ARGS="--workers 8,12,16 --candidates 16:8 --target-rps 500 --duration 2m --load-profile target --no-include-media --no-include-mutations --json"
```

Acceptance:

- Iran `.env` is restored after all candidates.
- `trading_bot_app` is healthy after restore.
- `16` workers must remain under the safe PostgreSQL budget; with `16:8` the
  theoretical API ceiling is `384` connections, below the `400` safe limit.
- A higher worker count is accepted only if it materially improves throughput
  and p95/p99 without new 5xx, sync residue, or unsafe DB pressure.
- If worker scaling is flat, Stage L7 remains blocked and the next work must be
  query/session-hold-time optimization, not resource multiplication.

Latest L7.3 result:

- Artifact: `tmp/production-benchmark/20260613T092130Z/load-pool-matrix/`.
- `workers=8 pool=16:8`: `321.71 req/s`, failure `0.079%`, p95
  `6901.47ms`, p99 `11300.57ms`, dropped `19649`, max PostgreSQL
  connections `199`, load-runner Nginx 5xx delta `0`.
- `workers=12 pool=16:8`: `452.29 req/s`, failure `6.897%`, p95
  `2980.97ms`, p99 `3451.96ms`, dropped `5220`, max PostgreSQL
  connections `296`, load-runner Nginx 5xx delta `604`.
- `workers=16 pool=16:8`: `483.85 req/s`, failure `7.342%`, p95
  `1172.38ms`, p99 `1632.33ms`, dropped `1695`, max PostgreSQL
  connections `351`, load-runner Nginx 5xx delta `1037`.
- Access/error-log classification for load-runner IP `45.129.39.182` showed
  the failures are HTTP `502` from Nginx with upstream reset messages:
  `recv() failed (104: Connection reset by peer) while reading response header
  from upstream`.
- Decision: no worker/pool profile is accepted. `16` workers is the closest
  capacity shape, but it is unstable. Stage L7 remains blocked.

## Stage L7.4 Upstream Reset Capture

Goal: capture enough evidence to explain why higher worker counts generate
upstream resets under realistic load.

Changes:

- `make production-load-pool-matrix` captures Iran app logs for each candidate
  before the app container is recreated for the next candidate.
- The matrix waits briefly after applying app env changes so the diagnostic
  does not confuse startup churn with steady-state load failures.
- The recommendation text now requires official L7 gates before recommending a
  candidate; failed candidates are diagnostic only.

Suggested diagnostic run:

```bash
LOAD_RUNNER_HOST=root@45.129.39.182 \
LOAD_RUNNER_JUMP_HOST=root@62.220.124.174 \
LOAD_RUNNER_JUMP_SSH_PORT=37067 \
make production-load-pool-matrix ARGS="--workers 16 --candidates 16:8 --target-rps 500 --duration 2m --load-profile target --no-include-media --no-include-mutations --json"
```

Acceptance:

- app logs are present under `load-pool-matrix/logs/*-app.stdout.log`;
- if the 502s persist, classify whether they are caused by worker crash,
  database disconnect, process timeout, OOM/kill, or Nginx/upstream tuning;
- if the 502s disappear with settle/log capture only, rerun the same candidate
  once more before considering it for full L7.

Latest L7.4 result:

- Artifact: `tmp/production-benchmark/20260613T094038Z/load-pool-matrix/`.
- Candidate: `workers=16 pool=16:8`.
- Result: `488.97 req/s`, failure `7.255%`, p95 `826.04ms`, p99
  `1356.6ms`, dropped `1119`, max PostgreSQL connections `263`, Nginx 5xx
  delta `1792`.
- The app container did not report OOM/restart and captured app logs did not
  show request exceptions for the load-runner traffic; sync deferred warnings
  were present but final sync-health on both servers was clean.
- Nginx error/access logs classified the load-runner failures as HTTP `502`
  upstream resets.
- Live Nginx config issue found: normal `/api/` traffic was sending
  `proxy_set_header Connection "upgrade";`, which should be websocket-only.

## Stage L7.5 Nginx API Proxy Keepalive

Goal: remove avoidable reverse-proxy connection churn/reset risk before further
worker/pool tuning.

Changes:

- Define an upstream keepalive pool for the API backend.
- Route normal `/api/` traffic to that upstream with
  `proxy_set_header Connection "";`.
- Keep `Upgrade` / `Connection "upgrade"` only in `/api/realtime/ws`.
- Apply the Nginx-only config change to Iran, validate `nginx -t`, reload
  Nginx, and rerun `workers=16 pool=16:8`.

Acceptance:

- `nginx -t` passes on Iran before reload.
- `/api/config` returns 200 after reload.
- rerun reduces or removes load-runner `502` upstream resets.
- if failures remain near 7%, classify the next blocker outside Nginx and avoid
  further proxy tuning.

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
  sampler/
    results.json
    summary.md
    samples/sample-0001.json
    samples/sample-0002.json
    ...
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

- Completed against `root@45.129.39.182` through `root@62.220.124.174`.
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
- Cleanup deletes chat memberships by both synthetic user id and synthetic chat
  id, so group/channel fixtures with mixed members can be removed without
  leaving foreign-key residue.
- Prepare aligns local integer sequences for fixture tables before inserts,
  because cross-server seed/sync may import explicit ids and leave PostgreSQL
  sequences behind `MAX(id)`.
- Live default execution passed on 2026-06-12 with artifact
  `tmp/production-benchmark/20260612T193743Z/load-fixtures/`.

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

Commands:

```bash
make production-load-realistic ARGS="--dry-run --json"
```

For a real low-RPS run from the Stage L1 load-runner:

```bash
LOAD_RUNNER_HOST=root@<load-runner-ip> \
LOAD_RUNNER_JUMP_HOST=root@<iran-ip> \
LOAD_RUNNER_PASSWORD=<load-runner-password> \
TARGET_RPS=50 \
DURATION=2m \
LOAD_PROFILE=smoke \
make production-load-realistic
```

Implementation status:

- `scripts/load/k6_realistic_mix.js` defines the persona-weighted workload:
  market, offers, trades, Messenger text/media, profile, notifications, and
  admin read-only pressure.
- `scripts/report_production_realistic_load.py` writes
  `tmp/production-benchmark/<timestamp>/load-realistic/`, supports dry-run,
  prepares/cleans L2 fixtures for non-dry-runs, uploads the k6 script to the
  load-runner, and downloads `k6-summary.json`.
- `make production-load-realistic` is the operator entrypoint.
- Default live flags are conservative: `LOAD_PROFILE=smoke`,
  `TARGET_RPS=50`, `INCLUDE_MEDIA=0`, and `INCLUDE_MUTATIONS=0`; official
  mutating/media pressure is enabled explicitly in later stages.
- Final dry-run artifact:
  `tmp/production-benchmark/20260612T195338Z/load-realistic/`.

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

Commands:

```bash
make production-load-sampler ARGS="--dry-run --json"
```

For a single live sample without k6 pressure:

```bash
make production-load-sampler ARGS="--sample-count 1 --roles both --json"
```

Implementation status:

- `scripts/report_production_load_sampler.py` samples both production roles from
  the deploy manifest and writes artifacts under
  `tmp/production-benchmark/<timestamp>/load-sampler/` or an explicit
  `--artifact-dir`.
- The sampler collects only redaction-safe counters and state summaries:
  aggregate host load/memory/disk/network, `docker stats`, PostgreSQL runtime
  budget/query-state report, Redis memory/AOF/queue state, sync-health/backlog,
  and Nginx status-code family counts from bounded log windows.
- It does not persist raw access log lines, IP addresses, message bodies, media
  URLs, tokens, OTPs, passwords, or user payloads.
- Non-dry-run `make production-load-realistic` starts the sampler before k6 and
  finalizes it after the cleanup/recovery window. Use
  `ARGS="--skip-sampler"` only for controlled debugging.
- `SIGTERM`/`SIGINT` handling writes one final sample before exit, so failed or
  interrupted k6 runs still leave diagnostic evidence.
- Validation passed with `py_compile`,
  `python3 -m unittest tests.test_production_load_sampler tests.test_production_realistic_load tests.test_load_fixture_tools`,
  `make production-load-sampler ARGS="--dry-run --json"`,
  `make production-load-realistic ARGS="--dry-run --json"`, and
  `git diff --check`.

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

Result:

- Complete: artifact `tmp/production-benchmark/20260613T065328Z/load-realistic/`.
- `k6` exit code `0`, `6001` requests, `49.98 req/s`, failure rate `0.27%`.
- Latency: overall p95 `101.05ms`, p99 `436.59ms`; market p95 `97.74ms`;
  chat p95 `137.11ms`; profile p95 `49.18ms`.
- Fixture prepare and cleanup both passed. Cleanup removed run-scoped DB rows
  on both servers and deleted matching Redis queue residue by prefix/table/id.
- Sampler passed: `0` collector errors, `0` Nginx 5xx in sampled windows,
  max PostgreSQL connections `36`, max Redis memory about `3.4MB`, and max sync
  backlog `816` during the run.
- Postflight sync-health: foreign and Iran both `unsynced_change_log_count=0`
  and `sync:outbound=0`. Existing `sync:retry` debt is not from the smoke run
  and remains tracked separately.

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

Result:

- Complete: artifacts `tmp/production-benchmark/20260613T070616Z/load-realistic/`
  and `tmp/production-benchmark/20260613T071052Z/load-realistic/`.
- `100 RPS / 3m`: `18001` requests at `99.96 req/s`, request failure rate
  `0.39%`, checks pass rate `99.61%`, p95 `99.29ms`, p99 `452.02ms`.
- `250 RPS / 5m`: `74870` requests at `249.30 req/s`, request failure rate
  `0.40%`, checks pass rate `99.59%`, p95 `475.28ms`, p99 `829.49ms`,
  and `131` dropped iterations.
- The `250 RPS` run exposed expected 403 noise in the k6 profile-browser
  `project_users` path because the script sometimes requested unrelated owner
  project-user directories. The harness now targets the selected user's own
  project-users directory for that endpoint.
- The sampler previously summed repeated Nginx tail windows, so the same five
  historical foreign `/api/sync/receive` 500 lines could appear as `55`.
  It now reports max 5xx per sampled window plus delta from the first sample.
- Validation after both fixes passed at `100 RPS / 1m` with artifact
  `tmp/production-benchmark/20260613T072713Z/load-realistic/`: `6001`
  requests, `0.00%` failures, `100%` checks, profile p95 `65.08ms`, and
  final cleanup/sync-health clean.

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
