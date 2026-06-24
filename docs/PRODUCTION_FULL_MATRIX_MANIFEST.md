# Production Full Matrix Manifest Contract

Date: 2026-06-24

Purpose: define the JSON manifest that a production full-matrix runner must
consume before creating any synthetic production data. The manifest is generated
by `scripts/build_production_full_matrix_manifest.py` and is intentionally
side-effect free.

## Command

Generate and validate the manifest:

```bash
make production-full-matrix-manifest ARGS="--prefix PFM_YYYYMMDD_HHMMSS_ --check --output /tmp/production-full-matrix-manifest.json"
```

The command must not connect to production, mutate databases, call Telegram, or
write anything except the optional output artifact.

When `--output` is provided, stdout prints only a compact summary by default.
Use `--print-full` only when the full JSON must be streamed to stdout.

## Schema

Top-level fields:

- `schema_version`: currently `production_full_matrix_manifest_v1`.
- `generated_at`: UTC timestamp.
- `prefix`: exact synthetic run prefix. It must pass the same guard as the
  production cleanup plan.
- `environment`: `production`.
- `mutates_production`: always `false` for the manifest generator.
- `catalog_docs`: source document paths.
- `runner_contract`: requirements the eventual runner must honor.
- `axes`: canonical surfaces, actor pairs, outage classes, offer types, offer
  shapes, negative cases, notification groups, read paths, and synced tables.
- `assertions`: reusable invariant definitions keyed by assertion id.
- `summary`: section counts used by tests and operators.
- `sections`: expanded scenario sections.
- `production_gate`: confirms this is manifest-only evidence.

## Sections

### `market_behavior`

Mirrors the existing comprehensive market/load catalog. It includes offer
creation, concurrent trade, non-concurrent trade, manual expiry, time expiry,
post-terminal rejection, active views, public detail, and market history.

Expected count: `228`.

### `delivery_contract`

Mirrors the notification delivery matrix:

`17 actor pairs * 4 surface pairs * 3 outage classes = 204`

Expected count: `204`.

### `targeted_trade_delivery_join`

Joins actor pair, surface pair, and outage with current business policy. This
section keeps both supported and policy-unsupported paths visible.

Expected count:

- total: `204`
- policy-supported: `108`
- policy-unsupported: `96`

Unsupported paths are required negative evidence, especially:

- tier2 customer as offer creator;
- tier2 customer as Telegram requester.

### `production_base_trade_shape`

This is the main expanded production trade-space:

`4 surface pairs * 17 actor pairs * 3 outage classes * 2 offer types * 3 offer shapes = 1224`

Expected count:

- total: `1224`
- policy-supported: `648`
- policy-unsupported: `576`

Each record states:

- surface quadrant: Iran to Iran, Iran to foreign, foreign to Iran, or foreign
  to foreign;
- offer home server;
- request source server;
- outage class;
- actor-pair family;
- offer type;
- offer shape;
- expected outcome: `trade_completed` or `policy_rejected`;
- assertion ids the runner must verify.

### `production_stress_overlay`

Applies high-risk execution overlays only to policy-supported base scenarios:

- `hot_wholesale_concurrent`
- `hot_retail_same_lot_concurrent`
- `hot_retail_mixed_lot_concurrent`
- `duplicate_idempotency_replay`
- `manual_expire_trade_race`
- `time_expire_trade_race`
- `read_during_write`

Expected count: `3672`.

Each overlay references a `production_base_trade_shape` scenario through
`base_manifest_id`. The runner must never apply stress overlays to a
policy-unsupported base scenario.

### `negative_business_guard`

Explicit negative cases that must be tested as first-class scenarios instead of
being treated as accidental errors. Examples include own-offer request, invalid
amount, market closed, inactive users, wrong authoritative server, bad internal
signature, stale Telegram button, and cleanup scope violation.

## Runner Requirements

A runner that consumes this manifest must:

- enable production isolation before creating synthetic data;
- use one exact prefix for all created rows;
- coordinate Iran and foreign execution;
- use real cross-server sync and authoritative routing;
- use aiogram Dispatcher or fake Bot API transport for high-volume Telegram
  paths;
- reserve a small manual real-Telegram E2E slice for channel/Bot API evidence;
- simulate short and medium outages reversibly;
- collect per-scenario artifacts;
- assert all `assertion_refs`;
- stop on safety-contract violations;
- run pre-run dry-run cleanup, post-run dry-run cleanup, hard-delete cleanup,
  and post-delete zero-count verification on both servers.

## Completion Criteria

The production full matrix is incomplete unless the final artifact shows:

- every manifest section count matches `summary`;
- every `required` negative guard was executed or explicitly owner-waived;
- all four surface quadrants executed;
- all actor-pair families were represented;
- all outage classes were represented;
- all offer types and shapes were represented;
- every policy-unsupported path was rejected without partial mutation;
- every policy-supported path met its assertion refs;
- cleanup verification returned zero prefixed rows on both servers.
