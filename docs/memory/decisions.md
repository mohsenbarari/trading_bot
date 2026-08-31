# Decisions

Entries are newest first.

- 2026-08-31 | `main` is the only long-lived branch; `candidate/wa-ir-standby-v1` stays local-only and is not a merge source. The canonical checkout owns ignored artifacts with bounded retention.

- 2026-08-29 | `PRIVATE_PRIMARY` adopts Shadow roots in-place and mounts secrets from `/srv/trading-bot/secure/market-data`. Both-role preflight and equal image digest are required. No live cutover; Product stays `LEGACY`.
- 2026-08-28 | Urgent `PRIVATE_PRIMARY` may skip staging/soak, but Product stays `LEGACY` until exact release/off-host backup/single owner/nine-source gap/fresh complete-grid `OK`/CAS. `NO_DATA` requires a proven missing same-commodity one-gram anchor and fresh melted data; no synthetic/waiver. After `PRIMARY_COMMITTED`, rollback is Product-only.
- 2026-08-26 | Parity freezes one HMAC owner window, pinned lanes, and one clock. Compare final facts/aligned snapshots; separate value, metadata, and schema drift. Minute XAU is no event oracle.
- 2026-08-25 | Market Intelligence is Docker-native. One immutable image exposes isolated commands; persistent data/models/sessions/secrets stay mounted. Shadow-first cutover forbids dual Telegram-session owners.
- 2026-08-25 | Market Facts first use an authenticated isolated private-network lane; product sync initially stays unchanged. General sync follows only after parity/failure/rollback/observability gates.
- 2026-08-24 | Normal capture uses authoritative `source_id`, receipt availability, 30m channel reconciliation, and 6h graph+2h ancestors. The 2026-08-28 cutover alone supersedes timing; one-gram `NO_DATA` still requires proven absence and gap-free transport.
- 2026-08-23 | Split Queue is fail-closed: `primary` polls, one `executor` owns lanes/OTP, APIs produce. Preserve rows after no-op rehearsal; pools all/executor=15+10 and primary=12+8. Retain lease ACK, 1.05s cadence, freshness/retention/`sent` index.
- 2026-08-22 | Release signatures ignore bytecode, not source. Quiet markets may publish bound `SAFE_NO_DATA`, then replace atomically. Queue forward redeploy keeps one owner; cutover evidence is separate. Iran nginx uses `www-data` ACL.
- 2026-08-21 | Queue-v1/guarded inference authorized; six staging identities passed and shared fleet stays off. Web/Bot limits include overtime; tier-2 cannot publish. OTP quota replaces oldest session and flags 2/24h, 5/7d, 7/30d.
- 2026-08-19 | Authorized profiles show full mobile/address as plain contact rows; unrelated presence, relation, trade, and management data stay excluded.
- 2026-08-17 | Staging OTP is encrypted on foreign Redis; API is producer-only; bot ACK+DELETEs terminal commands. SMS without approved staging credentials stays blocked.
- 2026-08-15 | Staging sync resets exact resources on both hosts, validates all 23 shared tables/both origins, and mounts probes with `APP_ENV_FILE=/dev/null`.
- 2026-08-12 | Relationship labels require eligible confirmed trades. Iran offers get one bounded signed-sync attempt; only full ACK delivers. Alembic restores `f9b` before `f9c`; `fb1` repairs all-absent `fa0`. Inference normalizes canonical Toman once.
- 2026-08-11 | MemoryCustodian governs reviewed Markdown through a thin `AGENTS.md` bootstrap; local `.env*.local` files stay untracked.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally. FastAPI, Telegram bot, and Vue PWA are first-class.
