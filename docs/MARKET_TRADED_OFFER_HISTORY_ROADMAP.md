# Market Traded Offer History Roadmap

## Goal

Show traded offers in the two-day read-only market history without changing
active-market behavior, fair-price validation, Web Push targeting, trade
execution, offer expiry, or sync write paths.

The market must keep active offers as the only interactive cards. Historical
cards are read-only and visually distinguish why the offer left the active
market.

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
- Use staging validation before any main promotion.

## Definitions

| Display state | Database source | Meaning |
|---|---|---|
| `expired` | `offers.status = expired` and no completed trade quantity | Offer died naturally by time limit and no quantity traded. |
| `traded` | `offers.status = completed` | Offer was fully traded. |
| `traded_partial_expired` | `offers.status = expired` plus completed trade quantity > 0 | A retail offer was partially traded, then expired. |

`traded_quantity` must come from completed `trades` rows grouped by
`trades.offer_id`. Do not infer it only from `quantity - remaining_quantity`.

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

## Stage TH3 - Frontend History State

Update `MarketView.vue` to load the new market-history endpoint for the read-only
two-day history area.

Rules:

- The main active offers list still comes from `/api/offers/`.
- History only appends in the `all` filter.
- Buyer, seller, and my-offers filters should not show expired/completed history.
- Refresh history on `offer:expired`, `offer:updated` with terminal status, and
  successful trade completion where needed.

Exit criteria:

- No active-market interaction depends on history rows.
- Existing active-offer UX is unchanged.

## Stage TH4 - Offer Card UI

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

## Stage TH5 - Realtime And Edge Cases

Validate realtime behavior:

- Full trade should remove the active offer and make it available in history.
- Partial retail trade should update remaining quantity while the offer is
  active.
- Partial retail offer should enter history as traded quantity when it later
  expires by time limit.
- Race between expiry worker and trade execution must preserve existing trade
  guards.

Exit criteria:

- No regressions in active offer removal, updated lots, or expiry archive.

## Stage TH6 - Staging Validation

Run focused tests and deploy only to staging.

Suggested checks:

- Backend tests for history query classification and aggregate quantity.
- Frontend tests for `OffersList` stamps and read-only controls.
- MarketView test for `all` filter history and buyer/seller/my tab exclusion.
- `git diff --check`.
- `npm run build` or focused frontend build/test if touched surface requires it.
- `scripts/deploy_staging.sh deploy`.
- `scripts/deploy_staging.sh health`.

Exit criteria:

- Staging confirms three scenarios:
  1. pure expired offer,
  2. completed wholesale offer,
  3. partial retail offer expired after trade.

## Stage TH7 - Promotion Readiness

Before merging to `main`:

- Confirm no production deploy command was run from the candidate branch.
- Summarize staging validation and rollback path.
- Request explicit user approval for merge.
- Request explicit user approval for production deployment if needed.
