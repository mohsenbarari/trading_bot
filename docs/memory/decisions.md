# Decisions

Entries are newest first.

- 2026-08-12 | Fresh Iran offers get one bounded post-commit attempt through signed sync; only full ACK marks the outbox delivered, while age/backoff fences and the regular worker recover. Reason: prompt foreign-queue handoff without a second protocol.
- 2026-08-12 | Canonical Alembic restores the deployed `f9b` merge before `f9c`; `fb1` validates complete coin schema, repairs only all-absent `fa0`, and rejects partial state. Reason: one revision had diverged parents.
- 2026-08-12 | Live estimator calibration stays in a sidecar, reads conversations read-only and imports legacy state only when empty; dashboard metadata refreshes with estimates and marks activity stale beyond the anchor. Reason: protect inputs/history and align displayed freshness with eligibility.
- 2026-08-12 | Product coin inference consumes canonical-Toman Market Store data; adapters normalize once. Reason: prevent drift and double conversion.
- 2026-08-12 | Snapshots rebuild atomically on every invocation, regardless of watermark. Reason: freshness, age and same-key corrections are time/content dependent.
- 2026-08-12 | Confirmed trades outrank offers for coin anchors; id breaks only equal-time ties. Reason: backfills must not displace later events.
- 2026-08-12 | Staging audits use shared volumes; missing history may reconstruct only immutable provenance and must label transient facts unverified. Reason: never fabricate evidence.
- 2026-08-12 | The 500-offer matrix uses 60/40 Bot/WebApp origin, random 0.8–4-second ingress, fake private transport, bounded lifecycle work, fail-fast tasks and redacted audits. Reason: reproducible evidence without pool pressure.
- 2026-08-11 | Queue-v1 retries only serialization/deadlock aborts before provider dispatch, inside the same bounded unstarted lease. Reason: no external effect occurred; other DB failures fail closed.
- 2026-08-11 | Queue-v1 retains an unstarted fenced lease only through a short absolute Redis cadence deadline; longer waits are durable retries. Reason: re-claiming causes DB churn, but long leases starve a lane.
- 2026-08-11 | Publication scans exclude offers with an existing non-final control/publish job. Reason: repeated deduplication delays central ingress and can age queued offers before worker admission.
- 2026-08-11 | Queue-v1 interaction probes model authenticated private chat and positive message identity. Reason: reject unanchored or cross-chat replies.
- 2026-08-11 | Multi-publisher B2B requires five capable identities; the feeder fixes one healthy lane at first publish and pairs its immutable owner/message with one dispatch command. Callbacks remain on the receiver; legacy routes use `primary`. Reason: Telegram edit ownership cannot transfer.
- 2026-08-11 | Telegram resume clears shared destination cadence and only preflight-approved lane blocks. Reason: recovery must not release unrelated publishers.
- 2026-08-11 | Telegram delivery evolves through central ingress, durable B2B command/receipt, and a lane fixed at first publish. Reason: recovery/idempotency stay internal and interactive posts cannot cross-edit.
- 2026-08-11 | Project memory is reviewed Markdown with a dependency-free pre-commit guard; local `.env*.local` files stay untracked. Reason: prevent credentials and personal data entering commits.
- 2026-08-10 | MemoryCustodian is the project memory source; `AGENTS.md` stays a thin bootstrap and loads only routed files. Reason: preserve cross-session context without automatic full-history injection.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally. Reason: wholesale replacement risks production behavior.
- 2026-08-10 | FastAPI API, Telegram bot, and Vue PWA are first-class surfaces. Reason: relevant surface contracts must all be considered.
