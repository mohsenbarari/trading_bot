# Telegram Delivery

- 2026-08-23 | Video broadcast uses central-bot `file_id` and Queue-v1 `sendVideo`; never binary/path/base64.
- 2026-08-23 | Split has one executor: `primary` owns polling/local ACK; `executor` owns queue lanes, OTP and global owner. Both leases fail closed; cutover needs postchecks and job-preserving rollback. APIs only produce.
- 2026-08-23 | Latency: ACK wakeup, shared HTTP, serial batch 8, claim index with `sent`, 1.05s/destination. Local ACK is lease-fenced; workers do not repeat edge callback answers.
- 2026-08-23 | Staging roles fail closed: `trading_bot_staging` foreign/bot, `_iran` Iran/API; deploy removes opposites. Repair preserves authority; quarantine needs durable replacement.
- 2026-08-22 | Pre-auth `CallbackQuery` replies retain that route; never adapt bot-authored `callback.message`. Registration handoff uses the origin event with actor/chat guards.
- 2026-08-21 | Sync promotes only Iran `primary/pending/v1` placeholders from newer foreign `publisher_1..5`; bind authority/identity, require one row plus transaction marker. Repair uses DB-sequenced `ChangeLog`.
- 2026-08-21 | Staging has five Publishers; credential/channel collisions block production. APIs are token-free; foreign assigns WebApp offers.
- 2026-08-18 | Offer IDs are local; suggestions keep `offer_public_id` and rebind to source. Keep bounded overtime polling. Terminal ratios stay bottom-left/LTR. Reachability: `telegram_id`, then username/`tg://user?id=`.
- 2026-08-17 | Mini App and `/api/auth/webapp-login` are retired; WebApp auth is OTP-first.
- 2026-08-19 | Queue-v1 runs on bot; APIs produce. OTP uses encrypted Redis, ACK+DELETE and quarantine. PG hints only wake; tables rule. ACK is receipt-only; success follows commit. Durable menu anchors preserve device Back.
- 2026-08-16 | Overtime mutations run only on offer/request home; mirrors reject them. Presentation follows offer home; interaction stays at origin. Cancellation is signed, idempotent, fail-closed and forwarded home.
- 2026-08-12 | The 500-offer matrix uses 60/40 Bot/WebApp, fake transport and bounded lifecycles. Unknown-client quarantine proves execution, not delivery.
- 2026-08-11 | Queue-v1 central ingress uses sticky five-publisher ownership. Co-located publishers locally ACK durable dispatch; Telegram Bot API is never bot-to-bot transport. Callbacks remain receiver-local; scans skip final jobs; freshness changes use fenced requeues.
- 2026-08-11 | Interaction probes need authenticated chat and positive message identity. Resume clears cadence; only preflight-approved lanes block.
