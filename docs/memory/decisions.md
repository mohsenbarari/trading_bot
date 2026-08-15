# Decisions

Entries are newest first.

- 2026-08-15 | Staging sync resets exact resources on both hosts, validates all 23 shared tables/both origins, resets before formal scenarios, and mounts probes with `APP_ENV_FILE=/dev/null`.
- 2026-08-13 | Coin estimator dashboard/collectors run from canonical `main`; mutable state lives under `/srv/trading-bot/production-data/coin-intelligence/estimator-live`, with privacy-safe projection of current group facts into the compatibility store.
- 2026-08-12 | Relationship shadow research reads verified canonical Market Store hot/archive at `available_at_utc`; labels use only eligible nonconditional confirmed coin trades, artifacts stay outside the repo, and no scheduler/auto-promotion ships.
- 2026-08-12 | Fresh Iran offers get one bounded signed-sync attempt after commit; only full ACK marks delivery, while age/backoff fences and the regular worker recover.
- 2026-08-12 | Canonical Alembic restores deployed merge `f9b` before `f9c`; `fb1` validates complete coin schema, repairs only all-absent `fa0`, and rejects partial state.
- 2026-08-12 | Estimator calibration stays in a sidecar, reads conversations read-only, imports legacy state only when empty, and refreshes dashboard freshness with estimates.
- 2026-08-12 | Coin inference consumes canonical-Toman Market Store data; adapters normalize once to prevent drift and double conversion.
- 2026-08-12 | Snapshots rebuild atomically every invocation because freshness, age and same-key corrections are time/content dependent.
- 2026-08-12 | Confirmed trades outrank offers for coin anchors; id breaks only equal-time ties so backfills cannot displace later events.
- 2026-08-12 | The 500-offer matrix uses 60/40 Bot/WebApp origin, random 0.8–4-second ingress, fake private transport, bounded lifecycle work, fail-fast tasks and redacted audits.
- 2026-08-11 | Queue-v1 retries only serialization/deadlock aborts before provider dispatch inside the same bounded unstarted lease; other DB failures fail closed.
- 2026-08-11 | Queue-v1 retains an unstarted fenced lease only through a short absolute Redis cadence deadline; longer waits are durable retries. Reason: re-claiming causes DB churn, but long leases starve a lane.
- 2026-08-11 | Publication scans exclude offers with an existing non-final control/publish job. Reason: repeated deduplication delays central ingress and can age queued offers before worker admission.
- 2026-08-11 | Queue-v1 interaction probes model authenticated private chat and positive message identity. Reason: reject unanchored or cross-chat replies.
- 2026-08-11 | Multi-publisher B2B requires five capable identities; the feeder fixes one healthy lane at first publish and pairs its immutable owner/message with one dispatch command. Callbacks remain on the receiver; legacy routes use `primary`. Reason: Telegram edit ownership cannot transfer.
- 2026-08-11 | Telegram resume clears shared destination cadence and only preflight-approved lane blocks. Reason: recovery must not release unrelated publishers.
- 2026-08-11 | Telegram delivery evolves through central ingress, durable B2B command/receipt, and a lane fixed at first publish. Reason: recovery/idempotency stay internal and interactive posts cannot cross-edit.
- 2026-08-11 | MemoryCustodian governs reviewed Markdown through a thin `AGENTS.md` bootstrap and dependency-free commit guard; local `.env*.local` files stay untracked.
- 2026-08-10 | Runtime `api/`, `bot/`, and `models/` remain authoritative while `src/` migrates incrementally. Reason: wholesale replacement risks production behavior.
- 2026-08-10 | FastAPI API, Telegram bot, and Vue PWA are first-class surfaces. Reason: relevant surface contracts must all be considered.
