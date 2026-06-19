# Bot/WebApp Integration Step 0C Semantic Drift Report

Date: 2026-06-19

Branch: `candidate/bot-webapp-integration`

Merge baseline:

- Merged source: `origin/main` at `63c643ced0c61a290feeb0598fa8e71ebe933cc0`
- Candidate merge commit: `1710c945`
- Post-merge divergence from `origin/main`: `0 41`

## Result

Step 0B refreshed the candidate branch from current `main` without textual conflicts. Step 0C found
semantic drift that matters for implementation, but the drift is already covered by the updated
roadmap and implementation contract. No new policy decision is required before Step 1.

Do not treat this report as production approval. It only records the post-merge baseline for
continued staging/candidate work.

## Current Main Behavior Accepted As Baseline

- Market history is now centered on `/api/offers/market-history`. The endpoint includes completed
  offers and expired offers that either expired by `time_limit` or have traded quantity.
- The legacy `/api/offers/expired` endpoint still exists for time-limit expired offers. Step 1
  should not remove or redesign it unless a separate product decision requires that.
- Telegram terminal offer state now has a dedicated service. It renders:
  fully traded as `🤝 ✅`, partially traded as `🤝 {quantity} تا ✅`, and expired as `❌`.
- Telegram terminal state edits the channel text and removes inline buttons. The service is
  foreign-only and treats Telegram "message is not modified" as idempotent success.
- Production deploy/release checks now reference the market-history asset/endpoint. Bot/WebApp
  integration work must preserve these checks unless tests prove they conflict with this contract.

## Drift Findings Covered By Existing Contract Steps

1. Offer public identity is still missing.

   `models/offer.py` still has local integer `id`, `home_server`, `expire_reason`, `expired_at`,
   `channel_message_id`, and `idempotency_key`, but no `offer_public_id` or equivalent stable
   public identity. This confirms Step 5A and Step 8A are still required.

2. Trades still reference offers by local integer ID.

   `models/trade.py` still stores `offer_id` as a local FK to `offers.id`. This is acceptable as a
   local database relationship, but cross-server commands must move to `offer_public_id` as
   documented in Step 5C, Step 5D, and Step 8A.

3. Trade history still carries raw mobile snapshots.

   `models/trade.py` still has `offer_user_mobile` and `responder_user_mobile`. Step 5C-0 correctly
   says the new `offer_requests` ledger must not silently add raw mobile fields, and Step 9C must
   classify the existing trade mobile snapshot policy before broad sensitive-data replication.

4. Telegram callback payloads still use integer offer IDs.

   `bot/callbacks.py` still defines `ChannelTradeCallback` as `channel_trade:{offer_id}:{amount}`.
   Step 8B remains necessary for versioned callback compatibility and canonical public identity
   resolution.

5. Sync receive remains ID-upsert based.

   `api/routers/sync.py` still upserts most tables by integer `id`. It also strips
   `channel_message_id` from synced offers because Telegram publication is local to foreign. This is
   compatible with the current baseline, but Step 8A must migrate cross-server identity to public
   identifiers and Step 7A must keep publication state separate from business truth.

6. Sync unknown-table handling is not yet fail-closed.

   The receive path logs `sync.unknown_table` and continues. Step 2B still must change this so
   unknown, unregistered, or policy-forbidden tables visibly fail and cannot be marked delivered as
   success.

7. Telegram publication idempotency is improved but not yet the final publication-state model.

   Synced offer publication uses `channel_message_id IS NULL` with row locking, and terminal state
   replay is idempotent through the Telegram service. This is a useful baseline, but Step 7A and
   Step 7B still need a formal publication state/dedupe model.

8. Expiry still has multiple mutation paths and bulk writes.

   `core/offer_expiry.py` expires stale offers by current server and writes `expire_reason =
   "time_limit"`/`expired_at`. Market close currently uses `market_closed`. This validates the
   Step 5B shared expire command and Step 5C-0 legacy `expire_reason` mapping.

9. Request ledger does not exist yet.

   There is still no durable `offer_requests` model/table. Request attempts that fail before trade
   creation are not yet captured as product history. Step 5C and Step 5D remain the required path.

## Contract Adjustments From Metadata Review

The metadata review changes added before Step 0A are still correct after merging `main`:

- Policy doc now records stable offer public identity, request ledger, expiry/source metadata, and
  safe-by-default public link behavior as non-negotiable policy.
- Required Sync Registry now has placeholders for `offers.public_id`/link fields, `offer_requests`,
  and `offer_publication_states` or equivalent.
- Contract now includes Step 5C-0 for minimum field policy before ledger schema work.
- Contract now requires public/internal failure reason separation, request-ledger state machine,
  idempotency uniqueness, retention/archive planning, and historical backfill without fabricated
  request attempts.

## Recommendation

Step 0 can be considered complete after verification commands pass and this report is committed and
pushed. The next implementation step remains Step 1: source surface and `Offer.home_server`
contract tests/behavior.
