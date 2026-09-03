# Telegram Delivery

- 2026-09-03 | Untraded expiry and final-tail preserve Telegram posts/buttons; overtime entry adds `⏳`. Stale clicks fail authoritative validation; trade edits are unchanged.
- 2026-08-29 | Outbox dependency waits are durable and back off 1/5/15/60s. Split-runtime teardown uses `docker rm -fv`, avoiding anonymous PG volume leaks.
- 2026-08-28 | Unknown channel sends fail closed and are never blindly retried. Only audited CLI reconciliation recovers confirmed absence across active Publisher lanes.
- 2026-08-23 | `primary` polls/ACKs; sole `executor` owns Queue-v1, OTP and locks; APIs produce only. Cutover preserves jobs, awaits old owners and pins image+SHA.
- 2026-08-23 | Panel precedes FSM/router. Commodity admin API uses configured foreign origin, never a fixed Compose alias, plus exact dev key; callbacks ACK before work and stale actions fail visibly. Durable anchors preserve device Back.
- 2026-08-23 | Latency uses ACK wakeup, shared HTTP, serial batch 8, claim index with `sent` and 1.05s/destination. Local ACK is lease-fenced; workers never repeat it.
- 2026-08-23 | Video broadcast uses central-bot `file_id` through Queue-v1 `sendVideo`; never binary/path/base64. Pre-auth callbacks retain their origin event with actor/chat guards.
- 2026-08-23 | Staging foreign runs bot/executor/five Publishers; Iran runs API. Exact inventory and collision guards apply; APIs remain token-free.
- 2026-08-21 | Sync promotes only newer foreign Publishers into Iran `primary/pending/v1`, authority/identity/transaction-bound. Repair uses sequenced `ChangeLog`.
- 2026-08-19 | OTP uses encrypted Redis, ACK+DELETE and quarantine. PG only wakes; tables decide. ACK is receipt, success follows commit. Mini App and `/api/auth/webapp-login` are retired; WebApp is OTP-first.
- 2026-08-18 | Offer IDs are local; suggestions retain `offer_public_id` and rebind at source. Overtime polling is bounded; mutations run only on offer/request home and signed cancellation forwards fail-closed. Terminal ratios stay bottom-left/LTR; reachability uses ID then username/tg URI.
- 2026-08-18 | Queue ingress uses sticky five-publisher ownership and local ACK; Telegram is never bot-to-bot transport. Callbacks stay local; requeues are fenced.
- 2026-08-12 | 500-offer matrix uses 60/40 Bot/WebApp and bounded lifecycles. Quarantine proves execution only; probes require authenticated message identity.
