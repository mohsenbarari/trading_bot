# Decisions

Entries are newest first.

- 2026-08-23 | Central-poller lock is monitored and fail-closes independently of the queue owner. Split cutover is a postcheck state machine with automatic job-preserving rollback. Bot pools use DB_BOT_*/STAGING_DB_BOT_*; APIs stay untouched. Canonical Alembic head is `ff6c7d8e9f01`. This mission did not deploy staging or production.
- 2026-08-23 | Candidate absorbed origin/main `b8746889` without editing main. Conflicts kept local ack + wakeup + batch 8; inference/ops commits auto-merged.
- 2026-08-23 | Split runtime is fail-closed: default `TELEGRAM_BOT_SPLIT_ENABLED=false` and role `all`. `primary` never owns the queue or OTP and must not acquire the global owner. `executor` (retired name `publishers`) is the only Queue-v1 owner of every lane plus OTP, taken before publisher polling or provider calls. Legacy accepts only `all`. Status remains READY FOR STAGING INTEGRATION REVIEW; no live percentiles.
- 2026-08-23 | Stage 12 sizes pools from slots, not live waits. `all` keeps 15+10. Split `primary` has no queue slots (ceiling 12+8 kept). `executor` owns every lane so its ceiling rose to 15+10. Settings `db_pool_size` stays 15.
- 2026-08-23 | Stages 8–11 stay: edge callback answer then durable witness; durable command is the B2B handoff with lease-fenced local ack and transactional wakeup; destination interval stays 1.05s; freshness validators stay and the last validate reuses locked Offer/state rows (8→6 reads). Retention preflights command and source holds before delete. Claim index covers `sent`.

- 2026-08-22 | Release signatures ignore bytecode but stay source-sensitive. Quiet-market inference may publish only bound `SAFE_NO_DATA`, then atomically replace it. Queue-v1 forward redeploys keep one owner; cutover and redeploy have separate evidence. Iran nginx gets named `www-data` execute ACL; image-cache skips need SHA/tree/signature; cross-host images bind digest, labels, and portable identity.
- 2026-08-21 | Queue-v1 and guarded inference rollout is authorized; six staging identities passed checks; shared fleet stays off. Web/Bot customer limits are authoritative, including overtime recheck; tier-2 cannot publish. OTP login at quota replaces the oldest session and flags 2/24h, 5/7d, 7/30d replacements.
- 2026-08-19 | Authorized profiles show full mobile/address as plain contact rows; unrelated presence, relation, trade, and management data stay excluded.
- 2026-08-17 | Staging OTP is encrypted on foreign Redis; API is producer-only; bot ACK+DELETEs terminal commands. SMS without approved staging credentials stays blocked.
- 2026-08-15 | Staging sync resets exact resources on both hosts, validates all 23 shared tables/both origins, and mounts probes with `APP_ENV_FILE=/dev/null`.
- 2026-08-13 | Coin estimator/sidecar run from canonical `main`; mutable state lives under the production-data estimator-live path.
- 2026-08-12 | Relationship labels stay external and require eligible confirmed trades. Fresh Iran offers get one bounded signed-sync attempt; only full ACK marks delivery. Alembic restores `f9b` before `f9c`; `fb1` repairs only all-absent `fa0`. Inference normalizes canonical-Toman Market Store data once.
- 2026-08-11 | MemoryCustodian governs reviewed Markdown through a thin `AGENTS.md` bootstrap; local `.env*.local` files stay untracked.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally. FastAPI, Telegram bot, and Vue PWA are first-class surfaces.
