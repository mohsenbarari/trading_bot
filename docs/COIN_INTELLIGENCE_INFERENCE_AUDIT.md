# Coin Intelligence — Inference Decision Audit

`coin_intelligence_inference_audits` holds one idempotent, append-only record
for a proposed product inference. Its legal fields are deliberately limited to
the opaque decision key, source surface, submitted price, settlement, result,
candidate count, the auto-selected canonical commodity (if any), versions, and
the Snapshot receipt/timestamp.

It does **not** contain raw offer text, free-text notes, Telegram/channel/chat
or message identifiers, usernames, user IDs, mobile numbers, or an offer/trade
reference. It must never become a substitute for the normal Offer audit trail.

The database migration adds an UPDATE/DELETE-blocking PostgreSQL trigger. A
same-key exact replay returns the existing row; a same-key divergent result
fails with an idempotency conflict. `CONFIRM` and `ABSTAIN` have no selected
commodity. This is intentional: P6 will record/validate explicit user choice
at submit time rather than silently treating a candidate as a chosen product.

No route, bot handler, worker, feature flag, or model runtime is activated by
this module.
