# Decisions

Entries are newest first.

- 2026-08-13 | Stage 8 schema 3 retains 270 expected outcomes and zero full-acceptance cells; slices include route-first `31c69d5a`, historical `4415b743` (P1 pending), invitation `4beeade2` (mocked DELETE-204/Chromium abort nonpromotable), and `338918d5` NONE-vnode Vazirmatn/font-synthesis retaining `font-sans`/FULL/MIXED across fades. Reason: bounded evidence cannot become acceptance or widen protected typography.
- 2026-08-13 | Stage 7 shared a11y changes are inert-by-default and call-site opt-in; reduced-motion is bound per keyed unprotected SECTION vnode, while protected full/mixed routes, TradingSettings calendar, and protected empty states keep prior behavior. Reason: route-destination state and shared defaults can otherwise leak into protected interiors despite unchanged direct-file hashes.
- 2026-08-11 | Staged project memory is checked by a dependency-free pre-commit guard, and local `.env*.local` files are excluded from Git. Reason: prevent credential-like and personal data from entering durable memory or commits.
- 2026-08-10 | Project memory uses MemoryCustodian as reviewed, repo-native Markdown under `docs/memory/`; keep `AGENTS.md` as a thin bootstrap and load only files routed by `manifest.md`. Reason: preserve cross-session context without automatic full-history or vector-store injection.
- 2026-08-10 | The implemented runtime in `api/`, `bot/`, and `models/` remains authoritative while `src/` is migrated incrementally under Clean Architecture. Reason: replacing runtime paths wholesale would risk production behavior.
- 2026-08-10 | The product has three first-class surfaces: FastAPI API, Telegram bot, and Vue PWA. Reason: a feature change must consider its relevant surface contracts rather than assuming a web-only application.
