# Market Fact transfer interruption — 2026-09-07

## What users experienced

Two coin groups on the estimator dashboard stopped advancing during the prior
trading day. This was not overnight market silence: the Web capture/parser held
valid GROUP_1/GROUP_2 offers with availability 2026-09-06 18:20:54/18:26:27 UTC,
while the Bot input projection stopped at 11:55:48 UTC (15:25:48 Tehran).

## Confirmed cause

The receiver's contiguous checkpoints were behind delivery checkpoints that the
sender had previously acknowledged. Four subsequent stream heads were rejected
with `SEQUENCE_GAP` and treated as permanent failures. Forty dead-lettered items
blocked more than 25,000 later deliveries. Both sender and receiver still wrote
fresh `live-ready` heartbeats, hiding the stopped data flow behind green health.

| Stream | Receiver checkpoint | Sender checkpoint | Missing original deliveries |
| --- | ---: | ---: | ---: |
| GROUP_1 | 5666 | 5669 | 3 |
| GROUP_2 | 18448 | 18451 | 3 |
| Wallex USDT | 432813 | 432891 | 78 |
| Binance PAXG | 90743 | 90768 | 25 |

These measurements prove checkpoint divergence. They do not by themselves prove
which earlier storage/restore operation introduced it. Receiver state currently
lives on the attached volume; never copy an actively written SQLite main file
without its transactional snapshot/WAL. A future relocation or restore must
verify sender → receiver → adapter checkpoints before declaring success.

## Recovery and permanent behavior

`core/market_intelligence/market_fact_recovery.py` validates the original retained
outbox envelopes, hashes and contiguous sequence, then uses the existing mTLS/HMAC
transport and unchanged receiver validation. It never lowers a checkpoint,
changes a fact, deletes failure evidence or resets attempt counters.

The incident replay restored all 109 missing originals. Validated ACKs allowed
the 40 exact `SEQUENCE_GAP` dead letters to be rearmed, retaining their history.
The normal sender then resumed the backlog. Capture, parser and Product authority
were not switched. Previously missed facts keep their real timestamps.

For subsequent `SEQUENCE_GAP` responses, the sender verifies the response belongs
to its exact request, then replays at most 100 retained original deliveries per
cycle. A lost replay ACK and temporary HTTP 408/425/429/5xx responses are
retryable; duplicate redelivery is idempotent.
Missing/compacted originals, hash/identity conflicts, or unbound responses cannot
skip the prefix and require diagnosis. No data is fabricated to make health pass.

Sender health is degraded whenever an unresolved dead letter exists or pending
deliveries are over 120 seconds old. Empty queues remain healthy during quiet
markets; the age of the last Telegram message alone does not trigger a restart.
When all pending work is blocked/backing off, the sender sleeps between probes.
Its Compose restart policy resumes unexpected process exits without a five-retry
ceiling, while preserving an operator's explicit stop.

## Scoped operator recovery

Run the module inside the existing sender environment. Without `--apply` it only
validates the bounded prefix. Supply the observed exact stream and checkpoints;
the maximum operator scope is 1,000 missing deliveries. Apply requires original
hash-checked envelopes, receiver ACK, a sender checkpoint compare-and-set and an
exact contiguous head containing only `SEQUENCE_GAP` failures. An advisory lock
prevents concurrent recovery of the same stream. Only counters are printed.

```text
python -m core.market_intelligence.market_fact_recovery \
  --stream <stream-id> --receiver-sequence <observed> \
  --sender-sequence <observed> [--apply]
```

## Verification requirements

Tests exercise an older real SQLite receiver, automatic prefix recovery, normal
next delivery, duplicate replay, lost ACK, unavailable/compacted originals,
hash mismatch and ACK binding. Operational closeout also requires each source's
latest canonical facts to reach the Bot Market Store, estimator inputs, and the
actual dashboard; a recent heartbeat alone is insufficient. Overnight quiet is
reported separately from transfer backlog and parser dispositions.

## Verified incident state at 04:42 UTC

- Authenticated, localhost HTTP verification of the actual `activity.html`
  returned 200 and displayed the last GROUP_1 offer at 21:50:27 Tehran and
  GROUP_2 at 21:55:16 Tehran on September 6. Latest displayed trades were
  21:34:04 / 21:27:39. These are source event times, not transfer receipt times.
- Both canonical groups reached the Bot Market Store and the legacy bridge
  reported zero source/destination lag. No new offers or trades were created
  for testing. The verification session was logged out afterward.
- At 04:41:33 UTC the real sender reported queue depth 0, dead-letter count 0,
  and oldest pending age 0. Its historical rejected counter correctly remains
  40; recovery does not erase historical failures.
- Other Telegram sources have a separate parser catch-up backlog. At 04:41
  approximately 105,000 dirty market messages remained, decreasing from about
  111,000 at 04:35. Global oldest-first processing allows a large XAU backlog
  to delay melted/Herat projection. Capture is active, but its heartbeat and
  the sender's empty queue do **not** prove parser-to-model currency for these
  sources. Do not mark the entire pipeline healthy until this is verified.
- The main disk remained at approximately 20 GB used / 17 GB available. Build
  context excludes runtime data and image transport must stream directly;
  do not create a second large image archive on the root disk.

## Deployment authorization and completed sender-only handoff

The execution environment initially required explicit image/destination approval.
The owner supplied it on September 7 for `fb95bbde` to the existing Web host
`65.109.220.59:37067`, activating only the data-transfer service. This authorization
was obtained before retrying the transfer; the original denial was not bypassed.

`scripts/incident_sender_handoff_20260907.py` completed `APPLIED_LIVE` at
05:37:51 UTC. Its preflight compared the full rendered Compose definitions,
validated the original parent maintenance lock and required exactly one sender
owner. Only image/release identity and the sender restart policy changed. Secret
and state mounts, capture and processor containers, all other running containers,
Product authority, and parent lock bytes remained unchanged. The prior runtime
was retained for a scoped rollback; no database, cursor, or historical failure
evidence was deleted or reset.

Exact deployed source is `fb95bbdece703a09eb6ca3d6b9e2fccf04abbe3b` (tree
`a6c61011ec069c1c33028e12edf5f75d0783e6eb`). Docker storage engines on the two hosts
report different local image IDs, so receipt verification also matched the
configuration, creation metadata, architecture/OS, and every content-layer ID:

- Local image ID: `sha256:186a9f5bf7721d95295bc9402c14538b3e61f00f9801277b0a3264dddcf69406`.
- Remote pinned image ID: `sha256:f5762ed500ad0c0656394e46fc98e73801d483576fba09eb40251c5f20579b9f`.
- Matching portable content digest: `251f8451d5ac35a542a464c2beb77d5d118b557b37e957d1f7aef4e540de672d`.
- Matching image-input signature: `3b4d4a244516949fdc1bcf59a30d428229b24aede66a8a2a240af1fb29e098f7`.

The approximately 153 MB image was streamed directly; no root-disk tar copy was
created. The root-private remote receipt/override is under
`/srv/trading-bot/incident-recovery/20260907-transfer/`; retain it while this
scoped override is active or this incident is open, then retire it through normal
release retention after an integrated release supersedes the override.

Post-deployment verification:

- At 05:38:36 UTC the new process had acknowledged 20 real deliveries, with
  zero duplicates, rejections, dead letters, or pending deliveries. Counters in
  its heartbeat are process-lifetime counters; historical failures remain in
  the database. No fabricated market events or live fault injection were used.
- At 05:43:35 UTC both receiver and model adapter covered all ten sender stream
  checkpoints observed after deployment. This proves delivery-prefix coverage,
  not the currency of input facts that the parser has not yet produced.
- An authenticated HTTP check of the actual dashboard again returned 200 and
  the same correct last GROUP_1/GROUP_2 source event times. Capture silence during
  market closure was not treated as a fault.
- Local recovery/transport/foundation and sender-handoff guards: 45 tests passed.
  The exact runtime image separately passed 26 recovery/transport tests without
  network access, under read-only and bounded-resource execution.

## Separate parser issue remains open

At 05:38 UTC, approximately 108,000 dirty market messages remained across melted,
Herat and XAU sources; coin dirty groups were zero. The earlier short-term decline
did not establish sustained draining. A sample of five old XAU dirty rows was
processed between checks, but the full backlog did not clear and melted/Herat
latest facts remain behind capture. Do not silently raise health freshness limits,
drop historical work, or refresh source timestamps to call this healthy. Parser
capacity/repeated-work and per-source scheduling need separate diagnosis and
verification. This sender-only handoff did not alter that processor.
