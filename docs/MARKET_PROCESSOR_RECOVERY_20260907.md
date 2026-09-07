# Market processor replay/backlog recovery — 2026-09-07

## Conversation projection optimization activated — 11:12 UTC; verified through 11:26 UTC

- Refactor commit `0625597f27daca7e591e9e277b662bb24db69083` replaces the
  full historical offer scan for every fact with an in-memory index per exact
  instrument/trade-form book, sorted once by event time. Two binary searches
  select only the existing inclusive five-minute window. The original function
  still enforces self-exclusion, `available_at_utc` causality, settlement-book
  preference/fallback, minimum evidence, median/MAD and 5% floor. No data is
  deleted, TTL shortened, persistent cache added or model policy/version changed.
- A test oracle retains the pre-index algorithm and compares every exclusion
  reason. Boundary coverage proves exactly 300 seconds is included, 301 seconds
  excluded, and a reference available in the candidate's future remains unusable.
  A 2,000-row complexity test bounds reference visits and prevents a return to
  the historical quadratic scan.
- Focused projection test: 12 passed initially; final combined group/bridge/
  dashboard suite: **57 passed in 8.13s**. Adjacent processor/export/transport/
  parent/handoff suite: **48 passed in 2.268s**. Syntax, lint and diff checks pass.
- Final production-data comparison was one frozen SQLite `mode=ro`, `query_only`
  transaction with a 512MiB/55s bound and low CPU priority. For **8,496 real
  rows**, all old/new decisions and reason codes were byte-for-byte equivalent
  (canonical digest `3f0d03255bb1a58fd214f05472ace039cec16ad57cd8852d98144f2623a35a8d`).
  Calculation fell from 8.976s to 0.274s (**32.7x**). An earlier frozen sample
  of 8,503 rows also matched and measured 44x; the row set naturally changed
  between read transactions, never within either comparison.
- The owner explicitly authorized activation of `0625597f` only for the local
  `coin-private-shadow-legacy-estimator-bridge`. The immutable release is live;
  market-processor was not rebuilt or replaced, Product remains `LEGACY`, the
  existing timer schedule is byte-identical, and all three writer-lock
  device/inode/link/mode/owner tuples are unchanged. The timer is enabled and
  active. Prior unit files are retained on the attached volume at
  `/srv/trading-bot/staging-data/incident-recovery/20260907-bridge-price-window/`.
  Rollback restores those exact unit files and d08; it must never deactivate
  projected rows or reset a database.
- The first production run completed at 11:12:33 with `OK`: Market projection
  2.701s, conversation projection 4.875s and total 7.588s. Repeated automatic
  runs remained `OK`, incremental and zero-restart. Their conversation phase
  measured 4.747–5.211s instead of the observed d08 range of 32.220–34.948s.
  Total duration varied from 19.757–23.592s when shared Market/conversation
  locks added 12.683–16.384s; lock contention, not the optimized calculation,
  is now the dominant local bridge delay.
- Nine real post-activation G1/G2 facts old enough to precede the sampled bridge
  cycle were traced without reading payload values: 9/9 existed in Legacy with
  an `APPLIED` ledger row and 9/9 existed in `canonical_group_projection`.
  Source-Store insert to conversation projection timestamp was 12.643–43.884s.
  Projection timestamp precedes transaction commit, so this is not mislabeled
  as exact browser latency. The authenticated activity endpoint subsequently
  returned HTTP 200 with 40 current rows (31 model, one review, eight audit) and
  a post-activation latest display time; the open page refreshes every 15s.
- The live PRIVATE_PRIMARY estimator snapshot at 11:26:33 was `OK` with 14/14
  cells `ESTIMATED`. The snapshot sender remained `live-ready`, retained its
  acknowledged receiver view and recovered transient consecutive failures to
  zero during observation; brief retry bursts still recur and remain observable.
  Remote market-processor stayed on d08, Docker healthy with zero restarts/OOM,
  backlog zero and no archive refusal/unavailable research context. Its sampled
  projection still cost 22.530s and remains the next measured bottleneck.
- The immutable release added 91MiB to the root filesystem, which remained 56%
  used with about 17GiB free. The unit backup uses 12KiB on the attached volume,
  which remained 55% used with about 44GiB free. No raw/fact/model data, cache,
  volume or prior release was deleted. The historical revision/raw-link audit
  remains open, so this activation closes the bridge price-window deployment,
  not the full Market incident.

## Approved follow-up deployed — 10:27–10:35 UTC

The user explicitly approved both the d08 image for **market-processor only**
on `65.109.220.59:37067` and the local estimator compatibility bridge from the
same commit. This supersedes the earlier pending authorization below.

- Transfer streamed over strict-host-checked SSH without a saved tar. Remote
  image `sha256:fb6f4d370f2ee6523cd04609f46ca1a9e6a1fdf91e27c0461ba261101d181a95`
  has the same portable content digest as the tested local image. Engine image
  IDs/sizes differ; the complete portable content binding, not their equality,
  was verified. All 13 updated handoff guard tests passed in 0.020s.
- Processor journal `/srv/trading-bot/incident-recovery/20260907-processor-latency/handoff.json`
  reached **APPLIED_LIVE at 10:27:38 UTC**. Container
  `1758e117ca8b8dd2a22579005e4dc6c398b38944c3e505649537b1a2f7701f05`
  is the sole owner. Bystander containers, mounts, parent lock, data/checkpoints,
  resource limits, export=500 and restart policy were preserved. Automatic
  rollback was armed before stopping the prior processor; the 921 image and
  complete preceding override chain remain available.
- Local bridge d08 completed its first run at **10:30:35 UTC**. The existing
  installer pinned its immutable source release; only the timer was paused while
  the old oneshot finished normally. The rendered service was byte-compared to
  its predecessor with only the release SHA replaced; the timer bytes/schedule
  and all three existing lock identities/modes were unchanged. Product remains
  LEGACY. Subsequent automatic runs succeeded, timer remains enabled/active.
  Prior service/timer backups are on the attached volume at
  `/srv/trading-bot/staging-data/incident-recovery/20260907-bridge-latency/`.
  Rollback restores those two unit files, daemon-reloads and resumes the timer;
  it must **not** call the row-deactivation rollback tool or reset databases.
  Keep prior release/units until follow-up acceptance; review backup retention
  on September 14 instead of retaining them indefinitely. No deletion scheduled.
- At 10:34:32: processor healthy, zero restarts/OOM, ingested backlog=0;
  cycle=32.770s, projection=21.814s, archive=9.870s (previously ~19s),
  archive refusals/unavailable contexts=0. Private snapshot 10:34:02 is OK,
  **14/14 ESTIMATED**. Bridge 10:34:08: OK, 281 receipts loaded,
  Market projection=3.743s, conversation projection=32.472s, total=36.235s.
- Authenticated activity page at 10:34:40 returned HTTP 200: 31 model labels,
  one review, eight audit; current-day event times are visible. Quality flags
  were not overridden, so legitimate review/audit rows remain possible.
- Four exact real offer keys checked at 10:31:31 were missing from Legacy,
  but all were committed into the conversation/display database by 10:32:58.
  Event-to-Legacy **insert timestamp** samples were 100.505, 87.311, 66.471 and
  122.236s. These are not browser visibility measurements: transaction commit,
  conversation projection and browser refresh occur afterward. Earlier 99–134s
  samples likewise measure Store insert time, not final screen visibility.
- **Residual latency is proven, not resolved.** A bounded read-only profile of
  8,518 actual group rows loaded them in 0.177s; model exclusion calculation
  took 26.925s under profiling. `_live_book_price_exclusion_reason` accounted
  for 26.423s and visited `_live_book_offer_ref` 11,791,883 times. The historical
  full-book loop is the next specific target: restrict candidate references to
  the same inclusive five-minute causal window while preserving availability,
  settlement fallback, self-exclusion, median/MAD and every quality decision.
  This subsequent algorithm change is **not** in d08 and was not implemented
  or activated in this handoff. Processor projection also still costs ~22s.
- No permanent raw/fact data were removed, no synthetic input/timestamps created,
  and no main ref, capture, sender, bot, DNS or writer authority changed.
  The historical exact-revision research-link gap remains open. Successful
  deployment and sampled liveness are not a declaration that the incident is fixed.

## Verified follow-up artifact — 10:18–10:21 UTC; not deployed

- Live processor remains `921b45a0`, same container/image, Docker healthy,
  zero restarts/OOM. Heartbeat 10:17:38: ingested parse backlog=0,
  cycle=42.650s, archive=19.425s, published=447, rejected=0, unavailable=0.
  Private snapshot at 10:18:02 is OK with **14/14 ESTIMATED** cells.
  These are point-in-time checks, not proof of all historical archive coverage.
- Authenticated activity page at 10:21:31 returned HTTP 200 with 40 labels:
  36 model, zero review, four audit. Classes were counted with an HTML parser;
  a first double-quote-only regex returned a false zero count and was discarded.
  Legitimate ambiguous/conditional/late rows retain their quality guards.
- Bridge `ba1f7616` remains unchanged. Its 10:18:06–10:18:45 incremental run
  took 39s; completed source/destination watermark differences were 43s for
  both groups. This is not event-to-web latency and does not replace the
  previously measured 99–134s end-to-end samples.
- Follow-up source is **`d08ec9b2ebf3c14781ea6d60bf7892b2b60386ed`**, committed
  only on `refactor/two-site-architecture`. Main remains clean at
  `32d266bd58eeb61f6ebd1e928761a95e5e6c7fd8`. A main-branch commit was rejected
  by execution review; edits were moved to the refactor worktree and only our
  uncommitted main edits were reversed. No reverse merge or remote push occurred.
- Immutable image `market_pipeline_release:d08ec9b2ebf3c14781ea6d60bf7892b2b60386ed`:
  local ID `sha256:7b9845106a6c8454d50daf5e23a4a5441b3276cf4b255078bbd5ef5355a065ca`,
  portable content digest `2155da129b2686a283acdcaf69f30c4064b9b1655c0a75d1e110697a11b52240`,
  input signature `98768423bb0a0901c173a115fff06409f4092d40fd7cf71aff1e79e8074ba761`.
  Runtime-context comparison to main contains only the three reviewed core
  changes; dependencies, Dockerfile, config, contracts and migrations are unchanged.
  Image size is 152,792,301 bytes; payload scan found no env/db/session/key files.
- 88 focused host tests passed in 6.472s, including bridge installer tests.
  **236 exact-image tests passed in 13.784s**: 198 pipeline/regression tests plus
  38 bridge tests. Network was disabled, root filesystem read-only, tmpfs=64MiB,
  memory=384MiB, CPU=1. Runtime core was imported from `/app`, not the host.
- No new image transfer, processor replacement or bridge activation has occurred.
  Scoped approval for this artifact and the separate local bridge was requested;
  until granted, the prior authorization covers only the already-live 921 image.
  Preserve data, writer locks, Product LEGACY mode and tested rollback. Keep the
  incident open for latency and exact-revision historical research-link evidence.

## Latest recovery checkpoint — 09:35–09:40 UTC

- `921b45a0` remains live. The queue for the ingested parse prefix reached zero
  for all sources at 09:15:35 and remained zero in subsequent samples; this
  does not include messages arriving after each cycle's spool read. The last
  one-gram dependencies advanced naturally through canonical export (119/250
  unexported in one probe, then 8/250). At 09:24:27 **all 14 private v8 estimates
  were available**, still 14/14 at 09:35:37. No forced replay, synthetic price,
  checkpoint reset or authority substitution was needed.
- **Latency is not resolved.** The 09:35 cycle took 41.933s. `terminal_at_utc`
  in parse lineage is a logical cycle timestamp, not actual completion time;
  the earlier available-to-terminal samples must not be used as a latency SLO.
  Exact-key real offer traces reached the legacy Product Store after about
  99s and 134s: G1 09:33:33 → 09:35:12.370615, and G1 09:34:48 →
  09:37:02.420679. Two sampled G2 keys were already in the private store but
  not yet in that Product store at the check. No missing-data conclusion follows
  merely from this transient delay.
- Installed bridge `ba1f7616` took 44s in its 09:36:53–09:37:37 run; its timer
  waits another 15s after completion. Its incremental path loads the complete
  receipt ledger twice. The prepared fix loads only recent retirement candidates
  plus exact changed/parent keys, in <=400-key chunks; full reconciliation keeps
  the complete ledger. Old corrections, recent missing-row retirement, source
  separation, both writer locks and Product `LEGACY` authority remain intact.
  A fixture with 100 old receipts loads only the two required receipts while
  preserving the old correction and recent retirement. Another fixture covers
  903 receipts, chunking, duplicates, unrelated sources and full reconciliation.
- Read-only timing of the installed exporter, with bucket refresh disabled only
  in the transient diagnostic, took 14.790s: groups 0.237, fresh 0.055,
  non-XAU history 5.668, XAU history 8.823, payload lookup 0.006. The prepared
  additive XAU covering index avoids wide-row reads for readiness lookup, keeping
  the same selected real buckets, IDs, ordering and all raw observations.
  Separate elapsed-time counters are prepared for processor and bridge phases.
- Historical archive-link gap remains open: three exact August 31 aggregate
  facts from the 08:32 export have identical revision-1/2 payloads, hashes,
  parser and quality, but revision 2 lacks the raw link. Current fact availability
  is September 3 while retained raw availability is August 31. Raw was not proven
  lost; no association was copied across revisions and no timestamps were changed.
- The latest explicit operational authorization names the **921 image and only
  market-processor**. The new follow-up artifact and the separate local
  `coin-private-shadow-legacy-estimator-bridge.service` need scoped activation
  authority before rollout. No bridge service change has been made. The incident
  must stay open despite 14/14 model coverage and zero parse backlog.

## Activation — 08:50 UTC; verification through 08:55 UTC

The user explicitly authorized image
`market_pipeline_release:921b45a045073dab91f14b56f4bb8fc7c9a25344` to
`65.109.220.59:37067`, replacing **only market-processor**, preserving data and
rollback. Transfer succeeded over strict-host-checked SSH without a root-disk
tar. Local/remote portable content digest matched:
`9791fee4402ef466ba0d33fdaf4163cea7686937a80d121633fb84249b9ab8c3`.
Remote image ID:
`sha256:70133301cfdebca98887e651bfba8a87f45da21580fb77df5088562255c47472`.

- The 195 exact-image regression tests remain applicable; runtime content did
  not change. All 13 updated handoff tests passed, including preserved export
  capacity/restart policy and the full prior override chain for rollback.
- Preflight passed. The scoped journal
  `/srv/trading-bot/incident-recovery/20260907-processor-sparse-minutes/handoff.json`
  reached `APPLIED_LIVE` at 08:50:27 UTC. Processor container
  `cb16e49998b76b1cdbf72b3029e6fbadc88fc8a3d54ea0be47575b46f2069e82`
  is Docker-healthy with zero restarts. Sole owner, mounts, parent lock and all
  bystander container identities/start times were verified unchanged. No data,
  checkpoint, capture, sender, Product, Writer or DNS changes were made.
- Export capacity stays 500. The first complete cycle was 27.011s with backlog
  27,939; at 08:53:32 backlog was 25,177 and cycle 42.684s (archive 19.244s).
  Both sampled cycles had zero export rejection/missing research context. This
  does **not** repair or waive the earlier exact-revision archive-link gap.
- Actual authenticated dashboard: HTTP 200 and confirmed activity page; 40 rows
  contained 34 model-input labels, zero review labels and six audit-only labels.
  Private v8 snapshot is fresh/OK with **13/14** estimated cells. ONE_GRAM/CASH
  still reports `NO_SAFE_SAME_COMMODITY_ANCHOR`. No synthetic input or authority
  substitution was used; the separately identified historical replay was not run.
- Read-only latest-30-created-live-message samples per group showed event-to-
  availability median 0s/max 1s. Availability-to-parse median 20s, max 51s (G1)
  and 41s (G2); this sample can straddle activation and is not a full end-to-end
  post-release SLO. The latest sampled event/parse pairs were G1 08:54:08/08:54:15
  and G2 08:54:54/08:54:57 UTC.
- Sampled processor memory 187.6 MiB/384 MiB, CPU 18.26%; local root still about
  17 GiB free and attached volume about 44 GiB free. Do not treat a short sample
  as proof of perpetual stability. Backlog, latency, one-gram readiness and the
  historical exact-revision archive-link audit remain open.

## Earlier checkpoint — 08:30–08:33 UTC

- `cb45aad7` remains active, with zero restarts at the read-only identity check.
  At 08:30:08 its parse backlog was 45,107, cycle 53.908s, archive published 445
  and rejected zero. Root free space: local about 17 GiB; current Web about
  75 GiB. The attached local volume has about 44 GiB free.
- **New open archive evidence:** that cycle reported 346 unavailable research
  contexts. A read-only PostgreSQL transaction sampled the latest 100 deliveries
  per research source: exact-context links missing for aggregate 100/100, flow
  1/100, G1 1/100, private-gold 0/100, G2 0/100. Aggregate examples are August 31
  observations exported as revision 2 on September 7; each sampled missing
  example still has one link on another revision. This does not prove raw loss,
  nor authorize attaching another revision's text without exact evidence.
  Do not claim zero missing context or complete archival readiness from an
  earlier healthy cycle. No repair/replay/database write was performed by this
  diagnostic; its PostgreSQL transaction was explicitly read-only and rolled back.
- After the user's generic confirmation, the same direct `921b45a0` image
  transfer was refused again because the execution layer requires explicit
  payload/destination authorization. A strict-host-checking read confirmed the
  image is absent on Web; `market-processor` remains on `cb45aad7`. No indirect
  transfer, in-place code replacement, deployment or data reset was attempted.
  The precise scoped authorization has been requested. The 195-test source
  checkpoint was already merged into the refactor branch as `aa3d2d24`.

## Earlier checkpoint — 08:21 UTC

- Active: `cb45aad7` with the separately journaled export batch of 500. Zero
  processor restarts; sender queue/dead letters/rejections zero. No export
  rejection or missing research context. Parse backlog 52,236; cycle 57.983s.
- Actual authenticated dashboard returned HTTP 200. Its 40 compact activity
  rows contained 30 model-input labels, 8 review labels and 2 audit-only labels.
  Both group lists show today's events through approximately 11:48 Tehran.
  This is not proof that every ambiguous offer should become eligible, nor
  proof of the requested real-time latency.
- Private reference snapshot: 12/14 estimated cells; IMAM/TOMORROW and
  ONE_GRAM/CASH still lack their historical melted dependencies downstream.
  Read-only producer verification found those real inputs already retained.
  The exact two installed-engine lookup scopes total 500 distinct facts,
  scope SHA256 `50c1146586b7224da3303ce2066634634463d8076fae85a9279213c2f2615245`.
  **No manual/forced replay of this scope has been performed.** Normal
  export is continuing. Any scoped replay must use the canonical exporter,
  owner locks and exact receipts; never copy fabricated prices into the model.
- Follow-up source `921b45a045073dab91f14b56f4bb8fc7c9a25344` passed all 195
  exact-image tests (12.429s). Separate anchor/fairness pytest: 13 passed.
  Its image is local only: the execution approval layer denied transfer to
  the current Web host, then denied it again after strict SSH trust verification
  and payload inspection. Only two runtime source files differ from `cb45`;
  dependency files are unchanged and the image contains no env/DB/session/key
  files. No workaround or in-place deployment was attempted. A trusted user
  approval explicitly naming **this image and destination** is still required
  by the execution layer. Do not mark this incident resolved.

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

Fairness `cb45aad7` activated at 07:55:40 UTC. At 07:55:54 the actual private
snapshot changed from SAFE_NO_DATA to OK, with 12/14 estimated cells and 36 real
prediction-ledger rows; no authority/policy change or synthetic input was needed.
The missing cells were IMAM/TOMORROW and ONE_GRAM/CASH. Installed estimator code
showed real coin anchors but no contemporaneous melted points: for example
ONE_GRAM/CASH at Sep06 08:31:17 and IMAM/TOMORROW at Sep06 18:04:04. Retained
export backlog included 26,732 private-gold, 10,253 aggregate and 2,569 Herat rows.
Batch capacity 100→500 on the same image activated at 08:02:45; initially
169–445 published facts/cycle, zero rejection/context loss, but cycles grew to
52–61s. This is recovery progress, not an acceptable final real-time latency.

Follow-up: sparse private-minute rebuilds read the entire earliest-to-latest
span before filtering requested minutes in Python. Mixing fresh and historical
work makes that span many hours. Select only actual requested minute prefixes
in <=500-key chunks through an additive private-source-only expression index;
use the same index for affected-minute discovery/retraction. Preserve exact
weighted prices, book boundaries, closed-minute and availability limits. A
test with 100 unrelated intervening rows reads only the three requested input
rows and proves identical weighted values, unchanged inputs and no implicit
commit. Existing private/capture/fairness tests pass.

Also reproduced the analogous XAU poison-input failure offline against `cb45`:
one out-of-range quote aborts the whole bulk batch. The follow-up keeps the bulk
fast path for healthy batches and, only on canonical-magnitude rejection, retries
per message with savepoints. Invalid raw remains FILTERED with its reason, valid
quotes continue, and unrelated store-contract errors remain fail-closed.

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
