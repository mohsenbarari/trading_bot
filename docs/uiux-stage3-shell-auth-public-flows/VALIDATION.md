# Stage 3 — Validation ledger

وضعیت: **`stage3_complete`**

این ledger baseline پیش از implementation را از گیت‌های closure جدا نگه می‌دارد. baseline تاریخی به‌تنهایی pass نبود؛ نتایج final، evidence و Git binding زیر مرجع closure هستند.

## هویت baseline

```text
branch = condidate/webapp-ui-ux-redesign-v2
comparisonBaseCommit = 3822df67a48e7ee3197bc6d67c79aa7ee84a7905
baselineWorktree = clean
baselineUpstreamAheadBehind = 0/0
stage3RuntimeWorkStarted = true
comparisonHeadCommit = bfe4e59192d678eaf4776fbc025d3aa0f431896d
comparisonHeadTree = 0b0e1b1e6f615a34622659fca351507e4f7c1404
observedImplementationRoutes = 30
observedImplementationScopes = route:5 / section:21 / off:4
observedImplementationShells = public:3 / focused-authenticated:1 / standard-authenticated:21 / protected-legacy:4 / system-recovery:1
observedProtectedHomeRegionContract = stage3-dashboard-market-region-v1
observedProtectedHomeRegionCompositeSha256 = f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860
observedCanonicalWebInvitationPathPattern = /i/[A-Za-z0-9]{8}
observedRawInvitationResponseException = GET /api/invitations/lookup/:code; no-store
observedRawInvitationUrlException = user-initiated https://t.me/<bot>?start=<raw-invitation>
invitationSecurityFocusedRegression = 23/23 passed
technicalGate = passed_with_disclosed_inherited_diagnostics_and_compose_fixture_caveat
protectedDiff = passed_zero_unauthorized_drift
localEvidence = passed_21_of_21
sites = passed_private_owner_only_source_bound
figmaClosure = passed_read_only_reference_hash_bound
finalRunArtifact = assets/gates/stage3-runtime-final.json
finalRunSha256 = 73de6208d8dc9ad8b3c67c3cf81548946898676ff8719b5ffca4faff52fa18b9
nextAuthorizedRuntimeStage = 4
stage4RuntimeImplementationAuthorized = true
stage4RuntimeWorkStarted = false
```

## baseline متمرکز تازه

command از `frontend/`:

```bash
npx vitest run src/App.test.ts src/components/AppAuthenticatedShell.test.ts src/components/AppToasts.test.ts src/components/BottomNav.test.ts src/components/PWAInstallOverlay.test.ts src/composables/useSessionApprovalRuntime.test.ts src/router/index.test.ts src/router/uiRouteContract.test.ts src/utils/auth.test.ts src/utils/pwaInstall.test.ts src/views/InviteLanding.test.ts src/views/LoginView.test.ts src/views/SetupPassword.test.ts src/views/WebRegister.test.ts --reporter=json --outputFile=/tmp/uiux-stage3-focused-baseline.json
```

| field | مقدار تازه |
| --- | --- |
| start | `2026-08-09T03:34:54.710Z` |
| exit | `0` |
| file | `14` |
| suite | `28/28` |
| test | `136/136` |
| failed | `0` |
| artifact | `/tmp/uiux-stage3-focused-baseline.json` |
| bytes | `44828` |
| SHA-256 | `52982ca5375ff265dae1abc3bc98b3f265b39864ad79d76730370c4478ac1e6a` |

توزیع تست در ۱۴ فایل:

| فایل | تست |
| --- | ---: |
| `src/App.test.ts` | 2 |
| `src/components/AppAuthenticatedShell.test.ts` | 3 |
| `src/components/AppToasts.test.ts` | 5 |
| `src/components/BottomNav.test.ts` | 10 |
| `src/components/PWAInstallOverlay.test.ts` | 5 |
| `src/composables/useSessionApprovalRuntime.test.ts` | 7 |
| `src/router/index.test.ts` | 3 |
| `src/router/uiRouteContract.test.ts` | 6 |
| `src/utils/auth.test.ts` | 25 |
| `src/utils/pwaInstall.test.ts` | 5 |
| `src/views/InviteLanding.test.ts` | 11 |
| `src/views/LoginView.test.ts` | 36 |
| `src/views/SetupPassword.test.ts` | 8 |
| `src/views/WebRegister.test.ts` | 10 |

این artifact در `/tmp` موقت است و خودش closure evidence نیست.

## baseline guard

command صحیح guard self-test:

```bash
npx vitest run scripts/design-system-v2-guard.test.mjs --reporter=json --outputFile=/tmp/uiux-stage3-guard-baseline.json
```

| field | مقدار تازه |
| --- | --- |
| start | `2026-08-09T03:35:48.206Z` |
| exit | `0` |
| file/suite/test | `1 / 4 / 39` |
| passed | `39/39` |
| bytes | `12522` |
| SHA-256 | `cef7ed242c4925a515d0f28992387ff06f9a46c6709355f4e3a6ce4fa966dcbe` |

`node --test scripts/design-system-v2-guard.test.mjs` command معتبر این suite نیست، چون فایل از state داخلی Vitest استفاده می‌کند. شکست آن invocation نادرست، شکست محصول یا guard نیست.

`npm run guard:ui` نیز در baseline exit `0` داشت:

```text
PASS undefined design-token guard
PASS hardcoded trade-side color guard
PASS new bespoke modal-overlay guard
PASS UIUX v2 scope guard (3 V2 CSS files, 29 product routes)
```

## baselineهای carry-forward

- Stage 2 serial baseline: `41` فایل، `84` suite، `452/452` تست؛ artifact `/tmp/uiux-stage2-runtime-baseline.json`.
- سه فایل Stage 3 که در آن run نبودند در baseline جداگانه `37/37` پاس شدند.
- union محاسباتی baseline برابر `44` فایل / `90` suite / `489` تست بود و به‌درستی PASS closure نشد؛ artifact سریال final جای آن را با `58` فایل / `118` suite / `664` تست گرفته است.
- Playwright `e2e/auth.spec.ts` در baseline دقیقاً `3` تست فهرست می‌کند؛ `--list` pass runtime نیست.
- visual baseline دقیقاً `26` سناریو دارد. شش مورد Auth برابر login/register/invite-landing در mobile-390 و desktop-1440 هستند. baseline setup-password و catch-all ندارد و باید با evidence واقعی افزوده شوند.

## focused invitation security regression

```bash
python3 -m unittest tests.test_logging_foundation tests.test_error_tracking
```

این run محدود دقیقاً `23/23` پاس است و `hide_parameters=true` در engine، پوشاندن prefixهای registration bearer برابر `INV/ACCT/CUST/REG` در formatterهای logging و redaction eventهای error tracking را پوشش می‌دهد. final serial Vitest و browser acceptance نیز مستقل از آن پاس شده‌اند.

## facts نهایی closure

- route/scope/shell cardinality برابر `30`، `ROUTE 5 / SECTION 21 / OFF 4` و `public 3 / focused 1 / standard 21 / protected 4 / system 1` است؛ route contract و scope manifest به‌ترتیب SHA-256 `f159a613ff4565daa6ab513974e9f8350d3093767971dce5f8135f4c1376d5b1` و `94fc3599a334098d41a39438092ee7c6e0b3f3f67140885addc3be33b80befaf` دارند.
- final serial Vitest برابر `58` فایل / `118` suite / `664` تست و صفر failure است؛ artifact `assets/gates/stage3-runtime-final.json` با SHA-256 `73de6208d8dc9ad8b3c67c3cf81548946898676ff8719b5ffca4faff52fa18b9` مرجع است.
- browser acceptance run `uiux-stage3-browser-20260809T115615647Z` برابر `23/23` است؛ metrics SHA-256 `e93d7ffa69d7dbbacbf6749f3a49030da9895b1e987925d96f23083dbaf3f52c` و pre/post source identity برقرار است.
- local evidence run `stage3-local-20260809T122824300Z-21fd706e` برابر `21/21` است. بسته frozen دقیقاً `31` فایل / `2599621` byte با aggregate SHA-256 `ba851f9714c55d1d35d15e49d51fca31ebf0ca6c20de3b31b8a2592567489d24` دارد و در closure docs تغییر نکرده است.
- Figma reread روی file `z8jgJxST4O2APzWnlyP9gv`، page `168:1974` و nodeهای `168:2017/2018/1979/1980` انجام شد؛ detached instance count برابر `0` و freeze جدید برابر false است.
- protected unauthorized drift و protected legacy normal fixture drift برابر `0` هستند؛ empty diff SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` و Home region v1 برابر `4553` byte / `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860` است.
- Sites project `appgprj_6a787773edb081918c882d90fdaa72a8`، version `appgprj_6a787773edb081918c882d90fdaa72a8~appgver_90ada74ab6348191834f5fcc2c4a74ed` شماره `1` و deployment `appgdep_6a787879a7dc81919502f4ff014d1dc5` با status `succeeded` source-bound هستند. access سفارشی owner-only برابر `1/0/0` و anonymous root/evidence هر دو `401 + no-store + no-referrer` است؛ errors-only logs و environment entries هر دو صفرند.
- implementation commit برابر `bfe4e59192d678eaf4776fbc025d3aa0f431896d`، tree برابر `0b0e1b1e6f615a34622659fca351507e4f7c1404`، parent برابر comparison base، exact path count برابر `120` و staged path-set SHA-256 برابر `fabe8de11af4c13240ba4adc62a717d4d4aa78213345e98be48ac8566e496f0e` است.

## ledger closure

| gate | شاهد نهایی | وضعیت |
| --- | --- | --- |
| route/Auth/recovery/security | final Vitest + browser metrics + source binding | `passed` |
| responsive/accessibility/layering | browser `23/23` روی هشت viewport | `passed` |
| type/build/guard | `vue-tsc`، production build، `guard:ui` و `45/45` self-test | `passed` |
| lint | Stage3-new diagnostics `0`؛ raw `184 errors / 1 warning` inherited و disclosed | `passed_delta_clean_only` |
| format | Stage3-new hunks `0`؛ `14` style-dirty file و یک generic SVG parser inference inherited/disclosed | `passed_delta_clean_only` |
| backend G1 | `231` pass و `20` opt-in PostgreSQL skip | `passed_with_opt_in_skips` |
| backend G2 | `47/47` | `passed` |
| backend G3 | literal: دو Compose failure فقط به‌علت نبود ignored `.env`؛ همه non-Compose/Dockerfile/Nginx pass؛ mirror byte-identical با `.env` خالی هر دو subtest pass | `closed_with_fixture_caveat` |
| protected | unauthorized drift `0`، legacy normal fixture drift `0`، region v1 exact | `passed` |
| local evidence | `21/21`، fail-closed و hash-bound | `passed` |
| Figma | چهار reference مستقیم، detached `0`، freeze جدید `false` | `passed_read_only_reference` |
| Sites | source-bound، custom owner-only، anonymous `401` | `passed` |
| Git binding | commit/tree/parent و exact `120`-path set | `passed` |
| content-necessity quantitative counts | assertion اختصاصی در frozen evidence وجود ندارد؛ عددی اختراع نشده است | `not_measured_non_contract_hard` |

Stage 3 `complete` است. Stage 4 تنها runtime stage بعدیِ مجاز است و `stage4RuntimeWorkStarted=false` باقی می‌ماند. production cutover اتمیک/maintenance یا version-gated reload یک release carry-forward است و Stage 3 ادعای zero-downtime compatibility ندارد.
