# Decisions

Entries are newest first.

- 2026-08-22 | Cross-host Iran image identity is bound by archive digest, exact release labels, and portable OS/architecture/created/config/rootfs content; target-local runtime image ID is used for container checks because Docker stores may expose config versus manifest digests for the same archive.
- 2026-08-21 | Queue-v1 and guarded inference production rollout is authorized. Five distinct staging Publishers passed provider identity/channel/permission readback; shared-fleet opt-in is disabled in both envs. Production deployment remains a separate operation and was not authorized in the staging release.
- 2026-08-21 | Customer relation limits are authoritative across Web and Bot: tier-1 offer totals/lots obey min/max, tier-2 cannot publish, and both tiers' completed source/responder trades obey min/max plus daily count/volume, including overtime preflight and final recheck. Every customer-limit denial uses only «شما مجاز به انجام این فعالیت نیستید.»
- 2026-08-21 | Web OTP login at a full session quota atomically replaces the oldest session after OTP proof; old-device approval is not required. Repeated replacements (2/24h, 5/7d, 7/30d) open a typed review-only user flag without restricting access, and active super admins receive deduplicated WebApp plus Queue-v1 Telegram alerts.
- 2026-08-19 | Profile access remains server-scoped to permitted identities. Within any authorized profile, full mobile and address are product-visible contact fields shown as two plain rows without privacy/help copy; unrelated presence, relation, trade, and management data remain excluded.
- 2026-08-17 | Staging login OTP is encrypted on foreign Redis and executed only by the credentialed bot; API stays producer-only. After a terminal result, ACK and DELETE the command; health is outstanding rather than historical XLEN; max-deliveries is Redis metadata, not a guessed count. Missing an approved staging SMS credential leaves SMS BLOCKED and must not force Telegram off or copy production secrets. Production Queue-v1 still needs a separate owner order.
- 2026-08-15 | Staging sync resets exact resources on both hosts, validates all 23 shared tables/both origins, resets before formal scenarios, and mounts probes with `APP_ENV_FILE=/dev/null`.
- 2026-08-13 | Coin estimator/sidecar run from canonical `main`; mutable state lives under the production-data estimator-live path; conversations stay read-only and dashboard projection is privacy-safe.
- 2026-08-12 | Relationship shadow research reads canonical Market Store hot/archive at `available_at_utc`; labels require eligible confirmed coin trades and stay external.
- 2026-08-12 | Fresh Iran offers get one bounded signed-sync attempt after commit; only full ACK marks delivery, while age/backoff fences and the regular worker recover.
- 2026-08-12 | Canonical Alembic restores deployed merge `f9b` before `f9c`; `fb1` validates complete coin schema, repairs only all-absent `fa0`, and rejects partial state.
- 2026-08-12 | Coin inference normalizes canonical-Toman Market Store data once; confirmed trades outrank offers, and atomic rebuilds absorb freshness/backfills/corrections.
- 2026-08-11 | MemoryCustodian governs reviewed Markdown through a thin `AGENTS.md` bootstrap; local `.env*.local` files stay untracked.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally.
- 2026-08-10 | FastAPI API, Telegram bot, and Vue PWA are first-class surfaces.
