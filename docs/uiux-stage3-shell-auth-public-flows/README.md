# Stage 3 — Shell, Auth and public flows

وضعیت جاری: **`stage3_complete`**

```text
stage3RuntimeImplementationAuthorized = true
stage3RuntimeWorkStarted = true
stage3Status = stage3_complete
stage3ComparisonBaseCommit = 3822df67a48e7ee3197bc6d67c79aa7ee84a7905
stage3ImplementationHeadCommit = bfe4e59192d678eaf4776fbc025d3aa0f431896d
stage3ImplementationTree = 0b0e1b1e6f615a34622659fca351507e4f7c1404
stage3TechnicalGate = passed_with_disclosed_inherited_diagnostics_and_compose_fixture_caveat
stage3ProtectedDiffStatus = passed_zero_unauthorized_drift
stage3EvidenceStatus = passed_frozen_31_file_package
stage3SitesStatus = passed_private_owner_only_source_bound
stage3FigmaClosureStatus = passed_read_only_reference_hash_bound
stage3BrowserAcceptanceStatus = passed_23_of_23
stage3CanonicalWebInvitationPathPattern = /i/[A-Za-z0-9]{8}
stage3InvitationSecurityFocusedRegression = 23/23 passed
nextAuthorizedRuntimeStage = 4
stage4RuntimeImplementationAuthorized = true
stage4RuntimeWorkStarted = false
```

این بسته closure مرحله سوم را به commit واقعی implementation، evidence محلی immutable، Figma read-only reference و Sites خصوصی bind می‌کند. Stage 3 کامل است؛ Stage 4 تنها مرحله runtime بعدیِ مجاز است و هنوز شروع نشده است.

closure دقیقاً `30` route را ثبت می‌کند: scope برابر `route 5 / section 21 / off 4` و shell برابر `public 3 / focused-authenticated 1 / standard-authenticated 21 / protected-legacy 4 / system-recovery 1`. route contract SHA-256 برابر `f159a613ff4565daa6ab513974e9f8350d3093767971dce5f8135f4c1376d5b1` و scope manifest SHA-256 برابر `94fc3599a334098d41a39438092ee7c6e0b3f3f67140885addc3be33b80befaf` است.

## مراجع

- [checkpoint Stage 3](../WEBAPP_UI_UX_REDESIGN_V2_STAGE3_SHELL_AUTH_PUBLIC_FLOWS_CHECKPOINT_20260809.md)
- [roadmap مصوب](../WEBAPP_UI_UX_REDESIGN_V2_ROADMAP_20260717.md)
- [runtime contract](RUNTIME_CONTRACT.md)
- [validation ledger](VALIDATION.md)
- [route/shell manifest](ROUTE_SHELL_MANIFEST.json)
- [protected diff manifest](PROTECTED_SURFACE_DIFF_MANIFEST.json)
- [content necessity matrix](CONTENT_NECESSITY_MATRIX.md)
- [Sites provenance](SITES_PROVENANCE.json)
- [Design Contract نهایی](../uiux-stage0b-final-system-contract/DESIGN_CONTRACT.md)
- [closure Stage 2](../WEBAPP_UI_UX_REDESIGN_V2_STAGE2_PROTECTED_DESIGN_SYSTEM_CHECKPOINT_20260809.md)

## خروجی‌های مرحله

- shell عمومی بدون navigation احرازشده یا reservation نوار پایین؛
- shell متمرکز `/setup-password` بدون navigation روزانه؛
- shell احرازشده استاندارد با حفظ wrapper و interior محافظت‌شده؛
- Auth canonical برای Login، Invite، Register و Setup Password؛
- 404/forbidden/deep-link recovery بدون blank، loop یا افشای route/backend؛
- PWA فقط پس از Home سالم و بیرون از public/loading/offline/security modal؛
- test و evidence تازه با protected drift صفر.

outcomeهای canonical System Recovery فقط `not-found`، `forbidden` و `deep-link-failure` هستند. recovery برای مهمان shell عمومی Auth و برای نشست محلی shell احرازشده دارد، بدون افشای path/target/secret.

مرز public Invite صریح است: تمام producerهای فعال URL دعوت Web در API، SMS و copy بات فقط pathname دقیق هشت‌کاراکتری `/i/[A-Za-z0-9]{8}` می‌سازند و responseهای create/list و relation داخلی هیچ field با نام `token` یا `invitation_token` ندارند. تنها استثنای response دارای raw invitation bearer، `/api/invitations/lookup/:code` با `no-store` است؛ `no-referrer` برای document/API، access-log-off در proxy و memory-only بودن client تا انتخاب مسیر exposure را محدود می‌کنند. در شاخه Web همین bearer فقط یک‌بار در body درخواست `POST /api/auth/registration-context/exchange` حمل و پس از نتیجه authoritative/terminal از حافظه قابل‌استفاده حذف می‌شود؛ Web Storage، `history.state`، DOM، cookie یا log محل نگهداری آن نیستند. تنها استثنای raw URL، لینک user-initiated تلگرام `https://t.me/<bot>?start=<raw-invitation>` است؛ purpose-bound است و raw fallback در Web را مجاز نمی‌کند.

سرور Web bearer را با context opaque و ده‌دقیقه‌ای Redis عوض می‌کند: کلید context از SHA-256 handle تصادفی ساخته می‌شود و مرورگر فقط cookie `HttpOnly` و `SameSite=Strict` را می‌گیرد؛ نام production/staging برابر `__Host-web_registration` با `Secure`، `Path=/` و بدون `Domain` است. response فقط context mask‌شده و factهای لازم `kind/progress/requires_otp` دارد و raw token/handle را برنمی‌گرداند.

idempotency مسیر مدرن Invite به یک `exchange_id` تصادفی ۲۵۶ بیتی و tab-local با TTL حداکثر ۱۰ دقیقه متکی است؛ record ثابت `sessionStorage` فقط همین شناسه غیر-bearer و timestamp را دارد و invitation code، route، mobile، OTP یا token در آن نیست. همان ID یا cookie دقیق context می‌تواند response مبهم exchange را resume کند؛ replay با ID متفاوت و بدون cookie درست fail-closed است. درخواست/تأیید OTP، تکمیل ثبت‌نام و Login→registration نیز response-loss را با state/receipt محدود، durable completion fact و همان cookie بازیابی می‌کنند. proof تأیید فقط به SHA-256 handle همان context و TTL باقی‌مانده آن bound است؛ proof سراسری raw-bearer وجود ندارد و completion helper خصوصی token verified را از context می‌گیرد. `/api/invitations/validate/{token}` unconditional و pre-DB با `410/no-store` بازنشسته است. سه route عمومی خام `register-otp-request`، `register-otp-verify` و `register-complete` نیز unconditional با `410/no-store` و قبل از هر Redis/DB/OTP/provider بازنشسته‌اند، نه compatibility API.

engine دیتابیس `hide_parameters=true` دارد تا SQL bind value وارد متن خطای SQLAlchemy نشود؛ redaction prefix-aware نیز bearerهای `INV`، `ACCT`، `CUST` و `REG` را در logging و error tracking می‌پوشاند. regression متمرکز `tests.test_logging_foundation + tests.test_error_tracking` دقیقاً `23/23` پاس شده است؛ این pass محدود جای final run، browser یا closure را نمی‌گیرد.

completion receipt تا navigation موفق authoritative باقی می‌ماند و بعد best-effort clear می‌شود؛ refresh مرحله اختیاری Telegram با session محلی معتبر باید پس از `/api/auth/me` به Home برود و context-miss مهمان terminal می‌ماند. helper مشترک navigation نتیجه non-null Vue Router را نیز failure می‌شمارد. Setup Password receipt موفق را در failure انتقال برای retry بدون mutation دوباره نگه می‌دارد و `405` را cause-neutral می‌کند؛ Login/intended-route و شاخه مستقیم Register→Home پس از navigation awaitشده موفق cleanup می‌شوند. شاخه اختیاری Telegram context terminal را پس از receipt + `/api/auth/me` معتبر + render مرحله ۴ پاک می‌کند و Skip ناموفق فقط transition retry state را نگه می‌دارد. browser acceptance نهایی این contractها را در run `uiux-stage3-browser-20260809T115615647Z` با `23/23` assertion پاس و metrics SHA-256 `e93d7ffa69d7dbbacbf6749f3a49030da9895b1e987925d96f23083dbaf3f52c` ثبت کرده است.

boot recovery و stale-chunk recovery فقط `pathname` هم‌origin را حمل می‌کنند و query/fragment قبلی را کنار می‌گذارند. document در HTML و پاسخ‌های frontend/register/invite/deploy دارای `Referrer-Policy: no-referrer` است. fallback فایل JS منقضی نیز contract برابر `410` و `no-store` دارد و script reload سمت Nginx ندارد؛ گیت backend/deploy این مرز را با caveat صریح `.env` در G3 بسته است.

مرز protected ادعای base-identical مطلق ندارد: interiorهای محافظت‌شده و fixtureهای normal شاخه legacy بدون drift مانده‌اند، اما دقیقاً دو delta مشترک مصوب وجود دارد—PWA روی protected دیگر render نمی‌شود چون Home-only است، و access denial/unavailable به System Recovery می‌رود. drift غیرمجاز و legacy normal fixture drift هر دو `0` هستند. region محافظت‌شده market در Home با الگوریتم canonical شش‌بخشی `stage3-dashboard-market-region-v1`، اندازه `4553` byte و composite SHA-256 برابر `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860` در base، final guard و Git-bound head یکسان است. `d037…` extraction قدیمی whole-file/legacy و خارج از قرارداد region Stage 3 است.

تنها استثنای ازپیش‌موجود برای منع log کردن OTP، مسیر صریح `STAGING_AUTH_VALUE_FOR_TEST_ONLY` است که فقط با هم‌زمانی `environment=staging` و `staging_log_otp_codes=true` فعال می‌شود؛ default و automation/example استقرار staging آن را `false` نگه می‌دارند. گیت نهایی این disposition محدود را بدون مجوز production یا گسترش log حفظ کرده است.

قرارداد opaque-cookie عمداً با Login JS قدیمیِ ازقبل‌بارگذاری‌شده که `registration_token` را در response انتظار دارد سازگار نیست و هیچ raw-token fallback مجاز نیست. پیش از deploy، cutover اتمیک/maintenance یا version-gated forced reload الزامی است؛ interruption تب قدیمی با reload قابل‌بازیابی پذیرفته می‌شود و ادعای zero-downtime compatibility وجود ندارد.

## closure و مرز ادعا

final Vitest برابر `58` فایل / `118` suite / `664` تست و صفر failure است؛ artifact آن SHA-256 `73de6208d8dc9ad8b3c67c3cf81548946898676ff8719b5ffca4faff52fa18b9` دارد. type/build/guard پاس هستند. ESLint و Prettier فقط delta-clean با `Stage3-new=0` هستند و raw inherited debt صریحاً blanket-pass نشده است. backend G1 برابر `231` pass و `20` opt-in skip، G2 برابر `47/47` و G3 literal دارای فقط دو failure مربوط به نبود ignored `.env` است؛ mirror byte-identical با `.env` خالی هر دو Compose subtest را پاس می‌کند.

local evidence run `stage3-local-20260809T122824300Z-21fd706e` برابر `21/21` است. بسته immutable آن دقیقاً `31` فایل و `2599621` byte با aggregate SHA-256 `ba851f9714c55d1d35d15e49d51fca31ebf0ca6c20de3b31b8a2592567489d24` دارد. Figma file `z8jgJxST4O2APzWnlyP9gv` روی page `168:1974` و nodeهای `168:2017/2018/1979/1980` با detached instance count برابر صفر read-only reread شده است.

Sites project `appgprj_6a787773edb081918c882d90fdaa72a8` روی [preview خصوصی](https://trading-bot-uiux-stage3-auth.mohsenbarari235.chatgpt.site) با access سفارشی owner-only، `allowed users=1 / groups=0 / external=0` و anonymous `401` برای root و evidence بسته شد. محیط صفر entry و errors-only log صفر است. state provider-managed bypass فقط به‌صورت «present» مشاهده شد؛ مقدار آن هرگز خوانده، استفاده، persist یا expose نشد.

implementation commit نهایی `bfe4e59192d678eaf4776fbc025d3aa0f431896d` با tree `0b0e1b1e6f615a34622659fca351507e4f7c1404`، parent همان comparison base، `120` path دقیق و path-set SHA-256 `fabe8de11af4c13240ba4adc62a717d4d4aa78213345e98be48ac8566e496f0e` است. content-necessity policy حفظ شده، اما countهای کمی چون در frozen evidence اندازه‌گیری نشده‌اند pass/zero اعلام نمی‌شوند.
