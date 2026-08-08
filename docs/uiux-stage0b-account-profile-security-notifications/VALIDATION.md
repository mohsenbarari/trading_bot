# Stage 0B-5 validation plan and record

تاریخ: ۲۰۲۶-۰۸-۰۸

وضعیت: **شواهد فنی کامل و در انتظار تأیید بصری مالک محصول.** Phase 0، Figma canonical، exportهای مستقیم، harness محلی و Sites خصوصی ثبت و راستی‌آزمایی شده‌اند؛ runtime implementation انجام یا مجاز نشده است.

## دامنه نهایی

- ۱۰ root موبایل دقیق `390×844`؛
- پنج proof دقیق `360 / 375 / 390 / 414 / 430 × 844`؛
- یک proof امنیت/نشست دقیق `1440×900`؛
- state atlas حساب، پروفایل، نشست، حافظه، اعلان و Push؛
- route و visibility matrix؛
- invariant پوسته `0B-2` با active destination برابر `حساب`؛
- content-necessity، privacy، action-truth، protected-surface، font، contrast، geometry و responsive audit؛
- داده کاملاً synthetic؛
- صفر تغییر runtime تا تأیید صریح `0B-6`.

## provenance و freeze نهایی Figma

| فیلد | مقدار |
| --- | --- |
| file | `z8jgJxST4O2APzWnlyP9gv` |
| page | `117:2` — `04 — Stage 0B-5 Account, Profile, Security & Notifications` |
| sectionها | `117:3` تا `117:8` |
| freeze | `2026-08-08T17:10:58.500Z` |
| audit | schema `2` در `2026-08-08T17:11:05.475Z` |
| capture مستقیم | `2026-08-08T17:13:12.738Z` |
| design baseline commit | `fa2a0a42934493752e7a7106e4dd10f168eb16d7` |
| audit metrics | SHA-256 `351f6afafb0e2d3b1a08e908dcd88cb72d9d2fd4fed8110c3fb22c12c6658d94` |

audit مستقیم نتیجه `27 / 27`، صفر blocker، صفر overflow، صفر text clip، صفر font/contrast failure و `142` semantic target با کمینه `44×44px` را ثبت کرد. همه متن‌های محصول Vazirmatn هستند.

## ۱۰ سناریوی موبایل — نهایی

| شناسه | root | عنوان | وضعیت |
| --- | --- | --- | --- |
| `0B5-M01` | `128:3` | مرکز حساب کاربر عادی | final |
| `0B5-M02` | `128:64` | مرکز حساب حسابدار | final |
| `0B5-M03` | `128:111` | پروفایل شخصی | final |
| `0B5-M04` | `129:132` | ویرایش avatar/address و feedback | final |
| `0B5-M05` | `129:187` | پروفایل عمومی و حریم خصوصی | final |
| `0B5-M06` | `131:823` | نشست‌های فعال | final |
| `0B5-M07` | `131:881` | تصمیم پایان نشست/نشست‌های دیگر | final |
| `0B5-M08` | `131:935` | حافظه محلی و پاک‌سازی | final |
| `0B5-M09` | `132:1627` | مرکز اعلان و realtime-new | final |
| `0B5-M10` | `132:1683` | Push قابل اقدام | final |

responsive rootها به‌ترتیب `141:456`، `141:512`، `141:568`، `141:624` و `141:680` هستند. desktop root دقیق `143:668` روی board `143:632` قرار دارد و همان ۱۹ fact موبایل را بدون KPI یا metadata تازه نگه می‌دارد.

## قرارداد exact assertions — پاس نهایی

harness و audit مستقیم دقیقاً همین ۲۷ شناسه را با همین ترتیب تولید کرده‌اند:

| ترتیب | assertion ID | معیار خروج | نتیجه |
| --- | --- | --- | --- |
| 1 | `font-vazirmatn-loaded` | همه متن‌ها Vazirmatn و چهار face محلی load شده | passed |
| 2 | `ten-mobile-scenarios-complete` | دقیقاً ۱۰ root canonical | passed |
| 3 | `mobile-roots-exact-390x844` | هر ۱۰ root دقیقاً `390×844` | passed |
| 4 | `no-product-overflow-or-clipping` | صفر overflow و clipping | passed |
| 5 | `touch-targets-44` | targetها حداقل `44×44` | passed |
| 6 | `cta-height-48` | CTA حداقل ۴۸ پیکسل | passed |
| 7 | `responsive-width-sweep` | هر پنج عرض دقیق و data-parity | passed |
| 8 | `desktop-security-sessions-1440x900` | proof مستقل دقیق | passed |
| 9 | `desktop-adds-no-facts` | دسکتاپ بدون fact/KPI تازه | passed |
| 10 | `shell-account-destination-invariant` | ترتیب پوسته مصوب و active=`account` | passed |
| 11 | `canonical-account-route-contract` | مقصدهای canonical یکتا و legacy فقط carry-forward | passed |
| 12 | `minimal-content-contract` | همه واحدهای پیش‌فرض توجیه Keep دارند | passed |
| 13 | `synthetic-identities-only` | صفر هویت یا شماره واقعی | passed |
| 14 | `account-hub-destinations-unique` | مقصدهای پروفایل/امنیت/حافظه/اعلان یکتا | passed |
| 15 | `accountant-account-scope-bounded` | حسابدار فقط profile/storage/notifications | passed |
| 16 | `self-profile-progressive-disclosure` | metadata ثانویه on-demand/removed | passed |
| 17 | `profile-address-feedback-in-context` | validation و triad نتیجه کنار فرم | passed |
| 18 | `public-profile-visibility-matrix-exact` | normal viewer: phone masked/address hidden | passed |
| 19 | `public-profile-actions-bounded` | action فقط موجود و مجاز | passed |
| 20 | `session-list-metadata-bounded` | device/platform/activity و signal لازم | passed |
| 21 | `session-decision-feedback-in-context` | current retained، confirm و outcome صادقانه | passed |
| 22 | `storage-action-feedback-in-context` | local scope و zero جدا از size-error | passed |
| 23 | `notification-center-metadata-bounded` | content/time/destination/single signal | passed |
| 24 | `notification-empty-error-semantics-distinct` | stateهای notification جدا | passed |
| 25 | `push-state-matrix-complete-and-truthful` | ۹ state و action فقط در state مجاز | passed |
| 26 | `recovery-state-atlas-complete` | ۱۵ group و nested stateهای لازم | passed |
| 27 | `protected-interiors-absent` | صفر interior بازار/پیام‌رسان | passed |

## Foundation و component validation

- ۶۵ variable، ۹ text style و ۲ effect موجود بدون collection/token تازه reuse شدند؛
- component catalog نهایی ۱۲ component set و ۵۴ variant دارد؛
- component setهای تازه: `UIUX/Account Action Row` روی `121:14`، `UIUX/Session Row` روی `122:1327` و `UIUX/Notification Row` روی `123:1330`؛
- variantهای Account-active Bottom Navigation روی `127:14` و `127:35`؛
- ۷۷ instance bound و صفر detached instance روی صفحه Stage؛
- minimum text contrast برابر `4.548:1` و minimum focus contrast برابر `3.972:1` با focus stroke سه‌پیکسلی است.

دو carry-forward غیرمسدودکننده باقی مانده‌اند:

1. avatar initials در component inherited، text style محلی exact متناظر ندارد؛ بااین‌حال Vazirmatn، fit و contrast پاس‌اند.
2. variantهای قدیمی Operations-active در Bottom Navigation بدهی focus/layout/style پیش از 0B-5 دارند؛ variantهای Account-active و همه rootهای این Stage قرارداد interaction را پاس کرده‌اند.

این دو مورد به‌عنوان pass صوری پاک نشده‌اند و در manifest و metrics ثبت شده‌اند.

## شواهد مستقیم Figma

| فایل | source node | ابعاد | SHA-256 |
| --- | --- | --- | --- |
| `assets/figma-account-profile-scenarios.png` | `117:4` | `1520×2200` | `1c0674024f5191e3a8b3b74d162e40e9aa827ac21a99dc5a887836c7170ebfb0` |
| `assets/figma-security-storage-scenarios.png` | `117:5` | `1520×1180` | `f361380c6755f1bde70ba957dd8583e42c88f861a09afbe9a7042de9ae136f18` |
| `assets/figma-notification-center-scenarios.png` | `117:6` | `1100×1180` | `6dc10354978a853e8e31d163d88716babd67df53a84f9e010891511c10d29c02` |
| `assets/figma-state-route-visibility-push-matrix.png` | `117:7` | `3060×2940` | `5df6e5ddcc7b0e54b818a239f95518840f9e34c939fc91bc67dad157fd88c565` |
| `assets/figma-responsive-and-desktop-proofs.png` | `117:8` | `4180×1280` | `3546789eea3dee0b35dc020c3edc2138a42d1f04b4f023779d01cc550fefb8fc` |
| `assets/figma-desktop-security-sessions-1440x900.png` | `143:668` | `1440×900` | `9a5b34bb9fd8aa46e4ce87af53a3a0c7f5f88b1fc43763218a6ccc58fcf7faff` |

capture مستقیم در `2026-08-08T17:13:12.738Z` انجام شده است. `assets/figma-stage0b5-audit-metrics.json` نیز SHA-256 برابر `351f6afafb0e2d3b1a08e908dcd88cb72d9d2fd4fed8110c3fb22c12c6658d94` دارد.

## harness محلی مشتق‌شده

run نهایی `2839230-1786210464518` در `2026-08-08T17:34:30.693Z` این نتایج را ثبت کرد:

- `27 / 27` passed، صفر failure و صفر page error؛
- `155` action target با کمینه `44×44px` و ۹ CTA با کمینه ارتفاع `48px`؛
- چهار face Vazirmatn Evidence؛
- پنج viewport دقیق و بدون overflow؛
- desktop screenshot دقیق `1440×900`؛
- هفت capture؛
- exact assertion set/order و pre/post assertionهای یکسان؛
- canonical DOM پیش و پس از capture برابر `c5693eb79e0405cd7946a7d3ebeedd6b9a8fac3b7fe3699454aeac4c82eae831`؛
- remeasurement پس از capture و atomic directory swap پاس؛
- metrics SHA-256 برابر `293524253132064c0056022132325e213f8122fc43c0bd8a3a9601a7f222ca91`.

| فایل محلی | ابعاد | SHA-256 |
| --- | --- | --- |
| `local-account-profile-scenarios.png` | `2096×1057` | `e20e9506559a13fa4ee10abd0e9818f1a45b1d627df2f2c313afd98738ab9c6d` |
| `local-profile-visibility-matrix.png` | `548×412` | `752b4bbd78ba0e210d1b1e36de84efef9ee6469e1b8fa70bf0473d5ed64623eb` |
| `local-security-storage-scenarios.png` | `1268×1056` | `57d0f6821811d9c03f8d1334840c8b87d832962b2928fd714940d2c3a80eeac3` |
| `local-notification-center-scenarios.png` | `854×1057` | `020de1fb8511dd1d5937358a3fc9998056e20a148338a8e0c095b43964b2d3ab` |
| `local-state-route-push-atlas.png` | `1466×2244` | `4dc14d64f2182d751732f8f49f5c48d47f1ef785f40b09be6caf81418de579b1` |
| `local-account-notifications-responsive-sweep.png` | `2092×1033` | `27473d99ea97c6366b8fe4ec054270cc56bc0ade2076754bedbe3fffbcf391ba` |
| `local-desktop-security-sessions-1440x900.png` | `1440×900` | `b05876afb412f5467f51d682a58ede7f23f203a230942ef3d9620c9ec09df950` |

HTML canonical harness برابر `83e4b8a12d04eba3ca547aa31b63ac28598b5192be3606580838c29b0450e77e` و capture script برابر `bfba40a15fee2edf4ee924703a02ba9b5de860ce8d92c1458526fbcdd7c222f3` است.

## state، truth و privacy coverage

- recovery atlas شامل ۱۵ group و ۱۴ nested substate نام‌گذاری‌شده است؛
- Push matrix هر ۹ state `checking / unsupported / insecure / server-disabled / permission-blocked / permission-default / subscribed / unsubscribed / error` را دارد؛
- visibility matrix چهار ردیف `self / normal viewer / authorized admin / unavailable` دارد؛
- normal viewer فقط phone masked و address hidden می‌بیند؛
- صفر cache معتبر با size-error یکی نیست؛
- true empty، category empty، error/retry، route-less و realtime-new جدا هستند؛
- item بدون route غیرتعاملی است؛
- category total از endpoint محدود به آخرین ۵۰ اعلان ساخته نمی‌شود؛
- «پایان همه نشست‌های دیگر» نشست جاری را حفظ می‌کند؛
- session inventory به‌عنوان local per-server توصیف شده و merge cross-server ادعا نمی‌شود.

## Sites preview — خصوصی و source-bound

| فیلد | مقدار |
| --- | --- |
| URL خصوصی | [trading-bot-uiux-stage0b5.mohsenbarari235.chatgpt.site](https://trading-bot-uiux-stage0b5.mohsenbarari235.chatgpt.site) |
| title / slug | `Trading Bot UI/UX — Stage 0B-5` / `trading-bot-uiux-stage0b5` |
| project | `appgprj_6a776942e35c819198a0dcab372ac65e` |
| source canonical | Figma file `z8jgJxST4O2APzWnlyP9gv`؛ Sites فقط derivative خصوصی |
| source commit | `9a710611d52ca24c5cd300fc010f464fb1ad33c3` |
| version | `1`؛ `appgprj_6a776942e35c819198a0dcab372ac65e~appgver_d0bbd46aed2481918e6dd16377916706` |
| deployment | `appgdep_6a776aae0604819185ff740c57054fac` روی `site---6a776942e35c819198a0dcab372ac65e` |
| زمان وضعیت موفق | `2026-08-08T17:43:19.978890Z` |
| زمان reread نهایی انتشار | `2026-08-08T17:43:58.035651Z` |
| local archive | `391385` بایت، SHA-256 `22d41b9fd89c7543c6be518fc7f23304daab84dd2390e936126bdd0a55f2f731` |
| connector-normalized content | `890880` بایت، `27` فایل، SHA-256 `058f397ec23d099c0ddcaf84e3f1a54ed1bcce86dc241cc43624b50d0bfc70a2` |
| drift review | `passed_artifact_and_source_bound`؛ تأیید بصری signed-in مالک pending |

access policy بلافاصله پیش و پس از deploy برابر `custom`، نقش جاری `owner`، یک allowed user، صفر group و صفر external visitor بود. anonymous probe در `2026-08-08T17:43:56Z` پاسخ `401` با `no-store`، `no-referrer` و عنوان `Sign in required` گرفت. bypass token درخواست نشد و signed-in live content واکشی نشد.

verification بسته:

- production build با Next.js `16.3.0`: passed؛
- `npm audit --audit-level=high`: صفر vulnerability؛
- Worker/ASSETS و بازگشت `503` بدون binding: passed؛
- سه URL probe محلی: هر سه `200`؛
- Worker SHA-256: `55e64c6d4c7bc3d45166f2ac5b2f350bc719e86074367b97d69d645ec35b40b3`؛
- hosting manifest SHA-256: `825bfeaf99c56601d8159d884036d41eb7876691aa1911e946e3c3ee07ed0621`؛
- HTML public/built byte-identical با SHA-256 برابر `83e4b8a12d04eba3ca547aa31b63ac28598b5192be3606580838c29b0450e77e`؛
- چهار فونت محلی Vazirmatn حاضر و byte-identical؛
- sensitive scan بدون source map، env/key یا log: passed؛
- Worker error event در پنجره ۱۵ دقیقه‌ای: صفر.

Sites موفقیت interaction، authorization، mutation، Push/realtime delivery یا session revocation runtime را اثبات نمی‌کند.

## baseline runtime

دستور دقیق:

```bash
cd frontend && npm run test:unit:run -- \
  src/views/AccountHubView.test.ts \
  src/views/ProfileView.test.ts \
  src/views/SettingsView.test.ts \
  src/views/NotificationsView.test.ts \
  src/router/index.test.ts \
  src/views/SetupPassword.test.ts \
  src/stores/notifications.test.ts \
  src/composables/useNotificationRuntime.test.ts \
  src/services/webPush.test.ts \
  src/utils/browserNotifications.test.ts \
  src/services/telegramLink.test.ts \
  src/components/PublicProfile.test.ts \
  src/components/UserProfile.test.ts \
  --maxWorkers=1 --no-file-parallelism
```

نتیجه: `13 / 13` فایل و `128 / 128` تست، exit code صفر، Vitest duration برابر `38.54s`. هشدار stale بودن Browserslist/caniuse-lite و logهای mock‌شده NetworkError، clear-cache، browser-notification failure و debug output موردانتظارند و test failure نیستند.

این baseline رفتار فعلی را ثبت می‌کند و پیاده‌سازی طراحی تازه را اثبات نمی‌کند.

## حدود ادعا و گیت

Figma، export، harness و Sites نمی‌توانند authorization، API mutation، redirect واقعی، session revocation، realtime recovery، Push delivery، cross-server/cross-channel sync، clipboard، focus، screen reader، keyboard یا failure race واقعی را اثبات کنند.

شواهد فنی `complete` است، اما owner visual approval هنوز pending است. `0B-6` آغاز نشده و runtime implementation تا تأیید صریح آن unauthorized می‌ماند.
