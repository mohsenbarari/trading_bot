# Stage 0B-3 validation

تاریخ: ۲۰۲۶-۰۸-۰۸

وضعیت: audit رسمی Figma پاس؛ طراحی و evidence در ۲۰۲۶-۰۸-۰۸ به‌صورت صریح توسط مالک محصول برای ادامه به `0B-4` تأیید شده است؛ runtime implementation انجام یا مجاز نشده است

## دامنه اعتبارسنجی

- صفحه Stage: `55:2`
- Foundations: `41:2`
- Components: `46:2`
- ۱۰ root موبایل ۳۹۰×۸۴۴
- پنج proof موبایل در عرض‌های ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰
- یک proof مستقل master/detail در ۱۴۴۰×۹۰۰
- state atlas برای loading/error/empty/search-empty/missing-detail
- content-necessity، action truth، protected-surface و contrast audit

## نتیجه audit رسمی Figma — schema 2

| کنترل | نتیجه |
| --- | --- |
| mobile root | `10 / 10` |
| responsive proof | `5 / 5` |
| desktop proof | `1 / 1` |
| overflow | `0` |
| missing font | `0` |
| text-height issue | `0` |
| forbidden copy | `0` |
| protected Market/Messenger leak | `0` |
| semantic target | `61` مورد، حداقل `48×44` |
| font family | فقط `Vazirmatn` |

rootهای موبایل:

```text
56:6  56:57  56:129
57:93  57:177  57:235
58:231  58:309
60:325  60:386
```

responsive proofها:

```text
62:397  62:465  62:533  62:602  62:670
```

desktop proof:

```text
63:544 — 1440×900
```

در جریان مالی، `M06` موبایل مرحله مرور before/after و اقدام تأیید را اثبات می‌کند؛ فرم ویرایش مرحله قبل در proof دسکتاپ رسمی و شواهد مشتق‌شده ثبت شده است.

## Foundation و component audit

| مورد | نتیجه |
| --- | --- |
| primitive variable | `20` |
| semantic variable | `26` |
| dimension variable | `19` |
| جمع variable | `65` |
| broken alias | `0` |
| component set | `5` |
| variant | `17` |

پنج component set مرجع، Button، Status، Relation Row، Authenticated Header و Bottom Navigation را پوشش می‌دهند. componentها در این Stage قرارداد طراحی‌اند و هنوز به runtime متصل نشده‌اند.

## contrast audit

هر ۹ جفت آزمون‌شده پاس شده‌اند:

```text
13.428  5.729  7.056  4.548  5.722
5.051   5.364  5.009  4.548
```

کمینه متن عادی برابر یا بیشتر از `4.5:1` است. این audit رنگ‌های نهایی Figma را پوشش می‌دهد؛ focus/state غیرمتنی در پیاده‌سازی باید حداقل `3:1` باقی بماند.

## invariantهای محتوایی و سناریویی

- Operations هیچ شمارنده مسیر/ابزار، role chip یا توضیح permission ندارد.
- حسابدار destinationهای مالک را نمی‌بیند و یک recovery معتبر به حساب دارد.
- موبایل در هر root فهرست یا جزئیات را نشان می‌دهد، نه هر دو را.
- count فقط برای pending queue اقدام‌پذیر استفاده شده است.
- customer financial edit مرور before/after و اثر future-only دارد.
- تغییر جدید، سوابق تکمیل‌شده را به‌صورت retroactive تغییر نمی‌دهد.
- accountant duty edit متن فعلی را در دو محل تکرار نمی‌کند و feedback در همان context است.
- session action فقط metadata لازم را نمایش می‌دهد؛ `home_server` حذف است.
- confirm حذف حساب، غیرفعال‌شدن وب‌اپ/بات، پایان نشست‌ها، انقضای آفر فعال، لغو دعوت pending و بسته‌شدن بازگشتی روابط متعلق یا لینک‌شده را توضیح می‌دهد.
- loading، load error، true empty، search empty و missing detail از هم متمایزند.
- desktop همان داده موبایل را در master/detail استفاده می‌کند و KPI یا navigation تازه ندارد.
- هیچ صفحه، FAB، status یا رفتار داخلی بازار و پیام‌رسان در artifact طراحی نشده است.

## شواهد مستقیم Figma

| فایل | source node |
| --- | --- |
| `assets/figma-operations-role-scenarios.png` | `55:19` |
| `assets/figma-customer-workspace-scenarios.png` | `55:22` |
| `assets/figma-accountant-workspace-scenarios.png` | `55:25` |
| `assets/figma-actions-and-state-atlas.png` | `55:28` |
| `assets/figma-responsive-and-desktop-proofs.png` | `55:31` |
| `assets/figma-desktop-customer-master-detail-1440x900.png` | `63:544` |

checksum همه شش خروجی مستقیم و فایل metrics ممیزی Figma در `FIGMA_SNAPSHOT_MANIFEST.json` ثبت شده است.

## harness محلی مشتق‌شده

دستور بازتولید:

```bash
node docs/uiux-stage0b-operations-workspaces/capture-evidence.cjs
```

هارنس پیش از promote artifactها، این کنترل‌ها را fail-closed اجرا می‌کند:

- width sweep دقیق ۳۶۰/۳۷۵/۳۹۰/۴۱۴/۴۳۰؛
- نبود overflow افقی و عمودی در rootهای محصول؛
- حداقل ۴۴×۴۴ برای target عمومی و ۴۸px برای CTA؛
- بارگذاری واقعی Vazirmatn؛
- جدایی list/detail موبایل و master/detail دسکتاپ؛
- وجود recoveryهای state atlas؛
- نبود واژه‌ها و اطلاعات ممنوع؛
- نبود interior بازار/پیام‌رسان؛
- ابعاد دقیق screenshot دسکتاپ ۱۴۴۰×۹۰۰.

اجرای نهایی محلی `17/17` assertion را با صفر failure و صفر page error پاس کرد. ۱۶ root سنجیده شد، ۱۷۸ target تعاملی حداقل `44×44` بودند، ۹ CTA حداقل ۴۸ پیکسل ارتفاع داشتند و هر چهار face فونت Vazirmatn بارگذاری شد. شناسه‌های همان ۱۷ assertion به‌صورت exact contract کنترل و اندازه‌گیری‌ها پس از capture دوباره اجرا شدند. هفت PNG مشتق‌شده و فایل metrics پس از بسته‌شدن مرورگر، با backup کامل و دو rename سطح پوشه در `assets/local-evidence/` منتشر شدند؛ در فاصله دو rename مسیر live ممکن است موقتاً وجود نداشته باشد، اما bundle ناقص یا مخلوط قابل انتشار نیست و recovery پیش از بررسی dependencyها اجرا می‌شود. هیچ پوشه staging باقی نماند. metrics دارای `runId` و checksum captureهای همان اجراست و نتیجه عددی و checksum همه artifactها در manifest ثبت شده است. harness منبع طراحی نیست و در اختلاف، قرارداد و nodeهای editable Figma اولویت دارند.

## baseline runtime

تست‌های runtime در این Stage فقط برای ثبت رفتار موجود اجرا می‌شوند؛ سبزبودن آن‌ها اجرای طرح تازه را اثبات نمی‌کند. دامنه baseline مرتبط:

```text
OperationsView
CustomerWorkspaceView
AccountantWorkspaceView
OwnerCustomerManagerModal
OwnerAccountantManagerModal
AppPrimitives
WorkspacePrimitives
router
BottomNav
```

اجرای سریالی مرجع ۷۴/۷۴ تست را پاس کرده است. اجرای موازی پیش‌فرض می‌تواند در محیط مشترک به contention و timeout نامرتبط برسد؛ نتیجه سریالی برای این checkpoint مبناست.

دستور مرجع از پوشه `frontend`:

```bash
npm run test:unit:run -- --maxWorkers=1 \
  src/views/OperationsView.test.ts \
  src/views/CustomerWorkspaceView.test.ts \
  src/views/AccountantWorkspaceView.test.ts \
  src/components/OwnerCustomerManagerModal.test.ts \
  src/components/OwnerAccountantManagerModal.test.ts \
  src/components/ui/AppPrimitives.test.ts \
  src/components/workspace/WorkspacePrimitives.test.ts \
  src/router/index.test.ts \
  src/components/BottomNav.test.ts
```

## حدود ادعا

شواهد static نمی‌توانند API واقعی، authorization، router history، بازیابی scroll، focus return، screen reader، keyboard موبایل، WebView تلگرام، شبکه کند، busy lock یا cascade دیتابیس را اثبات کنند. این موارد در Stageهای ۱، ۴، ۵، ۷ و ۸ validation اجرایی دارند.

این checkpoint از نظر طراحی و evidence فنی کامل و در ۲۰۲۶-۰۸-۰۸ به‌صورت صریح توسط مالک محصول برای عبور به `0B-4` تأیید شده است. این تأیید مجوز تغییر runtime نیست و runtime تا تأیید صریح `0B-6` ممنوع می‌ماند.
