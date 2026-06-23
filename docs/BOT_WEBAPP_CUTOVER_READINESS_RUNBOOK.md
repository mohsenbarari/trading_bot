# Bot/WebApp Cutover Readiness Runbook

This runbook belongs to Step 12 of
`docs/BOT_WEBAPP_INTEGRATION_IMPLEMENTATION_CONTRACT.md`.

Step 12 does not deploy to production and does not read production data. It prepares a readiness
gate for owner review after staging validation.

## Command

Run the candidate full Bot/WebApp matrix without pressure. This keeps all 228 logical market
scenarios from the previous full matrix and also generates the trade notification delivery matrix
for owner/customer/accountant/channel/outage coverage. The default profile lowers user count,
request rate, attempts, and write concurrency so it is a correctness retest, not a capacity proof:

```bash
python3 scripts/run_bot_webapp_candidate_full_matrix.py
```

The runner calls `scripts/run_staging_comprehensive_load_matrix.sh` without `--max-scenarios`,
`--family`, or `--scenario`; it must run every logical market scenario for release retest. It then
generates `trade-notification-delivery-matrix.json`, `trade-delivery-stage11-matrix.json`, and
`candidate-full-matrix-summary.json` in the artifact directory. Because this is intentionally not a
pressure test, do not use its low RPS as the Step 11B capacity proof.

For a command-only review without touching staging:

```bash
python3 scripts/run_bot_webapp_candidate_full_matrix.py --dry-run
```

Generate a passing snapshot template:

```bash
python3 scripts/report_bot_webapp_cutover_readiness.py --template > tmp/bot-webapp-cutover-snapshot.json
```

After filling the template with staging evidence, run:

```bash
python3 scripts/report_bot_webapp_cutover_readiness.py \
  --input tmp/bot-webapp-cutover-snapshot.json \
  --check \
  --json
```

For reviewable output:

```bash
python3 scripts/report_bot_webapp_cutover_readiness.py \
  --input tmp/bot-webapp-cutover-snapshot.json \
  --check \
  --markdown \
  --report-out tmp/bot-webapp-cutover-readiness.md
```

## Required Evidence

- `metadata.environment` must be `staging`, `synthetic`, `dry_run`, or `local`.
- `metadata.production_data_used=false` and `metadata.production_peer_used=false`.
- `metadata.production_deploy_command_run=false`.
- Both `roles.iran` and `roles.foreign` must be present and must declare the matching
  `server_mode`.
- Contract stages and required commits must be complete and pushed.
- `active_offer_count=0` on both servers.
- Backfill report must include total offers, public identifiers before and after migration, missing
  public identifiers, linked trades, old channel message bindings, and `legacy_unknown` rows.
- `offers_missing_public_id=0`, and `offers_with_public_id_after` must equal `total_offers`.
- Offer request ledger migration must be non-destructive and preserve trade history.
- Historical request ledger backfill must not run unless explicitly approved by the owner.
- Missing historical request attempts must remain `unknown_legacy`; they must not be invented.
- Sync backlog counters and partial failure counters must be zero.
- Publication pending, failed, lagged, and partial counters must be zero.
- Iran runtime guard must prove Telegram is blocked.
- Foreign runtime guard must prove WebApp and chat user surfaces are blocked.
- Registry coverage and sensitive-field policy must be complete.
- Migrations must be additive, non-destructive, forward-compatible, and compatible with code-only
  rollback.
- Rollback must disable new behavior or fail closed without deleting synced or migrated data.
- Observability, logs, sync-health review, alerts, backups, snapshots, and restore smoke evidence
  must be ready.
- Observability must show receipt backlog, oldest pending receipt, terminal receipt counts,
  Telegram failures, sync conflicts, and duplicate guards.
- Step 11 automated matrix and owner-led manual staging validation must be complete before
  production consideration.
- The Step 11B capacity report built by `scripts/report_bot_webapp_capacity.py` must be present in
  `global.staging_validation.capacity_report`, reviewed, and still have
  `production_gate.status=blocked_until_owner_staging_validation`.
- `capacity_report.correctness_failure_count` must be zero. Capacity warnings do not get hidden:
  if `capacity_report.capacity_warning_count` is non-zero, `capacity_warnings_reviewed=true` is
  required and the readiness report emits a warning.
- The Stage 11 trade delivery report built from `scripts/report_trade_delivery_staging_validation.py`
  and `scripts/report_trade_notification_delivery_matrix.py` must be present in
  `global.staging_validation.trade_delivery_report`, reviewed, valid, and still have
  `production_gate.status=blocked_until_owner_staging_validation`.
- The notification delivery matrix covers 17 actor pairs, 4 offer/request surface pairs, and 3
  connectivity classes:
  stable, short outage under two minutes, and medium outage around one hour.

## Stop Conditions

Abort the readiness review if any of these appear:

- any production data or production peer was used;
- any production deploy command was run;
- any active offer exists on either server;
- any offer lacks a public identifier after backfill;
- any historical request attempt was fabricated;
- sync backlog or partial publication state remains unresolved;
- Iran can call Telegram;
- foreign can serve WebApp or chat user surfaces;
- rollback requires deleting synced or migrated data;
- the Step 11B capacity report is missing, unreviewed, no longer production-gated, or contains any
  correctness failure;
- Step 11B capacity warnings exist but have not been explicitly reviewed;
- the Stage 11 trade delivery report is missing, unreviewed, invalid, no longer production-gated, or
  has unreviewed warnings;
- owner staging sign-off is missing.

## Output Meaning

- `status=passed` means the supplied staging/synthetic snapshot is ready for owner production
  review. It is not production approval.
- `status=failed` means at least one cutover blocker exists.
- `warnings` are accepted-risk visibility items, such as old Telegram channel bindings or
  `legacy_unknown` rows, not automatic blockers.
