# Decisions

Entries are newest first.

- 2026-08-13 | Stage 8 is an expected-access traceability draft: 30 routes × 9 exact profiles yield 270 source-derived outcomes, while full viewport/state/interaction acceptance, owner aesthetic sign-off, merge, Sites, staging, and production remain pending. Reason: static guard traceability is not executed UI/UX acceptance or release authority.
- 2026-08-13 | Stage 7 copy/keyboard/zoom/reduced-motion is opt-in on non-legacy shells; Market, Messenger, admin messages, and system settings stay on global `user-select: none` and Market FilterChips keep legacy focus. Reason: finish a11y polish without changing protected interiors.
- 2026-08-11 | Staged project memory is checked by a dependency-free pre-commit guard, and local `.env*.local` files are excluded from Git. Reason: prevent credential-like and personal data from entering durable memory or commits.
- 2026-08-10 | Project memory uses MemoryCustodian as reviewed, repo-native Markdown under `docs/memory/`; keep `AGENTS.md` as a thin bootstrap and load only files routed by `manifest.md`. Reason: preserve cross-session context without automatic full-history or vector-store injection.
- 2026-08-10 | The implemented runtime in `api/`, `bot/`, and `models/` remains authoritative while `src/` is migrated incrementally under Clean Architecture. Reason: replacing runtime paths wholesale would risk production behavior.
- 2026-08-10 | The product has three first-class surfaces: FastAPI API, Telegram bot, and Vue PWA. Reason: a feature change must consider its relevant surface contracts rather than assuming a web-only application.
