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
| Current product/test/guard commit | `2e687b6b5b1a295774b1162468350443224f3376` / tree `a05a0354049e6e57dc97bf2045e704825e20d0f0` |
| Stage 4 Market baseline | unchanged `162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058` |
| Frozen prior integration | `main-443ea5a-uiux-fed8fa49-market-integration` / `cff97c36…` |
| Frozen prior A+C overlay | `market-a-plus-c-visual-decision-clarity` / `e0b32d31…` / 162211 bytes |
| Prior successor disposition | `market-a-plus-c-lifecycle-clarity` / `3d512eac8b60c18e7c7139f8040ffcd0ff749853de117d4888754ca730a92b80` |
| Current successor disposition | `market-a-plus-c-perimeter-deadline-hourglass` / `f7bb91fa8b777d397cb0c51aa15481eb1eef5b83fa56383b478088e33fdfc4d8` |
| Current disposition overlay | 5 files / 19-file runtime / 165085 bytes / path-set `37aa0b51…` |

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
8. `1bfa389f` `docs(ui): record corrected Market A+C lifecycle evidence`
9. `2e687b6b` `refactor(ui): restore Market perimeter deadline motion`
10. this docs/memory commit

## 4. Changed files and why

| Path | Why |
| --- | --- |
| `MarketView.vue` | `--market-rail-max` cascade last at `60rem`; local 44px filter overlay; solid `--ds-primary-800` focus |
| `OffersList.vue` | offer-side vs user-action copy; compact accessible hourglass sticker; overtime/final_tail/expired/traded states; reduced-motion wins |
| `TradeLotSuggestionAlert.vue` | inverted responder recap/aria; solid focus |
| `OfferPreviewModal.vue` | «نوع لفظ شما» without inversion; solid focus |
| `AppOfferCard.vue` | server-authoritative SVG perimeter progress around the full card; still default-off unless a timer exists |
| Stage 4 guard + check + tests | successor `market-a-plus-c-perimeter-deadline-hourglass`; all prior hashes stay frozen |
| Market/Offers/Alert/A+C contract tests | deadline source, perimeter, accessible sticker, inversion, cascade order, focus token |
| browser harness | full-card perimeter/sticker geometry, lifecycle, named zoom/reflow/DPR, screenshots outside repo |

Unchanged on purpose: `AppFilterChips.vue`, `App.vue`, `main.css`, `useOffers.ts`, `settlementType.ts`, `offerLifecycle.ts`, Home, Messenger, Stage 8 matrix/receipts, visual-freeze JSON.

## 5. Product file SHA-256

| Path | SHA-256 |
| --- | --- |
| `frontend/src/views/MarketView.vue` | `5441b793a7ca2f50a34847775a24ab973f6433dfde72592aaae0640c4e4e68f2` |
| `frontend/src/components/OffersList.vue` | `61b7f6f9d662ba8160b4dd27e908f5fed1781480305e6ca4a56f906abb455cd1` |
| `frontend/src/components/TradeLotSuggestionAlert.vue` | `9674841528b6092832816744cf34e499b73b59e204503bfc5353ce965cab5452` |
| `frontend/src/components/OfferPreviewModal.vue` | `3278a01042eace0c754353a24a1de10afccd6e4c1899baa67ca927076a650a12` |
| `frontend/src/components/ui/AppOfferCard.vue` | `29dc50030550476956345b3bef54b9faef736cbe9e937be91a2bbc2df15a3fb2` |

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
| normal | `normal_deadline_ts` + `timer_total_seconds` | normal | مهلت اصلی · ~30:00 | ~50% of perimeter |
| critical normal | same, remaining/total < 15% | critical | مهلت اصلی · 3:20 | 5.53% danger perimeter |
| overtime | `final_deadline_ts` + `timer_total_seconds` | overtime | ~4:00 باقی‌مانده + hourglass sticker | ~80% warning perimeter |
| critical overtime | same window, remaining/total < 15% | overtime + critical | countdown + hourglass sticker | danger perimeter |
| final_tail | none | — | مهلت پایان یافته / در حال نهایی‌سازی | no perimeter |
| expired / traded | none | — | منقضی / معامله‌شده | no perimeter / no action |

Overtime progress resets on the new window; it does not continue the elapsed normal percent. The hourglass uses one restrained 3.6s two-dimensional turn and becomes static under `prefers-reduced-motion: reduce`; the explicit «وقت اضافه» text is retained as its accessible name, not duplicated visually in the header.

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
| Run | `market-ac-candidate-20260814T192603756Z` |
| Clean commit / tree | `2e687b6b5b1a295774b1162468350443224f3376` / `a05a0354049e6e57dc97bf2045e704825e20d0f0` |
| Dist SHA-256 | `f0ae7cae537fc12d1d38f1c78e37e7f2cc4394f34cedcfee97e2f29f7f040068` (172 files) |
| Report SHA-256 | `60697078cd8ca862d799cc887ced3adcdb22a4c81685d701bbd14f35f7cca4e6` |
| Scenarios | 111/111 |
| API | 1699 known / 0 unknown / 0 mutating |
| Diagnostics | 0 console / 0 page / 0 request failure / 0 external |
| Screenshots | 13 files outside the repo |

Covered: 360/375/390/414/430/768/1024/1440, loading, empty, dense, error, offline, closed, notice/admin/notify-off, normal buy/sell, critical normal, overtime buy/sell, critical overtime, final_tail, expired, traded, partial, traded-in-overtime, own offer, first tap, second-tap fixture, Escape, cancel, keyboard, preview, recent, scroll-end, reduced-motion overtime, CSS zoom 2, 320 reflow, DPR2 resolution, Home shared-consumer.

## 12. Tests and gates

| Gate | Result |
| --- | --- |
| Focused Market/Offers/A+C/guard | 4 files / 90 tests passed |
| Full serial Vitest | 163 files / 1875 tests passed |
| `npx vue-tsc --noEmit` | passed |
| Isolated production build | passed, `/tmp/market-a-plus-c-dist` |
| `npm run guard:ui` | passed, including perimeter-deadline-hourglass disposition |
| `git diff --check` | passed |
| `memory-custodian check` | run with the docs commit |

## 13. Memory

`docs/memory/areas/frontend-uiux.md` semantic-replaced the Market V2 entry only. Stage 6/7/8 facts were not rewritten.

## 14. Known limits

- Not mergeable until Stage 8-closed `main` is integrated and hashes are recomputed.
- Direction B remains rejected.
- Some non-trade controls can still be under 44px; report is scoped to `smallTradeTargetCount=0`.
- Screenshots stay outside the repo and must not be committed.
- Browser ran after commit against clean `2e687b6b`; source and dist stayed bound to that snapshot.

## 15. Rollback

Do not merge the branch. Leave it unused. Stage 4 baseline and prior dispositions remain frozen, so `main` is unaffected.
