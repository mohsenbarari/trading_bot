# Telegram Delivery

- 2026-09-01 | Web Writer changes never restrict Finland Bot. Its independent
  Telegram authority keeps all Bot capabilities active; explicit command/aggregate
  ownership prevents conflict without disabling or silently queuing Bot.
- 2026-08-29 | Outbox dependency waits stay durable/non-terminal with 1/5/15/60s
  backoff. Split teardown uses `docker rm -fv` to avoid anonymous PG volumes.
- 2026-08-28 | Unknown-channel `sendMessage` fails closed and is never silently
  retried. Only audited CLI recovery after confirmed absence covers every active lane.
- 2026-08-23 | `primary` polls/ACKs; one `executor` owns Queue-v1, OTP and global
  locks; APIs only produce. Cutover preserves jobs, awaits old owners and pins image+SHA.
- 2026-08-23 | Panel precedes FSM/router. Commodity admin uses configured origin
  plus exact dev key; callbacks ACK first, stale actions fail visibly, anchors preserve Back.
- 2026-08-23 | Latency uses ACK wakeup, shared HTTP, serial batch 8, a `sent` claim
  index and 1.05s/destination. Local ACK is lease-fenced and never repeated.
- 2026-08-23 | Broadcast video uses central-bot `file_id` via Queue-v1 `sendVideo`,
  never binary/path/base64. Pre-auth callbacks retain guarded origin events.
- 2026-08-23 | Staging foreign owns bot/executor/five Publishers; Iran owns API.
  Exact inventory removes opposites; collisions block production and APIs stay token-free.
- 2026-08-21 | Sync promotes only newer foreign `publisher_1..5` into Iran
  `primary/pending/v1`, authority/identity/transaction-bound; repair uses DB `ChangeLog`.
- 2026-08-19 | OTP uses encrypted Redis, ACK+DELETE and quarantine; PG only wakes.
  Tables decide, success follows commit, retired Mini App stays retired, Web is OTP-first.
- 2026-08-18 | Offer IDs stay local; `offer_public_id` rebinds suggestions at source.
  Polling is bounded, home owns mutations, signed cancellation fails closed, reachability
  uses ID then username/tg URI.
- 2026-08-18 | Queue ingress has sticky five-publisher ownership; co-located
  publishers ACK locally, Telegram is never bot-to-bot, and callbacks remain local.
- 2026-08-12 | The 500-offer matrix is 60/40 Bot/Web with fake transport and bounded
  lifecycles. Quarantine proves execution; probes require authenticated message identity.
