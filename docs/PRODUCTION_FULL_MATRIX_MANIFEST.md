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

Build the runner plan from a generated manifest:

```bash
make production-full-matrix-run ARGS="--manifest /tmp/production-full-matrix-manifest.json --output /tmp/production-full-matrix-run-plan.json"
```

The current runner is fail-closed and manifest-driven. It can select sections,
filter scenarios, shard large runs, and write a run plan. It does not execute
production writes yet. Passing `--execute` currently returns a blocked status
until production drivers are implemented explicitly.

Build the live preflight plan without running commands:

```bash
make production-full-matrix-run ARGS="--prefix PFM_YYYYMMDD_HHMMSS_ --mode preflight --output /tmp/production-full-matrix-preflight-plan.json"
```

Run the live non-mutating preflight only after reviewing the plan:

```bash
PRODUCTION_FULL_MATRIX_PREFLIGHT_CONFIRM=run-production-preflight \
make production-full-matrix-run ARGS="--prefix PFM_YYYYMMDD_HHMMSS_ --mode preflight --execute --output /tmp/production-full-matrix-preflight-result.json"
```

The preflight checks git state, manifest generation, local foreign compose
status, Iran compose status, Iran public config, isolation status, and cleanup
dry-run on both servers. It must not create users, offers, trades,
notifications, receipts, or Telegram messages.

Build the guarded two-server execution command plan without running it:

```bash
make production-full-matrix-run ARGS="--prefix PFM_YYYYMMDD_HHMMSS_ --mode execution-plan --output /tmp/production-full-matrix-execution-plan.json"
```

The execution-plan mode is still side-effect free. It expands selected
`manifest_id` rows into reviewed commands where a production driver exists, and
records `driver_gaps` for every selected row that does not yet have an
implemented production driver. The current implemented command plan covers:

- `production_base_trade_shape` user-to-user stable scenarios;
- `production_stress_overlay` user-to-user stable hot-offer concurrent
  families;
- all four WebApp/Telegram surface quadrants inside that scope;
- both offer types and all current offer shapes;
- four production negative-guard probes on Iran/WebApp:
  `own_offer_request`, `invalid_request_amount`, `retail_lot_unavailable`, and
  `already_completed_offer`.

With the current manifest count of `5555`, selecting the whole manifest yields
`68` command-plannable scenarios with these drivers:

- `24` base user-to-user stable trade-shape scenarios;
- `40` user-to-user stable hot-offer stress overlay scenarios.
- `4` negative business-guard scenarios with explicit no-partial-mutation
  assertions.

It intentionally does not yet implement production execution drivers for
customer/accountant actor pairs, short/medium outage simulation, market
behavior reads/expiry, targeted delivery join, or the remaining negative
business guards. Those must remain visible as `driver_gaps` and cannot be
treated as passed.
The execution-plan output also includes `driver_gap_summary.by_driver_gap_bucket`
and `driver_gap_roadmap`, which group the raw gap reasons into implementation
buckets sorted from easier to harder.

For a production release gate, build the execution plan with full-driver
coverage required:

```bash
make production-full-matrix-run ARGS="--prefix PFM_YYYYMMDD_HHMMSS_ --mode execution-plan --require-full-driver-coverage --output /tmp/production-full-matrix-execution-plan.json"
```

This remains side-effect free. It exits with code `2` and status
`blocked_driver_gaps` if any selected `manifest_id` still lacks an implemented
production driver. Use this gate before any real production full-matrix run so
an incomplete driver set cannot be mistaken for a complete pass. For intentionally
small rehearsals, apply filters first; the gate should pass only when every
selected scenario is command-plannable.

Current full-manifest gap buckets are expected to be:

- `negative_guard_driver`: `595`
- `specialized_user_stress_driver`: `96`
- `market_behavior_driver`: `228`
- `delivery_contract_driver`: `204`
- `targeted_join_driver`: `204`
- `outage_orchestration_driver`: `320`
- `customer_accountant_actor_driver`: `3840`

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

Current runner status:

- `scripts/run_production_full_matrix.py` consumes the manifest and emits a
  per-`manifest_id` run plan.
- It supports section filters, scenario-id filters, policy filters, surface,
  outage, actor, offer-type, shape filters, and deterministic sharding.
- It can execute a live non-mutating preflight only with
  `PRODUCTION_FULL_MATRIX_PREFLIGHT_CONFIRM=run-production-preflight`.
- It can build a guarded production execution command plan with
  `--mode execution-plan`.
- It can enforce full command-driver coverage for the selected scope with
  `--require-full-driver-coverage`; this blocks with `blocked_driver_gaps`
  until every selected scenario has an implemented production driver.
- It emits a machine-readable driver-gap roadmap so the next driver work can
  be prioritized by bucket instead of scanning thousands of raw `manifest_id`
  rows manually.
- It intentionally fails closed for automatic real execution until the
  per-section production drivers are implemented and reviewed.

Role-worker execution plan safety:

- `run-role-plan --patch-external-side-effects` disables Telegram, realtime,
  Web Push, channel edit, and market schedule side effects while preserving
  real cross-server forwarding.
- `--patch-boundaries` is only for local/staging smoke runs and must not be used
  for real two-server production execution because it patches cross-server
  forwarding to local helpers.
- Production role workers require
  `PRODUCTION_FULL_MATRIX_CONFIRM=execute-production-full-matrix`,
  `--allow-production-execution`, `TRADING_BOT_SERVICE=load_runner`, the
  correct `SERVER_MODE`, and an empty `BOT_TOKEN`.

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
