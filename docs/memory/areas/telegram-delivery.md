# Telegram Delivery

- 2026-08-17 | Telegram Mini App and `/api/auth/webapp-login` are retired product surfaces. Do not migrate, revive, provision API bot credentials for, or treat them as supported; current WebApp authentication is OTP-first.
- 2026-08-17 | Staging Telegram execution is Queue-v1 on the bot host; API/WebApp stay producer-only. Login OTP is an encrypted foreign Redis stream executed only by the bot. SMS fallback may stay BLOCKED without blocking Telegram OTP. Recipient-less residue stops health and uses the audited reconciler; never use direct SQL/provider cleanup. Production remains Legacy pending separate owner authorization.
- 2026-08-16 | Overtime follows offer home: foreign/bot-home uses its active Telegram runtime, starts the clock only after a message id, and queues channel refresh; Iran/WebApp-home presents in WebApp. Requester surface is audit-only.
- 2026-08-12 | The 500-offer matrix uses 60/40 Bot/WebApp origin, random 0.8–4-second ingress, fake private transport, bounded lifecycle work, and redacted audits.
- 2026-08-11 | Queue-v1 retries only pre-dispatch serialization/deadlock aborts inside a short fenced Redis cadence lease; other DB failures fail closed.
- 2026-08-11 | Publication scans exclude offers with an existing non-final control/publish job so central ingress is not delayed by repeated deduplication.
- 2026-08-11 | Queue-v1 interaction probes model authenticated private chat and positive message identity.
- 2026-08-11 | Multi-publisher B2B needs five capable identities; the feeder fixes one healthy lane at first publish. Callbacks stay on the receiver; Telegram edit ownership cannot transfer.
- 2026-08-11 | Telegram resume clears shared destination cadence and only preflight-approved lane blocks.
- 2026-08-11 | Telegram delivery uses central ingress, durable B2B command/receipt, and a lane fixed at first publish.
