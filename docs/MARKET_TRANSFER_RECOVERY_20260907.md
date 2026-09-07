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
cycle. A lost replay ACK is retryable and duplicate redelivery is idempotent.
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
