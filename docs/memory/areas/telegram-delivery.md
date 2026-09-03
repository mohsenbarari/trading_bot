# Telegram Delivery

- 2026-09-03 | Queue-v1 redeploy may adopt only the exact durable `AUTHORITY_TRANSFERRED` Market lock after journal/quiescence proof; it keeps the same inode through the two-host release and returns it unchanged.
- 2026-09-03 | Untraded expiry and final-tail preserve Telegram posts/buttons; overtime entry adds `⏳`. Stale clicks fail authoritative validation; trade edits remain unchanged.
- 2026-09-02 | Telegram execution remains Finland-only. SMS capability exists on both sites but only the human-selected current Web Writer/generation may execute: Finland tries Telegram then sends the same OTP locally by SMS after 40s; Iran uses local SMS only. Ambiguous provider results never trigger blind resend.
- 2026-09-01 | Web Writer changes never restrict Finland Bot. Its independent Telegram authority keeps every Bot capability active; explicit command/aggregate ownership prevents conflicts without silent queuing.
- 2026-08-29 | Outbox dependency waits stay durable/non-terminal with 1/5/15/60s backoff. Split-runtime teardown uses `docker rm -fv`, avoiding anonymous PostgreSQL volumes.
- 2026-08-28 | Unknown-channel sends fail closed and are never blindly retried. Only audited reconciliation after confirmed absence covers every active Publisher lane.
- 2026-08-23 | `primary` polls/ACKs; one `executor` owns Queue-v1, OTP and global locks; APIs only produce. Cutover preserves jobs, awaits old owners and pins image+SHA.
- 2026-08-23 | Panel precedes FSM/router. Commodity admin uses configured origin plus the exact dev key; callbacks ACK first, stale actions fail visibly and durable anchors preserve Back.
- 2026-08-23 | Latency uses ACK wakeup, shared HTTP, serial batch 8, a `sent` claim index and 1.05s/destination. Local ACK is lease-fenced and never repeated.
- 2026-08-23 | Broadcast video uses central-bot `file_id` via Queue-v1 `sendVideo`, never binary/path/base64. Pre-auth callbacks retain their guarded origin event.
- 2026-08-23 | Staging foreign owns bot/executor/five Publishers; Iran owns API. Exact inventory removes opposites; collisions block production and APIs stay token-free.
- 2026-08-21 | Sync promotes only newer foreign Publishers into Iran `primary/pending/v1`, bound to authority/identity/transaction marker; repair uses sequenced DB `ChangeLog`.
- 2026-08-19 | OTP uses encrypted Redis, ACK+DELETE and quarantine; PostgreSQL only wakes. Tables decide, success follows commit, retired Mini App stays retired and Web remains OTP-first.
- 2026-08-18 | Offer IDs remain local; `offer_public_id` rebinds suggestions at source. Home owns mutations, signed cancellation fails closed and reachability uses ID then username/Telegram URI.
- 2026-08-18 | Queue ingress uses sticky five-publisher ownership and local ACK; Telegram is never bot-to-bot transport. Callbacks stay local and requeues are fenced.
- 2026-08-12 | The 500-offer matrix uses 60/40 Bot/Web with fake transport and bounded lifecycles. Quarantine proves execution only; probes require authenticated message identity.
