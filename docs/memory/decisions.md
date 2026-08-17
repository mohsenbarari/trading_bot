# Decisions

Entries are newest first.

- 2026-08-16 | Customer identity is server-scoped to self, relation owner, and same-owner accountants; foreign lookup fails closed. Privileged user-management stays a separate control-plane surface.
- 2026-08-15 | Staging sync resets exact resources on both hosts, validates all 23 shared tables/both origins, resets before formal scenarios, and mounts probes with `APP_ENV_FILE=/dev/null`.
- 2026-08-13 | Coin estimator/sidecar run from canonical `main`; mutable state lives under the production-data estimator-live path; conversations stay read-only and dashboard projection is privacy-safe.
- 2026-08-12 | Relationship shadow research reads canonical Market Store hot/archive at `available_at_utc`; labels require eligible confirmed coin trades and stay external.
- 2026-08-12 | Fresh Iran offers get one bounded signed-sync attempt after commit; only full ACK marks delivery, while age/backoff fences and the regular worker recover.
- 2026-08-12 | Canonical Alembic restores deployed merge `f9b` before `f9c`; `fb1` validates complete coin schema, repairs only all-absent `fa0`, and rejects partial state.
- 2026-08-12 | Coin inference normalizes canonical-Toman Market Store data once; confirmed trades outrank offers, and atomic rebuilds absorb freshness/backfills/corrections.
- 2026-08-11 | MemoryCustodian governs reviewed Markdown through a thin `AGENTS.md` bootstrap; local `.env*.local` files stay untracked.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally.
- 2026-08-10 | FastAPI API, Telegram bot, and Vue PWA are first-class surfaces.
