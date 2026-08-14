# Market A+C Execution Evidence

Status: branch execution record for integration review
Authority: not Stage 8 closure, not merge, not deploy, not owner acceptance
Figma/Sites: unchanged DRAFT references only. No Code Connect claim.

## 1. Binding

| Field | Value |
| --- | --- |
| Branch | `feature/market-uiux-a-plus-c` |
| Base commit | `2fdd9d515a5d739885a4b5c30bf4d763c5927bfc` |
| Base tree | `bb8f233ad4a6692441198f09750d1274a894615e` |
| Ancestry | exact descendant of required `2fdd9d51` |
| Product/test HEAD when this note was written | `150bea27d078521c89eb08d5fb5704c4bd5da48e` |
| Product/test tree | `57641572c45933bef1308f171f1f7fd4114cf9f1` |
| Stage 4 Market baseline | unchanged `162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058` |
| Previous Market integration disposition | frozen `main-443ea5a-uiux-fed8fa49-market-integration` / `cff97c36…` |
| New Market disposition | `market-a-plus-c-visual-decision-clarity` / `e0b32d312b578fd6698beefb68e6d2a17c6c8efe024d408b917a05eb0dd5a531` |
| Disposition overlay | 5 files / 19-file runtime / 162211 bytes / path-set `37aa0b51…` |

This receipt does not claim merge authority. After Stage 8 closes on `main`, this branch must merge that history and rebaseline.

## 2. Commits on this branch

1. `a8ba804d` `docs(ui): bind Market A+C proposal to current source`
2. `66e594f8` `refactor(ui): establish Market A visual hierarchy and C decision clarity`
3. `ddca65f1` `fix(ui): keep Market pending lot targets at 44px during confirm pulse`
4. `150bea27` `test(ui): protect Market A+C behavior and accessibility`
5. memory + this evidence note (same docs commit)

## 3. Changed files and why

| Path | Why |
| --- | --- |
| `MarketView.vue` | A: title, open/closed chip, feed heading, desktop single column, preview scroll restore |
| `OffersList.vue` | A: remaining/price labels, buy/sell rail; C: decision panel, 44px lots, Escape/cancel, aria names |
| `AppOfferCard.vue` | default-off `decisionFocus` |
| `TradeLotSuggestionAlert.vue` | C recap, 44px, Escape close, token colors |
| `OfferPreviewModal.vue` | recap of type/amount/price/result, Escape cancel, 44px close |
| Stage 4 guard + check + tests | third disposition; baseline and prior integration stay frozen |
| Market/Offers/Alert/AppPrimitives tests + `MarketAPlusCContract.test.ts` | behavior, a11y, shared-consumer, bypass rejection |
| browser harness | candidate assertions for title, decision panel, recap, 44px |
| proposal + this evidence | source-bound notes only |
| `.gitignore` | allow only these two Market refactor docs |
| `docs/memory/areas/frontend-uiux.md` | semantic-merge of Market direction; Stage 6/7/8 facts kept |

Unchanged on purpose: `AppFilterChips.vue`, `App.vue`, `main.css`, `useOffers.ts`, `settlementType.ts`, Home `DashboardView.vue`, Stage 8 matrix/receipts, visual-freeze JSON.

## 4. A/C mapping

From A:

- chronological single feed
- header / status / filters / notices / cards / composer
- buy/sell rail + badge + text
- calmer metrics: باقی‌مانده / قیمت هر عدد / تومان
- desktop one column, not a split dashboard

From C, only after a lot is chosen:

- selected card `data-decision-focus="true"`
- recap of side, amount, price, remaining, expected result
- 44px lot targets
- second tap still required; pending copy stays `تایید N عدد؟`
- Escape / انصراف clear pending only
- other cards stay visible

From B, not implemented:

- buy/sell columns
- dense dashboard
- browse-model rewrite
- C split queue / hero-only feed
- composer «بعدی»
- Figma filter copy (`خرید/فروش/نقدی`)
- overtime on Market

## 5. Trade-safety invariants kept

- chronological order
- `viewer_effective_price` and canonical تومان
- remaining vs total quantity rules
- `getLotButtons()` set
- first tap pending 3s, second same amount POSTs `/api/trades/`
- in-flight lock and idempotency key
- own-offer cancel remains destructive DELETE
- preview `confirmClickLocked`
- Escape/cancel never POST
- no new endpoint or payload
- `/market` stays `FULL` / `protected-legacy` / `v2Scope: off`

## 6. Shared consumers

- `AppFilterChips` SHA-256 `66c9f96d8bab76b8ff6a2b055b77f2b4e4645512650fc8fbf12096e3881a9920` unchanged
- Home hero does not mount `OffersList` / `AppOfferCard`
- Messenger and Share Receive untouched
- Stage 8B marker still NONE-only; Market has no local font bridge

## 7. Browser

Same-origin production build and fixtures. No staging/production network. No product POST/PUT/PATCH/DELETE except fixture `POST /api/offers/parse` for non-final preview.

| Run | Result |
| --- | --- |
| Baseline `market-ac-baseline-20260814T162915435Z` | 80 scenarios / 79 pass / 1 fail (`escape-did-not-clear-pending`); idle `smallTradeTargetCount` 9 |
| Candidate `market-ac-candidate-20260814T164100783Z` | 80 / 80 / 0; title present; first-tap shows decision panel; Escape clears pending; preview recap visible; idle lots ≥44px; Home has 0 Market offer cards |

Candidate request counts: 1205 known / 0 unknown / 0 mutating. Dist fingerprint `8a823cd7d993a8c9c62b2a5077aed8f355d314916651a257f3188ef9109499f9` (171 files). Screenshots retained in repo: 0.

Viewports: 360×740, 375×812, 390×844, 414×896, 430×932, 768, 1024, 1440×900, plus 200% on 390 and reduced motion.

States covered: normal, empty, dense, error, offline, closed, admin, notify-off, notice, first-tap, escape-pending, recent-offers, preview, keyboard, zoom-200, reduced-motion, home shared-consumer.

200% zoom and reduced-motion scenarios passed. RTL present. No document overflow. No Stage 8B marker on Market. No overtime control on Market.

## 8. Tests and gates

| Gate | Result |
| --- | --- |
| Focused Market/Offers/Alert/AppPrimitives/MarketView/A+C contract | 103 passed |
| Home `DashboardView` + `App` typography | 43 passed |
| Full serial frontend Vitest before guard update | 1862 passed / 2 failed, both exact Stage 4 Market disposition tests |
| Those two tests after A+C disposition | passed; Stage 4 file 20/20 |
| `npx vue-tsc --noEmit` | passed |
| `npm run build` to an isolated out dir | passed, 171 files |
| `npm run guard:ui` | passed, including A+C disposition and Stage 8B NONE-only typography |
| `git diff --check` | passed |

## 9. Memory

`docs/memory/areas/frontend-uiux.md` semantic-merged the V2 direction entry:

- Market direction = A base + C decision/amount clarity
- B rejected for current rollout
- Market remains protected; source/browser rebaseline is mandatory

Stage 6/7/8 facts were not rewritten. `memory-custodian check` passed.

## 10. Known limits

- Receipt is not final acceptance and must not be merged until Stage 8 closed `main` is integrated and gates re-run.
- Two-tap same-control confirm is unchanged product contract; C mock “بعدی” / split queue were not copied.
- Candidate browser ran on the product overlay before the later test/guard commit; clean-bound revalidation is required on the final docs commit.
- Pulse feedback is opacity-only so pending lots do not shrink below 44px.

## 11. Rollback

Do not merge the branch. Leave it unused. Stage 4 baseline constants remain the previous immutable hashes, so `main` is unaffected.
