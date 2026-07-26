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

## Three-site placement and data flows

The project has three physical sites but only two business authorities. Market
intelligence must respect those existing boundaries:

| Site | Normal application role | Market-intelligence role |
| --- | --- | --- |
| `bot_fi` | permanent Telegram/foreign writer | live Telegram collection and local bot inference |
| `webapp_fi` | normal WebApp writer and Finland relay hub | local WebApp inference and verified Finland market-data projection |
| `webapp_ir` | fenced/dark WebApp standby | verified standby artifacts plus a future isolated Iran-only IME/USDT collector |

The current production dark-standby path on `webapp_ir` is DB-only and does
not yet authorize that collector. The future Iran collector must be a separate,
non-authoritative process with no application writer lease, no business
database write credential, no public API, and no ability to start WebApp jobs.
Adding that process requires its own deployment and safety review.

Market data and model artifacts form a separate distribution plane. They must
not be inserted into the product-sync change log or the WebApp DR business
event stream:

1. Telegram observations originate on `bot_fi`, are projected directly to
   `webapp_fi` inside Finland, and are periodically delivered to `webapp_ir` as
   verified standby deltas or compact feature snapshots.
2. IME and the selected Iran-accessible USDT feed originate in the isolated
   collector on `webapp_ir`. Immutable micro-batches are spooled locally,
   encrypted, published through private versioned Object Storage, verified by
   `webapp_fi`, and then made available to `bot_fi` through the Finland-local
   data path.
3. Promoted model bundles originate from one Finland training/promotion
   authority. The exact immutable version is delivered to `bot_fi` and
   `webapp_fi`, then copied to `webapp_ir` as a standby artifact.

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
uses the last verified Telegram/model snapshots plus any locally available
Iran feeds. It must expose per-source freshness, reduce confidence, widen the
range when justified, or abstain; it must never silently forward-fill missing
international inputs.

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

Phase 2 now adds a disabled-by-default local PostgreSQL evaluation ledger,
post-commit offer/trade observation, a gated Hybrid-v2 research candidate, and
an aggregate evaluation command. These additions remain non-authoritative and
are not a production/staging activation.

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
