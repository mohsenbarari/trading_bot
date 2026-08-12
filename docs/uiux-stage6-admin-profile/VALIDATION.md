# Validation — Stage 6 delivered Phase 1–6 scope

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

جزئیات ID/hash در `FIGMA_SNAPSHOT_MANIFEST.json` ثبت شده است؛ همان manifest، referenceهای live/editable Phase 4–6 را بدون ادعای screenshot hash/freeze جدید ثبت می‌کند.

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

## Closure boundary

`stage6CompleteAuthority=false` باقی می‌ماند. این بسته EVIDENCE_MANIFEST یا local freeze نیست و هیچ Sites project/preview، staging، production یا product deployment ایجاد یا تغییر نداده است.
