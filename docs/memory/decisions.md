# Decisions

Entries are newest first.

- 2026-08-23 | Stage 8 inverts DB-first only for `answerCallbackQuery`: the edge answers first, then the durable witness is marked `sent` with `answered_at_edge`. Failure to answer still enqueues recovery. Owner approved this exception.
- 2026-08-23 | Owner approved remaining Telegram latency stages on `candidate/telegram-dispatch-latency-v1`. Stage 7 adds fail-closed `TELEGRAM_BOT_RUNTIME_ROLE` (`all` default, `primary`, `publishers`) with disjoint lane ownership; the publishers compose service is profile-gated so current single-process deploys do not overlap.
- 2026-08-23 | Stage 6 midpoint is code-derived only: dead wait 1s→0, B2B batch 1→8, keepalive HTTP, one B2B session, claim index covers `sent`. Channel interval, three Telegram hops, and the shared process are unchanged. Live staging percentiles were not collected, so stage 7 stays blocked on owner approval plus live evidence.
- 2026-08-23 | Low-risk Telegram latency stages 1–5 are on `candidate/telegram-dispatch-latency-v1`: acknowledgement now emits the existing transactional wakeup; the gateway recycles one HTTP client per event loop; B2B dispatch claims a serial batch of 8; publisher B2B messages skip auth; the claim index covers `sent` and retention deletes terminal commands with their jobs. Sticky ownership, shared `destination_next`, and payload-free B2B stay locked. Stages 7–9 still need owner approval.
- 2026-08-23 | Telegram dispatch latency work lives on `candidate/telegram-dispatch-latency-v1` from `origin/main` `44babdc4`; execution contract is `docs/TELEGRAM_DISPATCH_LATENCY_ROADMAP.md`. Root causes: central bot and all five publishers share one process and one event loop; the only queue wakeup fires at job creation while a publisher lane cannot claim until its own B2B command is `acknowledged`, and the acknowledgement path emits no wakeup, so every channel post and edit pays a dead 0–1s wait; the gateway opens a fresh HTTP client per Telegram call; the B2B hop costs two extra Telegram round trips per job even though both sides share one database. Sticky publisher ownership, the shared `destination_next` gate, and payload-free B2B stay locked. Stage 8 (inline callback answer) and Stage 9 (skipping the B2B round trip when colocated) each need explicit owner approval because they change a stated contract.

- 2026-08-22 | Release input signatures exclude Python bytecode/cache artifacts while remaining sensitive to source drift.
- 2026-08-22 | Closed/quiet-market inference may publish only bound `SAFE_NO_DATA`: healthy retained checkpoints, no price authority or invented values, then atomic replacement by fresh rate-ready data.
- 2026-08-22 | Queue-v1 forward redeploys use the official two-host release while retaining one owner; cutover and redeploy have separate bound evidence.
- 2026-08-22 | Iran nginx gets only named `www-data` execute ACL; deploy proves index readable and runtime `.env` unreadable.
- 2026-08-22 | Iran image-cache skips require release SHA, tree, and input signature.
- 2026-08-22 | Cross-host images bind archive digest, release labels, and portable image identity; container checks use target-local image ID.
- 2026-08-21 | Queue-v1 and guarded inference production rollout is authorized. Six staging identities passed provider/channel/permission checks; shared fleet stays off and production deployment is separate.
- 2026-08-21 | Customer limits are authoritative on Web/Bot: tier-1 publish and both tiers' requests/trades obey min/max and daily count/volume, including overtime final recheck; tier-2 cannot publish. Denial text is «شما مجاز به انجام این فعالیت نیستید.»
- 2026-08-21 | OTP login at session quota atomically replaces the oldest session. Replacements at 2/24h, 5/7d, 7/30d create a review-only flag and deduplicated super-admin Web/Telegram alerts.
- 2026-08-19 | Authorized profiles show full mobile/address as plain contact rows; unrelated presence, relation, trade, and management data stay excluded.
- 2026-08-17 | Staging Telegram OTP is encrypted on foreign Redis, API is producer-only, and the bot ACK+DELETEs terminal commands; health counts outstanding work. SMS without approved staging credentials stays blocked. Production Queue-v1 needs owner authorization.
- 2026-08-15 | Staging sync resets exact resources on both hosts, validates all 23 shared tables/both origins, resets before formal scenarios, and mounts probes with `APP_ENV_FILE=/dev/null`.
- 2026-08-13 | Coin estimator/sidecar run from canonical `main`; mutable state lives under the production-data estimator-live path; conversations stay read-only and dashboard projection is privacy-safe.
- 2026-08-12 | Relationship shadow research reads canonical Market Store hot/archive at `available_at_utc`; labels require eligible confirmed coin trades and stay external.
- 2026-08-12 | Fresh Iran offers get one bounded signed-sync attempt after commit; only full ACK marks delivery, while age/backoff fences and the regular worker recover.
- 2026-08-12 | Canonical Alembic restores deployed merge `f9b` before `f9c`; `fb1` validates complete coin schema, repairs only all-absent `fa0`, and rejects partial state.
- 2026-08-12 | Coin inference normalizes canonical-Toman Market Store data once; confirmed trades outrank offers, and atomic rebuilds absorb freshness/backfills/corrections.
- 2026-08-11 | MemoryCustodian governs reviewed Markdown through a thin `AGENTS.md` bootstrap; local `.env*.local` files stay untracked.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally.
- 2026-08-10 | FastAPI API, Telegram bot, and Vue PWA are first-class surfaces.
