# Decisions

Entries are newest first.

- 2026-08-17 | Staging Telegram delivery execution is Queue-v1 on the bot host; API/WebApp stay producer-only. Production remains Legacy and needs a separate owner authorization.
- 2026-08-16 | Overtime follows offer home: foreign/bot-home uses its active Telegram runtime, starts the clock only after a message id, and queues channel refresh; Iran/WebApp-home presents in WebApp. Requester surface is audit-only.
- 2026-08-16 | Customer identity is server-scoped to self, relation owner, and same-owner accountants; foreign lookup fails closed. Privileged user-management stays a separate control-plane surface.
- 2026-08-15 | Staging sync resets exact resources on both hosts, validates all 23 shared tables/both origins, resets before formal scenarios, and mounts probes with `APP_ENV_FILE=/dev/null`.
- 2026-08-13 | Coin estimator/sidecar run from canonical `main`; mutable state lives under the production-data estimator-live path; conversations stay read-only and dashboard projection is privacy-safe.
- 2026-08-12 | Relationship shadow research reads canonical Market Store hot/archive at `available_at_utc`; labels require eligible confirmed coin trades and stay external.
- 2026-08-12 | Fresh Iran offers get one bounded signed-sync attempt after commit; only full ACK marks delivery, while age/backoff fences and the regular worker recover.
- 2026-08-12 | Canonical Alembic restores deployed merge `f9b` before `f9c`; `fb1` validates complete coin schema, repairs only all-absent `fa0`, and rejects partial state.
- 2026-08-12 | Coin inference normalizes canonical-Toman Market Store data once; confirmed trades outrank offers, and atomic rebuilds absorb freshness/backfills/corrections.
- 2026-08-12 | The 500-offer matrix uses 60/40 Bot/WebApp origin, random 0.8–4-second ingress, fake private transport, bounded lifecycle work, and redacted audits.
- 2026-08-11 | Queue-v1 retries only pre-dispatch serialization/deadlock aborts inside a short fenced Redis cadence lease; other DB failures fail closed.
- 2026-08-11 | Publication scans exclude offers with an existing non-final control/publish job so central ingress is not delayed by repeated deduplication.
- 2026-08-11 | Queue-v1 interaction probes model authenticated private chat and positive message identity.
- 2026-08-11 | Multi-publisher B2B needs five capable identities; the feeder fixes one healthy lane at first publish. Callbacks stay on the receiver; Telegram edit ownership cannot transfer.
- 2026-08-11 | Telegram resume clears shared destination cadence and only preflight-approved lane blocks.
- 2026-08-11 | Telegram delivery uses central ingress, durable B2B command/receipt, and a lane fixed at first publish.
- 2026-08-11 | MemoryCustodian governs reviewed Markdown through a thin `AGENTS.md` bootstrap; local `.env*.local` files stay untracked.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally.
- 2026-08-10 | FastAPI API, Telegram bot, and Vue PWA are first-class surfaces.
