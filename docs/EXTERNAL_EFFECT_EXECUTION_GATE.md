# Term-scoped external-effect execution gate

This is a bounded P0 fence for provider-visible background work during the
three-site Writer Witness transition.  It is intentionally separate from the
database writer-term gate: a valid Writer Witness lease alone cannot establish
whether a Telegram/SMS/Web Push effect from an interrupted term was already
observed, should be reconciled, or must not be resent.

## Status and default

The gate is **disabled by default**.  No runtime deployment is enabled by this
code change.  When disabled, it does not open either a Writer Witness lease or
an external-effect authorization file, so legacy worker behavior is preserved.

When explicitly enabled, these settings are required:

```text
EXTERNAL_EFFECT_EXECUTION_GATE_ENFORCED=true
EXTERNAL_EFFECT_EXECUTION_GATE_LOCAL_SITE=webapp_fi|webapp_ir
EXTERNAL_EFFECT_EXECUTION_GATE_AUTHORIZATION_FILE=/absolute/root-only/path.json
```

The existing `APPLICATION_WRITER_TERM_*` policy must also be enabled and prove
the local active Writer Witness term.  The authorization-file owner is forced
to UID 0 by settings; a deployment cannot override it.

## Authorization receipt

`core.external_effect_execution_gate` accepts only a bounded, exact JSON
schema (`external-effect-execution-authorization-v1`) from a root-owned
`0600`, single-link regular file below controlled, non-symlink ancestors.
It rejects a missing file, symlink, unsafe ownership/mode, concurrent mutation,
duplicate JSON keys, malformed data, an expired/stale receipt, a near-expiry
receipt, or an authorization for another scope.

The receipt binds all of the following exactly:

- local holder site;
- Writer Witness epoch, lease ID, term issue/expiry timestamps, and Witness
  transition ID;
- a sorted allow-list of named effect scopes, with no wildcard;
- `reconciliation_complete_no_resend`; and
- an opaque SHA-256 reference to the independently retained reconciliation
  evidence, its completion time, and a short authorization lifetime.

The runtime validates the Writer Witness term immediately before and after it
reads the receipt.  If an atomic lease replacement occurs during that check,
the worker cycle fails closed rather than carrying the old receipt into the new
term.

The explicit
`write_external_effect_execution_authorization(...)` helper is only a
root-side atomic installer: it writes a fresh `0600` temporary file, fsyncs it,
atomically replaces the leaf, and fsyncs the parent directory.  Application
startup and workers never call it.  It does not contact Witness, promote either
site, reconcile provider state, send a request, or create an authorization.

The receipt is **not evidence by itself** that reconciliation happened.  A
reviewed root-side control-plane/operator must first perform and retain the
actual reconciliation/no-resend decision, then install the corresponding
short-lived receipt.  A new Writer Witness term always requires a fresh
receipt; an old epoch, holder, lease, transition, or term interval cannot be
reused.

## Concrete scopes currently fenced

With enforcement enabled, the following **named effect/claim boundaries**
refuse when their scope lacks a fresh receipt.  The default-disabled path is a
no-op; it does not read a Writer Witness term or an authorization file.

| Scope | Concrete integration |
| --- | --- |
| `telegram_notification_outbox_delivery` | lease recovery, cycle start, and each claim-and-deliver iteration |
| `telegram_admin_broadcast_delivery` | lease recovery, cycle start, and each claim-and-deliver iteration |
| `trade_webapp_delivery` | lease recovery, cycle start, and each receipt claim/delivery iteration |
| `trade_telegram_delivery` | lease recovery, cycle start, and each receipt claim/delivery iteration |
| `offer_telegram_publication` | publication reconciliation start, every enabled publication callback, channel-state cycle start, and immediately before every channel edit |
| `telegram_bot_runtime` | the pre-existing bot-runtime startup fence and watchdog; it is not permission for individual Bot API writes |
| `telegram_bot_api_effect` | exact provider-visible aiogram methods from the production `run_bot` session (`send*`, supported edits/deletes, callback answers, and the explicitly listed chat-message mutations), plus the direct trade-suggestion edit and repeat-offer send boundaries |
| `telegram_direct_notification_effect` | the direct `telegram_gateway.send_message` boundaries in `core.utils.send_telegram_notification` and the Foreign direct branch of `core.notifications.send_telegram_message` |
| `telegram_otp_delivery` | Foreign Telegram OTP: immediately before its one-shot Redis receipt claim and immediately before the gateway send |
| `sms_provider_delivery` | both concrete SMS.ir sync/async HTTP adapters; the Stage-6 OTP claim paths and claimed-provider marker; Invitation SMS's durable claim and immediately-before-sender recheck |
| `web_push_delivery` | before Web Push subscription delivery-state work and immediately before every `pywebpush.webpush` provider call |

The aiogram middleware uses an explicit method allow-list rather than treating
all Bot API traffic as a wildcard.  In particular, read-only long-poll traffic
such as `getUpdates` is not an external effect and continues to be governed by
the runtime/Writer Witness controls.  The trade-suggestion listener itself is
not a transport gate: it may listen and read events, but a refusal at an actual
Telegram edit/send boundary propagates rather than being converted into a
retryable listener error.

The current exact Bot API allow-list is:
`answerCallbackQuery`, `answerInlineQuery`, `banChatMember`, `deleteMessage`,
`editMessageCaption`, `editMessageLiveLocation`, `editMessageMedia`,
`editMessageReplyMarkup`, `editMessageText`, `pinChatMessage`,
`restrictChatMember`, `sendAnimation`, `sendAudio`, `sendChatAction`,
`sendContact`, `sendDice`, `sendDocument`, `sendLocation`, `sendMediaGroup`,
`sendMessage`, `sendPhoto`, `sendPoll`, `sendSticker`, `sendVenue`,
`sendVideo`, `sendVideoNote`, `sendVoice`, `stopPoll`, `unbanChatMember`, and
`unpinChatMessage`.

The offer-publication callback is wrapped only when the gate is enabled, which
keeps the disabled runtime's callable behavior unchanged.  All other listed
worker cycles invoke their no-op disabled gate directly.

## Deliberately out of scope for this P0

This is not a global interceptor for every possible external call.  In
particular, it deliberately does **not** cover:

- sync/transport traffic (including the Iran branch of
  `core.notifications.send_telegram_message`), object storage, or database
  replication;
- standalone scripts, independent `Bot` sessions, raw HTTP clients, or future
  workers that do not use one of the named boundaries above;
- Bot API methods not in the explicit production-session allow-list, including
  future methods until they are reviewed and added; or
- other direct `telegram_gateway` callers beyond the named notification and
  OTP adapters above.

Consequently, this gate must not be described as a generic sync/transport
solution or a blanket guarantee for arbitrary scripts.  Each newly introduced
provider effect needs its own audited boundary and named scope before it can
rely on this fence.

It also does not atomically join provider-side effects to database claims,
perform a Witness CAS/promotion, install a deployment mount, or replace
provider-specific deduplication/reconciliation.  Each remaining surface needs
its own audited adapter and, where appropriate, an additional scope before it
can rely on this fence.
