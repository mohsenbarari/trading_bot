# Stage 4 — هسته استفاده روزانه

تاریخ آغاز و closure: ۲۰۲۶-۰۸-۰۹

وضعیت: **`stage4_complete`**

شاخه: `condidate/webapp-ui-ux-redesign-v2`

## وضعیت machine-readable نهایی

```text
stage4Status = stage4_complete
stage4RuntimeImplementationAuthorized = true
stage4RuntimeWorkStarted = true
stage4ComparisonBaseCommit = 9dfa961000832c830729ce67e8a54357915c716a
stage4ComparisonBaseTree = 1540c2534d8052a3a8cfcffcdc2f65e4b85fc874
stage4ImplementationCommit = 007f94d170cb02cd69911d9e1f122b83fbacd535
stage4ImplementationTree = 807a01c76c93489ccce1e5b72cea9c214fd52d31
stage4ImplementationPathCount = 67
stage4ImplementationPathSetSha256 = 25a5773b2e3ca1f6e45bbf48800dcac4ce3cd8e8125f1913fee674529720739f
stage4TechnicalGate = passed_with_inherited_diagnostics_disclosed
stage4ProtectedDiffStatus = passed_zero_unauthorized_drift
stage4BrowserAcceptanceStatus = passed_49_of_49_promotable
stage4EvidenceStatus = passed_frozen_70_file_package
stage4FigmaClosureStatus = passed_authored_snapshot_hash_bound
stage4SitesStatus = passed_private_owner_only_source_bound
nextAuthorizedRuntimeStage = null
stage5RuntimeImplementationAuthorized = false
stage5RuntimeWorkStarted = false
workStoppedAfterStage4 = true
```

implementation commit با parent دقیق comparison base ساخته و به tree و pathset بالا bind شد. طبق دستور کاربر، closure این مرحله مجوز Stage 5 نیست و کار پس از پایان Stage 4 متوقف است.

## ۱. دامنه بسته‌شده

Stage 4 طبق [roadmap](WEBAPP_UI_UX_REDESIGN_V2_ROADMAP_20260717.md) فقط هسته استفاده روزانه را پوشش داد:

- Dashboard؛
- Operations؛
- Account Hub؛
- Settings / Security / Storage؛
- Notifications و Push؛
- مهاجرت rollback-safe خانواده `WorkspaceShell` / `ds-workspace-*` به adapterهای V2، بدون جذب interiorهای Stage 5.

شش مقصد canonical برابر `/`، `/operations`، `/account`، `/account/security`، `/account/storage` و `/account/notifications` هستند. `/settings` و `/notifications` به‌ترتیب به routeهای نام‌دار `account-security` و `account-notifications` redirect می‌شوند. Back در Security، Storage و Notifications قطعی و به route نام‌دار `account` است.

قراردادهای مبنا [Stage 0B-2](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_HOME_SHELL_CHECKPOINT_20260808.md)، [Stage 0B-3](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_OPERATIONS_WORKSPACES_CHECKPOINT_20260808.md) و [Stage 0B-5](WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_ACCOUNT_PROFILE_SECURITY_NOTIFICATIONS_CHECKPOINT_20260808.md) حفظ شدند.

## ۲. نتیجه route، role و authority

route registry دقیقاً `30` route دارد: scope برابر `route 10 / section 16 / off 4` و protection برابر `none 23 / full 4 / mixed 3` است. شش route Stage 4 شامل پنج route-scope و یک section-scope mixed هستند.

- مالک/عضو واجد اختیار فقط با predicate واقعی به مشتریان، حسابداران یا Market دسترسی دارد؛
- حسابدار هیچ مقصد یا action مالک، Market، personal session، personal logout یا Telegram ندارد؛
- مدیر میانی دعوت‌نامه و کاربران را می‌بیند؛ مدیر ارشد علاوه بر آن کالاها، پیام‌های مدیریت و تنظیمات سیستم را دارد؛
- inactive/restricted فقط هشدار اثرگذار و مقصد واقعی دریافت می‌کند؛ badge مثبت و مقصد مرده وجود ندارد؛
- `CustomerWorkspaceView` و `AccountantWorkspaceView` فقط consumer regression adapter باقی ماندند و workflow/permission/API/IA آن‌ها وارد Stage 4 نشد.

## ۳. Security، Storage، Notifications و Push

- Security و Storage بر routeهای canonical جدا و mutually exclusive هستند؛
- inventory نشست local per-server است و UI هیچ merged/cross-server guarantee نمی‌سازد؛
- terminate نشست دیگر فقط با current-primary authority انجام می‌شود؛ logout others نشست جاری را حفظ می‌کند؛
- confirm، busy، success و failure کنار action می‌مانند و context در failure حفظ می‌شود؛
- Storage فقط cache/download همین browser/device را پاک می‌کند و size-error با صفر واقعی یکی نیست؛
- Notificationها فقط `معاملات` و `سایر` هستند؛ window محدود ۵۰تایی total نیست و count جعلی نمایش داده نمی‌شود؛
- item بدون route معتبر non-interactive می‌ماند و route/API/backend metadata خام نمایش داده نمی‌شود؛
- loading، true empty، category empty، initial error و retained refresh error مستقل‌اند؛
- Push دارای نه state قراردادی است و permission فقط با action صریح کاربر درخواست می‌شود؛ scope آن فقط browser/device فعلی است.

## ۴. نتیجه گیت‌های فنی

| گیت | نتیجه نهایی |
| --- | --- |
| frontend serial | `34` فایل / `450/450` تست؛ artifact SHA-256 `c2e4d8be51b88ebb7d7ab75c903ee60452fb03c0db6b9b317ead66a0bcd6a9fa` |
| guard | `3` فایل / `8` suite / `55/55` تست و `guard:ui` پاس |
| backend | `11` ماژول / `69/69` تست با DSNهای dummy PostgreSQL و بدون اتصال DB |
| type/build/diff | `vue-tsc`، production build و diff-check پاس؛ `2162` module، `31.50s`، PWA `161 entries / 4190.04 KiB` |
| lint | Stage4-new diagnostic برابر `0`؛ current `121 = 110E + 11W` و inherited `121`؛ raw exit غیرصفر blanket-pass نشده است |
| format | Stage4-new برابر `0`؛ current/inherited dirty برابر `22`؛ raw exit غیرصفر blanket-pass نشده است |
| final gate summary | `passed`؛ SHA-256 `f5f1b32ef85d010aa2134b3531f628ac941f91f9dc4adfb58ee46cfa39a86ac2` |

نخستین diagnostic backend با `sqlite+aiosqlite` به‌علت نبود dependency محیطی discard شد. rerun معتبر با PostgreSQL driver نصب‌شده پاس شد؛ دو `DeprecationWarning` ارثی `schemas.py` نیز ثبت شده‌اند. advisoryهای build درباره age پایگاه Browserslist و chunk بزرگ‌تر از ۵۰۰ KiB صریح‌اند.

## ۵. browser، Figma و evidence

browser acceptance نهایی در run `uiux-stage4-browser-20260809T180340666Z` با `49/49` assertion، `9` suite، هشت viewport و `22` screenshot پاس و promotable شد. metrics SHA-256 برابر `83445d91bd78fd0903f49833a5b72c5d49345d517d9e5ae05e2fdd42954cd01f` است. expected HTTP/console failureها به‌ترتیب `16/17` و unexpected diagnostics صفر بودند؛ source binding `398` فایل پیش/پس اجرا یکسان ماند.

Figma authored snapshot روی file `z8jgJxST4O2APzWnlyP9gv`، page `283:18`، root `283:19` و provenance node `291:554` بسته شد. audit مستقیم شش section، شش screen، `66` linked instance، detached برابر صفر و یک protected Market image را با error صفر ثبت کرد. سیزده export مستقیم `1118391` بایت و aggregate SHA-256 برابر `46e329154d226cc0ed6fb302b4c33b0215b29280a25d9d8abccfe1e6a266774a` دارند.

local evidence run `stage4-local-588243cd033dce300388` برابر `26/26` است. بسته frozen دقیقاً `70` فایل / `5863416` بایت با aggregate SHA-256 `8c123a1eeb717f799c0449443f2d8ea76f201a0ae2c31e062b1cff09584a7971` دارد. `EVIDENCE_MANIFEST.json` با SHA-256 `7a1a4a7da5c82f7c3744fba2f94adf0402dc6e6d5b47944234d2c0b266efdda8`، `FIGMA_SNAPSHOT_MANIFEST.json`، HTML، capture harness و همه assets در docs/Sites closure تغییر نکردند.

## ۶. protected boundary

- Market runtime: `19` فایل / `137246` بایت / pathset `37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589` / aggregate `162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058`؛
- Messenger runtime: `85` فایل / `1312405` بایت / pathset `f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58` / aggregate `f66debf9809180d97b2bac98f5195ba24200d3b61b0d8e0e5cd423a8a7b97248`؛
- Home market region: شش section / `4553` بایت / SHA-256 `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860`؛
- `AdminMessagesView.vue`: `5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a`؛
- `TradingSettings.vue`: `509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa`؛
- route protection: `4 full/off + 3 mixed` و manifest/runtime برابر `7/7`؛
- unauthorized source/behavior/visual drift، pathset drift و snapshot بدون disposition همگی صفر.

## ۷. Sites خصوصی

- project: `appgprj_6a78cd05d74c8191a9c5e095f15c6381`؛ slug: `trading-bot-uiux-stage4-daily-core`؛
- version: `appgprj_6a78cd05d74c8191a9c5e095f15c6381~appgver_8e6170326bdc819197ec4a44175859d0`، شماره `1`؛
- deployment: `appgdep_6a78ce229a0081919ae50975911e9e7d`، status `succeeded`؛
- Sites source commit: `b55e221cf4fe363a12e2bf6b0f6a212a9adcd2ae`؛
- URL: [private Stage 4 evidence preview](https://trading-bot-uiux-stage4-daily-core.mohsenbarari235.chatgpt.site)؛
- access سفارشی owner-only برابر users/accounts `1/1`، workspace/tenant groups `0/0` و external visitors صفر؛
- anonymous root و evidence هر دو `HTTP/2 401 + no-store + no-referrer`؛
- environment revision/entries برابر `0/0` و errors-only logs در ۱۵ دقیقه صفر؛
- local archive برابر `50` فایل / `1808222` بایت / SHA-256 `9ae7770165762ff3008fec93b7ff58cc369ecfdf73ab037a795181197251a861`؛ provider-stored archive برابر `50` فایل / `2406400` بایت / content hash `sha256:b3ac973d4248dda0730fa47c95cf6a89e43c51cb37d729fbc85371d602446ea4`.

provider-managed bypass فقط به‌صورت state موجود مشاهده شد؛ مقدار آن هرگز خوانده، استفاده، persist یا expose نشد و anonymous probe بدون bypass بود. provenance کامل در [SITES_PROVENANCE](uiux-stage4-daily-core/SITES_PROVENANCE.json) با SHA-256 `3197cac8f90dcc1abfc2d52ee4fa4d87059e34863250a8042709230eb1bde1f8` ثبت است.

## ۸. مراجع closure و توقف

- [Stage 4 package](uiux-stage4-daily-core/README.md)
- [Runtime contract](uiux-stage4-daily-core/RUNTIME_CONTRACT.md)
- [Content necessity matrix](uiux-stage4-daily-core/CONTENT_NECESSITY_MATRIX.md)
- [Validation ledger](uiux-stage4-daily-core/VALIDATION.md)
- [Route/surface manifest](uiux-stage4-daily-core/ROUTE_SURFACE_MANIFEST.json)
- [Protected manifest](uiux-stage4-daily-core/PROTECTED_SURFACE_DIFF_MANIFEST.json)
- [Frozen evidence manifest](uiux-stage4-daily-core/EVIDENCE_MANIFEST.json)
- [Sites provenance](uiux-stage4-daily-core/SITES_PROVENANCE.json)

Stage 4 کامل است. roadmap تغییر نکرده است، Stage 5 مجاز یا آغاز نشده و کار طبق دستور کاربر در همین نقطه متوقف می‌شود.
