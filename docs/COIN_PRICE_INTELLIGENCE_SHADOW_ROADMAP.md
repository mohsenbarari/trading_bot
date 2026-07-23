# Coin Price Intelligence — Shadow Introduction

## Decision

Branch: `candidate/coin-price-intelligence`

Base: `main` at `5af8edb6de08886c72f3a47516ab5e03b15a9caa`

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
- Live collectors are not yet migrated.

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

Collector execution is idempotent and restart-safe. The ounce source is
compacted to its newest quote per minute; melted-gold and dollar order flow is
not averaged away. Adding the source to the repository does not authorize
enabling it on any server.

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

## Activation gates

1. Shadow metrics demonstrate precision separately for each commodity,
   settlement, sparse/cold-start window, and market regime.
2. Evaluation is live-consistent and excludes the target trade's source offer.
3. Promotion evidence uses chronological, dependence-aware holdouts with enough
   distinct days and non-Imam confirmed trades.
4. Stale, missing, mismatched, or ambiguous data always abstains.
5. The next user-facing phase requires explicit owner approval and mandatory
   user confirmation before persistence.

## Configuration

- `COIN_INTELLIGENCE_SHADOW_ENABLED=false` by default.
- `COIN_INTELLIGENCE_BUNDLE_PATH` optionally overrides the repository bundle.
- `COIN_INTELLIGENCE_SNAPSHOT_PATH` is required before Shadow observation can
  run.

The Telegram CLI reads credentials only from
`COIN_MARKET_TELEGRAM_API_ID`, `COIN_MARKET_TELEGRAM_API_HASH`, and
`COIN_MARKET_TELEGRAM_PHONE`. Database and session paths are required through
CLI arguments or the corresponding template variables, must be outside the
repository checkout, and must not overlap. The blank template is
`config/coin-market-telegram.env.example`. Telethon remains in
`requirements-market-intelligence.txt`, outside the default application
requirements.

Enabling Shadow does not authorize staging or production deployment.
