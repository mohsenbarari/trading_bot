# Decisions

Entries are newest first.

- 2026-08-26 | Market parity uses one owner and a frozen HMAC-manifested receipt window, version-pinned clone lanes and one clock. Compare final facts/aligned snapshots without raw values; separate value, metadata and schema drift. Minute-compacted XAU is not the event oracle.
- 2026-08-25 | The new Market Intelligence pipeline is Docker-native and deploy-managed. One immutable SHA/digest image may serve multiple isolated commands; code/dependencies live in images, while databases, spools, checkpoints, models, sessions and secrets stay on protected persistent mounts. Cutover is shadow-first and never runs two owners for one Telegram session.
- 2026-08-25 | Cross-server Market Facts move first over the provider private network in an isolated durable lane; public product sync is unchanged initially. Migrate general sync transport only after private-path parity, failure, rollback, and observability gates pass. Private routing does not remove authentication/integrity checks.
- 2026-08-24 | Telegram capture promotion is cutover-seeded and causal: `source_id` is authoritative, receipt time controls availability, channel reconciliation is 30m, and coin groups use a 6h graph plus reply ancestors within the 2h trade window. Production also requires an open-market live gate; closed-market `SAFE_NO_DATA` is insufficient.
- 2026-08-23 | Split Queue is fail-closed: `primary` polls; one `executor` owns all lanes/OTP; APIs only produce. Cutover preserves jobs/rows and requires a no-op rehearsal. Pool ceilings: all/executor 15+10, primary 12+8. Lease ACK, 1.05s destination cadence, freshness, retention and `sent` index remain.

- 2026-08-22 | Release signatures ignore bytecode but stay source-sensitive. Quiet-market inference may publish only bound `SAFE_NO_DATA`, then atomically replace it. Queue-v1 forward redeploys keep one owner; cutover and redeploy have separate evidence. Iran nginx gets named `www-data` execute ACL; image-cache skips need SHA/tree/signature; cross-host images bind digest, labels, and portable identity.
- 2026-08-21 | Queue-v1 and guarded inference rollout is authorized; six staging identities passed checks; shared fleet stays off. Web/Bot customer limits are authoritative, including overtime recheck; tier-2 cannot publish. OTP login at quota replaces the oldest session and flags 2/24h, 5/7d, 7/30d replacements.
- 2026-08-19 | Authorized profiles show full mobile/address as plain contact rows; unrelated presence, relation, trade, and management data stay excluded.
- 2026-08-17 | Staging OTP is encrypted on foreign Redis; API is producer-only; bot ACK+DELETEs terminal commands. SMS without approved staging credentials stays blocked.
- 2026-08-15 | Staging sync resets exact resources on both hosts, validates all 23 shared tables/both origins, and mounts probes with `APP_ENV_FILE=/dev/null`.
- 2026-08-13 | Coin estimator/sidecar run from canonical `main`; mutable state lives under the production-data estimator-live path.
- 2026-08-12 | Relationship labels stay external and require eligible confirmed trades. Fresh Iran offers get one bounded signed-sync attempt; only full ACK marks delivery. Alembic restores `f9b` before `f9c`; `fb1` repairs only all-absent `fa0`. Inference normalizes canonical-Toman Market Store data once.
- 2026-08-11 | MemoryCustodian governs reviewed Markdown through a thin `AGENTS.md` bootstrap; local `.env*.local` files stay untracked.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally. FastAPI, Telegram bot, and Vue PWA are first-class surfaces.
