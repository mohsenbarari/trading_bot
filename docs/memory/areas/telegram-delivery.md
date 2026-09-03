# Telegram Delivery

- 2026-09-03 | Queue-v1 redeploy adopts only the durable `AUTHORITY_TRANSFERRED` Market lock after journal/quiescence proof; it keeps the inode through release and returns it unchanged.
- 2026-09-03 | Untraded expiry/final-tail preserve Telegram posts/buttons; overtime adds `⏳`. Stale clicks fail authoritative validation; trade edits remain unchanged.
- 2026-09-02 | Telegram executes only in Finland. SMS exists on both sites but only human-selected Web Writer/generation executes: Finland tries Telegram then the same OTP by SMS after 40s; Iran uses SMS. Ambiguous results never trigger blind resend.
- 2026-09-01 | Web Writer changes never restrict Finland Bot. Independent Telegram authority keeps all Bot capabilities active; explicit ownership prevents conflicts without silent queuing.
- 2026-08-29 | Outbox waits stay durable/non-terminal with 1/5/15/60s backoff. Split teardown uses `docker rm -fv`, avoiding anonymous PG volumes.
- 2026-08-28 | Unknown-channel sends fail closed and are never blindly retried. Audited reconciliation requires confirmed absence across active Publisher lanes.
- 2026-08-23 | `primary` polls/ACKs; one `executor` owns Queue-v1, OTP and locks; APIs only produce. Cutover preserves jobs, awaits old owners and pins image+SHA.
- 2026-08-23 | Panel precedes FSM/router; commodity admin uses configured origin/exact dev key. Callbacks ACK first, stale actions fail visibly and anchors preserve Back. Latency uses ACK wakeup, shared HTTP, batch 8, `sent` index and 1.05s/destination; local ACK is fenced and never repeated.
- 2026-08-23 | Broadcast uses central-bot `file_id` via Queue-v1 `sendVideo`, never binary/path/base64; pre-auth callbacks retain origin.
- 2026-08-23 | Staging foreign owns bot/executor/five Publishers; Iran owns API. Inventory removes opposites; collisions block production and APIs stay token-free.
- 2026-08-21 | Sync promotes newer foreign Publishers into Iran `primary/pending/v1`, authority/identity/transaction-bound; repair uses sequenced DB `ChangeLog`.
- 2026-08-19 | OTP uses encrypted Redis, ACK+DELETE and quarantine; PostgreSQL only wakes. Tables decide, success follows commit, retired Mini App stays retired and Web remains OTP-first.
- 2026-08-18 | Offer IDs stay local; `offer_public_id` rebinds at source. Home owns mutations, signed cancellation fails closed and reachability uses ID then username/Telegram URI.
- 2026-08-18 | Queue ingress uses sticky five-publisher ownership and local ACK; Telegram is never bot-to-bot transport. Callbacks stay local and requeues are fenced.
- 2026-08-12 | The 500-offer matrix uses 60/40 Bot/Web, fake transport and bounded lifecycles. Quarantine proves execution; probes require authenticated message identity.
