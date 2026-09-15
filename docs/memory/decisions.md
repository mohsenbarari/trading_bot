# Decisions

Entries are newest first.

- 2026-09-15 | A live automation cycle publishes only bounded phase/heartbeat metadata, never prompt or source. The monitor reports observed phase, phase age and heartbeat freshness; stale liveness is report-only and must never by itself terminate or restart a live Supervisor. Existing recovery for a separately missing Broker remains guarded and independent.

- 2026-09-15 | Automation efficiency must preserve final review and all quality gates: a known oversized Codex command event is drained without retaining its body so later completed-turn usage remains observable; other malformed telemetry remains UNKNOWN/fail-closed. Task identity may read non-model source only through the same link-safe reader with a 2 MB cap. A restart reuses only an exact committed `CONTINUE` checkpoint, and normal STOP keeps the Broker available until the Supervisor records `STOPPED` at a cycle boundary.

- 2026-09-12 | Automation deduplicates work by immutable semantic identity rather than task/run names: Stage + normalized documented gap + referenced symbols + scenario + source SHA manifest. Reservation occurs atomically before evidence/provider work, has no automatic expiry/release, and duplicate aliases fail closed. Existing-file outputs >=8192 UTF-8 bytes require compact replacements; full bytes are materialized for isolated focused tests before independent Gemini review. Equivalent substantive failures rescope instead of repeating.

- 2026-09-09 | CLI runtime execution uses an immutable canonical master (.local/tools/agy-<cli_version>-<first-16-sha>.pin) requiring absolute path, effective-UID ownership, regular file, single hard link, read-only mode, and exact pinned SHA-256, with removal of mutable alias fallback. All identity probes and provider calls execute only via a streamed per-invocation disposable copy in a mode-0700 temporary directory, never the canonical master. Literal AGY_CLI_DISABLE_AUTO_UPDATE=true is strictly required, with pre/post identity verification of version and hash failing closed on any drift. Disposable cleanup strictly precedes exclusive worktree lock release on success, error, and timeout. Explicit --cli inputs are similarly copied to disposable execution and validated against contract identity without requiring canonical read-only source.

- 2026-09-09 | Owner reconfirmed permanent standing authorization, without per-transfer confirmation, to send bounded non-secret source excerpts, tests, documentation, task JSON, proposals, reviews, and test evidence to Google Antigravity/Gemini for proposal-only workflow. Strictly excludes secrets, credentials, identity/auth material, user/personal data, production/operational data, deploy changes, DNS, Web Writer, fetch/push, and unrelated remote mutation. No other gate is widened.

- 2026-09-05 | Owner requires safe, simple and accurate implementation project-wide. Choose the smallest sufficient design and reassess recurring failure before adding controls. Automation must reduce development time and manual work; complexity needs a concrete benefit. Execution-specific contracts and gateway review status live in `areas/documentation-execution.md`.

- 2026-09-03 | Section 3 is approved: `MKT-0..MKT-13` execute in dependency order and activate only with Section 4. Completion requires no High/Critical gaps or behavior drift plus restore/rebuild/parity proof; thresholds stay evidence-derived.

- 2026-09-02 | `P1-01`: only `main` is long-lived; WA-IR is local and `.local` bounded. Registered, expiring Stage worktrees supersede its one-worktree rule.

- 2026-08-26 | Parity freezes one HMAC owner window, pinned lanes, and one clock. Compare final facts/aligned snapshots; separate value, metadata, and schema drift. Minute XAU is no event oracle.
- 2026-08-25 | Market Intelligence is Docker-native. One immutable image exposes isolated commands; persistent data/models/sessions/secrets stay mounted. Shadow-first cutover forbids dual Telegram-session owners.
- 2026-08-25 | Market Facts first use an authenticated isolated private-network lane; product sync initially stays unchanged. General sync follows only after parity/failure/rollback/observability gates.
- 2026-08-24 | Normal capture uses authoritative `source_id`, receipt availability, 30m reconciliation, and 6h graph+2h ancestors. Only the 2026-08-28 cutover supersedes timing; one-gram `NO_DATA` requires proven absence and gap-free transport.
- 2026-08-23 | Split Queue is fail-closed: `primary` polls, one `executor` owns lanes/OTP, APIs produce. Preserve rows after no-op rehearsal; pools all/executor=15+10 and primary=12+8. Retain lease ACK, 1.05s cadence, freshness/retention/`sent` index.
- 2026-08-22 | Release signatures ignore bytecode, not source. Quiet markets may publish bound `SAFE_NO_DATA`, then replace atomically. Queue forward redeploy keeps one owner; cutover evidence is separate. Iran nginx uses `www-data` ACL.
- 2026-08-21 | Queue-v1/guarded inference authorized; six staging identities passed and shared fleet stays off. Web/Bot limits include overtime; tier-2 cannot publish. OTP quota replaces oldest session and flags 2/24h, 5/7d, 7/30d.
- 2026-08-19 | Authorized profiles show full mobile/address as plain contact rows; unrelated presence, relation, trade, and management data stay excluded.
- 2026-08-17 | Staging OTP is encrypted on foreign Redis; API is producer-only; bot ACK+DELETEs terminal commands. SMS without approved staging credentials stays blocked.
- 2026-08-15 | Staging sync resets exact resources on both hosts, validates all 23 shared tables/both origins, and mounts probes with `APP_ENV_FILE=/dev/null`.
- 2026-08-12 | Relationship labels require eligible confirmed trades. Iran offers get one bounded signed-sync attempt; only full ACK delivers. Alembic restores `f9b` before `f9c`; `fb1` repairs all-absent `fa0`. Inference normalizes canonical Toman once.
- 2026-08-11 | MemoryCustodian governs reviewed Markdown through a thin `AGENTS.md` bootstrap; local `.env*.local` files stay untracked.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally. FastAPI, Telegram bot, and Vue PWA are first-class.
