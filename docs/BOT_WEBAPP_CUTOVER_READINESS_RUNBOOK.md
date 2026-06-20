# Bot/WebApp Cutover Readiness Runbook

This runbook belongs to Step 12 of
`docs/BOT_WEBAPP_INTEGRATION_IMPLEMENTATION_CONTRACT.md`.

Step 12 does not deploy to production and does not read production data. It prepares a readiness
gate for owner review after staging validation.

## Command

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
- Both `roles.iran` and `roles.foreign` must be present and must declare the matching
  `server_mode`.
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
- Rollback must disable new behavior or fail closed without deleting synced or migrated data.
- Observability, logs, sync-health review, alerts, backups, snapshots, and restore smoke evidence
  must be ready.
- Step 11 automated matrix and owner-led manual staging validation must be complete before
  production consideration.

## Stop Conditions

Abort the readiness review if any of these appear:

- any production data or production peer was used;
- any active offer exists on either server;
- any offer lacks a public identifier after backfill;
- any historical request attempt was fabricated;
- sync backlog or partial publication state remains unresolved;
- Iran can call Telegram;
- foreign can serve WebApp or chat user surfaces;
- rollback requires deleting synced or migrated data;
- owner staging sign-off is missing.

## Output Meaning

- `status=passed` means the supplied staging/synthetic snapshot is ready for owner production
  review. It is not production approval.
- `status=failed` means at least one cutover blocker exists.
- `warnings` are accepted-risk visibility items, such as old Telegram channel bindings or
  `legacy_unknown` rows, not automatic blockers.
