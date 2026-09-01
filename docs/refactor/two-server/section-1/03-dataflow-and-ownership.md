# Dataflow and Ownership Baseline

Status: code/runtime baseline; target ADRs still required

## Application table policy

The code registry covers all 59 ORM model tables: 23 shared, 33 local and 3
internal-bookkeeping. This classification describes the current two-Finland
runtime; `P2-00/P2-01` must separately approve the Finland↔Iran contract.

### Shared business/operational tables (23)

```text
accountant_relations, admin_broadcast_messages, admin_market_messages,
commodities, commodity_aliases, customer_relations, invitations,
market_runtime_state, market_schedule_overrides, notifications,
offer_publication_states, offer_requests, offers,
telegram_admin_broadcast_receipts, telegram_admin_broadcasts,
telegram_link_tokens, telegram_notification_outbox, trade_delivery_receipts,
trades, trading_settings, user_blocks, user_notification_preferences, users
```

These tables must have one canonical row history during Finland consolidation.
Equal counts are insufficient: stable identity, field exclusions, business hash,
FKs, sequences, money/inventory invariants and side-effect ledgers must agree.

### Surface/site-local tables (33)

```text
chat_files, chat_members, chats, coin_intelligence_inference_audits,
coin_intelligence_inference_outcomes, coin_intelligence_market_outbox,
conversations, invitation_identity_reservations, invitation_sms_deliveries,
market_channel_notice_receipts, messages, offer_expiry_command_receipts,
push_subscriptions, session_login_requests, single_session_recovery_admin_targets,
single_session_recovery_requests, telegram_channel_membership_sagas,
telegram_delivery_feeder_states, telegram_delivery_jobs,
telegram_delivery_provider_outcomes, telegram_delivery_reconciliation_evidence,
telegram_delivery_resume_operations, telegram_delivery_runtime_gates,
telegram_interaction_anchor_states, telegram_publisher_dispatch_commands,
telegram_registration_command_receipts, telegram_registration_intents,
telegram_scheduled_operations, upload_batches, upload_sessions,
user_counter_event_receipts, user_flags, user_sessions
```

`NO_SYNC` does not mean disposable. During consolidation these tables are handled
by domain:

- Web-local auth/session/Messenger/upload/Push state is imported from the current
  Web-Finland database only.
- Telegram delivery, registration, saga, publisher and scheduler state is
  imported from Bot-Finland only, after the old executor is fenced.
- local audit/projection/outbox tables are either imported with their owning
  domain or deliberately rebuilt according to an approved per-table rule.

### Internal bookkeeping (3)

```text
change_log, sync_apply_watermarks, sync_blocks
```

These rows describe two old replication runtimes. They are not merged as product
truth. The target starts with an explicit bootstrap checkpoint and fresh
bookkeeping after shared and local data validation.

## Current flow and target translation

| Flow | Current contract | Target Finland contract |
| --- | --- | --- |
| shared mutation | local DB transaction + change log + peer apply | one local DB transaction; cross-site outbox only where Iran needs it |
| Web session/message/upload | Web-Finland local | Web capability local to Finland Primary |
| Telegram delivery/publication | Bot-Finland local | `TELEGRAM_OWNER` capability local to Finland Primary |
| Offer expiry | each server mutates its own `home_server` rows | explicit home authority; one scheduler dispatch, idempotent command |
| trade Web delivery | old `iran` server label | `WEB_DELIVERY_OWNER` capability |
| trade Telegram delivery | old `foreign` label | `TELEGRAM_OWNER` capability |
| connectivity monitor | old Web-Finland label | local Web/standby observability capability |
| market schedule | authoritative state and surface side effects split by host | one authoritative transition plus separately idempotent Web/Bot effects |
| sync worker | both old hosts | no internal Finland peer; keep only Finland↔Iran transport in section 2 |

The implementation must replace `SERVER_IRAN/SERVER_FOREIGN` checks that really
mean a capability, but preserve `home_site` where it is a business authority.
Blindly setting `SERVER_MODE=foreign` or enabling every job on the new server
would either suppress Web jobs or duplicate side effects.

## Process, poller and timer ownership

The exact current-task seed inventory is tracked in
`inventory/runtime-task-ownership.json`. It covers API leader coordination and
conditional jobs, Bot primary/publisher pollers, Queue-v1/OTP versus the mutually
exclusive legacy delivery set, Market capture/store/estimator transport, host
timers and the observed obsolete staging/three-site runtimes.

The target composition follows these rules:

1. `web_api` owns HTTP/WebSocket and one API leader but receives no Telegram
   credential merely because it shares a host with Bot.
2. `bot_primary` and optional publisher/executor roles form one disjoint
   `TELEGRAM_OWNER` set. Primary, Queue-v1 and legacy worker sets cannot overlap.
3. recurring business jobs call an authority-checked domain command; Redis leader
   election alone is execution coordination, not business authority.
4. Market capture, archive/store, estimator and snapshot/fact transport remain
   separate processes. Section 3 assigns their final capability and persistence
   roots before `P1-03` enables them.
5. staging and three-site processes observed on production hosts have target
   capability `NONE`, but only after traffic/write/credential/schedule proof and
   a separately approved decommission manifest.

`P1-03` must bind every target task seed to an exact compose service, image
digest, secret mount, DB/Redis pool, readiness probe and restart policy. An entry
without such a binding blocks runtime activation.

## Deterministic Finland merge contract

The future `P1-05` runner must execute this order on isolated clones:

1. freeze exact source snapshots and record schema/version/checksums;
2. select one canonical copy of each shared table after full parity/conflict
   analysis; never union both copies blindly;
3. import Web-local tables from Web-Finland and Bot-local tables from Bot-Finland;
4. process tables whose ownership is mixed through explicit mapping rules;
5. transfer media by content hash and prove every database reference resolves;
6. migrate Market archive/capture state under a separate replay/retention
   contract;
7. rebuild caches and internal sync bookkeeping rather than treating Redis or
   old watermarks as product truth;
8. validate FKs, unique keys, sequences, timestamps, business hashes,
   money/inventory/settlement invariants, outbox counts and side-effect dedupe;
9. run twice from clean input and once from a killed/resumed checkpoint; all
   business outputs and conflict reports must match;
10. keep both source snapshots immutable until the protected rollback window ends.

Any financial/inventory conflict, missing media, unknown writer or unexplained
row divergence is a blocker. There is no last-write-wins fallback.
