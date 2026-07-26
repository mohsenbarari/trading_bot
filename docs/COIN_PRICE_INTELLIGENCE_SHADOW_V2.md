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

No model executes during flush or commit. The post-commit worker reloads the
committed row, calculates one non-authoritative prediction, and persists it in
an independent transaction. Rollback clears pending Shadow events.

The cutoff captured immediately after commit is used as prediction `as_of`.
A snapshot generated after that cutoff is rejected. This prevents a delayed
worker from using future inputs. A later outcome is linked only when:

`prediction.as_of_utc < outcome.occurred_at_utc`

Repeated delivery of the same project event is idempotent independently of
bundle or snapshot version. A concurrent duplicate is resolved by the unique
run key. The parent run is flushed before its children so PostgreSQL foreign
key order is deterministic.

The current project schema has no paper/certificate trade form. Project offers
and trades are therefore classified as `PHYSICAL`; `CASH` and `TOMORROW`
remain distinct settlement values. Telegram melted-gold and dollar
classifiers retain their separate PHYSICAL/PAPER rules.

### PostgreSQL Shadow ledger

The migration creates five local tables:

- `coin_intelligence_shadow_runs`
- `coin_intelligence_shadow_feature_snapshots`
- `coin_intelligence_shadow_predictions`
- `coin_intelligence_shadow_parser_results`
- `coin_intelligence_shadow_outcomes`

The tables contain compact normalized features, versions, prediction ranges,
bounded diagnostics, and immutable later outcome scores. They do not contain
raw offer text, Telegram IDs, channel names, URLs, phone numbers, user IDs,
or raw offer/trade public IDs. Project subjects are linked through an opaque
SHA-256 fingerprint.

Every prediction has `is_authoritative=false`. Every collected project run
has `training_eligible=false`. A completed project trade creates an
`UNREVIEWED` outcome and therefore cannot enter promotion evidence.
`training_eligible=true` and a `REVIEWED` or `TRUSTED` outcome are both
required by the report contract. A future review workflow must create
immutable review evidence rather than silently changing a prediction.

All five tables are `INTERNAL_BOOKKEEPING` in the sync registry. They never
enter legacy product sync or the three-site DR business event stream. A future
cross-site research consolidation must use the separate verified
market-intelligence/Object Storage plane.

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

The current snapshot evidence does not yet publish the required
`same_market_history` feature. Consequently the no-live-anchor Hybrid-v2 path
will visibly gate off until feature snapshot v2 is implemented. This is an
intentional readiness signal, not a fallback to an invented price.

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
```

Required runtime paths and bounds:

```dotenv
COIN_INTELLIGENCE_BUNDLE_PATH=
COIN_INTELLIGENCE_SNAPSHOT_PATH=
COIN_INTELLIGENCE_SHADOW_TIMEOUT_SECONDS=1.0
COIN_INTELLIGENCE_SHADOW_MAX_INFLIGHT=16
COIN_INTELLIGENCE_SHADOW_SAMPLE_RATE=1.0
```

Persistence, project events, and numeric-v2 cannot be enabled while the
top-level Shadow flag is false. Timeout, queue, and sample-rate settings fail
startup validation when outside their safe bounds. Project-event observation
requires persistence, and numeric-v2 requires both persistence and
project-event observation; partial flag combinations fail startup.

Enabling code flags is not deployment authorization. A valid snapshot
producer, writable migrated local database, resource budget, alerting, and a
rollback procedure are separate activation gates.

## Verified in this branch

- focused parser/service/ranker/ledger/evaluation/sync tests;
- feature-off product behavior;
- PostgreSQL migration upgrade and downgrade on an isolated database;
- real PostgreSQL prediction-to-outcome linkage;
- duplicate outcome suppression;
- strictly-prior outcome linkage and future prediction exclusion;
- Shadow tables excluded from project sync/DR business replication;
- migration metadata has one head.

The repository's complete clean-database Alembic history currently stops at a
pre-existing assertion in revision
`f2c7d8e9a0b1_allow_offer_republish_per_home.py`, before reaching this
revision. The new Shadow revision itself passes isolated upgrade/downgrade
from its declared parent. Existing installations already at that parent can
apply it; clean-bootstrap migration history needs a separate project-wide
repair and review.

## Known challenges before any live enablement

1. **Durable execution:** the current bounded tasks are in-process and
   best-effort. A process crash can lose an observation. Python thread timeout
   also cannot terminate work already executing in a thread. Move numerical
   and Gemma candidates to a dedicated bounded worker before sustained live
   load.
2. **Snapshot scheduling:** collector, snapshot pipeline, artifact placement,
   and target-site schedules are still operator/offline only. No deployment
   has been enabled.
3. **Feature snapshot v2:** same-market prior history, cash/tomorrow basis,
   Tehran banking-time state, continuous regime vector, source vintages,
   liquidity, and missingness signatures are required before Hybrid-v2 can
   evaluate stale-anchor windows.
4. **Review and quality gate:** crossed-book outer-extreme rules,
   discontinuity quarantine, immutable correction review, expiry weights, and
   trusted label promotion are not connected to project events yet.
5. **Sparse markets:** current evidence is Imam/CASH dominated. Half, quarter,
   one-gram, low-date, TOMORROW, and reopening cohorts must remain Shadow or
   abstain until later independent outcomes exist.
6. **Gemma:** Gemma parser and bubble diagnostics remain external research.
   GGUF weights, prompts containing protected text, sessions, and generated
   datasets must not be committed. A local adapter needs strict JSON schema,
   deterministic numerical reconstruction, timeout/resource limits, and
   independent evaluation before repository activation.
7. **Operator surface:** there is no repository admin comparison/review UI in
   this slice. Any future endpoint must use existing admin authorization and
   expose only bounded diagnostics.
8. **Three-site data plane:** Shadow tables stay local. Telegram, IME, USDT,
   snapshots, model bundles, and evaluation aggregates require the separately
   verified Object Storage/direct-Finland distribution design; business sync
   is forbidden for these payloads.
9. **Retention:** operational feature snapshots need a reviewed retention and
   sampling job before high-volume enablement.
10. **Promotion:** no global switch may promote this feature. Promotion is per
    component and per `commodity/settlement/trade_form` after a later
    chronological untouched cohort.

## Next implementation slices

1. versioned normalized project/Telegram/IME/USDT event and feature-snapshot-v2
   contracts;
2. quality/quarantine/review ledger and outer-extreme tests;
3. durable Shadow worker with load shedding and shutdown behavior;
4. continuous regime-v2 and same-market anchor history producer;
5. low-date, cash/tomorrow-basis, and conditional-interval candidates under
   independent gates;
6. strict local Gemma parser second opinion;
7. admin-only comparison/review surface;
8. target-site artifact/data distribution and live Shadow soak testing.
