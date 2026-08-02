# Stage 1 — فهرست lineage و تصمیم consolidation برای staging

وضعیت: `IN_PROGRESS`

Branch: `stage/three-site-staging-01-baseline`

Candidate: `candidate/three-site-staging`

Base: `198c2d65a4edb11f51d5b92b9fc0fca747cb97da`

Roadmap: `docs/THREE_SITE_STAGING_RELEASE_ROADMAP.md`

این سند فقط منشأ و تصمیم تغییرات را ثبت می‌کند. وجود یک commit در branchهای
قدیمی به‌معنای مجوز merge آن نیست.

---

## 1. backup پیش از consolidation

پیش از ایجاد candidate جدید، backup کامل Git ساخته و verify شد:

- Path: `/root/trading-bot/git-backups/trading-bot-pre-stage1-20260802.bundle`
- Size: `81843085 bytes`
- SHA-256: `c923a93285984f4dcfd9027b15f5e6497f6e4fbd64fb42f8451107789c96ade3`
- Refs: `316`
- Verification: `git bundle verify` موفق؛ complete history ثبت شده است.

tagهای ایمنی مستقل:

| Tag | Commit |
|---|---|
| `backup/stage1-main-20260802` | `9105264000f51b480eb88f1f80845fd7608bd6b2` |
| `backup/stage1-roadmap-g0-20260802` | `198c2d65a4edb11f51d5b92b9fc0fca747cb97da` |
| `backup/stage1-legacy-candidate-20260802` | `2152e053617562a089c1d9eb7a709118d13fed8f` |
| `backup/stage1-full-matrix-relay-20260802` | `1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2` |
| `backup/stage1-production-integration-20260802` | `a9e68a96c6befd5379c8d1f001df75601e2cb75c` |
| `backup/stage1-release0-final-20260802` | `bdc01701279e52c4f5833dd9cb3a67aace6dddf0` |
| `backup/stage1-emergency-audit-20260802` | `e1385cdc7cf4eb9daa6f20953cbacd1e96d8cc57` |
| `backup/stage1-audited-checkpoint-20260802` | `1a07b9df0f717bd62fe5eda61b9a0f8f81aba5dc` |
| `backup/stage1-coin-price-20260802` | `21f131e6dbaddd26b592f609e3f590ab6d162b21` |

هیچ branch یا worktree قدیمی در Stage 1 حذف نمی‌شود.

---

## 2. معانی تصمیم‌ها

- `ADOPT`: commit با حفظ منشأ به candidate منتقل می‌شود.
- `REIMPLEMENT`: invariant لازم است، اما patch قدیمی مستقیم قابل استفاده نیست؛
  تغییر کوچک و متناسب با candidate نوشته می‌شود.
- `DEFER`: برای M1/M2 ضروری نیست یا باید در Stage بعد/roadmap pre-production
  بررسی شود.
- `REJECT`: تغییر نامرتبط، متناقض یا مخرب است و وارد staging candidate نمی‌شود.
- `RETAIN_BASE`: تغییر از قبل در main/base وجود دارد و نگه داشته می‌شود.

---

## 3. تصمیم branch-level

| Lineage | Divergence از main | اندازه tree diff | تصمیم | علت |
|---|---:|---:|---|---|
| `main@91052640` | `0 behind / 0 ahead` | baseline | `RETAIN_BASE` | شالوده canonical و دو hardening نهایی main |
| `candidate/three-site-production-ready` | `0 / 2` | `3 files, +2015` | `REJECT` | parser قیمت و گزارش؛ تغییر معماری ندارد و قیمت نامرتبط است |
| `feature/three-site-full-matrix-live-driver-v3` | `2 / 4` | `135 files, +36575/-344` | `DEFER` | Full Matrix کامل شرط M1/M2 نیست؛ patch بسیار بزرگ است |
| `fix/full-matrix-relay-approval-lifetime` | `2 / 16` | `197 files, +69876/-1548` | `SELECTIVE` | پنج hardening کوچک approval پذیرفته می‌شوند؛ driver و campaignهای بزرگ deferred هستند |
| `work/production-three-site-integration-91052640` | `0 / 89` | `328 files, +301305/-2559` | `DEFER` | production-shadow خارج از roadmap staging است |
| `work/release0-final-integration` | `220 / 96` | `987 files, +113806/-224491` | `REJECT` | بر پایه lineage قدیمی، حذف گسترده DR و ناسازگار با main فعلی |
| `audit/emergency-provenance-coherent` | `220 / 33` | `791 files, +13277/-223775` | `DEFER` | emergency production/IR و ناسازگار با baseline staging |
| `checkpoint/three-site-mvp-audited-20260801` | `220 / 75` | `1663 files, +472605/-225175` | `DEFER` | checkpoint forensic است، نه release candidate |
| `candidate/coin-price-intelligence` | `2 / 19` | `133 files, +34160/-60` | `REJECT` | قابلیت مستقل بازار؛ خارج از معماری سه‌سایته |

`DEFER` و `REJECT` به‌معنای حذف تاریخچه نیستند؛ bundle و tagهای بالا بازیابی
آن‌ها را حفظ می‌کنند.

---

## 4. تصمیم commit-level برای lineage Full Matrix

### 4.1 commitهای پذیرفته‌شده برای Stage 1

این پنج commit یک زنجیره کوچک hardening برای approval relay هستند و مستقل از
live driver قابل استفاده‌اند:

| Commit | تصمیم | invariant |
|---|---|---|
| `8af0c02556a50206af41642554a59e6dca1db5e9` | `ADOPT` | verifier اجازه fallback مخفی به env برای Witness trust key نمی‌دهد |
| `b03c682f45c1e0ecf83dd13b0d3106754ec82c66` | `ADOPT` | reusable session فقط در مسیر صریح relay پذیرفته می‌شود |
| `0f6b687803f5ee5b31bee943652928b50399b9e5` | `ADOPT` | scope پیش‌فرض session به عملیات زنده ضروری محدود می‌شود؛ Stage 3 برای actionهای اضافه allowlist صریح می‌دهد |
| `da72f60a39c2d060f36aec50b1ba8e7ab0eb33a1` | `ADOPT` | relay receipt به hash دامنه session متصل می‌شود |
| `0f79f63f853be46d19048502cbc909ebc37a2437` | `ADOPT` | failover verifier کلید Witness را از backend config امضاشده می‌گیرد |

این commitها به همان ترتیب تاریخی cherry-pick و تست می‌شوند. conflict یا
وابستگی اعلام‌نشده باعث توقف و `REIMPLEMENT` خواهد شد، نه branch جدید.

### 4.2 commitهای deferred

| Commit/range | تصمیم | مقصد |
|---|---|---|
| `76d5d37b..223c5927` | `DEFER` | live driver و activation assertion؛ pre-production Full Matrix |
| `cb0ee67b` | `DEFER` | approval trust مختص Full Matrix driver |
| `348fa1e2..13b4df32` | `DEFER` | midpoint/refresh ceremony مختص Full Matrix بلندمدت |
| `faed5577` | `DEFER` | relay material automation؛ Stage 3 فقط اگر host audit نیاز آن را اثبات کند |
| `e0963369` | `DEFER` | campaign seed publisher بزرگ؛ workflow موجود برای M1 کافی است |
| `984ab71f` | `DEFER` | fresh Full Matrix campaign baseline با ۴۶ فایل و ۲۲هزار خط |
| `1ddf277b` | `DEFER` | Queue activation؛ فقط Stage 6 و به‌صورت direct one-line successor |

---

## 5. reimplementationهای کوچک Stage 1

### 5.1 قرارداد convergence upload

وضعیت main:

- runtime در `c16fc867` عمداً از presigned PUT به signed S3 form POST تغییر کرده؛
- `wa_ir_object_storage_preflight_agent.py` POST و form fields را validate می‌کند؛
- تست `test_three_site_staging_convergence_evidence.py` هنوز mock و assertion
  قدیمی PUT را نگه داشته است.

تصمیم: `REIMPLEMENT TEST`؛ runtime POST حفظ و تست با contract واقعی POST همسان
می‌شود. live Arvan upload در Stage 3 انجام می‌شود و Stage 1 شبکه خارجی را تغییر
نمی‌دهد.

### 5.2 Queue baseline

وضعیت main:

- runtime env پیش‌فرض owner=`legacy` و cutover=`false` است؛
- code capability در `3138d0c2` به `True` تغییر کرده است؛
- verifier activation یک baseline `False` و successor مستقیم `True` می‌خواهد.

تصمیم: `REIMPLEMENT BASELINE`؛ readiness constant در candidate Stage 1 به
`False` بازگردانده می‌شود و تست‌های runtime fail-closed اجرا می‌شوند. activation
در Stage 6 باید commit مستقیم و تک‌خطی جداگانه باشد.

---

## 6. فایل‌ها و قابلیت‌هایی که نباید وارد Stage 1 شوند

- `core/market_intelligence/group_trade_parser.py` از candidate قبلی؛
- `deploy/production/three-site-shadow/*`؛
- `scripts/production_shadow_*` و production cutover controllerها؛
- `deploy/emergency-ir/*` و emergency artifact publishers؛
- `scripts/full_matrix_drivers/driver.py` و `scripts/full_matrix_live/*`؛
- release0 v2 reconciliation و production lease overlays؛
- Queue activation commit.

G1 باید با `git diff --name-status main..candidate/three-site-staging` این مرز را
دوباره اثبات کند.

---

## 7. وضعیت تصمیم‌ها

- Backup: `PASSED`
- Candidate isolation: `PASSED`
- Branch-level inventory: `COMPLETED`
- Five approval hardenings: `APPLIED_AND_TESTED`
- Convergence POST test alignment: `APPLIED_AND_TESTED`
- Queue disabled baseline: `APPLIED_AND_TESTED`
- G1 decision: `ACCEPTED`

### 7.1 منشأ و commit مقصد تغییرات پذیرفته‌شده

| Source | Candidate commit | نتیجه |
|---|---|---|
| `8af0c02556a50206af41642554a59e6dca1db5e9` | `72e2956ef2c16fa0bbaca766061c8ec01ed4f1dc` | `ADOPTED` |
| `b03c682f45c1e0ecf83dd13b0d3106754ec82c66` | `11ca6503a4845650447f2c64fb7710027c8b6b3b` | `ADOPTED` |
| `0f6b687803f5ee5b31bee943652928b50399b9e5` | `e1ec7036235756f223e05270ebd0134e373ea58f` | `ADOPTED` |
| `da72f60a39c2d060f36aec50b1ba8e7ab0eb33a1` | `2348dba2a58adf94e9aab5b5d376ed21ae66dfe0` | `ADOPTED` |
| `0f79f63f853be46d19048502cbc909ebc37a2437` | `e5fc5b483778281ab4050d718da706eddf53b66a` | `ADOPTED` |
| contract واقعی presigned POST در main | `03613052add09c6c624885596639405cc084db69` | `REIMPLEMENTED_TEST` |
| Queue-disabled staging baseline | `2da32750f85a1283aade34c5f83fc328562c3255` | `REIMPLEMENTED_BASELINE` |

تمام cherry-pickها بدون conflict انجام شدند. تست‌های approval relay شامل ۲۷ تست،
تست convergence شامل ۶ تست و تست‌های Queue baseline شامل ۱۲ تست بودند و همگی
موفق شدند. مجموعه هسته ۹۴ تست و discovery سه‌سایتی ۱۹۱ تست نیز بدون failure
عبور کردند؛ در discovery تعداد ۲۴ تست وابسته به PostgreSQL واقعی، مطابق مرز
Stage 1، skip شد.
