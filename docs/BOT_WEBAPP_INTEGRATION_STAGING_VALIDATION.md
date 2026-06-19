# Bot/WebApp Integration Staging Validation

This document is the owner-led staging checklist for Step 11 of
`docs/BOT_WEBAPP_INTEGRATION_IMPLEMENTATION_CONTRACT.md`.

## Scope

- Use only `candidate/bot-webapp-integration` unless the owner explicitly approves another branch.
- Use staging peers and synthetic staging data only.
- Do not connect Iran staging to Telegram.
- Do not connect foreign staging to WebApp/frontend routes.
- Do not use production peers, production Telegram channels, or production data.
- Do not merge or deploy to production from this validation.

## Automated Gate

Run the matrix gate from the repository root before any manual scenario pass:

```bash
python3 scripts/report_bot_webapp_integration_matrix.py --check --json
python3 -m unittest tests.test_bot_webapp_integration_matrix tests.test_scripts_surface_smoke
```

The gate must report:

- `matrix.passed=true`
- `matrix.manual_signoff_required=true`
- no `failures`
- no `missing_coverage_refs`

## Evidence To Capture

- Current branch and commit SHA.
- Staging deploy artifact or image tag.
- Automated matrix command output.
- Iran and foreign sync-health snapshots before and after the run.
- DB evidence for each synthetic offer: `offer_public_id`, `home_server`, `status`,
  `remaining_quantity`, `expire_reason`, `expire_source_surface`, `expire_source_server`,
  `expired_by_user_id`, `expired_by_actor_user_id`.
- Offer request ledger rows for success, rejected, duplicate, stale/conflict, lot unavailable, and
  after-expiry paths.
- Telegram channel post state for active, fully traded, partially traded, and expired offers.
- WebApp list/detail/realtime visibility state for created, updated, traded, and expired offers.
- Session/surface behavior for a user active on both WebApp and Telegram bot.

## Scenario Checklist

Use `scripts/report_bot_webapp_integration_matrix.py --markdown` as the authoritative scenario list.
Each `S11-*` scenario needs a pass/fail note and linked evidence.

Minimum manual batches:

- Bot creates a foreign-home offer; Iran WebApp sees it.
- WebApp creates an Iran-home offer; foreign Telegram channel sees it.
- Public offer link opens on Iran WebApp from both surfaces.
- Owner expires an offer from WebApp and from bot; peer surface converges.
- Full trade, partial trade, and expiry update Telegram terminal text and remove buttons.
- WebApp and bot request/trade against both Iran-home and foreign-home offers.
- Request ledger captures accepted, rejected, duplicate, stale/conflict, lot-unavailable, and
  after-expiry outcomes.
- Same user is active on both surfaces; offer home follows source platform, not user home.
- Short outage replay converges to the latest terminal state.
- Medium/long outage recovery keeps active publication gated until full catch-up, then expires
  pre-recovery active local-only offers instead of publishing them active to the peer.
- Cutover rehearsal confirms zero active offers, clean sync-health, clean backlog, and fresh shared
  state before any switch decision.
- Rollback/fail-closed rehearsal preserves data without destructive cleanup.

## Stop Conditions

Stop immediately and do not continue the validation if any of these happen:

- The branch is not `candidate/bot-webapp-integration`.
- Any command targets production peers or production data.
- Iran attempts to call Telegram.
- Foreign serves WebApp/frontend routes.
- Sync-health is dirty before a scenario that requires clean catch-up.
- A rejected remote-home mutation mutates local state.
- A medium/long outage recovery publishes pre-recovery active local-only offers as active to the
  other server.
- Unknown/forbidden/sensitive sync input is marked successful or synced.
- Telegram terminal posts keep interactive buttons after completed/expired states.
- WebApp public views expose internal request/ledger failure context.

## Sign-Off

Step 11 is not production-ready until the owner confirms that:

- the automated matrix passed,
- all manual staging scenarios passed,
- logs, sync-health, DB state, Telegram publication state, WebApp realtime state, and session
  surface behavior were reviewed,
- and no stop condition was observed.
