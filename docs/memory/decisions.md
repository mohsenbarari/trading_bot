# Decisions

Entries are newest first.

- 2026-08-11 | Telegram channel delivery will evolve to central ingress, a durable internal command record, B2B dispatch/receipt, and a publisher lane fixed at first publish. Reason: recovery/idempotency must remain internal and interactive posts cannot safely cross-edit between bots.
- 2026-08-11 | Staged project memory is checked by a dependency-free pre-commit guard, and local `.env*.local` files are excluded from Git. Reason: prevent credential-like and personal data from entering durable memory or commits.
- 2026-08-10 | Project memory uses MemoryCustodian as reviewed, repo-native Markdown under `docs/memory/`; keep `AGENTS.md` as a thin bootstrap and load only files routed by `manifest.md`. Reason: preserve cross-session context without automatic full-history or vector-store injection.
- 2026-08-10 | The implemented runtime in `api/`, `bot/`, and `models/` remains authoritative while `src/` is migrated incrementally under Clean Architecture. Reason: replacing runtime paths wholesale would risk production behavior.
- 2026-08-10 | The product has three first-class surfaces: FastAPI API, Telegram bot, and Vue PWA. Reason: a feature change must consider its relevant surface contracts rather than assuming a web-only application.
