# Stage 3 — پوسته، ورود و جریان‌های عمومی

تاریخ آغاز و closure: ۲۰۲۶-۰۸-۰۹

وضعیت: **`stage3_complete`**

شاخه: `condidate/webapp-ui-ux-redesign-v2`

## وضعیت machine-readable نهایی

```text
stage3Status = stage3_complete
stage3RuntimeImplementationAuthorized = true
stage3RuntimeWorkStarted = true
stage3ComparisonBaseCommit = 3822df67a48e7ee3197bc6d67c79aa7ee84a7905
stage3ImplementationHeadCommit = bfe4e59192d678eaf4776fbc025d3aa0f431896d
stage3ImplementationTree = 0b0e1b1e6f615a34622659fca351507e4f7c1404
stage3ImplementationPathCount = 120
stage3ImplementationPathSetSha256 = fabe8de11af4c13240ba4adc62a717d4d4aa78213345e98be48ac8566e496f0e
stage3TechnicalGate = passed_with_disclosed_inherited_diagnostics_and_compose_fixture_caveat
stage3ProtectedDiffStatus = passed_zero_unauthorized_drift
stage3EvidenceStatus = passed_frozen_31_file_package
stage3SitesStatus = passed_private_owner_only_source_bound
stage3FigmaClosureStatus = passed_read_only_reference_hash_bound
stage3BrowserAcceptanceStatus = passed_23_of_23
nextAuthorizedRuntimeStage = 4
stage4RuntimeImplementationAuthorized = true
stage4RuntimeWorkStarted = false
```

commit نهایی Git در `2026-08-09T13:02:18+00:00` با parent دقیق comparison base ساخته شد. Stage 4 تنها مرحله runtime بعدیِ مجاز است؛ این authorization به معنی شروع Stage 4 نیست.

## دامنه بسته‌شده

- public، focused-authenticated و standard-authenticated shell؛
- `/login`، `/i/:code`، `/register`، `/setup-password` و catch-all system-owned؛
- PWA Home-only، toast، connection status و session approval layering؛
- focus/keyboard/reduced-motion/back/refresh/error-persistence در Auth؛
- Web invitation short-link و opaque registration context بدون raw bearer persistence؛
- System Recovery با outcomeهای canonical `not-found`، `forbidden` و `deep-link-failure`؛
- حفظ protected interiors و legacy normal fixtures بدون drift غیرمجاز؛
- Figma read-only parity، evidence محلی immutable و Sites خصوصی owner-only.

route contract دقیقاً `30` route دارد: scope برابر `route 5 / section 21 / off 4` و shell برابر `public 3 / focused-authenticated 1 / standard-authenticated 21 / protected-legacy 4 / system-recovery 1` است. SHA-256 route contract برابر `f159a613ff4565daa6ab513974e9f8350d3093767971dce5f8135f4c1376d5b1` و scope manifest برابر `94fc3599a334098d41a39438092ee7c6e0b3f3f67140885addc3be33b80befaf` است.

## نتیجه گیت‌های فنی

| گیت | نتیجه نهایی |
| --- | --- |
| final serial Vitest | `58` فایل / `118` suite / `664` تست / `0` failure؛ SHA-256 `73de6208d8dc9ad8b3c67c3cf81548946898676ff8719b5ffca4faff52fa18b9` |
| browser acceptance | run `uiux-stage3-browser-20260809T115615647Z`؛ `23/23`؛ metrics SHA-256 `e93d7ffa69d7dbbacbf6749f3a49030da9895b1e987925d96f23083dbaf3f52c` |
| type/build/guard | `vue-tsc` pass؛ production build pass؛ guard pass و self-test `45/45` |
| lint | delta-clean با Stage3-new diagnostics برابر `0`؛ raw `184 errors / 1 warning` inherited و disclosed |
| format | delta-clean با Stage3-new hunks برابر `0`؛ raw inherited format debt و generic SVG parser inference disclosed |
| backend G1 | `231` pass و `20` opt-in PostgreSQL skip |
| backend G2 | `47/47` pass |
| backend G3 | literal دقیقاً دو Compose failure به‌علت نبود ignored `.env`؛ همه non-Compose/Dockerfile/Nginx pass؛ mirror byte-identical با `.env` خالی هر دو Compose subtest را pass می‌کند |
| invitation logging/error regression | `23/23` pass؛ SQLAlchemy `hide_parameters=true` و prefixهای `INV/ACCT/CUST/REG` |
| protected | unauthorized drift `0`، legacy normal fixture drift `0`، snapshot update `0` |

هیچ blanket pass برای raw ESLint/Prettier یا literal G3 ادعا نمی‌شود؛ closure بر delta-clean بودن و caveatهای دقیق بالا متکی است.

## protected boundary

چهار route full-protected `/market`، `/chat`، `/share-receive` و `/admin/channels` روی `v2Scope=off` مانده‌اند. دو delta مشترک مصوب فقط PWA Home-only و System Recovery برای access denial/unavailable هستند و مجوز تغییر protected interior نیستند.

protected diff دقیقاً `0` byte با SHA-256 خالی `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` است. Home market region با الگوریتم شش‌بخشی `stage3-dashboard-market-region-v1` در base، final guard و Git-bound head دقیقاً `4553` byte و SHA-256 `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860` دارد.

## evidence و Figma

local evidence run `stage3-local-20260809T122824300Z-21fd706e` برابر `21/21` است. بسته frozen دقیقاً `31` فایل / `2599621` byte با aggregate SHA-256 `ba851f9714c55d1d35d15e49d51fca31ebf0ca6c20de3b31b8a2592567489d24` دارد. این بسته، شامل `EVIDENCE_MANIFEST.json`، `FIGMA_SNAPSHOT_MANIFEST.json`، HTML، capture harness و assets، در docs closure تغییر نکرده است.

Figma file `z8jgJxST4O2APzWnlyP9gv` روی page `168:1974` و nodeهای `168:2017`، `168:2018`، `168:1979` و `168:1980` read-only reread شد؛ detached instance count برابر `0` و freeze جدید برابر false است.

## Sites خصوصی

- project: `appgprj_6a787773edb081918c882d90fdaa72a8`؛ slug: `trading-bot-uiux-stage3-auth`؛
- version: `appgprj_6a787773edb081918c882d90fdaa72a8~appgver_90ada74ab6348191834f5fcc2c4a74ed`، شماره `1`؛
- deployment: `appgdep_6a787879a7dc81919502f4ff014d1dc5`، status `succeeded`؛
- source commit: `6a40b53a7333c9841f083456de88e701b98c4bd2`؛
- URL: [private Stage 3 evidence preview](https://trading-bot-uiux-stage3-auth.mohsenbarari235.chatgpt.site)؛
- access: custom owner-only با `allowed users=1 / groups=0 / external=0`؛
- anonymous root و evidence: هر دو `401 + no-store + no-referrer`؛
- errors-only logs: `0`؛ environment entries: `0`؛
- local archive: `42` فایل / `1272101` byte / SHA-256 `060d1369eeab1d3790ecab7a091ef3df811a850771e5bd337b0208e56bcab7d4`؛
- provider-normalized archive: `42` فایل / `1843200` byte / SHA-256 `e9a1f2a94da6c3bd59dbf68594e41184a160dd7c653f14183a3a0c7d6ffc62c5`.

state provider-managed bypass فقط به‌صورت «present» مشاهده شد؛ مقدار آن هرگز خوانده، استفاده، persist یا expose نشد. provenance کامل در [SITES_PROVENANCE](uiux-stage3-shell-auth-public-flows/SITES_PROVENANCE.json) ثبت است.

## مرز محتوا و release

[Content necessity matrix](uiux-stage3-shell-auth-public-flows/CONTENT_NECESSITY_MATRIX.md) policy را حفظ می‌کند، اما frozen evidence assertion کمی مستقلی برای always-visible/duplicate/metadata/counter count ندارد. بنابراین این countها `not_measured_by_frozen_evidence` هستند و صفر یا pass اختراعی اعلام نشده‌اند؛ این measurement گیت contract-hard تکمیل Stage 3 نیست.

opaque-cookie cutover عمداً با Login JS قدیمیِ ازقبل‌بارگذاری‌شده که `registration_token` انتظار دارد سازگار نیست؛ raw fallback ممنوع است. production deploy باید atomic/maintenance یا version-gated forced reload داشته باشد. Stage 3 ادعای zero-downtime compatibility ندارد؛ این مورد release carry-forward است، نه technical closure failure.

## مراجع closure

- [Stage 3 package](uiux-stage3-shell-auth-public-flows/README.md)
- [Runtime contract](uiux-stage3-shell-auth-public-flows/RUNTIME_CONTRACT.md)
- [Validation ledger](uiux-stage3-shell-auth-public-flows/VALIDATION.md)
- [Route/shell manifest](uiux-stage3-shell-auth-public-flows/ROUTE_SHELL_MANIFEST.json)
- [Protected manifest](uiux-stage3-shell-auth-public-flows/PROTECTED_SURFACE_DIFF_MANIFEST.json)
- [Frozen evidence manifest](uiux-stage3-shell-auth-public-flows/EVIDENCE_MANIFEST.json)
- [Sites provenance](uiux-stage3-shell-auth-public-flows/SITES_PROVENANCE.json)

Stage 3 کامل است. Stage 4 مجاز و شروع‌نشده باقی می‌ماند.
