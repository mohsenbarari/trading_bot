# Coin Price Intelligence — Shadow v2 Integration

## Status

This branch contains the first project-integrated Shadow-v2 foundation. Every
new runtime path is disabled by default and non-authoritative. It does not
replace the commodity, price, range, offer, trade, parser response, bot
response, WebApp response, notification, or DR business event.

The existing product rule is unchanged: when the user omits a commodity, the
shared product parser still selects canonical `امام`. Shadow only observes the
same parse asynchronously.

## Included in this branch slice

### Shared parser observation

`bot/utils/offer_parser.py` remains the shared parser used by the bot and the
WebApp parse endpoint. For implicit-commodity input it schedules the existing
ranker after the product decision is complete. The request path does not await
model inference.

The ranker emits only a canonical commodity name from the verified bundle. It
does not emit or map a model catalog integer to a PostgreSQL commodity ID.
Aliases remain an input parsing mechanism; a Shadow output must exactly match
one canonical commodity name.

### Post-commit project adapter

SQLAlchemy listeners collect only:

- event kind (`OFFER` or completed `TRADE`);
- local integer row ID used for a later local read;
- business event time;
- the UTC cutoff captured immediately after commit.

No model executes during flush or commit. The post-commit adapter enqueues a
normalized local row identity. A dedicated PostgreSQL-backed worker reloads
the committed row, calculates one non-authoritative prediction, and persists
it in an independent transaction. Rollback clears pending Shadow events.

The cutoff captured immediately after commit is used as prediction `as_of`.
A snapshot generated after that cutoff is rejected. This prevents a delayed
worker from using future inputs. A later outcome is linked only when:

`prediction.as_of_utc < outcome.occurred_at_utc`

Repeated delivery of the same project event is idempotent independently of
bundle or snapshot version. A concurrent duplicate is resolved by the unique
run key. The parent run is flushed before its children so PostgreSQL foreign
key order is deterministic.

The durable queue uses `FOR UPDATE SKIP LOCKED`, bounded leases, lease
recovery, exponential retry, a terminal failed state, and one idempotency key
per local business row. Durability starts after the post-commit enqueue has
committed. The intentionally tiny commit-to-enqueue gap is not described as
atomic delivery: coupling optional Shadow writes to the business transaction
would violate the fail-open product boundary. Raw parser text has no durable
job; deterministic parser comparison remains bounded best effort, and Gemma
raw text exists only in process memory.

The current project schema has no paper/certificate trade form. Project offers
and trades are therefore classified as `PHYSICAL`; `CASH` and `TOMORROW`
remain distinct settlement values. Telegram melted-gold and dollar
classifiers retain their separate PHYSICAL/PAPER rules.

### PostgreSQL Shadow ledger

The two Shadow migrations create eight local tables:

- `coin_intelligence_shadow_runs`
- `coin_intelligence_shadow_feature_snapshots`
- `coin_intelligence_shadow_predictions`
- `coin_intelligence_shadow_parser_results`
- `coin_intelligence_shadow_outcomes`
- `coin_intelligence_shadow_jobs`
- `coin_intelligence_shadow_quality_decisions`
- `coin_intelligence_shadow_reviews`

The tables contain compact normalized features, versions, prediction ranges,
bounded diagnostics, and immutable later outcome scores. They do not contain
raw offer text, Telegram IDs, channel names, URLs, phone numbers, user IDs,
or raw offer/trade public IDs. Project subjects are linked through an opaque
SHA-256 fingerprint.

Every prediction has `is_authoritative=false`. Every collected project run
has `training_eligible=false`. A completed project trade creates an
`UNREVIEWED` outcome and therefore cannot enter promotion evidence.
An accepted immutable review plus a nonzero quality decision is required by
the report contract before project evidence can enter a promotion cohort.

All eight tables are `INTERNAL_BOOKKEEPING` in the sync registry. They never
enter legacy product sync or the three-site DR business event stream. A future
cross-site research consolidation must use the separate verified
market-intelligence/Object Storage plane.

### Feature Snapshot v2 and Regime v2

`COIN_FEATURE_SNAPSHOT_V2_20260726` is built at the committed offer cutoff.
Its history query requires `run.as_of_utc < cutoff`; an outcome is used only
when `outcome.occurred_at_utc < cutoff`. The target offer, same-timestamp
rows, future outcomes, and model predictions used as pseudo-labels are
excluded.

The snapshot retains explicit Tehran date, weekday, minute-of-day, research
banking-window state, missingness, source vintage, liquidity, continuous
direction/volatility/agreement scores, and the latest same-settlement regime.
Cash/tomorrow basis requires at least five one-to-one paired observations no
more than fifteen minutes apart. An unreviewed offer decays from full live
weight to one third during its first five minutes and has zero realtime
authority after five minutes; its immutable row remains available for later
reviewed offline research. Realtime history uses `realtime_weight`, never
`training_weight`; a confirmed trade has a larger base live weight than a
fresh offer, while a zero training weight still keeps that row out of training
eligibility.

`COIN_REGIME_V2_SHADOW_20260726` makes melted gold, dollar, USDT, XAU and IME
the directional evidence. Coin offers and trades confirm tolerance and
liquidity; they cannot lead regime direction. The result records continuous
direction, volatility, agreement, cross-source disagreement, liquidity,
confidence and hysteresis. Disagreement produces `RANGE`, not an automatic
shock.

### Quality gate and immutable review

`COIN_OFFER_QUALITY_V2_SHADOW_20260726` implements the exact project rule:

- sell below the **lowest active buy**;
- buy above the **highest active sell**.

In a normal/range market these observations receive zero realtime and zero
training weight, including a later linked trade. A reduced-weight Shadow-only
exception is possible only when independent underlying Regime-v2 direction,
confidence and agreement confirm the same directional move. Coin order flow
alone cannot authorize the exception.

A six-percent structural discontinuity is a conservative versioned research
quarantine, not an automatic correction. Reviews are append-only and coded:
`ACCEPT_ORIGINAL`, `ACCEPT_CORRECTION`, `REJECT_LABEL`,
`KEEP_UNREVIEWED`, or `AMBIGUOUS`. Reviewer identity is an opaque hash; raw
offer text and free-form review notes are forbidden. The report reads the
latest immutable review, recalculates corrected scores when explicitly
accepted, and still rejects any quality-zero row from promotion evidence.

### Numerical candidates

The persisted primary is the current deterministic range from the immutable
local snapshot.

`HYBRID_V2_RESEARCH_20260725` is a separate candidate role and is initially
gated to `امام/CASH/PHYSICAL`:

- a shared live anchor at most five minutes old yields the same range as the
  primary;
- otherwise it requires strictly-prior same-market history and intrinsic
  value;
- missing inputs produce an explicit `GATED_OFF` result with no fabricated
  number;
- future and same-timestamp history is ignored;
- a valid candidate interval is a union with the primary interval and can
  never narrow it.

Feature Snapshot v2 now publishes its strictly-prior history, so this path can
evaluate when enough qualified history exists.

Two additional independently gated candidates are recorded:

- `LOW_DATE_PHYSICAL_V2_SHADOW_20260726` accepts only physical 750-fineness
  melted gold per mesghal, uses coefficients `2.253`, `2.253/2`, and
  `2.253/4`, and keeps CASH/TOMORROW bubble history separate. Missing or
  dimensionally invalid physical evidence produces `GATED_OFF`.
- `CASH_TOMORROW_BASIS_V2_SHADOW_20260726` is for premium coins only, runs
  only without a fresh same-market anchor, and requires five strictly-prior
  paired basis observations. It never blindly relabels cash as tomorrow.

### Gemma parser candidate

Gemma is a local, independently gated second opinion after every successful
product parse. It never changes `ParsedOffer`. Cold `llama-cli` per request
was rejected by a real runtime test because model startup exceeded the parser
budget. The repository therefore defines one warm `llama-server` sidecar:
its GGUF volume is read-only, it has no published host port, and the adapter
accepts only the fixed Docker-local endpoint. The container is limited to one
parallel request, four inference threads, a 2K context, 10 GiB memory, four
CPUs, 128 PIDs, dropped capabilities, low-verbosity logging and a read-only
root.

HTTP and model-output sizes plus wall time are bounded. Only a strict
normalized JSON object is accepted. Thinking/reasoning output is disabled and
decoding is constrained by an exact JSON Schema; the adapter still validates
the response independently. Unknown keys, noncanonical commodities, guessed
or incomplete non-abstaining values, and oversized output fail closed. When
the text has no explicit coin name, this independent parser must abstain from
the commodity field instead of copying the product's default-Imam rule. It may
still extract explicit side, settlement, quantity and price for field-level
comparison. Raw input, prompt, response content and model weights are not
persisted or logged.

A real CPU-only warm-sidecar smoke test returned the exact normalized fields
for a synthetic explicit-Imam offer in roughly thirty seconds. This confirms
the isolation and schema path, but also confirms that this Gemma build is an
offline/background evaluation candidate—not a synchronous product parser.
Only one Gemma task may be in flight; later samples are dropped with a bounded
metric while it is busy.

### Evaluation

`scripts/report_coin_intelligence_shadow.py` reads immutable outcomes and
produces aggregate metrics only. It separates:

- operational unreviewed/reviewed evidence;
- promotion-eligible reviewed/trusted evidence.

It reports primary and candidates separately by canonical commodity,
settlement, and trade form. Metrics include MAPE, median and p90 absolute
error, signed bias, interval coverage, interval width, and abstentions. It also
reports paired candidate-versus-primary error improvement and coverage delta
for predictions from the same run. Abstention is not counted as zero error.
Exact prices and subject identifiers are not emitted.

The command requires `--acknowledge-shadow-only`. A report file must be
outside the repository:

```bash
python scripts/report_coin_intelligence_shadow.py \
  --acknowledge-shadow-only \
  --output /var/lib/trading-bot/coin-shadow/report.json
```

## Configuration

All flags default to false:

```dotenv
COIN_INTELLIGENCE_SHADOW_ENABLED=false
COIN_INTELLIGENCE_SHADOW_PERSIST_ENABLED=false
COIN_INTELLIGENCE_SHADOW_PROJECT_EVENTS_ENABLED=false
COIN_INTELLIGENCE_SHADOW_NUMERIC_V2_ENABLED=false
COIN_INTELLIGENCE_SHADOW_FEATURE_V2_ENABLED=false
COIN_INTELLIGENCE_SHADOW_QUALITY_GATE_ENABLED=false
COIN_INTELLIGENCE_SHADOW_LOW_DATE_V2_ENABLED=false
COIN_INTELLIGENCE_SHADOW_BASIS_V2_ENABLED=false
COIN_INTELLIGENCE_SHADOW_DURABLE_WORKER_ENABLED=false
COIN_INTELLIGENCE_SHADOW_GEMMA_PARSER_ENABLED=false
```

Required runtime paths and bounds:

```dotenv
COIN_INTELLIGENCE_BUNDLE_PATH=
COIN_INTELLIGENCE_SNAPSHOT_PATH=
COIN_INTELLIGENCE_SHADOW_TIMEOUT_SECONDS=1.0
COIN_INTELLIGENCE_SHADOW_MAX_INFLIGHT=16
COIN_INTELLIGENCE_SHADOW_SAMPLE_RATE=1.0
COIN_INTELLIGENCE_SHADOW_WORKER_POLL_SECONDS=1.0
COIN_INTELLIGENCE_SHADOW_WORKER_LEASE_SECONDS=60
COIN_INTELLIGENCE_SHADOW_WORKER_MAX_ATTEMPTS=5
COIN_INTELLIGENCE_SHADOW_GEMMA_ENDPOINT=http://coin_intelligence_gemma_server:18123/v1/chat/completions
COIN_INTELLIGENCE_SHADOW_GEMMA_TIMEOUT_SECONDS=90.0
```

Persistence and every candidate require the top-level Shadow flag.
Project-event observation requires persistence and the durable-worker gate;
Feature v2 requires project events; quality, low-date and basis candidates
require Feature v2; Gemma requires persistence. Partial flag combinations and
unsafe time/resource bounds fail startup.

The worker is under the Compose profile `coin-intelligence-shadow` and is
therefore absent by default. The following is a future activation command,
not deployment authorization:

```bash
docker compose --profile coin-intelligence-shadow up -d \
  coin_intelligence_shadow_worker coin_intelligence_gemma_server
```

Enabling code flags is not deployment authorization. A valid snapshot
producer, writable migrated local database, resource budget, alerting, and a
rollback procedure are separate activation gates.

## Verified in this branch

- focused parser/service/ranker/ledger/evaluation/sync tests;
- feature-off product behavior;
- PostgreSQL upgrade/downgrade/re-upgrade through both Shadow migrations on
  an isolated database;
- real PostgreSQL durable enqueue, claim and completion;
- real PostgreSQL prediction-to-outcome linkage;
- immutable accepted-correction review and promotion-cohort recalculation;
- duplicate outcome suppression;
- strictly-prior outcome linkage and future prediction exclusion;
- Shadow tables excluded from project sync/DR business replication;
- Compose profile validation and a real warm Gemma JSON-Schema smoke test;
- 159 focused integration/regression tests, including the shared manual offer
  parser contract;
- migration metadata has one head.

The repository's complete clean-database Alembic history currently stops at a
pre-existing assertion in revision
`f2c7d8e9a0b1_allow_offer_republish_per_home.py`, before reaching this
revision. Both Shadow revisions pass isolated
upgrade/downgrade/re-upgrade from their declared parent. Existing
installations already at that parent can apply them; clean-bootstrap migration
history needs a separate project-wide repair and review.

## Documented next steps (not implemented in this slice)

Items 1–6 are implemented as disabled, non-authoritative Shadow components.
Sparse markets still abstain or remain Shadow until independent outcomes are
large enough; implementation is not evidence for promotion. The following
items are explicitly deferred:

7. **Operator surface:** there is no repository admin comparison/review UI in
   this slice. A future page must use the project's existing admin session,
   authorization, CSRF and audit boundaries; it must not ask for a separate
   token. Timestamps are displayed explicitly in `Asia/Tehran`. It may show
   bounded normalized primary/candidate differences, quality reason codes and
   outcome IDs, and may append one of the coded immutable review actions. It
   must not edit prior outcomes/reviews, expose raw prompts or silently turn a
   correction into training data.
8. **Three-site data plane:** Shadow tables stay local. Telegram, IME, USDT,
   snapshots, model bundles, and evaluation aggregates require the separately
   verified Object Storage/direct-Finland distribution design; business sync
   is forbidden for these payloads. This slice documents the contract but
   deliberately implements none of it while the three-site architecture is
   incomplete. `webapp_ir`/`wa-ir` owns future approved Iranian exchange/IME
   gold-and-coin feeds and selected USDT extraction; `bot_fi`/`bot-fl` is the
   logical consumer through encrypted Object Storage and the `webapp_fi`
   relay, not through a new direct dependency. The reverse recovery path
   checks model manifests and transfers compact required input
   deltas/snapshots to `webapp_ir` at least three scheduled windows per Tehran
   day, plus after an approved model release. Unchanged model objects are not
   recopied.

   The implementation slice must define origin authority, cutoff/watermark,
   schema/bundle/application compatibility, hashes, atomic activation,
   previous-known-good rollback, replay/idempotency, missed-window alerting,
   maximum staleness and fail-closed behavior for every object. During an
   Iranian international outage, a separately promoted `webapp_ir` may use
   only its last verified compatible recovery package plus fresh local
   exchange/IME and USDT data; stale international inputs keep their real age,
   confidence is degraded/widened or inference abstains, and isolated
   training/promotion is forbidden. `webapp_ir` remains dark standby and
   receives no application writer or background-job authority merely because
   market artifacts are present.
9. **Retention:** operational feature snapshots and completed/failed jobs need
   a reviewed retention and sampling worker before high-volume enablement.
   High-frequency offer/trade sequences cannot be destructively replaced by
   minute averages because streak, ordering and trade linkage are model
   features. A future policy must separately define the hot raw-normalized
   window, compact aggregates, immutable reviewed outcomes, evaluation
   artifacts, queue tombstones and deletion audit. No duration is approved in
   this slice.
10. **Promotion:** no global switch may promote this feature. Promotion is per
    component and per `commodity/settlement/trade_form`, including separate
    CASH/TOMORROW and sparse/reopening cohorts. Each candidate needs a later
    chronological untouched cohort, minimum distinct market days and confirmed
    trades, point-error/bias/coverage targets, resource and latency budgets,
    rollback, and an explicit owner decision. Gemma parser fields are promoted
    independently; numerical candidates cannot inherit parser approval or the
    reverse.
11. **Legacy clean bootstrap:** the pre-existing clean-database Alembic chain
    still stops at the older republish migration described above. A separate
    project-wide slice must reproduce the failure on a new database, repair it
    only with a forward/compatible migration strategy, and verify full
    `base -> head`, downgrade boundaries and an existing-production upgrade.
    This slice validates the two Shadow migrations from their declared parent
    but intentionally does not alter that historic migration.

## Next implementation slices

1. admin-only comparison/review surface;
2. target-site artifact/data distribution;
3. retention/sampling worker;
4. untouched chronological live Shadow soak test;
5. component-and-market-specific promotion review;
6. separate legacy clean-bootstrap migration repair.
