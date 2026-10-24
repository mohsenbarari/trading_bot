# Web recovery: runtime observations, 24 October 2026

This is a bounded incident observation, not a passing security acceptance.
Times below are UTC. The agent performed read-only diagnostics; the supervising
operator performed the reviewed maintenance/recovery actions. No raw private
logs, environment values, credentials, SQL text or business records are included.

## Recovery state at 06:29:12

The original `security-web-engine-20261023-07` transaction stopped before Docker
installation, after stopping application writers. Its journal remains
`writers_quiesced`. The first owned restore started the original applications
but could not prove all-container health before restoring native timer intent.

The separately reviewed owned-intent continuation, committed as `3939767e`,
restored all 27 original restart policies and the four originally active
timers. These timers were independently observed active, enabled and without
pending jobs:

- `coin-intelligence-capture-shadow-external.timer`
- `coin-intelligence-capture-shadow-input.timer`
- `market-pipeline-watchdog.timer`
- `trading-bot-sync-health-sampler.timer`

At 06:29:12, 26 containers were running; the intentionally stopped migration
remained stopped with restart policy `no`. The processor alone reported Docker
`Health.Status=unhealthy`; that does not mean all other services were stable.
The blobs worker separately restarted repeatedly, as detailed below.
The continuation exited with status **3**, and its separate audit recorded
`intent_restored_health_incomplete`, `security_transition_completed=false`.
The original transaction journal and previous recovery audit were preserved;
the source fence and global reservation were retained. No new transaction,
security upgrade, kernel reboot or successful acceptance is implied.

## Processor: progress with unacceptable heartbeat freshness

The actual unchanged processor image is
`sha256:678aaf5cd8e954f368fc514430e5e63e638d84b2883194465ed0847287bc511c`.
Its public source bytes were independently read and matched the reviewed
worktree exactly:

| Public source | Bytes | SHA-256 |
| --- | ---: | --- |
| `private_coin_processor.py` | 45,362 | `7c6a0e161bf4625dad42bf14877a3257899aa5b2d3c41da4f210b4f1d5bf3465` |
| `private_pipeline_foundation.py` | 40,300 | `89c271d9024c3191be57db70a10f6db7bb1ed183962d28bc399dc1d7c6ae7018` |

The processor writes its heartbeat only after `process_coin_spool_cycle`
returns. The strict health check rejects a stale heartbeat. Three samples
showed the same heartbeat timestamp, while PID 7 remained alive:

| Observation | Heartbeat age | Last health exit | Failing streak |
| --- | ---: | ---: | ---: |
| 06:25:36 | 245.349 s | 1 | 20 |
| 06:25:47 | 256.606 s | 1 | 21 |
| 06:25:57 | 266.833 s | 1 | 22 |

Schema `market_processor/4.0`, live mode, `live-shadow-ready`, all nine source
names, the expected causal-counter keys and `shadow_only=true` were present.
Correct schema and a live PID did not establish freshness.

The heartbeat advanced at **06:25:58.183704**. Its numeric counters for that
completed cycle reported:

- Total cycle: **266.867 s**, including projection **245.495 s**, archive
  **11.455 s**, review/commit **6.369 s**, spool **3.093 s**, causal inputs
  **0.394 s**, setup **0.007 s**.
- 20,410 records, 20,390 changes, 745 market messages reprojected; both pending
  market-message and coin-group counts were zero at the completed boundary.
- Projection status `caught-up`; 25 archive items selected/published, none
  rejected in that completed cycle.

Thus work was progressing, and the stale health represented a real multi-minute
gap between completed cycles. It was not proved to be a schema error or a
permanent SQL deadlock. Caught-up counters do not override the freshness gate.

Two further samples ten seconds apart showed 407,359,488 additional physical
processor read bytes, 268,554,240 additional read-character bytes, 2.705 CPU
seconds and unchanged write counters. Memory was 402.4–402.5 MB against the
384 MiB limit, with 181,882,880 swap bytes. The cgroup memory-limit event count
rose from 99,448 to 100,870; OOM and OOM-kill counts remained zero. I/O pressure
was about 19%, and one wait-channel observation was `folio_wait_bit_common`.

PostgreSQL showed one application backend changing from idle/`ClientRead` to
active/`DataFileRead`, with maximum observed query age zero seconds, no reported
deadlocks and increasing numeric transaction/read counters. No SQL text was
read. Together these facts support heavy read/reclaim pressure during long
projection work; they do not prove the precise internal query responsible.

## Separate blobs startup failure

One earlier original-running blobs worker exited with the static error code
`SignatureDoesNotMatch` during `GetBucketVersioning`. Provider exception text
and credentials were not exposed. Full metadata observed by the supervising
operator at 06:27:15 reported restart count **12** and `Restarting=true`; an
independent peer observed **14** and `Restarting=true` at 06:29:19. Whether this
startup failure existed before the maintenance is **unknown**: bounded logs
before 05:48:39 were absent. The aggregate diagnostic at 06:29:12 excluded
currently running rows from its unhealthy/stopped list, so it supplied no
restart-count evidence for blobs. An earlier report of zero for that moment
was unsupported and is corrected here. A brief running snapshot alone is
insufficient: the continuation
requires at least 30 seconds of repeated full health/baseline success with
unchanged start times and restart counters for all 26 original running
containers. Processor health prevented that complete acceptance.

## Boundaries and next evidence

No processor restart, source edit, memory-limit increase or health-age increase
was performed by the diagnostic agent. Changing a resource budget requires its
own reviewed, measured operation. At 06:30:01, the host reported 7,926,348 KiB
total RAM and 2,924,492 KiB available (about 7.56/2.79 GiB). Processor limits
were 402,653,184 RAM bytes and 805,306,368 combined RAM-plus-swap bytes, with
one CPU. Thus its additional swap allowance was 384 MiB, not 768 MiB. These
facts alone are not proof of a safe new limit. Any proposal must retain host
headroom, distinguish Docker's combined
RAM-plus-swap limit from additional swap allowance, preserve data and show a
repeatable reduction in cycle duration without weakening readiness.

Successful finalization still requires authentic owned-fence proof and stable
original all-container/database/API/native health. This note cannot authorize
adopting a dead owner, releasing the retained fences or retrying an old unit.
