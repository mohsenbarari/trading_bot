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
- The same small, immutable bundle is deployed locally beside the Telegram bot
  in Finland and beside the WebApp API in Iran.
- Inference therefore has no synchronous cross-server dependency.
- Live market inputs are supplied through a local, atomically replaced,
  versioned JSON snapshot. No absolute `/tmp` path is embedded in code or the
  bundle.
- The numerical range model is carried for version/provenance validation, but
  range generation still happens in the external snapshot producer during this
  first slice. The snapshot producer and live collectors are not yet migrated.

## Commodity identity

Bundle catalog integers are model-catalog identifiers, not PostgreSQL primary
keys. A later activation slice must map the inferred canonical commodity name
through the local `commodities` and `commodity_aliases` tables and use that
server's actual database ID. Shadow mode records no replacement ID.

## Included in phase 1

- fail-closed checksum, size, schema, status, and code-version verification;
- data-minimized Shadow bundle with no raw/training payload or machine path;
- deterministic price-to-commodity ranker;
- bounded local snapshot provider;
- feature flags disabled by default;
- Shadow observation only for intentionally implicit-commodity offers;
- low-cardinality metrics with no raw message, price, user ID, or personal data;
- focused regression tests.

## Excluded from phase 1

- any change to the default-Imam parser behavior;
- automatic or suggested commodity selection;
- WebApp or bot ambiguity UI;
- PostgreSQL market-intelligence schema or cross-site synchronization;
- Telegram, USDT, ounce, IME, dollar, or melted-gold collectors;
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

Enabling Shadow does not authorize staging or production deployment.
