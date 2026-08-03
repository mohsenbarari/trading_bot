# Coin Price Intelligence — Shadow Introduction

## Decision

Branch: `candidate/coin-price-intelligence`

The branch has since been rebased through a merge with the current three-site
`main` architecture. Phase 1 remains the immutable offline foundation. The
project-integrated Shadow-v2 slice is documented in
[`COIN_PRICE_INTELLIGENCE_SHADOW_V2.md`](COIN_PRICE_INTELLIGENCE_SHADOW_V2.md).

The first repository slice is observation-only Shadow mode. It must not change
the existing intentional product rule that an offer without an explicit
commodity defaults to `امام`. It must not alter the parser response, offer
preview, persisted `commodity_id`, or user-visible flow.

## Runtime boundary

- One numerical model bundle is trained and versioned centrally.
- During normal operation, the same small, immutable bundle is available
  locally beside the Telegram bot on `bot_fi` and beside the active WebApp API
  on `webapp_fi`.
- `webapp_ir` keeps a verified standby copy of the compatible bundle and input
  snapshot. It does not run the WebApp API, inference, or application
  background jobs while it is dark standby.
- Only after controlled WebApp-authority promotion may `webapp_ir` run the
  local inference path for the Iran-served WebApp.
- Inference therefore has no synchronous cross-server dependency.
- Live market inputs are supplied through a local, atomically replaced,
  versioned JSON snapshot. No absolute `/tmp` path is embedded in code or the
  bundle.
- The deterministic range snapshot producer is repository-owned. It reads a
  normalized SQLite observation database read-only, enforces a strict source
  cutoff, applies the verified base model, low-date physical-melted policy, and
  coin-anchor transfer policy, then replaces the JSON snapshot atomically.
- `scripts/build_coin_intelligence_snapshot.py` is an offline Shadow entry
  point only. No scheduler, live collector, deployment, or user-facing
  activation is introduced by this slice.
- Telegram collection source is present but not enabled. External live
  collectors, scheduling, and deployment integration are not yet migrated.

## Shadow cycle orchestration

`scripts/run_coin_intelligence_shadow_cycle.py` composes one bounded local
cycle under a non-blocking filesystem lock:

1. optionally run the explicit Telegram collector;
2. validate the normalized SQLite schema;
3. calculate per-source freshness without exposing prices;
4. build a candidate snapshot at one strict cutoff;
5. require valid ranges for every canonical commodity and settlement;
6. compare the candidate with the previous short-horizon snapshot;
7. atomically publish the candidate and a separate health JSON.

The cycle does not replace the previous snapshot when schema, freshness,
bundle, model, interval, or anomaly validation fails. Snapshot publication
itself uses a durable atomic file replacement. It rejects missing or unexpected
rates, inverted ranges, full interval spans above 25%, and same-bundle center
changes above 30% inside six hours; changes above 8% are retained as Shadow
warnings. Re-running the same deterministic cutoff is idempotent, while a
version collision with different content is rejected.

Input quality is `HEALTHY`, `DEGRADED`, or `INSUFFICIENT`. A current melted,
dollar, USDT, or generic-coin anchor is required before candidate generation.
Ounce and closed-market IME observations may remain visible as
`REFERENCE_ONLY`, but transport time never refreshes their source age. A
degraded input may attempt a candidate; publication still fails if any of the
14 required rates is `NO_DATA`.

The health artifact stores only stage states, source ages, counts, relative
changes, reason codes, and versions. It contains no raw message, absolute
price, credential, Telegram message ID, or user data. The command requires
`--acknowledge-shadow-only`, all runtime paths must be outside the repository,
and Telegram collection cannot be combined with a historical `--as-of`.
Nothing invokes this command automatically yet.

## Telegram collector boundary

The production Telegram extraction source is repository-owned because its
source classification, parsing, normalization, deduplication, and schema
contract are part of the model input pipeline. Its current CLI is an explicit
Shadow-only operator tool; it is not installed in the default application
image, scheduled, deployed, or started by any application process.

Only executable source adapters, parsers, normalized event contracts, fixture
messages, tests, and non-secret configuration templates belong in Git.
Telegram API credentials, phone numbers, bot tokens, session files, raw chat
exports, raw-message databases, and generated training datasets do not.
Credentials must be injected by the deployment secret mechanism; session and
checkpoint state must live in a protected runtime volume. Test fixtures must
be synthetic or irreversibly minimized.

The collector writes the same normalized `price_events` contract consumed by
the producer and retains source event time. The model table contains neither
raw text, Telegram message ID, channel name, nor source code. A separate
four-row checkpoint table retains only the latest message ID per public source
for restart safety. A 16-byte opaque message key makes edits and retries
idempotent without exposing the public message ID to the model.

The public source catalog currently contains `abshdh`, `NaghdP`,
`ToofanHarirodOfficial`, and `qheimat_ounce`. Melted-gold gram prices and
hourly open/high/low summaries are ignored. Cash/physical and
today/tomorrow remain independent axes. Only explicit cash/official
melted-gold and explicit cash dollar quotes are PHYSICAL; other melted-gold
and Herat forms are PAPER. NaghdP trades are linked only to a strictly earlier
same-price, same-settlement offer inside a bounded window.

Normalized source consumers must preserve the same two-axis contract:

- `سکه نقدی` with `PHYSICAL` form is the cash generic-Imam reference. Its
  independent settlement may legitimately be `UNKNOWN` because the source
  label describes delivery form, not a separate today/tomorrow term.
- `سکه حواله` with `PAPER` form is the explicitly named tomorrow proxy. It may
  have `TOMORROW` or `UNKNOWN` settlement, remains lower-confidence, and must
  never be relabelled as a physical tomorrow observation.
- an explicitly cash Herat quote may likewise be `PHYSICAL` with `UNKNOWN`
  settlement. Consumers must not discard that series or silently replace it
  with USDT merely because the independent settlement field is absent.

Herat continuity has a separate, auditable contract. USDT is never a numeric
substitute for a missing Herat quote. A Herat observation inside the live
60-second window is used directly. Otherwise, the latest settlement-compatible
real Herat quote (at most seven days old) remains the price anchor. The model
compares a smoothed USDT reference at that anchor time with the current
smoothed USDT reference and applies the relative return to the Herat anchor
only when its magnitude exceeds the initial 0.10% noise deadband:

```text
usdt_return = current_usdt / anchor_time_usdt - 1
estimated_herat = anchor_herat * (1 + applied_usdt_return)
```

An upward return moves the estimate upward, a downward return moves it
downward, and a return inside the deadband leaves the Herat anchor unchanged.
The resulting point is explicitly `ESTIMATED`/`BRIDGED` and carries anchor
time, anchor age, both USDT references, raw return, applied return, and trend.
If either the Herat anchor or a comparable USDT reference is unavailable, the
result is `NO_DATA`; returning the USDT price under a Herat label is forbidden.

Cash Herat has an additional settlement-time contract and must not use the
generic USDT bridge directly. When a fresh cash quote is absent, the last real
cash quote is paired with the tomorrow Herat quote at the same anchor time.
The current tomorrow quote is then the primary movement driver. Historical
paired observations support an asymmetric initial response: cash receives
`0.90` of an upward tomorrow move and `1.05` of a downward move. A move inside
the 0.10% deadband does not move the cash anchor. Outside banking availability,
the cash/tomorrow basis widens by an initial 150 toman per closed hour, capped
at 1,500 toman. The default observed schedule is 08:00–17:00 Tehran from
Saturday through Wednesday, 08:00–12:00 on Thursday, and closed on Friday.
All schedule and coefficient values are explicit policy constants pending
later walk-forward calibration. The output records the cash anchor, tomorrow
anchor/current values, direction, beta, banking state, closed hours, time
widening, and cash/tomorrow basis before and after adjustment.

Collector execution is idempotent and restart-safe. The ounce source is
compacted to its newest quote per minute; melted-gold and dollar order flow is
not averaged away. Adding the source to the repository does not authorize
enabling it on any server.

## Temporal self-audit and controlled learning

The estimator must be evaluated as it would have existed at a historical
cutoff. A score obtained by fitting and testing on the same complete month is
invalid even when the feature rows themselves look chronological.

The research cycle therefore uses multiple global, non-overlapping future
folds. For every fold:

1. transfer coefficients are fit strictly before the fold's validation
   window;
2. point-policy selection and interval calibration use only that validation
   window;
3. the later test window is scored without refitting;
4. all events at the same timestamp are predicted before any becomes an
   anchor;
5. test residuals are recorded as diagnostic hypotheses and cannot mutate or
   promote the evaluated artifact.

Two scenarios are mandatory:

- `ONLINE_PRIOR` simulates ordinary operation, where each newly observed coin
  frame may anchor only later frames;
- `FROZEN_COIN_ANCHOR` freezes coin evidence at the test origin while allowing
  later underlying observations. This measures market reopening, overnight or
  holiday gaps, and 10–30 minute periods without a coin offer.

Diagnostics are broken down by canonical commodity, market regime,
cash/tomorrow settlement, offer/book/trade evidence, anchor-age cohort,
elapsed future horizon, input-coverage signature, and selected method. The
audit additionally records:

- the observed coin-to-intrinsic-gold ratio and bubble distribution per
  commodity and regime;
- coefficient and effective-pair stability across folds;
- interval misses above and below the range;
- residual correlation with melted gold, generic Imam, Herat/USDT, ounce, and
  standardized IME inputs;
- sparse cells that must remain Shadow or abstain.

Prediction evidence lives in a separate normalized research ledger without
raw text, Telegram identifiers, sender identity, or source links. A future
candidate may be motivated by this ledger, but it must be calibrated on
training/validation evidence and judged on a genuinely later fold. The system
must never learn from its own predictions as if they were ground truth.

The 2026-07-24 multi-origin diagnostic has 644 five-minute market frames and
259 non-overlapping future predictions in each scenario. It found:

- about `0.772%` online MAPE and `0.799%` frozen-anchor MAPE;
- only about `81.9%` and `78.8%` interval coverage, below the locked 95%
  promotion target;
- strong under-coverage after 6–24 hour and multi-day anchor gaps;
- only one one-gram and two tomorrow observations in the future folds;
- 16 trade frames, which perform better than offer/book frames but remain too
  few for broad promotion claims.

This evidence does not authorize test-driven widening or promotion. The next
interval candidate must use conditional calibration by evidence strength,
regime, and anchor-age on validation only, then wait for a later untouched
cohort.

## Commodity identity

Bundle catalog integers are model-catalog identifiers, not PostgreSQL primary
keys. The model emits a canonical commodity name that must equal one exact
`commodities.name` value. A later activation slice must resolve that canonical
name against the active site's local `commodities` table and use the single
matching row's actual database ID.

`commodity_aliases` is only an input-parsing aid: an explicit user alias points
to its canonical `commodities` row before inference. It is not an output
fallback and must not participate in model-result-to-database mapping. A
missing or non-unique canonical-name match must fail closed and require user or
operator resolution. Shadow mode records no replacement ID.

## Offer attributes and explicit mint years

An offer can carry material market attributes without creating a new canonical
commodity. The first such attribute is the coin mint year. `ربع ۱۴۰۳`,
`ربع ۱۴۰۴`, `نیم ۱۴۰۴`, and `امام ۱۴۰۳/۱۴۰۴` remain respectively `ربع بهار`,
`نیم بهار`, and `امام` in the project catalog. A future parser must preserve
the stated year as structured offer metadata rather than create a commodity,
alias, or PostgreSQL identity for it.

The canonical attribute contract is documented in
[`COIN_PRICE_INTELLIGENCE_OFFER_ATTRIBUTE_CONTRACT.md`](COIN_PRICE_INTELLIGENCE_OFFER_ATTRIBUTE_CONTRACT.md).
Its key rule is that an omitted year is `UNSPECIFIED`, not a claim that the
coin is a non-year variant. Explicit years are parsed by deterministic,
context-aware rules first; a constrained local LLM may later handle only
ambiguous text and cannot create an authoritative label by itself.

The 2026-07-24 research cohort found that explicit years are materially
different conditional price modes: quarter 1403 and 1404 were approximately
4.77% and 3.00% below nearby unspecified quarter observations, and half 1404
was approximately 1.56% below nearby unspecified half observations. Blindly
merging them into a single range is therefore invalid, but deleting them is
also invalid because it degraded reopening/frozen-anchor evidence.

The approved future direction is conditional modeling: canonical commodity,
mint year, and mint-year status are separate fields. Explicit-year observations
may become a strictly-prior auxiliary direction or range-tail signal only if a
future holdout demonstrates benefit; they must never override a fresh
canonical anchor. No runtime behavior changes in this phase.

The same research identified a positive, but test-touched, data-quality
hypothesis: widening the strictly-prior discontinuity reference window from
20 to 30 minutes removed four isolated observations from 5,959 offers and
reduced both online and frozen-anchor point error while narrowing the measured
mean range. It is deliberately not promoted. It needs a later untouched cohort
before it can change any quality gate.

## Melted-market relationship discovery — 2026-08-03

The dedicated relationship-discovery contract is documented in
[`MELTED_MARKET_RELATIONSHIP_DISCOVERY_SHADOW.md`](MELTED_MARKET_RELATIONSHIP_DISCOVERY_SHADOW.md).
It adds a separate point-in-time research graph for paper normal/reverse/swim,
today/tomorrow, physical, conditional and support-market behaviour.  Its goal
is to find, then strictly validate, cross-market and lead/lag relations before
they can inform sparse-coin, reopening, or regime decisions.  It is aggregate
only, leakage-safe, disabled from runtime use and has no automatic promotion.

## Three-site placement and data flows

This section is a **design contract only**. The three-site architecture is not
complete, so this feature must not add a collector, scheduler, sync worker,
Object Storage transfer, runtime role, deployment unit, network route, or
activation flag for that architecture yet. In project terminology,
`wa-ir` means `webapp_ir` and `bot-fl` means the Finland bot site `bot_fi`.

The project has three physical sites but only two business authorities. Market
intelligence must respect those existing boundaries:

| Site | Normal application role | Market-intelligence role |
| --- | --- | --- |
| `bot_fi` | permanent Telegram/foreign writer | live Telegram collection and local bot inference |
| `webapp_fi` | normal WebApp writer and Finland relay hub | local WebApp inference and verified Finland market-data projection |
| `webapp_ir` (`wa-ir`) | fenced/dark WebApp standby | planned Iran feed owner and verified model/data standby |

The current production dark-standby path on `webapp_ir` is DB-only and does
not yet authorize the collector or any market-intelligence sync process. The
future Iran collector must be a separate, non-authoritative process with no
application writer lease, no business database write credential, no public
API, and no ability to start WebApp jobs. Adding it requires its own
implementation, deployment and safety review after the three-site foundation
is complete.

Market data and model artifacts form a separate distribution plane. They must
not be inserted into the product-sync change log or the WebApp DR business
event stream:

1. Telegram observations originate on `bot_fi`, are projected directly to
   `webapp_fi` inside Finland, and are periodically delivered to `webapp_ir` as
   verified standby deltas or compact feature snapshots.
2. Approved Iranian exchange/IME gold-and-coin instruments and the selected
   Iran-accessible USDT feed originate in the isolated collector on
   `webapp_ir`. The logical consumer is `bot_fi` (`bot-fl`).
   This does not require or authorize a direct `webapp_ir -> bot_fi` network
   link: immutable micro-batches are spooled locally, encrypted, published
   through private versioned Object Storage, verified/relayed by `webapp_fi`,
   and then made available to `bot_fi` through the Finland-local data path.
3. Promoted model bundles originate from one Finland training/promotion
   authority. The exact immutable version is delivered to `bot_fi` and
   `webapp_fi`, then copied to `webapp_ir` as a standby artifact.

When connectivity is available, exchange/IME and USDT projection from
`webapp_ir` to the logical `bot_fi` consumer is a near-realtime market-data
flow. Its design target is delay measured in tens of seconds, with an explicit
source watermark. Failure queues immutable local micro-batches for replay;
reconnect must preserve source event time and must not collapse
sequence/order-flow information into a misleading average.

The reverse resilience flow from Finland to `webapp_ir` is periodic rather
than synchronous:

- schedule at least three reconciliation windows per Tehran day (before the
  main market opens, mid-session, and after the main session); exact times
  remain deployment configuration;
- transfer a model payload only when its immutable version changes, but verify
  its manifest and local availability in every reconciliation window;
- transfer compact normalized market-data deltas and a full recovery snapshot
  needed to restart inference at every window;
- trigger an additional delivery after an explicitly approved model/bundle
  release rather than waiting for the next window;
- alert on missed windows and record the age of the last **verified**
  checkpoint; an attempted or downloaded-but-unverified transfer is not a
  successful checkpoint.

The minimum recovery package on `webapp_ir` is versioned and self-contained:

- every locally enabled deterministic range/commodity bundle and, only if
  separately approved, its compatible local language-model artifact;
- feature schema, canonical commodity names, parser/normalization contract,
  required application-version range, and content hashes;
- latest compact CASH/TOMORROW feature snapshots, Telegram-derived melted
  gold/dollar/XAU inputs, coin anchors, qualified offer/trade order-flow
  features, low-date physical references, basis history, calendar features,
  source cutoffs and missingness;
- local exchange/IME and USDT watermarks plus enough retained normalized
  observations to resume sequence features without using predictions as
  labels.

Raw Telegram text, channel identifiers, user identifiers, sessions,
credentials and unreviewed training exports are not part of this package.
The full model is not copied repeatedly when unchanged: content-addressed
objects and immutable manifests make the three daily checks inexpensive.

Every market-data batch is append-only and identified by origin, dataset
family, UTC partition, sequence, schema version, and payload SHA-256. Ingest is
idempotent and fails closed on a gap, invalid hash, incompatible schema, or
sequence regression. Source observation time is retained; transport time never
turns stale data into a fresh observation.

Any payload whose source or destination is `webapp_ir` follows the established
Iran Object Storage boundary: client-side encryption, versioned objects,
content/ciphertext manifests, and short-lived presigned URLs. SSH may carry
only bounded control commands and status JSON. Finland-only payloads use the
approved direct encrypted and resumable Finland path and do not transit Iran
Object Storage.

During an Iranian international outage, `bot_fi` remains active and
`webapp_fi` remains its Finland peer. If `webapp_ir` is promoted, its inference
uses only its last atomically activated, verified compatible model/recovery
snapshot plus newly collected local exchange/IME and USDT data. It retains
the previous known-good artifact for rollback and never trains or promotes a
new bundle while isolated. International inputs continue aging from their
original source timestamps. The estimator must expose per-source freshness,
reduce confidence, widen the range when justified, or abstain; it must never
silently forward-fill missing international inputs or claim the same
confidence as a fully connected site.

After connectivity returns, both directions replay from their last contiguous
watermark. A source has exactly one origin authority (`webapp_ir` for approved
exchange/IME and USDT observations, `bot_fi` for Telegram observations, and
the Finland promotion authority for bundles), so conflict resolution never
chooses the newest transport timestamp. No model/data artifact changes
business writer authority or enters the product-sync change log.

This market-intelligence plane does not change the fixed business topology:
`bot_fi <-> webapp_fi <-> webapp_ir`. It adds no direct
`webapp_ir -> bot_fi` dependency and grants no new business writer authority.

## Included in phase 1

- fail-closed checksum, size, schema, status, and code-version verification;
- data-minimized Shadow bundle with no raw/training payload or machine path;
- deterministic price-to-commodity ranker;
- bounded local snapshot provider;
- deterministic offline range snapshot producer;
- low-date physical-melted and strictly-prior coin-anchor runtime overlays;
- atomic snapshot builder CLI with explicit input and output paths;
- fail-safe one-cycle Shadow orchestrator, health artifact and concurrency
  lock;
- data-minimized Telegram parsers and normalized SQLite writer;
- explicit, unscheduled Shadow Telegram CLI with optional Telethon dependency;
- feature flags disabled by default;
- Shadow observation only for intentionally implicit-commodity offers;
- low-cardinality metrics with no raw message, price, user ID, or personal data;
- focused regression tests.

## Excluded from phase 1

- any change to the default-Imam parser behavior;
- automatic or suggested commodity selection;
- WebApp or bot ambiguity UI;
- PostgreSQL market-intelligence schema or cross-site synchronization;
- live Telegram, USDT, ounce, IME, dollar, or melted-gold collectors and their
  schedulers;
- model training and promotion;
- raw exports, SQLite datasets, Telegram sessions, API credentials, or personal
  data;
- production or staging enablement.

Phase 2 now adds the disabled local PostgreSQL evaluation/review ledger,
durable project-event worker, Feature Snapshot/Regime v2, quality quarantine,
Hybrid/low-date/cash-tomorrow candidates, strict local Gemma parser second
opinion, and aggregate evaluation. These additions remain non-authoritative
and are not a production/staging activation. Deferred items 7–11
(operator UI, three-site data plane, retention, promotion, and legacy clean
bootstrap repair) are enumerated in
[`COIN_PRICE_INTELLIGENCE_SHADOW_V2.md`](COIN_PRICE_INTELLIGENCE_SHADOW_V2.md).

A later source-only slice versions the private JSON event listener and the
group/gold normalization pipeline while leaving activation and scheduling
unapproved. See
[`COIN_PRIVATE_EVENT_INGESTION.md`](COIN_PRIVATE_EVENT_INGESTION.md). This does
not change the phase-1 exclusion of public live market collectors or authorize
the deferred three-site data plane.

## Activation gates

1. Shadow metrics demonstrate precision separately for each commodity,
   settlement, sparse/cold-start window, and market regime.
2. Evaluation is live-consistent and excludes the target trade's source offer.
3. Promotion evidence uses chronological, dependence-aware holdouts with enough
   distinct days and non-Imam confirmed trades.
4. Stale, missing, mismatched, or ambiguous data always abstains.
5. Explicit offer attributes are schema-validated, extracted without
   fabricating missing values, and evaluated separately for `EXPLICIT`,
   `INFERRED`, and `UNSPECIFIED` cohorts.
6. The next user-facing phase requires explicit owner approval and mandatory
   user confirmation before persistence.

## Configuration

- `COIN_INTELLIGENCE_SHADOW_ENABLED=false` by default.
- `COIN_INTELLIGENCE_BUNDLE_PATH` optionally overrides the repository bundle.
- `COIN_INTELLIGENCE_SNAPSHOT_PATH` is required before Shadow observation can
  run.
- `COIN_INTELLIGENCE_SHADOW_PERSIST_ENABLED=false` keeps PostgreSQL writes
  disabled.
- `COIN_INTELLIGENCE_SHADOW_PROJECT_EVENTS_ENABLED=false` keeps post-commit
  project observation disabled.
- `COIN_INTELLIGENCE_SHADOW_NUMERIC_V2_ENABLED=false` keeps Hybrid-v2 disabled.
- Feature-v2, quality, durable-worker, low-date-v2, basis-v2 and Gemma each
  have an independent false-by-default gate.
- bounded timeout, in-flight, and sampling settings are listed in
  `config/coin-intelligence-shadow.env.example`.

The Telegram CLI reads credentials only from
`COIN_MARKET_TELEGRAM_API_ID`, `COIN_MARKET_TELEGRAM_API_HASH`, and
`COIN_MARKET_TELEGRAM_PHONE`. Database and session paths are required through
CLI arguments or the corresponding template variables, must be outside the
repository checkout, and must not overlap. The blank template is
`config/coin-market-telegram.env.example`. Telethon remains in
`requirements-market-intelligence.txt`, outside the default application
requirements.

Enabling Shadow does not authorize staging or production deployment.

## Training audit — 2026-08-01

The current runtime evidence was rebuilt after reconciling the rolling private
group staging window with the preserved active history. This audit changed
data artifacts and Shadow candidates only; it did not promote a numerical
candidate or enable a repository feature flag.

### Evaluation rules now enforced

- all point-in-time features are strictly prior to the target trade;
- the target offer/reply economic chain is purged from its own features;
- chronological fit/validation/test chain splits replace random row splits;
- hyperparameters are selected on validation only, while promotion evidence is
  reported on the untouched test split;
- confidence intervals use paired, chain-level bootstrap samples;
- results are sliced by commodity, settlement, trade form, regime, freshness,
  and sparse/cold-start condition where sample size permits;
- no candidate is promoted without enough independent test chains, distinct
  test days, non-Imam evidence, interval coverage, and a statistically positive
  improvement.

### Current evidence and decisions

The group-anchor snapshot contains 1,317 eligible offers and 91 independent
confirmed-trade chains. Its untouched test contains only 19 chains from one
day. The best simple prior-event anchor reached 0.150% MAPE versus 0.156% for
the latest-offer baseline; the paired confidence interval includes no material
improvement. The tuned weighted anchor was worse at 0.174% MAPE. It remains
Shadow because the sample and day counts are insufficient.

The relevance classifier uses 9,230 frozen reviewed examples plus 136 explicit
adjudications. On its chronological holdout, the selected auto-keep threshold
achieves 95.4% precision. No threshold demonstrates the required 98% safe
auto-reject target, so automatic rejection is disabled and uncertain messages
are retained for review. The trade-pair candidate similarly failed the
auto-confirm precision gate; it is restricted to reject-only second opinion,
where its measured negative predictive value is 98.8%.

The updated numerical candidate accepted 6,250 weighted observations. On a
shared confirmed-trade comparison it reduced point MAPE from about 0.997% to
0.390%, but its paired evidence and coverage are not uniformly adequate. In
particular, interval coverage for half and one-gram coins is sparse and poor.
The registry therefore rejected promotion. CatBoost challengers also remain
Shadow: the confirmed-only and chain-purged variants underperformed, while the
offer-augmented variant improved point MAPE by about 15% but achieved only
about 70% interval coverage. The four-fold walk-forward audit reports 0.595%
online-prior MAPE and 86.1% interval coverage; it is diagnostic evidence, not a
promotion result. IME history currently covers only one usable source day and
cannot support a promotion claim.

The Gold lifecycle slice currently covers 2026-07-30 through 2026-08-01 and
contains about 54,000 model events, including about 6,100 confirmed trades and
8,200 minute-feature rows. Cross-source minute matching validates the new
normal-paper feed against NaghdP and Abshdh: median differences are 0 bps and
median absolute differences are approximately 2.5 and 1.9 bps respectively.
Reverse and swim quotes retain their expected directional basis and therefore
remain separate features. Physical-today quotes align closely with Abshdh
(about 3.1 bps median absolute difference), while physical-tomorrow has much
lower overlap and a roughly 46 bps basis; the latter must not be merged as if
it were the same market. This short window supports feature validation, not a
supervised promotion decision.

Gemma remains a strict parser second opinion. It is not a numerical pricing
authority and cannot replace deterministic parsing, causal price validation,
or abstention. Fine-tuning is deferred until a larger, balanced, human-reviewed
corpus exists; prompt-only benchmark results are versioned separately from
production decisions.

The 2026-08-01 CPU audit also verified the serving contract. An unconstrained
legacy ten-case prompt achieved only 60% exact-row accuracy after reasoning was
correctly disabled (field accuracies 80–100%). The repository adapter, which
uses the exact JSON Schema and independent validation, returned the expected
result on all four contract smoke cases: three explicit commodities and one
unnamed offer that correctly abstained while retaining the explicit numeric
fields. Per-message latency was 29–38 seconds on this CPU, confirming that the
model is suitable only for sampled background comparison at present.

### Retraining cadence

Live ingestion and deterministic reconciliation run continuously. Relevance
scoring is score-only in the live loop; model retraining runs separately from
frozen/reviewed labels. Training-snapshot and evaluation jobs fingerprint their
inputs and produce no new artifacts when data has not changed. Trade-pair and
numerical challengers train on a slower cadence and always remain candidates
until all activation gates above pass. Previous and daily known-good data
backups permit rollback without accumulating a backup for every incoming
message.

## Residual-learning research path — 2026-08-01

The next numerical layer is intentionally residual-first: the deterministic
anchor/intrinsic/basis model remains the baseline, while a candidate learns
only the error observed later on reviewed confirmed trades. The online Bayesian
candidate is strictly prior, time-decayed, shrunk toward zero, bounded, and
cannot narrow the primary range. It has no model-write or promotion path.

CatBoost and PySR are offline challengers built from the same minimized,
reviewed-only export. CatBoost predicts residuals and calibrates its interval on
a separate chronological window. PySR can propose interpretable equations but
is research-only. No weekly or nightly process may alter active coefficients;
it may only create a versioned candidate report. Promotion remains an explicit
per-market decision after untouched walk-forward evidence, adequate coverage,
and an owner review.
