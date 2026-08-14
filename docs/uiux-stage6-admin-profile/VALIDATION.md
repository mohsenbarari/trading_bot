# Validation — Stage 6 delivered Phase 1–19 scope

## Historical Phase 1–3 source binding

| field | value |
| --- | --- |
| branch | `condidate/webapp-ui-ux-redesign-v2` |
| commit | `3283a6e38209cb06d352740dae5b05bce5ba9002` |
| tree | `7284ec4aac1980c0f61201e3346841425f6bcb09` |
| parent | `63bc6827af63c722e7f1c156b3d47825afc18eae` |
| tracked worktree during final browser receipts | clean before/after, identical |

## Historical Phase 1–3 technical receipts

- `cd frontend && npm run test:unit:run -- --no-file-parallelism --maxWorkers=1`: **154 test files / 1700 tests passed / 436.71s** on the clean final source.
- `npx vue-tsc --noEmit`: pass (`1.61s`).
- `npm run build`: pass (`32.20s`).
- `npm run guard:ui` و `git diff --check`: pass.
- focused profile tests: **75/75** pass.
- backend focused authority/projection/notification group: **131** tests pass (dummy local test configuration; inherited warningها خارج از نتیجه‌اند).
- Playwright collection: 24 test در 4 spec pass شد. اجرای live E2E شروع نشد، چون `127.0.0.1:8000/api/config` در محیط محلی در دسترس نبود؛ این failure محصول یا staging/production نیست.

## Historical Phase 1–3 browser acceptance

آخرین aggregate run: `uiux-stage6-aggregate-browser-20260811T203934914Z`.

- top-level: `passed`, `promotable=true`, **17/17** assertion و 4 screenshot (`627319` bytes).
- child Phase 2: `uiux-stage6-phase2-browser-20260811T204010055Z`، **17/17** assertion و 14 screenshot؛ binding `a07065db60eaa28e917ab19957b8f5a273e683c1342a87962ebc6946cd0140d0` برای 393 source file.
- child Phase 3: `uiux-stage6-phase3-browser-20260811T204053994Z`، **14/14** assertion و 12 screenshot (`1476288` bytes)؛ binding `d4216b636958ac6293b8d481cf79157efe5f2fcb1d978acad25204de245e23e1` برای 399 source file.
- aggregate binding: 560 source file، `6a4ba01a41ce97494ae1b95bdab605b88293b15c368cdb426c235bb358a1b3fd`؛ metrics SHA-256 `29e99fe327070db076cdd4dd3ffe4154f83f1247253c7bee11e0373359fa9bfc` و binding SHA-256 `2f7663e3225775545866fdd9d5cc508623742bac9c407467675a97f8bebd0919`.
- Phase 2 child diagnostics: 0 unexpected console/page/API/request/transport violation؛ `expectedProfileResponseConsoleEvents=4` برای 403/404 fixture و `externalRequestsBlocked=15` ثبت شد.
- Phase 3 child diagnostics: 0 unexpected console/page/API/request/transport violation؛ counterهای هم‌نامِ Phase 2 ندارد، اما counterهای خودِ Phase 3 یعنی `expectedHttpErrors=4` برای 403/404 و `externalTrafficIntercepted=13` برای Telegram loader local-intercepted ثبت شده‌اند.
- source، Git، harness و environment در receipt aggregate pre/post identical هستند.

Browser harness با fixtureهای synthetic اجرا شده است. بنابراین behaviorهای محدوده‌شدهٔ browser را اثبات می‌کند، نه availability یا enforcement مستقلِ backend زنده.

فهرست دقیق سه harness، سه run نهایی و renderهای Figma با bytes/hash/projection در `DELIVERED_SCOPE_EVIDENCE_INVENTORY.json` ثبت شده است. این فهرست فقط allowlist review تاریخی Phase 1–3 است.

## Historical Phase 1–3 Figma

post-fix read-only audit: `assets/figma/final-provenance-20260811T204635Z/stage6-final-figma-provenance-audit.json`، SHA-256 `ccdea4bd31124d759c68ed89e16c9ed73290f04e2bb58359b4138e8ed575b89b`.

- result: `pass_with_documented_page_sibling_topology`.
- هر سه Phase، label قابل‌دیدنِ `source 3283a6e3` و `دادهٔ synthetic` دارند.
- پنج render از page/root/Phase 1/2/3 پس از اصلاح بازبینی شده‌اند؛ clipping/overlap label دیده نشد.
- Phase 3 sibling صفحهٔ root `321:19` است، نه child آن؛ این disclosure است و ادعای یک bundle nested واحد مجاز نیست.

جزئیات ID/hash در `FIGMA_SNAPSHOT_MANIFEST.json` ثبت شده است؛ همان manifest، referenceهای live/editable Phase 4–14 را بدون ادعای screenshot hash/freeze جدید ثبت می‌کند.

## Phase 4 و Phase 5 — supplemental receipts، نه freeze

| phase | source | browser receipt | result |
| --- | --- | --- | --- |
| 4 — invitation management | `7ac46a36a2ba968246bd285c357bc362a328cdd2` / tree `70fd42de45f7df36ee034b8c0a2ca8ba1823f07a` | `uiux-stage6-phase4-invitations-20260812T052926717Z` | `8/8` assertion، `8` screenshot، promotable؛ metrics `e2a58abee27a7a7cf93748bb0660f7cbdb54f4d2abb07faedb2a44fdc8ef4e3f` و binding `7799d590f2cc8cf457155591892615f7ee86a3d0a792ca8edbe4774af0e36711`. |
| 5 — public block/unblock | `5ca7d00120c693c8c8507656dbe203dd530396a5` / tree `9776550334dfc45a563e1b5fd221d63156334c36` | `uiux-stage6-phase5-public-block-20260812T062238392Z` | `7/7` assertion، `6` screenshot، promotable؛ metrics `5a88e7c01641738d7ba667a188b354386e786d2200a8511b7556b1e25dc9a70e` و binding `b91ac618e35f1582ebb316a711ceb541c1758615a6b16ea8a13b2175a9ad1ab0`. |

این receiptها در checkpoint با contractهایشان آمده‌اند؛ به inventory/aggregate immutable Phase 1–3 افزوده نشده‌اند.

## Phase 6 — workspace account deletion

| field | value |
| --- | --- |
| source commit | `06579e2bbccbb2b8a33bd9a92bc55a851e8a2329` |
| source tree | `bcdb89069aec2619cc8e7e7da6c0126bf9b22986` |
| parent | `7ff647f370ab83c0893403f6ec6b8362140a0939` |
| source binding | `fcf977ab478b6c2c38fe4f719e90b2302f48039774b72e9fcb9da8a8aa1eb63e` (393 files) |

- focused dialog test: **6/6** pass؛ Customer/Accountant workspace serial suite: **3 files / 85 tests** pass؛ design-system guard suite: **43/43** pass.
- `vue-tsc --noEmit`، `npm run build`، `npm run guard:ui`، `npm run test:guard:stage4` (**11/11**) و `git diff --check`: pass.
- browser receipt: `uiux-stage6-phase6-account-delete-20260812T075056249Z`، `passed`/`promotable=true`، **18/18** assertion و **20** screenshot. metrics SHA-256 `5bd62ad9ea00292cc8620005695f3207b2847caab667c5013738b84241220ecb`؛ binding SHA-256 `64f2eb7974e2692a2840e0080dfd53ac20fa1364f3fd50d33cc0cbf24b7d7a3f`؛ harness SHA-256 `cd4c6bd5385627fb596a43487419d477d90764dc44f6af83d8ee4833927d86a0`.
- synthetic browser matrix Customer/Accountant را در 360، 390 و 1440 پوشش می‌دهد: portal full viewport، focus trap، Escape/cancel بدون DELETE، API با `expected_action=delete-account`، receipt صحیح `id/status=deleted`، و recoveryهای 400/403/404/malformed/network با dialog/relation/route پایدار و پیام ثابت امن.
- diagnostics غیرمنتظره صفر است. فقط شش HTTP 400/403/404 و دو network-failure fixture دقیقاً برای DELETE synthetic expected/classified هستند؛ 17 loader خارجی قبل از transport محلی intercepted شده‌اند.
- Figma live evidence در page `321:18`، sibling section `422:636` ثبت شد: W1=`422:638` و W2=`422:661`، هر دو `390×844`. backdrop=`422:653` دقیقاً `390×844` است و dialog=`422:654` در `(16,74,358,696)` کاملاً داخل viewport می‌ماند. typography Vazirmatn و input/acknowledgement variable-bound هستند؛ label قابل‌دیدن `422:637` شامل `source 06579e2b · دادهٔ synthetic` است و audit URL/email/phone/raw-server-detail نیافت.

## Phase 7 — پایان امن یک نشست workspace

| field | value |
| --- | --- |
| source commit | `24a8d0f500e798c70eb94764045ee9ed90151b99` |
| source tree | `c611f9612ce45ac698d5a76589b5a2474e0860e5` |
| parent | `5a1ac968e07f94442174b1e39e96b97a2f7d620e` |
| source binding | `6fde9fb1b0f53fd2820c932b14165a7a9d98fd35fb3b7719aa41f61602f62354` (393 files) |

- Customer/Accountant workspace serial suite: **80/80** pass؛ `vue-tsc --noEmit`، `npm run guard:ui` و `git diff --check`: pass.
- browser receipt: `uiux-stage6-session-browser-20260812T121245634Z`، `passed`/`promotable=true`، **18/18** assertion و **16** screenshot. metrics SHA-256 `e7a558edd7c4d55972fe9ee279d8925cf02f999aa26d9186d0252035f932b28d`؛ binding SHA-256 `24022d531dfba3349f729f9945debf2eb0968ea315a584306b93b74f1023fd38`؛ harness SHA-256 `e2a6d6781f9024b02fa67fd61cafd192af8a4387d0cfa758a9fb60e48df50402`.
- matrix synthetic Customer/Accountant را در 360/390/1440 برای focus-trap، Escape/cancel بدون DELETE، receipt صحیح، و 400/403/404/malformed/network با dialog/route/relation/session ثابت و بدون raw payload پوشش می‌دهد. diagnostics غیرمنتظره صفر است؛ 6 HTTP fixture، 2 network console/failure fixture و 17 loader خارجی locally intercepted صریحاً expected/classified هستند.
- Figma live reference در page `321:18`، sibling section `442:701` است: W1=`442:703` و W2=`442:733`، هر دو `390×844`. dialog=`442:719` در `(16,278,358,288)` داخل viewport است؛ label `442:702` شامل `source 24a8d0f5 · دادهٔ synthetic` است. audit همهٔ فونت‌ها را Vazirmatn، CTAهای open/cancel را Secondary و CTA تأیید را Primary، و scan phone/email/URL/raw-error را clean ثبت کرد.

این receipt محلی، mutable و supplemental است؛ evidence artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند.

## Phase 8 — بازیابی امن mutation رابطهٔ workspace

| field | value |
| --- | --- |
| source commit | `4165ddd5280d2d5485b77ca194d3592e4d239f8b` |
| source tree | `ebead37b3116be33c20645a284c8a40bf093cd23` |
| parent | `c6c902fd80b3ffd4570a46f96a43d7c47c4dca2b` |
| source binding | `83d0089b7987e08e42755e8a014bc089fff4da661573e2d853a9bc7dc9531a46` (389 files) |

- Customer/Accountant workspace focused suite: **80/80** pass؛ `vue-tsc --noEmit`، `npm run guard:ui` و `git diff --check`: pass.
- browser receipt: `uiux-stage6-relation-browser-20260812T125450Z`، `passed`/`promotable=true`، **540** assertion، **36** screenshot و 36 context ایزوله. metrics SHA-256 `f2a07d11799200a97da00aeee1c1f0732092546696f2c89bc3d55d2b2c3f4ce6`؛ binding SHA-256 `7c1d78badf989f24d71641ca6ad843bc0c098a3a9d2bb4117bcbb5ec15add9cf`؛ harness SHA-256 `cd3dc40be39b3c4a227356e3c90aca0561a392b102b511b2e0a92a6e5e262339`.
- matrix synthetic چهار action Customer cancel/close و Accountant cancel/delete را پوشش می‌دهد: در 360 dialog/focus/Escape/cancel بدون DELETE؛ در 390، 400/403/404/wrong-id/wrong-status/network با dialog/relation/path/query ثابت و safe copy؛ و در 390/1440 فقط receipt دقیق همان relation با `revoked` یا `deleted` target را حذف و navigation مجاز را فعال می‌کند.
- 32 DELETE محلیِ fixture (24 failure و 8 success) صریحاً expected هستند؛ DELETE ناشی از Escape/cancel، traffic خارجی، console/page error غیرمنتظره، native dialog یا source/Git/env delta صفر است. raw detail/message در DOM، URL/history یا storage دیده نشد.
- Figma live/editable در page `321:18`، sibling section `451:766` ثبت شد: W1=`451:768` confirmation و W2=`451:791` safe recovery، هر دو `390×844`. dialog=`451:784` در `(16,278,358,288)` داخل viewport است؛ label `451:767` شامل `source 4165ddd5 · دادهٔ synthetic` است. audit همهٔ 37 text node را Vazirmatn و scan URL/email/phone/raw-server-error-or-receipt را clean ثبت کرد.

این receipt محلی، mutable و supplemental است؛ evidence artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند.

## Phase 9 — حذف کالا و نام مستعار با receipt دقیق

| field | value |
| --- | --- |
| source commit | `2aa32c6d48a8b693de8ff37c310d995a4748efa8` |
| source tree | `81e1111a3ab74731da91aee71d3f477afd92e598` |
| parent | `4553c70530bfc4f51006c3e361738a1775f994b9` |
| source binding | `5c1b01f0cff45fca67ccf463878c63214cd7e2ad187e573f7acb7de91e530ac1` (393 files) |

- `npm run build`: pass (`52.90s`). اجرای serial کامل با timeout پیش‌فرض 10s برابر **153/154** file و **1713/1714** test بود؛ تنها موردِ باقی‌مانده یک timing flake بازتولیدپذیر و source-unrelated در `ChatView` است. rerunهای مربوط و اجرای timeout 30s برابر **109/109** pass شدند؛ بنابراین این مورد به‌صورت qualified ثبت می‌شود، نه pass کامل serial در 10s.
- browser receipt: `uiux-stage6-phase9-commodity-delete-20260812T141257269Z`، `passed`/`promotable=true`، **21** assertion و **20** screenshot. metrics `stage6-phase9-commodity-delete-metrics.json` SHA-256 `c382eb97d3b57d91de371969dbf20e2b7d2a3ee2bfcd52b1933501a5f303f845`؛ binding SHA-256 `439a9098daef9f44845badb80043db9854b997ae05849863844888d851060c4d`؛ harness SHA-256 `8539fbff4303841d457d22db859d6d628e6e97cf615070caa051ea0672041f58`.
- Figma live/editable در page `321:18`، sibling section `455:831` است: W1=`455:833` و W2=`455:856`، هر دو `390×844`. backdrop=`455:848` در `(0,0,390,844)` و dialog=`455:849` در `(16,278,358,288)` کاملاً داخل viewport هستند؛ label `455:832` دقیقاً `Phase 9 · حذف امن کالا و نام مستعار · source 2aa32c6d · دادهٔ synthetic` است. audit همهٔ 37 text node را Vazirmatn، 9 instance را linked و unsafe scan را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 10 — حذف امن کاربر مدیریت و پایان نشست‌ها

| field | value |
| --- | --- |
| source commit | `0839eb091b20438e603e265f5c9b9a6cbe5ae19b` |
| source tree | `f466bfde632bc8663334b5bcbc1aa411e011c2bc` |
| parent | `64d3bb97fb9d7aee46b32b456d4d1e438c5f360d` |
| source binding | `0476f7801b46a7f53cbf43ebf05d252dc9b46798b02113bc9da5e6997af81bd3` (393 files) |

- focused `UserProfile.test.ts`: **27/27** pass؛ `vue-tsc --noEmit`، `npm run guard:ui` و `git diff --check`: pass.
- browser receipt: `uiux-stage6-phase10-admin-user-delete-20260812T173239804Z`، `passed`/`promotable=true`، **13** assertion و **14** screenshot. metrics `stage6-phase10-admin-user-delete-metrics.json` SHA-256 `c402634eee20b818f455c0a1c1a7d3681ae1304769d951da172e74dc5bdd8d74`؛ binding SHA-256 `683197885b66b6e11472e9c73db38d15d997672fd4bf4d5fa6d2e541a72d19a0`؛ harness SHA-256 `5f63cdc208d6260c2de14d3676d56a3aaa1905d63faeae71aa2a9ab898febb5a`.
- matrix synthetic مسیر `/admin/users/:id` را پوشش می‌دهد: در 360 geometry/focus/Escape/cancel بدون mutation برای حذف و پایان نشست‌ها؛ در 390/1440 فقط receipt دقیق `200` حذف را به فهرست برمی‌گرداند و پایان نشست‌ها را با عدد صحیح می‌پذیرد؛ 400/403/404/malformed/network دیالوگ، کاربر نمایش‌داده‌شده و مسیر را با copy ثابت امن نگه می‌دارند. diagnostics غیرمنتظره صفر است؛ 4 HTTP fixture، 1 network failure و 1 network console error صریحاً expected/classified هستند.
- Figma live/editable در page `321:18`، sibling section `464:896` است: W1=`464:898` و W2=`464:921`، هر دو `390×844`. backdrop=`464:913` در `(0,0,390,844)` و dialog=`464:914` در `(16,278,358,288)` کاملاً داخل viewport هستند؛ label `464:897` دقیقاً `Phase 10 · حذف امن کاربر مدیریت و پایان نشست‌ها · source 0839eb09 · دادهٔ synthetic` است. audit همهٔ 37 text node را Vazirmatn، 9 instance را linked و unsafe scan شامل account_name/موبایل/receipt انگلیسی را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 11 — نشست‌های امن حساب

| field | value |
| --- | --- |
| source commit | `7be4c830e0f476b3c56f82fd37d1ad9bc37652f4` |
| source tree | `4aeb347f799f72a254179c5977a668bc805cddbd` |
| parent | `5d7008f17704437e975f8d128adc3a08f83fd2bb` |
| source binding | `383dfc6234e24c76c08b5417537f228f9d345fa622517b1e2584006b27dbf48d` (393 files) |

- focused `SettingsView.test.ts`: **27/27** pass؛ `vue-tsc --noEmit`، `npm run guard:ui` و `git diff --check`: pass.
- browser receipt: `uiux-stage6-phase11-account-security-20260812T184803019Z`، `passed`/`promotable=true`، **13** assertion و **14** screenshot. metrics `stage6-phase11-account-security-metrics.json` SHA-256 `8d01ae58a9928a804b2b1eaa727eb432cd28be570cd6660bf5dfaa13fda3d63c`؛ binding SHA-256 `5a99061f227420c0e63268bc547088d9bcaeb5a25849172c6dbed1bb3dd68159`؛ harness SHA-256 `80beb81575093cb300cba2aeece66651fcf045eb8fe845382d3339cd1b5d99c1`.
- matrix synthetic مسیر `/account/security` را پوشش می‌دهد: در 360 geometry/focus/Escape/cancel بدون mutation برای پایان نشست و خروج از نشست‌های دیگر؛ در 390/1440 فقط receipt دقیق `200` پایان نشست را اعمال می‌کند و خروج دیگران را با الگوی عددی می‌پذیرد؛ 400/403/404/malformed/network دیالوگ، فهرست نشست‌ها و مسیر را با copy ثابت امن نگه می‌دارند. diagnostics غیرمنتظره صفر است؛ 4 HTTP fixture، 1 network failure و 1 network console error صریحاً expected/classified هستند.
- Figma live/editable در page `321:18`، sibling section `466:961` است: W1=`466:963` و W2=`466:986`، هر دو `390×844`. backdrop=`466:978` در `(0,0,390,844)` و dialog=`466:979` در `(16,278,358,288)` کاملاً داخل viewport هستند؛ label `466:962` دقیقاً `Phase 11 · نشست‌های امن حساب · source 7be4c830 · دادهٔ synthetic` است. audit همهٔ 37 text node را Vazirmatn، 9 instance را linked و unsafe scan شامل نام دستگاه/receipt خام را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 12 — پاک‌سازی حافظه محلی حساب

| field | value |
| --- | --- |
| source commit | `61e4e70f16735166ac4e26ed978580ddb1311624` |
| source tree | `78ae43c1ab0227691b5bc5c9f92f57f93f70877b` |
| parent | `cf605637c5ae46bb449a0d1d2963afc97a469b82` |
| source binding | `84b24039432882fdea3fd47ba53597fbd30341dade71880fe2457d49eb2bd877` (393 files) |

- focused `SettingsView.test.ts`: **28/28** pass؛ `npm run guard:ui` و `git diff --check`: pass؛ `vue-tsc --noEmit` خطای تازه‌ای روی `SettingsView` ندارد.
- browser receipt: `uiux-stage6-phase12-account-storage-20260812T192405395Z`، `passed`/`promotable=true`، **6** assertion و **6** screenshot. metrics `stage6-phase12-account-storage-metrics.json` SHA-256 `a2a0ee567c2ecb4181a0724e6d8d6e52ef536dc670d6779cd0f9067790f4d76b`؛ binding SHA-256 `126996f74083dfe1700516a34cf5293c46916afc93d30535d31d9ca78174de93`؛ harness SHA-256 `4abcecc37ffd9e239b5d14fbc30f2956996cda59e422b300224b7609b78b7bd3`.
- matrix synthetic مسیر `/account/storage` را پوشش می‌دهد: در 360 geometry/focus/Escape/cancel بدون پاک‌سازی یا reload؛ در 390/1440 پاک‌سازی محلی موفق اندازه را صفر می‌کند و reload را یک‌بار ثبت می‌کند؛ شکست محلی دیالوگ، اندازه و مسیر را با copy ثابت امن نگه می‌دارد. diagnostics غیرمنتظره صفر است.
- Figma live/editable در page `321:18`، sibling section `468:1026` است: W1=`468:1028` و W2=`468:1051`، هر دو `390×844`. backdrop=`468:1043` در `(0,0,390,844)` و dialog=`468:1044` در `(16,278,358,288)` کاملاً داخل viewport هستند؛ label `468:1027` دقیقاً `Phase 12 · پاک‌سازی حافظه محلی · source 61e4e70f · دادهٔ synthetic` است. audit همهٔ 37 text node را Vazirmatn، 9 instance را linked و unsafe scan شامل جزئیات داخلی حافظه را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 13 — تأیید امن تغییر وضعیت حساب، رفع مسدودیت و رفع محدودیت

| field | value |
| --- | --- |
| source commit | `b4c8fec657bb78d848ddfb0c5be2b33812c80a64` |
| source tree | `e313defd57d42476ff68be08e3128233229dd2dd` |
| parent | `08ec74302b34b2da72b2c25e37d69c232ebdf8d5` |
| source binding | `97b87be3eeb324c6a18931d857ce01725a791e932072eac6acde1c418331bec4` (393 files) |

- focused `UserProfile.test.ts`: **28/28** pass؛ `npm run guard:ui` و `git diff --check`: pass؛ `vue-tsc --noEmit` خطای تازه‌ای روی `UserProfile` ندارد.
- browser receipt: `uiux-stage6-phase13-admin-account-status-20260812T194220288Z`، `passed`/`promotable=true`، **13** assertion و **13** screenshot. metrics `stage6-phase13-admin-account-status-metrics.json` SHA-256 `56b25e7f9d29bae4717f949683665b80e6acfadf8b25ec4fd357a06d02b7a1f9`؛ binding SHA-256 `bc14aabb365f85cc6eea7c0d8b5816679e4159cd9b902abfd0fdbfd7daf6d7d3`؛ harness SHA-256 `220ceb82ce10a1eeb6164ff54473a73a2a129ff3eeddc0ebfc9699c6b1fd09ee`.
- matrix synthetic مسیر `/admin/users/:id` را پوشش می‌دهد: در 360 geometry/focus/Escape/cancel بدون mutation برای تغییر وضعیت، رفع مسدودیت و رفع محدودیت؛ در 390/1440 فقط receipt دقیق `200` غیرفعال‌سازی را اعمال می‌کند؛ 400/403/404/malformed/network دیالوگ، کاربر نمایش‌داده‌شده و مسیر را با copy ثابت امن نگه می‌دارند. diagnostics غیرمنتظره صفر است؛ 4 HTTP fixture، 1 network failure و 1 network console error صریحاً expected/classified هستند.
- Figma live/editable در page `321:18`، sibling section `470:1091` است: W1=`470:1093` و W2=`470:1116`، هر دو `390×844`. backdrop=`470:1108` در `(0,0,390,844)` و dialog=`470:1109` در `(16,262,358,320)` کاملاً داخل viewport هستند؛ label `470:1092` دقیقاً `Phase 13 · تأیید امن تغییر وضعیت حساب · source b4c8fec6 · دادهٔ synthetic` است. audit همهٔ 37 text node را Vazirmatn، 9 instance را linked و unsafe scan شامل account_name/موبایل/receipt انگلیسی را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 14 — یکپارچگی ظاهر مدیریت کاربر

| field | value |
| --- | --- |
| source commit | `f33d7fce3513386c05083573f93be31f9d1d7219` |
| source tree | `b622300eb559af6161f8001c1e50aa90aa2ea136` |
| parent | `1420076aece41d064649573e44891b7b3df145ee` |
| source binding | `66422fc458a1de597fc91612bc08d1bbb046ce417dd0321deb523d3fa67a6c6c` (393 files) |

- focused `UserProfile.test.ts`: **29/29** pass؛ `npm run guard:ui` و `git diff --check`: pass؛ `vue-tsc --noEmit` خطای تازه‌ای روی `UserProfile` ندارد.
- browser receipt: `uiux-stage6-phase14-admin-user-chrome-20260812T200513178Z`، `passed`/`promotable=true`، **7** assertion و **5** screenshot. metrics `stage6-phase14-admin-user-chrome-metrics.json` SHA-256 `2fd4c66e53e1850d4f79daf8f3000629efbf87836ead52ec84ecc4a276c975ef`؛ binding SHA-256 `616ab1bb5f37db30445dbcf6de0d046ceb83952b2c76aa4b78ed41e38571b93e`؛ harness SHA-256 `02c9d1af8f22578045ae9573642ff0a776374357ebd8c6e04bba7f9337352c2f`.
- matrix synthetic مسیر `/admin/users/:id` را پوشش می‌دهد: منوی کارت اقدام در 360 و 390 بدون overflow افقی؛ فرم محدودیت در 390 و 1440 با ورودی `ui-input` و دکمهٔ پاورقی داخل viewport؛ مدت مسدودیت در 390 با دکمه‌های `ui-button` داخل viewport. diagnostics غیرمنتظره صفر است و mutation زنده رخ نداده است.
- Figma live/editable در page `321:18`، sibling section `472:1156` است: W1=`472:1158` و W2=`472:1181`، هر دو `390×844`. backdrop=`473:4996` در `(0,0,390,844)` و dialog=`473:4997` در `(16,162,358,520)` کاملاً داخل viewport هستند؛ label `472:1157` دقیقاً `Phase 14 · یکپارچگی ظاهر مدیریت کاربر · source f33d7fce · دادهٔ synthetic` است. audit همهٔ 50 text node را Vazirmatn، 14 instance را linked و unsafe scan شامل account_name/موبایل/receipt انگلیسی را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 15 — یکپارچگی ظاهر فهرست کاربران مدیریت

| field | value |
| --- | --- |
| source commit | `e4a234106e132c2b4758f856d54a1836ec7bb9f7` |
| source tree | `5c16e550acb28afdc928161047fb766713fca205` |
| parent | `9eee93f583760d95bdce2f4b4b7158adc9bdfb65` |
| source binding | `8e93b61ffabf9d357d03cd5a7a55a1b02208d82b8e250dc1a4b7402fcf2667e0` (393 files) |

- focused `UserManager.test.ts`: **9/9** pass؛ `npm run guard:ui` و `git diff --check`: pass.
- browser receipt: `uiux-stage6-phase15-admin-user-directory-20260812T202011978Z`، `passed`/`promotable=true`، **6** assertion و **4** screenshot. metrics `stage6-phase15-admin-user-directory-metrics.json` SHA-256 `1ebf36ddfab4575907cf65ed67356affd8b10a902b60cef18d2523de58ba1c7f`؛ binding SHA-256 `612135acf4959e4deff40c8835e45f0e24a13dbe18525ff68297c350dc483fd7`؛ harness SHA-256 `cb83f0e02028d735d625ee60c5cf93fc1b0e0d4a52bb669958662273a8fa9118`.
- matrix synthetic مسیر `/admin/users` را پوشش می‌دهد: فهرست `ui-list-item` در 360، 390 و 1440 بدون overflow افقی؛ جستجوی محلی در 390 بدون نشت `q` به URL. diagnostics غیرمنتظره صفر است و mutation زنده رخ نداده است.
- Figma live/editable در page `321:18`، sibling section `474:1223` است: W1=`474:1225` و W2=`474:1251`، هر دو `390×844`. overlay کلون‌شده مخفی است؛ label `474:1224` دقیقاً `Phase 15 · یکپارچگی ظاهر فهرست کاربران · source e4a23410 · دادهٔ synthetic` است. audit همهٔ 64 text node را Vazirmatn، 10 instance را linked و unsafe scan را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 16 — یکپارچگی ظاهر پوسته پروفایل عمومی

| field | value |
| --- | --- |
| source commit | `208edb374eeaecf6dea50f58ed3f86d7bb3b019a` |
| source tree | `36ced6afe032e442ac72cc73bdf9e8405a671f94` |
| parent | `8f44adaea48820a7069162ce1c0e636d6c1749f2` |
| source binding | `40e4238ea6fa62d065b56859754791dc02c03325c283b6bf514b162b681ecc12` (393 files) |

- focused `PublicProfile.test.ts`: **56/56** pass؛ `npm run guard:ui` و `git diff --check`: pass.
- browser receipt: `uiux-stage6-phase16-public-profile-chrome-20260812T205426458Z`، `passed`/`promotable=true`، **6** assertion و **4** screenshot. metrics `stage6-phase16-public-profile-chrome-metrics.json` SHA-256 `d41663cb395cea687f1688b346cbd3334ca84636c9977f62f4c5bed376d623d9`؛ binding SHA-256 `20ea694968d6e3867110df7a3a319e847cdf9a85533d9c5d8d749e1420093024`؛ harness SHA-256 `4ef35faf944ae58d575efe573bddaa70c1acb378e2fac79cfb3d489a60f69c63`.
- matrix synthetic مسیر `/users/:id` و `/profile` را پوشش می‌دهد: پوستهٔ پروفایل خود با `ui-icon-button` در 360، 390 و 1440 بدون overflow افقی؛ بازیابی خطا در 390 با `ui-button` و دو GET بدون mutation. diagnostics غیرمنتظره صفر است و mutation زنده رخ نداده است.
- Figma live/editable در page `321:18`، sibling section `477:1285` است: W1=`477:1287` و W2=`477:1345`، هر دو `390×844`. overlay کلون‌شده مخفی است؛ label `477:1286` دقیقاً `Phase 16 · یکپارچگی ظاهر پوسته پروفایل عمومی · source 208edb37 · دادهٔ synthetic` است. audit همهٔ 49 text node را Vazirmatn، 10 instance را linked و unsafe scan را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 17 — یکپارچگی ظاهر انتخاب تاریخ سفارشی مدیریت

| field | value |
| --- | --- |
| source commit | `d0d756fe9a8fe8d359c039f6e9d346f28f292999` |
| source tree | `8eb80e24681e7753441e1fd6d74e9695d2a3e200` |
| parent | `172c242ad26a8c0899dd51189792d3cc55a5a6e2` |
| source binding | `20fdeaac75a5732e783fecbe0bfa2511c615cfdc999b827f81584b8580af45e1` (393 files) |

- focused `UserProfile.test.ts`: **29/29** pass؛ `npm run guard:ui` و `git diff --check`: pass.
- browser receipt: `uiux-stage6-phase17-admin-custom-date-20260812T210602968Z`، `passed`/`promotable=true`، **6** assertion و **4** screenshot. metrics `stage6-phase17-admin-custom-date-metrics.json` SHA-256 `3b562d2e7f2f0e14ee83c371e7481361fc8dad61bbe79cfea318d0dcc088d10c`؛ binding SHA-256 `345bb62707945af672489e31cf805b4700628eb4acbe0cf4cf0d16b1ee918c90`؛ harness SHA-256 `1497cf4e70b6303c76036d0c43b95905e8dc3c5a6c7ce277806dafec5b2784c7`.
- matrix synthetic مسیر `/admin/users/:id` را پوشش می‌دهد: ماشهٔ تاریخ سفارشی محدودیت در 360، 390 و 1440 داخل viewport با `ui-button`؛ ماشهٔ تاریخ سفارشی مسدودیت در 390 بدون mutation. diagnostics غیرمنتظره صفر است و mutation زنده رخ نداده است.
- Figma live/editable در page `321:18`، sibling section `481:1327` است: W1=`481:1329` و W2=`481:1376`، هر دو `390×844`. backdrop برای اثبات overlay دیده می‌شود؛ label `481:1328` دقیقاً `Phase 17 · یکپارچگی ظاهر انتخاب تاریخ سفارشی مدیریت · source d0d756fe · دادهٔ synthetic` است. audit همهٔ 66 text node را Vazirmatn، 14 instance را linked و unsafe scan را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 18 — یکپارچگی ظاهر پوسته باقی‌مانده پروفایل عمومی

| field | value |
| --- | --- |
| source commit | `b3f0fe00d451c6e2dd82b1a9f8c306a88e954953` |
| source tree | `ce5f2e5fd3973a12ed555af9d605c6dbcae7b24d` |
| parent | `4eb96c19d6b4b18b78fcec36edc2ad809b3a64a4` |
| source binding | `8e982de690fabbe3d41809c789dc5d06fffdf9ff9b4bd57e06ab1050d62ee64a` (393 files) |

- focused `PublicProfile.test.ts`: **56/56** pass؛ `npm run guard:ui` و `git diff --check`: pass.
- browser receipt: `uiux-stage6-phase18-public-profile-remaining-chrome-20260812T211745381Z`، `passed`/`promotable=true`، **6** assertion و **4** screenshot. metrics `stage6-phase18-public-profile-remaining-chrome-metrics.json` SHA-256 `f1b6d296362d46e246477566340f31e3de06ddca1aa3357edc2e0a67e7a22fb1`؛ binding SHA-256 `3d6bcb96d673b661bbec9ac0833073e4afed5e286ea1d7d9e8e64396f5043a14`؛ harness SHA-256 `1412401f20d1dbaa19579131d1bc15a7dff60441fb8b8a463b30246cf5888eb2`.
- matrix synthetic مسیر `/users/:id` و `/profile` را پوشش می‌دهد: آواتار توکن‌دار در 360، 390 و 1440 بدون overflow افقی؛ لینک طرف معامله در 390 با رنگ `--ds-success-700` و navigation فقط `{ id }` بدون mutation. diagnostics غیرمنتظره صفر است و mutation زنده رخ نداده است.
- Figma live/editable در page `321:18`، sibling section `483:1373` است: W1=`483:1375` و W2=`483:1422`، هر دو `390×844`. overlay کلون‌شده مخفی است؛ label `483:1374` دقیقاً `Phase 18 · یکپارچگی ظاهر پوسته باقی‌مانده پروفایل عمومی · source b3f0fe00 · دادهٔ synthetic` است. audit همهٔ 56 text node را Vazirmatn، 10 instance را linked و unsafe scan را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Phase 19 — یکپارچگی ظاهر کارت اقدام پروفایل عمومی

| field | value |
| --- | --- |
| source commit | `92e30fef4d414e7144eca995fc12a075c042aa0b` |
| source tree | `a1b86940a9e1e96cf87af18eb4a57cdd977deece` |
| parent | `4ca812e781c1dc7689a8da0ab437d0df92c4670c` |
| source binding | `60e0b08357ccae097df31a781e9a48718d2c06009cf8be1aba5a8e373c57fa3d` (393 files) |

- focused `PublicProfile.test.ts`: **56/56** pass؛ `npm run guard:ui` و `git diff --check`: pass.
- browser receipt: `uiux-stage6-phase19-public-profile-action-cards-20260812T212620735Z`، `passed`/`promotable=true`، **6** assertion و **4** screenshot. metrics `stage6-phase19-public-profile-action-cards-metrics.json` SHA-256 `7a2f6e6a39cf5681ceb15de288e0160371f6be3d7dfb0afc6bc9c4f45ab5077c`؛ binding SHA-256 `b00a0dee736a03b5e020f9d1aefcfd34361897fa376e4ac940f0a9f9c021610e`؛ harness SHA-256 `3fa192d58cac1b344b1e804f701b21c2548e6eaba84f40b7966ae59f7646f8ee`.
- matrix synthetic مسیر `/users/:id` و `/profile` را پوشش می‌دهد: کارت اقدام تنظیمات با `ui-action-card` و رنگ `--ds-primary-800` در 360، 390 و 1440 بدون overflow افقی؛ کلیک تنظیمات در 390 همان `settings` را بدون mutation می‌فرستد. diagnostics غیرمنتظره صفر است.
- Figma live/editable در page `321:18`، sibling section `485:1414` است: W1=`485:1416` و W2=`485:1465`، هر دو `390×844`. overlay کلون‌شده مخفی است؛ label `485:1415` دقیقاً `Phase 19 · یکپارچگی ظاهر کارت اقدام پروفایل عمومی · source 92e30fef · دادهٔ synthetic` است. audit همهٔ 70 text node را Vazirmatn، 10 instance را linked و unsafe scan را empty ثبت کرد.

این receipt محلی، mutable و supplemental است؛ artifactهای آن عمداً به `selectedArtifacts` تاریخی Phase 1–3 افزوده یا freeze نشده‌اند و رفتار live backend را مستقل اثبات نمی‌کنند.

## Closure — runtime `3e62accd`

| field | value |
| --- | --- |
| status | `stage6_complete` |
| authority | `stage6CompleteAuthority=true` |
| runtime commit | `3e62accdd157bed5dc6f2ed974e56e07c7349910` |
| runtime tree | `3f4a186e46b12aee326c699cd1975ba34e485be7` |
| focused | `13` file / `232` test pass |
| full serial Vitest | `156` file / `1779` test pass |
| `vue-tsc --noEmit` | pass |
| production build | pass؛ `169` file؛ SHA-256 `ee970be6ff3570f4c325c3f420c816b5fa61beb67a9b4071d208b2b3657c50db` |
| `guard:ui` | pass؛ TradingSettings Stage 6 reset-dialog disposition؛ Stage 4 baseline retained |
| `git diff --check` | pass |
| browser | `stage6-closure-20260814082632509`؛ `40/40`؛ report `306ef9d1495046fbd8ab579029e3ea56179b9e904597ed55a110bf7a9a49dce5`؛ harness `78f1bd2b5b1d4aee5f28dc2f71a43c54d18c20e8a5c797035368d3d0fdec3de3` |
| unknown/mutating API | `0` / `0` |
| unexpected console | `0` پس از طبقه‌بندی 403 fixture |
| Figma sibling | `645:1693` روی page `321:18`؛ historical overwrite نشده |
| Sites | evidence خصوصی owner-only اجرا و بررسی شد؛ project `appgprj_6a7ee21d0f288191b8f7f3221d3aff14`؛ version `appgprj_6a7ee21d0f288191b8f7f3221d3aff14~appgver_8797af6c5af481918d77b6509424e68d`؛ deployment `appgdep_6a7ee780c6988191bffa60213296e7cf`؛ visibility=`private-owner-only`؛ owner قابل مشاهده؛ ناشناس `401` + `no-store`؛ public/staging/production=`false` |

تنها `ACTIONABLE_GAP` بسته‌شده: native `confirm` بازنشانی تنظیمات غیر بازار در `TradingSettings.vue`، commit `3e62accd`.

Stage 8 دست‌نخورده مانده: `270` / full `0` / slices `12` / scenarios `163` / authority `false`.

جزئیات ماشین‌خوان در `STAGE6_CLOSURE_LEDGER.json` (SHA-256 `fd13fa2aae27a6d514eeb4a0e9d22647f115f37fdc92aadac9e52ffea6260787`)، `STAGE6_CLOSURE_EVIDENCE_MANIFEST.json` (SHA-256 `8330517eba375445e245b43de4046421c649f9ea29b330771663947e733cc2fd`) و `STAGE6_PRIVATE_SITES_CLOSURE_RECEIPT.json` (SHA-256 `fdbb5e195729dda41698393c91da8601332a844077f80e22221c31ca1e9849ed`) است.
