# Market A+C Proposal

Status: source-bound draft for `feature/market-uiux-a-plus-c`
Authority: visual/interaction only. Not Stage 8 closure, not merge, not deploy.
Figma/Sites: DRAFT reference only. No source mapping claimed.

## 1. Source snapshot

Recorded at worktree start, before product edits.

| Field | Value |
| --- | --- |
| Worktree | isolated Market branch worktree |
| Branch | `feature/market-uiux-a-plus-c` |
| Base / HEAD | `2fdd9d515a5d739885a4b5c30bf4d763c5927bfc` |
| Tree | `bb8f233ad4a6692441198f09750d1274a894615e` |
| Parent | `3fa04eb7847541714a47c7cfc63c3b8509ae953f` |
| Ancestry | exact descendant of required `2fdd9d51` |
| Porcelain at start | clean |
| Protection | `/market` = `FULL` / `protected-legacy` / `v2Scope: off` |
| Stage 4 Market runtime | 19 files, current disposition `main-443ea5a-uiux-fed8fa49-market-integration` |
| Market runtime hash | `cff97c36d965737605b80c098918c517999fb11f2c66108c2dae4573aac07867` |
| Stage 8B typography | marker only when `protection === NONE`; Market stays unmarked |
| Dist fingerprint used for baseline | `a55af89c8a388e583723e6217e3333d24188be47f0a51ed88496963e02939ca4` (171 files) |

Protected Market runtime files and SHA-256 at start:

| Path | SHA-256 | Bytes |
| --- | --- | --- |
| `frontend/src/views/MarketView.vue` | `a03b608c63d2fc4ae397399ffb1bb5cf9d2b88adf201e4cf4dd4cd3a981a8d11` | 55865 |
| `frontend/src/components/OffersList.vue` | `5e1d017e17f772e9a1621be54af16758128aaceb687942c123fc68bbfa21d6d9` | 42142 |
| `frontend/src/components/OfferPreviewModal.vue` | `8a8aa129152070e192876eb9924e56d860c60b610cc4b2695a929d0c0dfa3e42` | 11377 |
| `frontend/src/components/TradeLotSuggestionAlert.vue` | `5aff9633825d5b39559d0ac724e27b840cecd5a30cb7153e99b9783ea035b22e` | 10781 |
| `frontend/src/components/ui/AppOfferCard.vue` | `edf2a78ed0a556b4b5e6ae2dbb81c6499da305ef5e36fc2de26c5271e1fff864` | 739 |
| `frontend/src/components/ui/AppTradeActionButton.vue` | `2d1cfd29e943e2a060fd23781c4d4707364983844e62e376707f14474d38691f` | 543 |
| `frontend/src/composables/useOffers.ts` | `4ce35b122ccfe94bcdac910663b9409211cac50eedd4bc0e08293e6067865bec` | 12998 |
| `frontend/src/composables/useMarketRuntime.ts` | `3b6164b80ce335df453e6442a101ac49fcb24fae84780424ba2c7d17770f4a66` | 5736 |
| `frontend/src/utils/settlementType.ts` | `4b1648a7310806d4d4bee7e5b241af663c6c998aaa7dde279ebee63a3dc6e5af` | 761 |
| `frontend/src/router/uiRouteContract.ts` | `8e7495a05dfbf6b36bcc242af1b20919bef56a8bd0ed9458360d36cf79fba144` | 10039 |

`AppFilterChips.vue` SHA-256 `66c9f96d8bab76b8ff6a2b055b77f2b4e4645512650fc8fbf12096e3881a9920` — shared, default unchanged.

## 2. Current Market

Familiar single-column `/market`:

1. sticky header: notification toggle + three filter groups
2. open/closed notice
3. admin message
4. chronological offer feed
5. composer / recent-offers / send
6. offer preview modal
7. lot-suggestion Teleport
8. two-tap trade on the same lot button

Observed source facts:

- No persistent selected-offer state. Closest state is `pendingConfirm = "offerId:amount"` for 3 seconds.
- Quantity is a gray pill; remaining is not labeled as باقی‌مانده.
- Price is a number without «قیمت هر عدد» / «تومان».
- Lot buttons are `min-width: 50px` and `padding: 6px 10px` — below 44×44.
- Pending confirm only changes the same button text to `تایید N عدد؟`.
- Escape does not clear pending confirm.
- Buy/sell uses badge text plus color tokens; rail from direction A is absent.
- Desktop column uses global `--ds-page-max-width: 480px`.
- Overtime preference is not rendered in Market (`MarketView.test.ts` already forbids `.market-overtime-pref`).
- Filter copy is product contract: `همه / خریدار / فروشنده / لفظ‌های شما` and `همه تسویه‌ها / نقد حاضر / فردا`. Figma chips `خرید / فروش / نقدی` are not a defect.

## 3. Evidence-backed problems

These are from current source plus local same-origin production-build browser probes. Difference from Figma alone is not a defect.

| Finding | Evidence | Action |
| --- | --- | --- |
| Lot / trade targets below 44×44 | Baseline probe `smallTradeTargetCount` = 9 on a 4-offer normal card set; 51 on dense 18-offer set. CSS `padding: 6px 10px`. | C: raise Market-local lot targets to ≥44px |
| No Escape cancel for pending lot | Baseline interaction `escape-pending` failed: pending remained. Source has no keydown handler. | C: Escape/Cancel clears pending only |
| Selected offer is not visually distinct | Source: only the tapped button gets `.pending`. Card has no decision-focus. | C: card focus + recap after first tap |
| Amount / remaining / price are easy to mix | Quantity pill and price share one wrapped row without labels. | A readability + C recap after selection |
| Primary action and final confirm share one control with little recap | Second tap is the only confirm; no amount/price/side/result restatement. | C panel; keep two-tap contract |
| Desktop column stays phone-narrow | `--ds-page-max-width: 480px` on header/content/composer. | A: Market-local wider single column at ≥1024 |
| Telegram script fetch | Browser requested `https://telegram.org/js/telegram-web-app.js`. Existing shell companion, not a Market layout defect. | Harness fulfills locally; no product change |
| Overtime pending GETs | `/api/trades/overtime-requests/pending-*` from app shell. Must not appear in Market UI. | Fixture empty list; no Market render |

Not defects:

- Chronological mixed buy/sell feed
- Two-tap same-amount confirm
- `viewer_effective_price` authority
- Canonical تومان semantics
- Filter labels that differ from Figma
- Vazirmatn already on `body`; Market has no Stage 8B marker
- Home hero not using `OffersList`

## 4. Figma mapping (A base + C decision only)

File `z8jgJxST4O2APzWnlyP9gv`. Code Connect is not claimed.

| Node | Name | Use |
| --- | --- | --- |
| `650:143` | section | orientation only |
| `650:144` | board | orientation only |
| `653:185` | A mobile 390×844 | page structure |
| `652:143` | A desktop 1440×900 | one controlled column |
| `658:331` | C mobile | decision/amount clarity after action |
| `657:304` | C desktop | decision clarity only; not the split queue |

### From A — implement

- Keep chronological single feed
- Header + status + filters + notice + cards + composer
- Calm density, buy/sell rail + badge + text
- Desktop: one column ≈960px, not a dashboard
- Price and quantity more readable
- Semantic chips already exist; restyle locally

### From C — implement only after lot selection

- Selected card `is-decision-focus`
- Remaining box + selected amount as text and number
- 44px lot targets
- Separate recap: side, amount, price, expected result
- Hint: final confirm is the second tap of the same amount
- Other offers stay visible and usable
- Back/Cancel/Escape do not mutate

### From C — adapt, do not copy as business change

- C mock shows a focused “hero” card and peeks the next offer. That would change browse model. Keep A feed; only highlight the pending card.
- C mock lot set `1 / 2 / 4` is illustrative. Keep `getLotButtons()` contract.
- C mock “بعدی” composer button is not current product. Keep send + recent-offers.
- C desktop two-column queue is rejected with direction B.

### From B — explicitly out

- Buy/sell column split
- Dense dashboard
- Removing chronological order
- Changing browse model

## 5. Files required

Expected product edits (minimum):

- `frontend/src/views/MarketView.vue` — A header/title/column; preview scroll restore
- `frontend/src/components/OffersList.vue` — A card hierarchy + C decision panel
- `frontend/src/components/ui/AppOfferCard.vue` — default-off `decisionFocus`
- `frontend/src/components/TradeLotSuggestionAlert.vue` — 44px + recap + Escape
- `frontend/src/components/OfferPreviewModal.vue` — clearer recap + Escape

Guard / tests / docs:

- `frontend/scripts/lib/stage4-protected-surface-guard.mjs` — new A+C disposition after evidence
- `frontend/scripts/check-stage4-protected-surfaces.mjs`
- `frontend/scripts/stage4-protected-surface-guard.test.mjs`
- `frontend/src/components/OffersList.test.ts`
- `frontend/src/views/MarketView.test.ts`
- `frontend/src/components/TradeLotSuggestionAlert.test.ts`
- `frontend/scripts/lib/market-a-plus-c-browser.mjs`
- `frontend/scripts/market-a-plus-c-browser.mjs`
- this proposal + later evidence note

Do not edit: `AppFilterChips.vue`, `App.vue`, `main.css`, `useOffers.ts`, `settlementType.ts`, Stage 8 matrix/receipts, `VISUAL_FREEZE_PROTECTED_SURFACES.json`, Home `DashboardView.vue`.

## 6. Shared consumers

| Component | Consumers | Strategy |
| --- | --- | --- |
| `AppFilterChips` | Market, Notifications, Customer/Accountant workspace, PublicProfile | no component change; Market already has local classes |
| `AppOffer*` / `AppTradeActionButton` | `OffersList` only | Market-owned; additive default-off props only |
| `AppSettlementBadge` | OffersList + OfferPreviewModal | no semantic change |
| Home MIXED hero | `DashboardView` `home-market-widget` | untouched |
| Messenger / Share Receive | none of these primitives | untouched |

## 7. Protected boundaries

- Keep `protection: FULL` and `v2Scope: off`
- Do not rewrite Stage 4 baseline hash `162e9e61…`
- Add a third Market disposition after implementation + evidence
- Do not expand Stage 8B marker to FULL
- Do not change `body`, `#app`, `.app-shell`, `font-sans`
- Do not render overtime preference in Market
- Do not change trade POST payload, idempotency, lot math, or preview/create offer API

## 8. Component strategy

- No new shared primitive unless an existing one cannot carry a default-off prop
- `AppOfferCard` gets `decisionFocus?: boolean = false`
- Lot button styles stay in `OffersList` scoped CSS (already owns `.trade-btn`)
- Suggestion alert stays Teleport-to-body; add recap locally
- Preview stays in-tree dialog; add recap locally
- No `:has()` for core logic
- No fragile absolute card layout; Figma absolute frames are reference only

## 9. Typography strategy

- Figma uses Vazirmatn. `body` already loads Vazirmatn.
- Market remains FULL and must not receive `app-route--persian-typography`
- No Market-local font-family bridge in this rollout
- LTR/mono/numeric exceptions stay as they are
- Teleports do not get a new typography root

## 10. Teleport strategy

| Teleport | Change |
| --- | --- |
| recent-offers dropdown | keep; no typography class |
| TradeLotSuggestionAlert | keep; C recap + 44px + Escape close (no mutation) |
| OfferPreviewModal | not Teleport; Escape cancel already non-mutating |

## 11. Trade-safety invariants

Unchanged:

- chronological order
- buy/sell meaning and `viewer_effective_price`
- canonical تومان
- remaining vs total quantity rules
- lot button set from `getLotButtons`
- first tap pending 3s, second tap same amount executes
- in-flight lock, idempotency key, uncertain recovery
- own-offer cancel stays DELETE and destructive
- preview confirm lock
- cancel / Escape / Back never POST trade
- no overtime UI on Market
- no new endpoint or payload

If C visual language conflicts with lot contract, the contract wins.

## 12. Accessibility acceptance

- WCAG 2.2 AA
- Main trade lots and own-offer cancel ≥44×44
- Accessible name on every trade control (side + amount + commodity)
- Buy/sell not color-only (badge text + rail + name)
- focus-visible ≥3:1
- Tab order preserved
- Escape clears pending / closes dialog without mutation
- No nested interactive
- RTL preserved
- 200% zoom on 390 keeps controls
- reduced-motion disables pulse/ring animation
- live region for pending recap

## 13. Visual acceptance

- Still looks like current Market, calmer
- One column on desktop
- Selected pending card is obvious
- Remaining and selected amount are readable
- Confirm recap restates amount, price, side, expected result
- Other cards remain in the list

## 14. Rollback

1. Do not merge the branch
2. Delete the worktree / leave the branch unused
3. Stage 4 baseline constants stay immutable, so `main` is unchanged
4. If a disposition was added, revert that commit to restore the previous integration hash

## 15. Commit plan

1. `docs(ui): bind Market A+C proposal to current source`
2. `refactor(ui): establish Market A visual hierarchy and C decision clarity`
3. `test(ui): protect Market A+C behavior and accessibility`
4. `docs(ui): record Market A+C execution evidence`

Guard hash update stays in the test/guard commit, not mixed into an unexplained product dump.

## 16. Explicitly out of scope

- Direction B
- C split-queue browse model
- C “بعدی” composer
- Changing filter labels to Figma copy
- Market-local Vazirmatn marker
- Overtime on Market
- API / store / payload / permission changes
- Stage 8 matrix, receipts, visual-freeze JSON, acceptanceAuthority
- Home hero, Messenger, Share Receive
- Push, staging, production, Figma, Sites
- Final acceptance or merge authority

## 17. Baseline browser note

Local production build + same-origin fixtures. No staging/production network. No product POST/PUT/PATCH/DELETE except fixture `POST /api/offers/parse` for non-final preview. Trade second tap was not executed.

Clean same-origin matrix after classifying the existing Telegram companion script and empty shell GETs:

| Field | Value |
| --- | --- |
| Run id | `market-ac-baseline-20260814T162915435Z` |
| Scenarios | 80 |
| Passed | 79 |
| Failed | 1 (`escape-did-not-clear-pending`) |
| Viewports | 8 |
| API requests | 1221 known / 0 unknown / 0 mutating |
| Screenshots retained in repo | 0 |

Probe facts on normal 390:

- 4 offer cards; dense 18; empty 0
- `smallTradeTargetCount = 9` (lot controls below 44px)
- `decisionPanelVisible = false`
- first tap sets pending; preview opens from parse fixture
- no document overflow, no overtime control, no Stage 8B marker
- Home shared-consumer scenario passed (no Market offer cards)
