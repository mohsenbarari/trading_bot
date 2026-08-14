# Market A+C Lifecycle Evidence

Status: branch execution record for independent integration review
Authority: not Stage 8 closure, not merge, not deploy, not owner acceptance
Figma/Sites: unchanged DRAFT references only. No Code Connect claim.

## 1. Binding

| Field | Value |
| --- | --- |
| Branch | `feature/market-uiux-a-plus-c` |
| Required base | `2fdd9d515a5d739885a4b5c30bf4d763c5927bfc` / tree `bb8f233ad4a6692441198f09750d1274a894615e` |
| Ancestry | exact descendant of required `2fdd9d51` |
| Product commit | `8e759ab2ceb0bab384c34d8babe1d7611ede4484` / tree `9129ce6bcd90d77868a33e0f6ffa9d5ab7115daa` |
| Test/guard commit | `3fdceb9f11dc15925e8003c1bb311e44c3a0eed5` / tree `53ef64444a5d721085c89ca9e8128370f109b13d` |
| Stage 4 Market baseline | unchanged `162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058` |
| Frozen prior integration | `main-443ea5a-uiux-fed8fa49-market-integration` / `cff97c36…` |
| Frozen prior A+C overlay | `market-a-plus-c-visual-decision-clarity` / `e0b32d31…` / 162211 bytes |
| Successor disposition | `market-a-plus-c-lifecycle-clarity` / `3d512eac8b60c18e7c7139f8040ffcd0ff749853de117d4888754ca730a92b80` |
| Disposition overlay | 5 files / 19-file runtime / 163628 bytes / path-set `37aa0b51…` |

This receipt does not claim merge authority. After Stage 8 closes on `main`, this branch must merge that history and recompute hashes.

## 2. Historical clean-bound baseline (before this audit fix)

Keep this as the last clean run of the previous overlay. Do not treat it as the current candidate.

| Field | Value |
| --- | --- |
| Run | `market-ac-candidate-20260814T165141825Z` |
| Commit / tree | `00a84c520502de9966eccb21b8997595978bcc51` / `27a04c8cfd35c241b535ff3425d8dfebb90b4c1e` |
| Report SHA-256 | `1cb6bcfa72491719ce27d5ce153e18fac6054bed6d7f6ffe1996e9969dc0ebd2` |
| Dist | `d16ebc0c92e651eea7fdd3478d1d276b84528dd9aa5298901fd7c4146d66599b` |
| Scenarios | 80/80 |
| API | 1232 known / 0 unknown / 0 mutating |
| Screenshots in repo | 0 |

Corrections to that older note: the full suite was 163 files / 1866 tests, not «1663 فایل»; `deviceScaleFactor=2` is not 200% page zoom; do not claim every interactive ≥44px; do not claim loading/slow/stale unless those states actually ran.

## 3. Commits on this branch

1. `a8ba804d` `docs(ui): bind Market A+C proposal to current source`
2. `66e594f8` `refactor(ui): establish Market A visual hierarchy and C decision clarity`
3. `ddca65f1` `fix(ui): keep Market pending lot targets at 44px during confirm pulse`
4. `150bea27` `test(ui): protect Market A+C behavior and accessibility`
5. `00a84c52` `docs(ui): record Market A+C execution evidence`
6. `8e759ab2` `fix(ui): refine Market desktop lifecycle and trade perspective clarity`
7. `3fdceb9f` `test(ui): bind Market lifecycle accessibility and real zoom evidence`
8. this docs/memory commit

## 4. Changed files and why

| Path | Why |
| --- | --- |
| `MarketView.vue` | `--market-rail-max` cascade last at `60rem`; local 44px filter overlay; solid `--ds-primary-800` focus |
| `OffersList.vue` | offer-side vs user-action copy; horizontal deadline bar; overtime/final_tail/expired/traded chips; reduced-motion wins |
| `TradeLotSuggestionAlert.vue` | inverted responder recap/aria; solid focus |
| `OfferPreviewModal.vue` | «نوع لفظ شما» without inversion; solid focus |
| `AppOfferCard.vue` | unchanged in this audit fix; still default-off `decisionFocus` |
| Stage 4 guard + check + tests | successor `market-a-plus-c-lifecycle-clarity`; prior hashes stay frozen |
| Market/Offers/Alert/A+C contract tests | deadline source, inversion, cascade order, focus token |
| browser harness | geometry, lifecycle, named zoom/reflow/DPR, owner screenshots outside repo |

Unchanged on purpose: `AppFilterChips.vue`, `App.vue`, `main.css`, `useOffers.ts`, `settlementType.ts`, `offerLifecycle.ts`, Home, Messenger, Stage 8 matrix/receipts, visual-freeze JSON.

## 5. Product file SHA-256

| Path | SHA-256 |
| --- | --- |
| `frontend/src/views/MarketView.vue` | `5441b793a7ca2f50a34847775a24ab973f6433dfde72592aaae0640c4e4e68f2` |
| `frontend/src/components/OffersList.vue` | `310992fd5cb6a8197fa6c8c3f7293bcad9af1f6f39a7105f6ec4079d17c53a5e` |
| `frontend/src/components/TradeLotSuggestionAlert.vue` | `9674841528b6092832816744cf34e499b73b59e204503bfc5353ce965cab5452` |
| `frontend/src/components/OfferPreviewModal.vue` | `3278a01042eace0c754353a24a1de10afccd6e4c1899baa67ca927076a650a12` |
| `frontend/src/components/ui/AppOfferCard.vue` | `6c9844533065cb51603b9e55b9a22b8822cfeb2ebda24fde39a913079df970e6` |

## 6. Offer-side vs user-action

| Offer side | Visible badge | User action | Preview (own offer) |
| --- | --- | --- | --- |
| sell | فروش | خرید N عدد | not inverted |
| buy | خرید | فروش N عدد | «نوع لفظ شما: خرید» |

Payload, `/api/trades/`, idempotency, lot math, and two-tap are unchanged.

## 7. Geometry

| Viewport | Title | Header | Content | Composer | First card |
| --- | --- | --- | --- | --- | --- |
| 390×844 | 358 / left 16 | 358 / 16 | 390 / 0 | 358 / 16 | 358 |
| 1024×768 | 960 / 32 | 960 / 32 | 960 / 32 | 960 / 32 | 928 |
| 1440×900 | 960 / 240 | 960 / 240 | 960 / 240 | 960 / 240 | 928 |

1440 outer rail is `960±0`. Cards left the previous 448px mobile stretch. Document overflow 0. Filter-strip overflow is internal only.

## 8. Deadline measurements

| State | Source | Phase | Label | `--t-pct` |
| --- | --- | --- | --- | --- |
| normal | `normal_deadline_ts` + `timer_total_seconds` | normal | مهلت اصلی · ~30:00 | ~50 |
| critical normal | same, remaining/total < 15% | critical | مهلت اصلی · 3:20 | 5.53 |
| overtime | `final_deadline_ts` + `timer_total_seconds` | overtime | وقت اضافه · ~4:00 | ~80 |
| critical overtime | same window, remaining/total < 15% | overtime + critical | وقت اضافه | danger fill |
| final_tail | none | — | مهلت پایان یافته / در حال نهایی‌سازی | no bar |
| expired / traded | none | — | منقضی / معامله‌شده | no bar / no action |

Overtime progress resets on the new window; it does not continue the elapsed normal percent.

## 9. Focus and targets

Focus token: `--ds-primary-800` / `#92400e`, 2px solid, 2px offset.

| Adjacent surface | Contrast vs ring |
| --- | --- |
| white / modal | 7.09:1 |
| buy `#ecfdf5` | 6.73:1 |
| sell `#fef2f2` | 6.48:1 |
| overtime `#fffbeb` | 6.84:1 |
| expired `#f8fafc` | 6.78:1 |
| traded `#f0fdfa` | 6.80:1 |

All ≥ 3:1. Keyboard probe on a lot control recorded a solid 2px outline.

Targets: trade lots, own-offer cancel, preview close/actions, suggestion actions ≥44. `smallTradeTargetCount=0`. Shared `AppFilterChips` default remains 40px; Market-local overlay raises those chips to 44. Do not claim every interactive on the page is ≥44. Mobile `smallTargetCount` was 1 on some probes.

## 10. Zoom methodology

1. Page zoom: Chromium CSS `zoom=2` on `documentElement`. This is a layout-equivalent page zoom, not `deviceScaleFactor`.
2. Reflow: viewport `320×740` effective CSS width. No document overflow.
3. `deviceScaleFactor=2` is named `dpr-2-resolution` only and is not called 200% zoom.

## 11. Current browser candidate

Same-origin production build. No staging/production network. Fixture-bound `POST /api/trades/` is allowed only as a local fixture; mutating product count stayed 0.

| Field | Value |
| --- | --- |
| Run | `market-ac-candidate-20260814T183343974Z` |
| Dist SHA-256 | `7be414c8630ca82b253912e4f5fbcfb2362f7808a84a99e43e1fdfbb22733e17` (172 files) |
| Report SHA-256 | `be3f59b356f9bde78c6bbbfc9268b9c02c4b8690dc18e20710dcdbf162d80623` |
| Scenarios | 111/111 |
| API | 1705 known / 0 unknown / 0 mutating |
| Screenshots | 13 files outside the repo |

Covered: 360/375/390/414/430/768/1024/1440, loading, empty, dense, error, offline, closed, notice/admin/notify-off, normal buy/sell, critical normal, overtime buy/sell, critical overtime, final_tail, expired, traded, partial, traded-in-overtime, own offer, first tap, second-tap fixture, Escape, cancel, keyboard, preview, recent, scroll-end, reduced-motion overtime, CSS zoom 2, 320 reflow, DPR2 resolution, Home shared-consumer.

## 12. Tests and gates

| Gate | Result |
| --- | --- |
| Focused Market/Offers/Alert/A+C/guard | 77 passed in the last focused rerun; MarketView 37 and Alert 6 also passed earlier |
| Full serial Vitest | 163 files / 1874 tests passed |
| `npx vue-tsc --noEmit` | passed |
| Isolated production build | passed, `/tmp/market-a-plus-c-dist` |
| `npm run guard:ui` | passed, including lifecycle-clarity disposition |
| `git diff --check` | passed |
| `memory-custodian check` | run with the docs commit |

## 13. Memory

`docs/memory/areas/frontend-uiux.md` semantic-replaced the Market V2 entry only. Stage 6/7/8 facts were not rewritten.

## 14. Known limits

- Not mergeable until Stage 8-closed `main` is integrated and hashes are recomputed.
- Direction B remains rejected.
- Some non-trade controls can still be under 44px; report is scoped to `smallTradeTargetCount=0`.
- Screenshots stay outside the repo and must not be committed.
- Browser ran against the uncommitted overlay that became `8e759ab2` + `3fdceb9f`; source matches those commits.

## 15. Rollback

Do not merge the branch. Leave it unused. Stage 4 baseline and prior dispositions remain frozen, so `main` is unaffected.
