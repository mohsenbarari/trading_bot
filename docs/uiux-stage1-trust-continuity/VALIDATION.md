# Stage 1 — Validation ledger

وضعیت کلی: `complete_with_explicit_visual_carry_forward`; گیت trust/state پاس است و visual exact suite به‌درستی PASS ادعا نمی‌شود.

## مبنای مقایسه

- base: `e6dcad4b157c6fad7930ba9709f28f546068f5f8`؛
- runtime head: `c5fc56996d7f8fa9c575646d70e8785811564633`؛
- baseline قدیمی Stage `0B-6`: `35/35` فایل و `322/322` تست؛ فقط نقطه شروع و نه evidence fresh Stage 1.

## commit ledger

| slice | commit | وضعیت |
| --- | --- | --- |
| foundation | `88f167d8bfe16b410e5a690a4329c202d27dc24d` | `landed` |
| notifications | `a552e264ba858ab9e78f1075b3109a1fcdba9dc0` | `landed` |
| initial route truth | `8e96beef53485d8c48c2fccbb5d600e9d9bc4f07` | `landed` |
| workspaces | `bea58d4e4aaae6e73b98c3ad323e2eb85fa89581` | `landed` |
| admin lists/forms | `6f12fbfc44325b5b0485118fa585c4e2a5072b77` | `landed` |
| dashboard recovery | `a4828a5a9fcf50d6254d014feea522f307c0beab` | `landed` |
| UserProfile | `8a3ff7519677702c65a760518df8e3690b968fa7` | `landed` |
| Auth | `43df38fcc46e6eb3b4106b36d0376cf961e0c41f` | `landed` |
| customer nav clearance | `f897a6fa9920c112efa7336d67479654f1d4ce4e` | `landed` |
| visual route fixtures | `c5fc56996d7f8fa9c575646d70e8785811564633` | `landed` |

## fresh technical gate

| gate | وضعیت | نتیجه |
| --- | --- | --- |
| focused Stage 1 unit | `passed` | `39/39` فایل؛ `413/413` تست؛ fail/skip `0`; `/tmp/uiux-stage1-runtime-gate.json`; SHA-256 `2d2063d9a3d7e4192f5909212ee565c64ae984cefe9cf26208072cc4f564b2b8`; `108745.17ms`; start `2026-08-08T22:37:05.294Z` |
| protected regression unit | `passed` | `10/10` فایل؛ `231/231` تست؛ fail/skip `0`; `/tmp/uiux-stage1-protected-unit-gate.json`; SHA-256 `b8195779fcb9fd7edf7f03eaf8353cc491c3f983c447f2d990773ca0040e491f`; `40258.86ms`; start `2026-08-08T22:39:14.373Z` |
| full unit suite | `passed` | `132/132` فایل؛ `1255/1255` تست؛ fail/skip `0`; `/tmp/uiux-stage1-full-unit-gate.json`; SHA-256 `adb1beada7cf71de2f3324ec906e3ff1248987de5d00a5e9a989f40349813460`; `289284.44ms`; start `2026-08-08T22:40:11.890Z` |
| `vue-tsc --noEmit --pretty false` | `passed` | exit `0` |
| production build | `passed` | `npm run build`; exit `0`; `2148` module؛ فقط warning معمول Browserslist/chunk-size |
| UI guard | `passed` | `npm run guard:ui`; سه guard؛ exit `0` |
| viewport acceptance | `passed` | `e2e/non-messenger-viewport.spec.ts`; عرض‌های `360/375/390/414/430/768/1024/1440`; `8/8` |
| mock market mutation | `passed` | `e2e/market-mutation-ux.spec.ts`; `2/2`; mock-only |
| protected source diff | `passed` | changed path `0`; diff SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Dashboard protected region | `passed` | base/head هر دو SHA-256 `ac18a6f06dc95bf77d4c577e5cf97ec8248fe1dde8ae09cafab5370fc27adbd5` |
| snapshot mutation check | `passed` | snapshotهای `non-messenger-visual-baseline` بدون تغییر |

Vite در E2E به‌علت backend mockشده گاهی websocket proxy `EPIPE` ثبت کرد؛ تست‌ها سبز ماندند و این log product failure نیست.

## visual exact comparison

وضعیت: `known_drift_carried_forward_stage2`؛ **نه passed**.

- total `26`؛ passed `21`؛ failed `5`؛ exit code `1`؛ مدت حدود `1.5m`؛ snapshot update `0`؛
- `mobile-390:market`: `1725px`; همان mismatch دقیق `1725px` روی build base دست‌نخورده `e6dcad4b` بازتولید شد و protected source diff صفر است؛ inherited renderer/snapshot drift؛
- `mobile-390:customers`: `2243px`; list-only/state continuity تأییدشده در برابر snapshot خالی قدیمی؛
- `mobile-390:profile`: `7887px`; self fixture کامل و منبع‌حقیقت در برابر placeholder قدیمی؛
- `desktop-1440:profile`: `9965px`; همان drift fixture profile؛
- `mobile-390:invite-landing`: `16442px`; دعوت pending معتبر/canonical در برابر snapshot invalid/قدیمی؛ desktop invite پاس است.

این پنج comparison نیازمند rebaseline/audit آگاهانه در Stage 2 هستند. هیچ‌کدام با update خودکار snapshot پنهان نشده‌اند. mismatch بازار به‌طور مستقل روی base بازتولید شد و چهار mismatch دیگر مستقیماً به fixture/state قدیمی قابل انتساب‌اند؛ بنابراین برای گیت truth/state Stage 1 non-blocking هستند.

## E2E mutation quarantine

suiteهای real mutation مربوط به market offers/schedule/messenger اجرا نشدند: backend disposable در دسترس نبود، port `8000` بسته و Docker unavailable بود. چون این سناریوها DB و global market settings را mutate می‌کنند، اجرای آن‌ها روی محیط غیر disposable ناامن است. وضعیت آن‌ها `not_run_environment_safety` است و pass ادعا نمی‌شود؛ mock-only مجاز `2/2` پاس است.

## failure-path acceptance

- initial API failure به blank، false empty یا infinite loading ختم نمی‌شود؛
- retained refresh داده معتبر را حفظ می‌کند؛
- retry query/selection/draft/modal/step را نگه می‌دارد؛
- mutation هم‌کلید فقط یک request می‌فرستد؛
- confirm حساس تا receipt معتبر باز و error inline باقی می‌ماند؛
- notification reconnect history/preference مجاز را refetch و rows را با ID dedupe می‌کند و total نمی‌سازد؛
- deep link در error به landing/not-found دروغین redirect نمی‌شود؛
- protected Market/Messenger و freezeهای Stage 1 source/behavior diff ندارند.

## تصمیم گیت

```text
stage1Status = complete
stage1TechnicalGate = passed
visualComparison = known_drift_carried_forward_stage2
nextAuthorizedRuntimeStage = Stage 2
stage2RuntimeImplementationAuthorized = true
stage2RuntimeWorkStarted = false
```

Stage 2 مجاز است و در زمان این closure هنوز شروع نشده است.
