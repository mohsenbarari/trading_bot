# Market Traded Offer History Roadmap

## Goal

Show traded offers in the two-day read-only market history without changing
active-market behavior, fair-price validation, Web Push targeting, trade
execution, offer expiry, or sync write paths except for the explicit
cross-server read-model/tagging contract below.

The market must keep active offers as the only interactive cards. Historical
cards are read-only and visually distinguish why the offer left the active
market.

This branch also has a cross-server requirement: the same historical state must
be visible on both servers after sync, with the correct tag in both WebApp and
Telegram bot/channel surfaces. WebApp remains Iran-only and Telegram remains
foreign-only; the history state must travel through offer/trade sync, not by
making Iran call Telegram or by making foreign serve the WebApp.

## Non-Negotiable Safety Rules

- Do not include historical offers in active-offer price comparison logic.
- Do not include historical offers in market Web Push first-live-offer logic.
- Do not make expired or completed offers tradable from the frontend or backend.
- Do not revive expired offers when offer lifetime settings change.
- Keep buyer/seller/my-offers tabs limited to active offers unless explicitly
  changed later.
- Keep accountants blocked from market access.
- Keep customer tier users out of the two-day market history unless explicitly
  changed later.
- Do not introduce a separate cross-server "history state" source that can drift
  from `offers` and `trades`. The display state is derived from synced offer
  terminal fields plus completed trade aggregate unless a later design explicitly
  adds a reviewed materialized projection.
- Iran-side code must never update Telegram traded tags directly. When a
  WebApp/Iran offer becomes traded or partial-traded-expired, the foreign server
  must apply the synced state and perform the Telegram side effect there.
- Foreign-side code must not serve the WebApp history UI. When a Bot/foreign
  offer becomes traded or partial-traded-expired, Iran must apply the synced
  state and render the WebApp tag locally.
- Use staging validation before any main promotion.

## Definitions

| Display state | Database source | Meaning |
|---|---|---|
| `expired` | `offers.status = expired` and no completed trade quantity | Offer died naturally by time limit and no quantity traded. |
| `traded` | `offers.status = completed` | Offer was fully traded. |
| `traded_partial_expired` | `offers.status = expired` plus completed trade quantity > 0 | A retail offer was partially traded, then expired. |

`traded_quantity` must come from completed `trades` rows grouped by
`trades.offer_id`. Do not infer it only from `quantity - remaining_quantity`.
The API can represent `traded_partial_expired` as `history_state = traded` plus
`is_partially_traded = true`; the display contract still treats it as a distinct
visual state.

## Cross-Server Sync Contract

The new history state is a read model, not an independent business mutation.
Both servers must be able to derive the same tag from the same synced data.

Required synced inputs:

- `offers.status`: terminal state must sync for `completed` and `expired`.
- `offers.remaining_quantity`: required for active-card removal and consistency
  with completed/partial state.
- `offers.lot_sizes`: required so peer surfaces remove stale trade buttons after
  partial trades.
- `offers.expire_reason`: only `time_limit` expired offers are included in
  two-day market history.
- `offers.expired_at`, `offers.updated_at`, and `offers.created_at`: required
  for the history window and stable sorting.
- `trades.offer_id`, `trades.status`, `trades.quantity`, `trades.created_at`,
  and, if populated later, `trades.completed_at`: required for completed trade
  aggregate and event time.

Canonical derivation:

- `traded`: `offers.status = completed`, or a terminal expired offer with
  completed trade quantity greater than zero.
- `traded_partial_expired`: `offers.status = expired`,
  `expire_reason = time_limit`, completed trade quantity greater than zero, and
  completed trade quantity less than original offer quantity.
- `expired`: `offers.status = expired`, `expire_reason = time_limit`, and no
  completed trade quantity.
- API representation may keep `history_state = traded` for
  `traded_partial_expired`, but it must also set `is_partially_traded = true`
  and include the exact `traded_quantity`.

Timing and ordering rules:

- Offer and trade rows must both be synced. An offer terminal update without its
  completed trade rows is incomplete for history tagging.
- The peer must refresh or recompute history when either the offer terminal
  update or the related completed trade arrives.
- If sync arrival is temporarily out of order, the UI must converge to the final
  tag after both inputs arrive. Tests must cover offer-first and trade-first
  arrival.
- Current implementation uses `Trade.created_at` as the completed event time
  because immediate completed trades do not consistently set `completed_at`.
  Before changing that, either set `completed_at` reliably in all execution
  paths or keep `created_at` as the canonical history event time.
- `channel_message_id` remains foreign-owned. It must not be overwritten by
  offer sync from Iran, but the foreign server must still use the synced terminal
  state to update Telegram channel buttons and traded-post tags where required.

Surface tag contract:

- WebApp card tag:
  - `expired`: existing `منقضی` stamp.
  - `traded`: `معامله‌شده`.
  - `traded_partial_expired`: `معامله‌شده · {traded_quantity} عدد`.
- Telegram channel/bot tag:
  - Active offers keep trade buttons.
  - `completed` and time-limit expired offers with completed quantity must show
    a traded tag and have trade buttons removed.
  - Pure time-limit expired offers only need trade buttons removed. They do not
    need an expired tag on Telegram.
  - Editing the Telegram post text is required only for traded states:
    `completed` and `traded_partial_expired`.
  - This side effect runs only on foreign, even when the terminal mutation
    originated on Iran.

Known current code facts:

- `core/events.py` already includes `offers.status`, `remaining_quantity`,
  `lot_sizes`, `expire_reason`, `expired_at`, and trade quantity/status/time
  fields in sync payloads.
- `/api/sync/receive` orders `offers` before `trades`, defers FK failures, and
  strips incoming `channel_message_id` for offers so Telegram publication stays
  foreign-owned.
- `GET /api/offers/market-history` currently derives the history row from
  terminal offers plus completed trade aggregate.
- `MarketView.vue` still loads `/api/offers/expired`; frontend work must switch
  the history area to `/api/offers/market-history`.
- `OffersList.vue` currently treats only `status === 'expired'` as historical;
  it must use `is_read_only` and `history_state`.
- Telegram channel publish/update helpers currently remove buttons for inactive
  offers but do not yet provide a distinct traded tag/edit contract.

## Stage TH0 - Branch And Baseline

1. Work only on `candidate/market-traded-history`.
2. Confirm `main` and `origin/main` are aligned before starting implementation.
3. Confirm no unrelated local changes exist.
4. Keep existing open branches untouched.

Exit criteria:

- Branch is created from current `main`.
- Staging instructions document the branch intent.

## Stage TH1 - Backend Read Model

Status: Completed on 2026-06-18 in `candidate/market-traded-history`.

Add a dedicated read-only market-history query path, preferably a new endpoint:

```text
GET /api/offers/market-history?since_hours=48&skip=0&limit=25
```

The endpoint should return active-market-compatible offer cards with extra
history metadata:

```ts
history_state: 'expired' | 'traded'
history_label: string
traded_quantity: number
is_partially_traded: boolean
is_read_only: true
history_event_at: string
```

Query rules:

- Include only the last 48 hours.
- Include `offers.status = completed`.
- Include `offers.status = expired` with `expire_reason = time_limit`.
- For expired offers, include completed trade aggregate so partially traded
  retail offers can render as traded history.
- Sort by `history_event_at DESC`, where completed offers use latest completed
  trade time and expired offers use `expired_at`.
- Keep pagination with `skip` and `limit`.

Exit criteria:

- Existing `/api/offers/` active feed remains unchanged.
- Existing `/api/offers/expired` behavior remains backward compatible unless it
  is intentionally replaced after tests.

Implementation notes:

- Added `GET /api/offers/market-history`.
- The endpoint is read-only and returns terminal market history rows with
  `history_state`, `history_label`, `traded_quantity`, `is_partially_traded`,
  `is_read_only`, and `history_event_at`.
- The active feed `/api/offers/` and the legacy `/api/offers/expired` endpoint
  remain unchanged.
- Focused backend tests cover the query shape, customer-hidden guard, completed
  history metadata, and partially traded expired metadata.

## Stage TH2 - Backend Guards And Indexing

Status: Completed on 2026-06-18 in `candidate/market-traded-history`.

Add or confirm an efficient trade aggregate path:

- Prefer an index covering `trades.offer_id`, `trades.status`, and
  `trades.created_at` if missing.
- Ensure customer-chain trades are not double-counted; aggregate only rows whose
  `offer_id` is the source offer id.
- Ensure completed/cancelled/pending trade statuses are separated.

Exit criteria:

- Two-day history query does not scan unnecessary trade rows.
- Unit tests cover wholesale completed, retail fully completed, retail partial
  expired, and pure expired offers.

Implementation notes:

- Added `ix_trades_completed_offer_history` as a PostgreSQL partial index over
  `(offer_id, created_at DESC)` where `status = 'COMPLETED'` and `offer_id IS
  NOT NULL`.
- The model metadata now declares the same partial index.
- The existing `market-history` aggregate already counts only source-offer rows
  (`trades.offer_id IS NOT NULL`) and only completed trades
  (`trades.status = completed`), so customer-chain legs without `offer_id` are
  not double-counted.
- Focused tests now cover wholesale completed, retail fully completed, retail
  partial-expired, and pure expired history metadata.
- Alembic head advanced to `f5b6c7d8e9a0`.

## Stage TH3 - Frontend History State

Status: Completed on 2026-06-18 in `candidate/market-traded-history`.

Update `MarketView.vue` to load the new market-history endpoint for the read-only
two-day history area.

Rules:

- The main active offers list still comes from `/api/offers/`.
- History only appends in the `all` filter.
- Buyer, seller, and my-offers filters should not show expired/completed history.
- Refresh history on `offer:expired`, `offer:updated` with terminal status, and
  successful trade completion where needed.
- Refresh history on synced peer events too. Iran WebApp must refresh after a
  Bot/foreign offer becomes completed or partial-traded-expired and the synced
  state is applied locally.
- Use `/api/offers/market-history`; do not continue using `/api/offers/expired`
  for the new read-only history area once TH3 is implemented.

Exit criteria:

- No active-market interaction depends on history rows.
- Existing active-offer UX is unchanged.

Implementation notes:

- `MarketView.vue` now loads read-only two-day history from
  `/api/offers/market-history?skip=...&limit=25`.
- The active list still comes from `/api/offers/`; history rows are appended
  only while the `all` market filter is selected.
- Buyer, seller, and my-offers filters continue to render only active feed rows.
- Market history refreshes after `offer:expired`, `offer:completed`, terminal
  `offer:updated`, and successful child trade/cancel completion events.
- Incoming history rows are normalized to `is_read_only = true` unless the
  backend explicitly says otherwise.
- `OffersList.vue` received a minimal read-only guard so history rows cannot
  render trade or cancel controls before the dedicated TH4 visual stamps are
  implemented.
- Focused frontend tests cover endpoint usage, filter isolation, terminal
  realtime refresh behavior, customer/accountant exclusion, and read-only traded
  history rows.

## Stage TH4 - Offer Card UI

Status: Completed on 2026-06-18 in `candidate/market-traded-history`.

Update `OffersList.vue` so read-only history cards are driven by metadata, not
only by `status === 'expired'`.

Rendering rules:

- `history_state = expired`: show the existing expired stamp.
- `history_state = traded`: show a distinct traded stamp.
- Partial retail expired offer: show the same traded stamp plus traded quantity,
  for example `معامله‌شده · 23 عدد`.
- Remove all trade buttons and timers from read-only historical cards.
- Keep visual density close to the current compact market card style.

Exit criteria:

- Completed and partially traded-expired offers are visually distinct from pure
  expired offers.
- All historical cards are read-only in the DOM and not only visually disabled.
- The same WebApp tag is rendered for locally originated and peer-synced offers.
- A terminal offer never exposes trade/cancel controls after sync convergence.

Implementation notes:

- `OffersList.vue` now derives the history stamp from `history_state` and
  `is_partially_traded`, not only from `status`.
- `history_state = expired` renders the existing red `منقضی` stamp.
- `history_state = traded` renders a distinct `معامله‌شده` stamp.
- Partial traded-expired rows render `معامله‌شده · {traded_quantity} عدد`.
- Historical traded rows display `traded_quantity` in the quantity chip instead
  of showing `remaining_quantity = 0`.
- All read-only history rows keep timer classes/styles and trade/cancel controls
  out of the DOM.
- Focused `OffersList` tests cover pure expired, completed traded, and partial
  traded-expired history cards.

## Stage TH4B - Telegram Bot/Channel History Tag

Status: Completed on 2026-06-18 in `candidate/market-traded-history`.

Add the matching Telegram-side visual state for terminal offers. This stage is
foreign-only and must not introduce any Iran-to-Telegram path.

Rules:

- On the foreign server, when a synced or local offer becomes `completed`, remove
  channel trade buttons and edit/mark the post as `معامله‌شده`.
- When a time-limit expired offer has completed trade quantity greater than zero,
  remove buttons and edit/mark the post as
  `معامله‌شده · {traded_quantity} عدد`.
- When a time-limit expired offer has no completed trade quantity, remove buttons
  only. No Telegram expired tag or post-text edit is required for this state.
- If editing message text is too risky for the first slice, the roadmap must at
  minimum define a safe traded-tag mechanism before promotion. Removing buttons
  alone is enough for pure expired offers, but not for traded offers.
- Telegram update must be idempotent. Direct sync push and worker replay must not
  duplicate edits or create conflicting tag states.

Exit criteria:

- Foreign updates Telegram traded tags for both local Bot offers and synced
  WebApp/Iran offers.
- Foreign removes Telegram trade buttons, without requiring an expired tag, for
  pure time-limit expired offers.
- Iran never calls Telegram in tests or staging logs.
- Replaying the same terminal sync item does not duplicate Telegram side effects.

Implementation notes:

- Added `core/services/telegram_offer_channel_service.py` as the shared Telegram
  channel-offer renderer and side-effect gateway.
- The shared helper builds active channel text/keyboards and terminal history
  text from the same formatter used by channel publishing.
- Completed offers edit the Telegram channel post text with `معامله‌شده` and
  remove trade buttons.
- Time-limit expired offers with inferred completed quantity edit the channel
  post text with `معامله‌شده · {traded_quantity} عدد` and remove trade buttons.
- Pure time-limit expired offers only remove trade buttons; no Telegram expired
  text edit is performed.
- Terminal Telegram mutations are foreign-only. Helper calls return without
  Telegram I/O when `current_server() != foreign`.
- Replays are idempotent because Telegram `message is not modified` responses
  are treated as successful terminal convergence.
- `trade_execute`, API trade execution, auto-expiry, and sync terminal-offer
  convergence now use the shared helper.
- Focused backend tests cover formatter/tag contract, foreign-only guard,
  idempotent replay, pure-expired button removal, completed text edit, expiry
  side effects, sync terminal collection, and existing trade/offer helper paths.

## Stage TH5 - Realtime And Edge Cases

Validate realtime behavior:

- Full trade should remove the active offer and make it available in history.
- Partial retail trade should update remaining quantity while the offer is
  active.
- Partial retail offer should enter history as traded quantity when it later
  expires by time limit.
- Race between expiry worker and trade execution must preserve existing trade
  guards.
- Cross-server Bot offer -> WebApp history: a foreign-created offer that becomes
  completed must disappear from Iran active market and reappear in Iran history
  with `معامله‌شده`.
- Cross-server WebApp offer -> Telegram traded tag: an Iran-created offer that
  becomes completed must sync to foreign and update the Telegram traded
  tag/buttons there.
- Cross-server pure expiry: an offer that expires by time limit without completed
  quantity must remove Telegram buttons on foreign after sync, but must not
  require a Telegram expired tag/edit.
- Cross-server partial retail flow: a partial trade on one surface followed by
  time-limit expiry on the offer home server must sync both completed trade
  quantity and expired offer state so both surfaces render
  `معامله‌شده · {traded_quantity} عدد`.
- Sync race: test both arrival orders, offer terminal update before trade row and
  trade row before offer terminal update. The final rendered tag must converge
  after both sync inputs apply.

Exit criteria:

- No regressions in active offer removal, updated lots, or expiry archive.
- Both surfaces converge to the same history tag after sync, without making the
  wrong server perform the side effect.

## Stage TH6 - Sync And Read-Model Validation

Add focused tests for the cross-server contract before staging deployment.

Backend tests:

- `market-history` derives `traded`, `traded_partial_expired`, and `expired`
  from synced `offers` and `trades` rows.
- Completed trade aggregate counts only `trades.status = completed` and only
  source-offer rows where `trades.offer_id` is the offer id.
- Offer-first and trade-first sync arrival both converge to the same final
  history result.
- A synced terminal offer does not overwrite foreign `channel_message_id`.
- A replayed sync item does not duplicate Telegram traded-tag edits or button
  removal side effects.

Frontend tests:

- `MarketView.vue` loads `/api/offers/market-history`.
- `OffersList.vue` uses `history_state`, `traded_quantity`, and `is_read_only`
  for stamps and controls.
- Completed and partial-traded-expired cards are read-only in DOM and do not show
  trade or cancel buttons.

Bot/Telegram tests:

- The Telegram traded-tag formatter produces the same traded labels as the WebApp
  contract; pure expired Telegram state only requires button removal.
- Telegram update execution is blocked on Iran and allowed only on foreign.
- Local Bot-originated terminal offers and synced WebApp-originated terminal
  traded offers both update Telegram traded tags on foreign.

Exit criteria:

- Cross-server history/tag tests pass before any staging deploy.
- Tests prove the feature is derived from synced offer/trade data, not from a
  separate unsynced UI-only flag.

## Stage TH7 - Staging Validation

Run focused tests and deploy only to staging.

Suggested checks:

- Backend tests for history query classification and aggregate quantity.
- Sync tests for offer/trade terminal state and completed trade aggregate on both
  servers.
- Frontend tests for `OffersList` stamps and read-only controls.
- MarketView test for `all` filter history and buyer/seller/my tab exclusion.
- Bot/Telegram formatter or side-effect tests for traded tags and expired button
  removal.
- `git diff --check`.
- `npm run build` or focused frontend build/test if touched surface requires it.
- `scripts/deploy_staging.sh deploy`.
- `scripts/deploy_staging.sh health`.

Exit criteria:

- Staging confirms three scenarios:
  1. pure expired offer,
  2. completed wholesale offer,
  3. partial retail offer expired after trade.
  4. foreign/Bot-originated completed offer visible as traded in Iran WebApp
     history after sync.
  5. Iran/WebApp-originated completed offer tagged as traded by foreign
     Telegram after sync.

## Stage TH8 - Promotion Readiness

Before merging to `main`:

- Confirm no production deploy command was run from the candidate branch.
- Summarize staging validation and rollback path.
- Request explicit user approval for merge.
- Request explicit user approval for production deployment if needed.
