# Codex Final Review — Plan Governance

- Reviewer authority: `CODEX_FINAL_REVIEWER`
- Review base SHA: `f3be9ae2c4e9df0da31abaa41e91f327eb9573a2` (`main`)
- Reviewed head SHA: `90b8bf4737ba934dac02f7b04781ad9f5fd5e7d0`
- Review date (UTC): `2026-09-03`
- Verdict: `APPROVE`
- Receipt type: `PLAN_GOVERNANCE_FINAL`
- Unauthorized external mutation: `NONE`

## Scope and findings

- طرح پنج‌بخشیِ قبلاً تأییدشده، ترتیب ۵۶ Stage، قرارداد معماری و invariantهای
  Web/Bot حفظ شده‌اند.
- اختیار اجرا به Cursor Coordinator/Worker و اختیار پذیرش نهایی به Codex تفویض
  شده است؛ تأیید مرحله‌ای کاربر در چرخهٔ Stage وجود ندارد.
- یک Worker نویسنده حالت پیش‌فرض است؛ Worker نویسندهٔ دوم فقط با Pairing Receipt،
  scope و lock جدا و rollback مستقل مجاز است. integration همیشه ترتیبی است.
- candidate ابتدا روی integration branch اعمال و تست می‌شود؛ Codex همان integration
  commit را می‌پذیرد. promotion به `main` یک Branch Barrier مستقل دارد.
- انتقال Web Writer و DNS همچنان اقدام صریح عامل انسانی در Dashboard است و deploy
  اجازهٔ auto-promotion ندارد.
- تغییر نسبت به `main` به مستندات، Cursor contract و `.gitignore` مستندات محدود است؛
  application/runtime/deploy code و هیچ سیستم خارجی تغییر نکرده‌اند.
- finding باز با شدت `High` یا `Critical`: `NONE`.

## Verification

| Check | Result |
| --- | --- |
| YAML parse، ۵۶ Stage، dependency DAG و wave coverage یکتا | `PASS` |
| authority، حداکثر دو Worker، serial integration و branch barrier | `PASS` |
| Markdown local links در بستهٔ refactor/Cursor | `PASS — 25 files` |
| JSON parse | `PASS` |
| `git diff main...HEAD --check` | `PASS` |
| `memory-custodian check` | `PASS` |
| focused Market compatibility tests | `PASS — 45 tests in 4.03s` |
| secret-pattern scan روی commit حاکمیت | `PASS — no match` |
| repository status پیش از صدور receipt | `CLEAN` |

دستور تست سازگاری:

```text
PYTHONPATH=. APP_ENV_FILE=config/unit-test.env.example pytest -q \
  tests/test_capture_event_adapter.py \
  tests/test_market_pipeline_stage8_transport.py
```

## Decision and conditions

commit برابر `90b8bf4737ba934dac02f7b04781ad9f5fd5e7d0` برای بستهٔ نهایی پلن و حاکمیت
اجرا پذیرفته شد.

این receipt هیچ deploy، provisioning، secret access، data mutation، DNS/Writer
change، cleanup مخرب یا retirement را مجاز نمی‌کند. قدم بعدی فقط Branch Barrier
برای fast-forward کردن همین بسته روی `main` است. پس از آن Coordinator باید
integration branch را از `main` تمیز بسازد و نخستین Assignment را با یک Worker
نویسنده برای refresh و تکمیل evidenceهای `P1-00` صادر کند؛ `P1-00` با این receipt
`COMPLETE` نشده است.
