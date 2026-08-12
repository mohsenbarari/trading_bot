# Decisions

Entries are newest first.

- 2026-08-12 | Staging live matrix uses 500 seeded 60/40 Bot/WebApp offers with random 0.8–4-second ingress; private delivery uses a fake transport. Direct WebApp skips post-response tasks, overtime is ordered at ≤20 operations, a blank active bot callback retries ≤2 times, lifecycle task failures abort before another offer, and audit retains redacted failure class/code plus attempts. Reason: reproducible evidence without invalid-run growth, timer pressure, or synthetic private delivery.
- 2026-08-11 | Queue-v1 retries only serialization/deadlock aborts before provider dispatch, inside the same bounded unstarted lease. Reason: no external effect occurred; other DB failures fail closed.
- 2026-08-11 | Queue-v1 retains an unstarted fenced lease only through a short absolute Redis cadence deadline; longer waits are durable retries. Reason: re-claiming causes DB churn, but long leases starve a lane.
- 2026-08-11 | Publication scans exclude offers with an existing non-final control/publish job. Reason: repeated deduplication delays central ingress and can age queued offers before worker admission.
- 2026-08-11 | Queue-v1 interaction probes model the authenticated private chat and positive message identity. Reason: the durable adapter must reject unanchored or cross-chat replies as production does.
- 2026-08-11 | With multi-publisher B2B enabled, foreign feeder atomically persists one healthy publisher lane at first publication; legacy/B2B-disabled routes retain `primary`. Reason: preassigning primary bypasses selection and concentrates traffic.
- 2026-08-11 | Telegram resume clears shared destination cadence and only preflight-approved lane blocks. Reason: recovery must not release unrelated publishers.
- 2026-08-11 | Publish/edit jobs match their persisted owner before provider execution; callbacks stay on the receiving bot. Reason: Telegram cannot transfer interactive posts across bot identities.
- 2026-08-11 | A publisher dispatch command is paired 1:1 with its publisher-owned job; owner/message identity is immutable. Reason: retries and recovery must not reroute or prematurely execute a live post.
- 2026-08-11 | Multi-publisher configuration is all-or-nothing: five distinct, capability-checked identities. Reason: avoid cross-bot edits and partial activation.
- 2026-08-11 | Telegram delivery evolves through central ingress, durable B2B command/receipt, and a lane fixed at first publish. Reason: recovery/idempotency stay internal and interactive posts cannot cross-edit.
- 2026-08-11 | Project memory is reviewed Markdown with a dependency-free pre-commit guard; local `.env*.local` files stay untracked. Reason: prevent credentials and personal data entering commits.
- 2026-08-10 | MemoryCustodian is the project memory source; `AGENTS.md` stays a thin bootstrap and loads only routed files. Reason: preserve cross-session context without automatic full-history injection.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally. Reason: wholesale replacement risks production behavior.
- 2026-08-10 | FastAPI API, Telegram bot, and Vue PWA are first-class surfaces. Reason: relevant surface contracts must all be considered.
