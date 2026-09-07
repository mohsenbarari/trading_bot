# Market processor replay/backlog recovery — 2026-09-07

## Current follow-up authorization and activation attempt

The user explicitly approved transferring this incident's code/image to the
current Web host and replacing only `market-processor`, retaining data and
rollback. Source `7c35c511` passed all 106 exact-image tests (10.144s) and was
streamed to the host without a tar archive on the root disk. Portable content
digest: `8c0b17f3eb65e9b0437ea606bc5cda8fb0ca4051218ec34a2e52d81b6297f859`.
The handoff now pins that artifact against the prior `83a698e0` artifact.
At 07:29 UTC the prior processor had 34 restarts, exit 1, no OOM. Repair accepts
this explicitly recorded degraded baseline; it does not relax NEW liveness,
ownership, image, mount, export or bystander checks. A fallback to the known
broken prior artifact is reported as degraded, never as healthy rollback.
Activation passed at 07:34:37 UTC: healthy Docker, zero restarts, unchanged
bystanders/parent/mounts, no export rejection or missing research context. The
exact poison message is now FILTERED/PRICE_OUT_OF_CANONICAL_RANGE; original
lineage is retained. Catch-up and end-to-end acceptance remain open.

### Review-label and live-latency findings

Recent dashboard rows retain upstream `PENDING_REVIEW` with
`INSUFFICIENT_OR_AMBIGUOUS_STRICTLY_PRIOR_SAME_BOOK_ANCHORS`; explicit named
offers include ELIGIBLE rows, so this is not a blanket UI-only flag. The parser's
mounted prediction ledger has zero rows, with an existing PRIVATE_PRIMARY
authority epoch. Its live PRIVATE_PRIMARY snapshot is SAFE_NO_DATA, all 14 cells
reporting NO_FRESH_MELTED. Do not switch authority or substitute the separate
legacy dashboard's estimates to hide the missing input.

The bounded parse queue used global oldest-first ordering, making new independent
gold/XAU facts wait behind unrelated history. Fair selection now reserves at
least half of each batch for global oldest history and shares the other half
across five sources. XAU, aggregate and self-contained private offer revision
histories can select actual fresh messages. Herat and melted-flow retain their
within-source oldest-first ordering because they have prior-fact dependencies.
The final selected batch is ordered causally; event times, economics, checkpoints,
raw retention, model/quality policies and Product authority are unchanged.
Small batches (<10) and unlimited/offline callers preserve old selection order.
Additive indexes bound each source lookup. Tests cover lane fairness, old-history
progress, future exclusion, exact-once drain, indexes on retained/new schema and
full private offer/trade parity against FIFO after catch-up. Deployment pending.

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

Covering-index runtime `83a698e0` passed activation at 06:45:56 UTC. Its exact
image passed 81 tests in 10.444 seconds. A normal cycle at 06:46:28 was 17.507s
(archive phase 15.367s), versus earlier 72–76s cycles. Queue: 120,132 at
activation, 110,683 at 06:50:20 and 101,581 at 06:57:15. Hourly spool rotation
was observed: a cycle reread 20,000 records, all duplicates, without recreating
that work. During this replay cycle total duration was 40.565s. Later mixed
source parsing still took 34.207s, so one fast cycle is not an all-load SLO.

Additional bounded research lookup: the old context loader fetched unrelated
group/channel text before Python filtered event keys. SQL now selects only the
export batch's keys, in chunks of at most 500, with an additive projection-key
index. Tests cover all returned content, missing keys, chunking and old-schema
index adoption. A real-inode-replacement test also checks three spool rotations.

A local raw TTL expiry is not proof of missing permanent research evidence.
Availability accounting now checks the existing PostgreSQL link for the exact
fact ID, revision, source, raw role and plaintext-hash-bound message when local
context is unavailable. No previous revision is substituted or raw data invented;
a genuinely absent link remains unavailable. Read-only samples of the latest
10 facts from each of both groups/private-gold/two melted channels all had their
durable context links. This sample does not prove coverage of all history.

Bounded-context source `36794a31` passed 102 tests inside its exact image in
9.771s. Its transfer was denied by the execution approval layer because that
code/image payload and destination were not considered explicitly authorized.
No alternative transfer or in-place deployment was attempted. That image is
not the running production version.

### New poison-message failure discovered at 07:04 UTC

The running `83a698e0` process had last completed a cycle at 07:02:47 with
94,650 pending rows, then restarted 11 times. Capture and sender remained
healthy with zero sender backlog/dead letters. This is **not a stable processor**.
OOM was false; the old entrypoint reported only `runtime_dependency_failure`.

Read-only validation of 431 public rows from the next 1,000 queued messages
reproduced `MarketStoreContractError` for USD_HERAT message 151917, parsed price
22,400 Toman/USD, outside the existing canonical range. The diagnostic used the
already-installed image, `mode=ro` plus `query_only`, disabled all fact/checkpoint
writes and did not publish raw text. SQLite sidecar creation required the normal
service UID/directory access; an initial read-only-directory probe could not open
the WAL database and was not treated as a data finding.

Follow-up fix: isolate each public message in savepoints on both stores; preserve
the caller's outer commit boundary. Filter only the existing canonical-magnitude
policy exception, retain the input and terminal disposition, and continue with
healthy messages. Mixed valid/invalid lines cannot leave partial model facts.
Other contract/storage errors still fail closed. Add a specific rejection counter
and payload-free exception type/code-location diagnostics for unexpected failures.
Tests reproduce a 22,400 quote, a partially valid multiline message, successful
subsequent input, outer rollback, unrelated-error propagation and log redaction.
Local final regression run: 106 tests in 53.399s, 104 passed and two encryption
tests skipped because the host lacks pyaes (those tests passed in the preceding
102-test immutable-image run). Foundation/error-redaction run: 16 passed.

Pending: user authorization accepted by the execution layer for transferring
the incident-fix images to current Web host `65.109.220.59`, deploying the final
fix to **market-processor only**, then drain and verify the complete pipeline.
Do not use the future Finland host `65.109.214.203` for this incident.
The sender recovery is separately recorded in
`MARKET_TRANSFER_RECOVERY_20260907.md`.

Do not declare this incident resolved merely because deployment or Docker health
passes. Persist the final runtime identifiers, measured drain/latency and checks
below after observing the deployed process.
