# Telegram analysis interruption — 2026-10-18

## Confirmed cause

Capture continued on both Telegram accounts, and receiver/adapter checkpoints
were aligned. Parsing stopped: the processor's last successful heartbeat was
2026-10-17 08:58:20 UTC, with over 1,300 observed automatic restarts by the next
morning. Exit code was 1, not an OOM kill; disk capacity remained available.
This is distinct from the October 3 receiver/TLS incident.

A lock-protected single-cycle reproduction of the exact installed image traced
`MarketStoreContractError: quantity_must_be_positive` to a USD_HERAT message at
08:58:17 UTC. The message's explicit quantity really was zero; this was not a
thousands-separator parsing error. No text, identities, quantities or prices
were printed or retained in incident artifacts. The shared transaction retried
this same invalid message, preventing all other sources, including G1/G2, from
completing their projection cycle. Raw capture/cursors remained durable.

The host supervisor also renewed its 180-second startup grace on every process
restart. A perpetually failing container could therefore remain STARTUP_GRACE
despite a day-old processor heartbeat. Docker restart alone cannot cure poison
input.

## Corrections and boundaries

- `67ff59cb`: classify only known invalid numeric input: invalid/nonpositive
  price or quantity, and the existing canonical-magnitude rejection. Preserve
  the per-message savepoints; a multi-line message cannot publish a partial
  prefix before rejecting its invalid remainder. Store FILTERED lineage with
  `INVALID_QUANTITY`, `INVALID_PRICE` or the existing price-range disposition.
  Retain raw evidence, causal identities and normal retention. Healthy messages
  and independent sources continue; a second cycle does not retry the poison.
- Keep identity, chronology, schema, unit, database and unknown contract errors
  fail-closed. No fabricated quantity, discarded queue, counter reset, financial
  gate weakening, model change or authority substitution.
- Add `input_policy_rejections` to actual cycle counters. XAU keeps its bulk fast
  path, falling back to per-message atomic isolation only for known input errors.
- Bound startup grace across failed restarts of the same container. Only a real
  fresh, release-bound heartbeat clears that failure chain; a genuinely new
  deployment gets its own grace. A restarting container past grace reports
  `CRASH_LOOP`/`DEGRADED`, not healthy or perpetually starting. Intentional stops
  remain respected; recovery cooldown and deployment/session locks stay intact.
- `71f08c18`: preserve the installed processor's bounded calibration scan,
  covering XAU export index/query and phase timings. These installed performance
  guards had diverged from main; the hotfix must not regress them.

## Scoped rollout

Initial numeric-isolation release: `71f08c18985342b85353b717f446ade053cf8f4c`.
Portable image digest:
`42cf85238c4cbdb1e8129beec8b55c8ada0503b5ebc35b311116031fdec6322f`.
It activated successfully, with one input rejection, zero archive rejection and
zero new processor restarts. Both groups reached today's Bot store. First
recovery cycle was 132 seconds; subsequent cycles were 51–59 seconds. The poison
message's durable disposition was verified as FILTERED/INVALID_QUANTITY.

This exposed a recovery starvation issue: all 25 archive slots went to coin
groups while fresh melted references waited, leaving the real Primary model
SAFE_NO_DATA. Follow-up `b560ef620f213e23236ef33f079f50e82a09481f` reserves bounded
independent group/reference shares and historical progress for production-sized
batches. Small/offline batches preserve their prior order. Fresh lookups use an
additive non-XAU source/time index; XAU searches only real closed 15-second
model buckets. Existing dependency archival still precedes trade publication.
No stream sequence, input price, freshness gate or revision identity changes.
The regression test proves busy G1/G2 plus a high-volume external source cannot
hide minute gold, Herat, real XAU or the oldest historical fact, with no duplicate
selection and an indexed query plan.

First fairness candidate: `trading-bot-market:recovery-b560ef62`, portable content digest
`7b263f86df72a530ec4794beeb89efbaab2a94a98b3c78fbd48f078d37b3837b`.
This follow-up replaces only the now-running processor using an ordinary guarded
handoff, not the broken-prior repair exception.

Its activation did not pass: guaranteed historical work exposed the legacy
unbounded historical XAU/non-XAU search, previously hidden behind a full coin
batch. The new-release heartbeat did not arrive within 300 seconds. The guard
rolled back to the working numeric-isolation release, preserving the failed
journal and all state. Do not report that candidate as live or extend/fake its
readiness proof. Primary remained OK during verification.

Final correction `aeaa3f0c2be3fab3ce440575b5ce2e2ed22fb0d1` bounds historical
discovery to 500 narrow index entries per partition/cycle. A separate durable
cyclic discovery cursor advances only through accounted/selected prefix work,
never past unselected pending entries. Cursor updates share the normal export
transaction, roll back on failure and wrap to discover edits behind the prior
window. Existing stream checkpoints, fact/ACK ledgers and receipts are never
reset. Real closed XAU buckets and dependency archival remain unchanged.
Final image tag is `trading-bot-market:recovery-aeaa3f0c`; portable digest is
`85a74698e9688444bc821ea9d5ce6b07895e9d3359c8916eea2414a647fa3b00`.

The image and guardian scripts were transferred to the user-confirmed WA-FI.
Only market-processor may be replaced. Its explicit repair gate binds the known
prior release `9d752171fee23b20afdef4bd6efc8c3962262586`, exit-1 restart loop,
non-OOM state, role/schema, full active Compose chain and unchanged mounts.
The deployment lock, exclusive state owner, fresh new-release heartbeat, image
digest and bystander guards remain required. A rollback to this known broken
prior is labeled ROLLED_BACK_DEGRADED, never healthy.

Activation journal is retained in the protected incident operations directory.
The watchdog update is installed on both existing hosts. Product mode, DNS,
Writer, sessions, sender/receiver, database contents and checkpoints are not
reset or replaced. Catch-up is not accepted as completed until source facts,
model snapshots and the authenticated actual web activity agree.

## Tests and live acceptance

- 82 tests passed directly against the first fairness Python 3.11 image, without external
  networking or production mounts. Coverage includes the actual zero-quantity
  failure pattern, rollback of a partially parsed multi-line message, continued
  coin/Herat/XAU processing, idempotent follow-up, zero-price XAU bulk isolation,
  unknown-error fail-closed behavior, bounded crash-loop grace, cooldown and
  exact repair scoping.
- The final combined transport, archival, parsing, trade, adapter, snapshot and
  display regression suite passed all **186** tests. It additionally covers
  bounded discovery, no skipped pending work, transactional rollback and
  discovery of revised older inputs.
- All **83** direct final-image tests passed on Python 3.11, with external
  networking disabled and no production data/secret mounts. Imports used the
  built image; one static source-inspection test also received its exact source
  file read-only at the expected verification path.

At 08:06:05 UTC, before the fairness follow-up, the Primary model had already
recovered to OK/version 887023 on numeric-isolation release 71f08c18. Fairness is
preventive hardening against the demonstrated recovery starvation, not a claim
that the stopped process remained broken until the final release.

## Verified live result

The bounded-history handoff reached APPLIED_LIVE on release aeaa3f0c. Its actual
WA-FI Docker image ID is
`sha256:678aaf5cd8e954f368fc514430e5e63e638d84b2883194465ed0847287bc511c`;
the portable digest above matched independently. Original mounts/Compose chain,
exclusive owner and all bystanders were preserved. Exact initial, failed and
final activation journals remain separate in the protected incident directory.

- By 08:18:35 UTC the processor completed multiple real cycles with zero new
  restarts, zero archive rejection, and zero pending coin groups. Measured steady
  cycles were 24.896 and 26.147 seconds; archive phases 9.011 and 9.547 seconds,
  versus roughly 51–59/24–39 seconds on the initial numeric-isolation release.
- Both capture accounts remained live, capture sequences increased, and all ten
  supervised roles reported LIVENESS_OK. Both watchdog timers were active/enabled.
- Sender queue/oldest age/dead letters/retry alerts were zero. All ten
  receiver-to-adapter checkpoint gaps were zero at the acceptance sample.
- Bot and WA-FI agreed on OK snapshot version 887171 at 08:18:24.805258 UTC.
  The next actual snapshot advanced to 887172. Both group inputs were FRESH.
- Authenticated actual dashboard and activity HTTP returned 200. Dashboard was
  RUNNING/HEALTHY, PRIVATE_PRIMARY (not the legacy fallback), with 14 estimated
  cells and snapshot age 3.544 seconds. Today's offers from both groups were
  visible; data and financial eligibility remain distinct. Diagnostic sessions
  logged out afterward.
- Discovery cursors advanced (non-XAU 1653, XAU bucket 119178176 at one sample),
  demonstrating actual bounded historical traversal, not a skipped/faked queue.

Historical parsing catch-up is **not complete**: 51,815 older market messages
remained at 08:18:35, down from roughly 68,000. Keep background recovery running;
do not describe the entire historical queue as drained. Current group input and
reference publication no longer wait for that history, and no raw/ledger/receipt
or checkpoint was cleared to obtain the live result. Silence, ambiguous input,
source closure and safe abstention remain legitimate states, not hangs to hide.

This closes the reproduced numeric-poison/crash-loop and unbounded-discovery
failure paths; it is not an absolute uptime guarantee against network/provider
failure, corrupt storage, expired credentials or new unknown contract defects.

Prior recovery constraints, exact research-revision link audit and causal replay
notes remain in [the September recovery report](MARKET_PROCESSOR_RECOVERY_20260907.md).
Receiver/transport recovery remains documented in [the October 3 report](MARKET_PIPELINE_RESILIENCE_20261003.md).
