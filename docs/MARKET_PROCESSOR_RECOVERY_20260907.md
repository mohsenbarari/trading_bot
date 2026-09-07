# Market processor replay/backlog recovery — 2026-09-07

## Cause and scope

After the sender recovery, the processor still had about 156,000 dirty public
messages at 06:02 UTC. This was not explained by market closure. Completed
explicit backfills for XAUUSD, MELTED_AGGREGATE, MELTED_FLOW and USD_HERAT were
requeued whenever hourly spool retention replaced an input file's inode.
Those sources have generic lineage, not the promotion-specific lineage which
the duplicate test incorrectly required. Five sampled XAU identities had a
retained seen-event identity and terminal PARSED lineage from September 4,
yet were pending again on September 7. Read-only staging integrity passed;
only one processor owned the database.

Historical export also scanned every raw observation although only one actual
XAU quote per closed 15-second bucket is export-eligible. The old historical
query exceeded a 12-second diagnostic deadline; its plan was `SCAN o`.
The live processor had read roughly 5.2 TB since September 5 and was still
reading about 35 MB/s during a three-second sample. These are cumulative/process
measurements, not an assertion that all reads originated in that one query.

## Changes and invariants

1. Already-seen public explicit identities are duplicates. Keep the one-time
   promotion-lineage upgrade for private-primary and both coin groups.
2. Historical export is the ID-ordered merge of two disjoint candidates:
   non-XAU observations from an additive partial index, and selected XAU bucket
   keys looked up in the observation key index. Existing group-first and
   fresh-first selection, ledger retry conditions, and exact historical ID
   order remain unchanged. Raw ticks and original event timestamps are retained.
3. Health counters expose durable pending market messages per source, pending
   coin groups and cycle duration. `projection_status` distinguishes catching-up
   from caught-up. This is local parse readiness, not proof of archive delivery,
   downstream model readiness or source freshness. Existing heartbeat/Docker
   liveness alone must not be presented as end-to-end readiness.
4. The processor's restart policy becomes `unless-stopped`; durable owner locks,
   cursors, archive transaction ordering and integrity refusals remain intact.

No data/checkpoints are reset, no retention policy is relaxed, no synthetic price
or timestamp is created, and no trading, DNS, writer or capture authority changes.

## Regression evidence before deployment

- Public replay test failed for all four affected sources before the fix and
  passed afterward. Repeated replay preserves terminal lineage and causal time;
  pending work and new identities remain supported.
- 80 capture/processor/transport/regression tests passed in 55.048 seconds.
- Updated processor metric tests: 9 passed in 1.088 seconds (including a new
  pending-work-without-new-spool-records test).
- Historical parity covers mixed raw XAU/public history, six batch sizes,
  successful exports, missing/stale semantic receipts, changed observations,
  permanent refusals and hash-mismatch retries. Query-plan assertions prevent
  returning to a full raw-XAU scan. All 300 fixture observations remain stored.

## Activation and acceptance

First processor deployment `ab205f08` passed at 06:33:29 UTC, with unchanged
bystanders and parent authority, zero restarts/export refusals, and 128,604
pending messages explicitly reported as catching-up. The exact image passed
81 tests in 9.940 seconds. Actual authenticated dashboard HTTP 200 showed
today's group offers, but this did not close the other-source backlog.

Post-deployment measurement found another source of historical scan cost:
741,314 non-XAU observations and 1,142,304 ledger rows required random reads of
wide payload/receipt tables. The non-XAU selection alone exceeded a 15-second
diagnostic deadline. A second scoped optimization adds covering readiness
indexes for the non-XAU observations and both export receipts. Historical
candidate queries return IDs only; payloads are fetched only for the final
bounded, ID-ordered batch. Parity and covering-query-plan tests protect this.
No previously created index or fact is removed. Separate archive-phase timing
exposes whether remaining delays are in parse or delivery preparation.

Pending: second immutable image/handoff, all-source backlog drain, transport
checkpoint coverage, model/dashboard evidence and observation through rotation.
The sender recovery is separately recorded in
`MARKET_TRANSFER_RECOVERY_20260907.md`.

Do not declare this incident resolved merely because deployment or Docker health
passes. Persist the final runtime identifiers, measured drain/latency and checks
below after observing the deployed process.
