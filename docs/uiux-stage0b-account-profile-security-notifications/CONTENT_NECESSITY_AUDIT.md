# Stage 0B-5 content-necessity audit

وضعیت: **قرارداد Phase 0، audit مستقیم Figma schema 2، harness محلی `27 / 27` و پیش‌نمایش خصوصی source-bound پاس شده‌اند؛ تأیید بصری مالک محصول pending است.**

اصل مرجع: هر واحد پیش‌فرض باید تصمیم، اقدام، وضعیت ضروری یا پیشگیری از ریسک را روشن کند. وجود داده در API، فضای خالی، route یا نقش کاربر به‌تنهایی دلیل نمایش نیست.

| قاب | پرسش کاربر | Keep | On demand | Remove از نمای پیش‌فرض |
| --- | --- | --- | --- | --- |
| `M01` مرکز حساب عادی | مقصد کار شخصی من کجاست؟ | هویت پس از load؛ مقصدهای یکتای پروفایل، امنیت، حافظه و اعلان؛ Telegram فقط اگر واقعاً مجاز و unlinked | جزئیات ثانویه حساب | badge تکراری «فعال»، نام فرضی «کاربر»، دو مقصد برای یک route، server/path metadata، کارت‌های summary |
| `M02` مرکز حساب حسابدار | کدام کار شخصی واقعاً برای من مجاز است؟ | پروفایل، حافظه، اعلان | توضیح کوتاه فقط هنگام منع اثرگذار | نشست/logout/Telegram، کارت بلند محدودیت، status تزئینی |
| `M03` پروفایل شخصی | هویت لازم و اقدام قابل‌ویرایش من چیست؟ | avatar، نام، شناسه لازم، mobile read-only، آدرس، edit action | membership، history و relation context فقط هنگام نیاز واقعی | trade/relation count، project users، shortcut تکراری، route/server metadata |
| `M04` ویرایش پروفایل | چه چیزی قابل تغییر است و نتیجه چه شد؟ | avatar/address، privacy helper لازم، validation و feedback همان‌جا | راهنمای فرمت در صورت خطا | password/MFA/email/mobile/name edit خیالی، success جدا از فرم، پاک‌کردن ورودی در failure |
| `M05` پروفایل عمومی | آیا این همان فرد است و چه چیزی مجاز است ببینم؟ | identity لازم، phone masked، address hidden، یک header و action مجاز | روابط/تاریخچه و داده کامل فقط با مجوز واقعی | phone/address کامل برای viewer عادی، header تکراری، route wording، count و تاریخچه همیشه‌نمایان |
| `M06` نشست‌ها | کدام دستگاه‌ها متصل‌اند و کدام اقدام امن است؟ | device، platform، last activity، signal لازم current/primary | IP و جزئیات فنی مفید | `home_server`، چند badge هم‌معنا، metadata کامل user-agent، count تزئینی |
| `M07` پایان نشست | دقیقاً کدام نشست پایان می‌یابد؟ | target device، حفظ نشست جاری، copy «نشست‌های دیگر»، confirm و outcome همان‌جا | جزئیات ریسک کوتاه | «خروج از همه» گمراه‌کننده، ادعای revoke در failure، toast مبهم |
| `M08` حافظه محلی | چه چیزی روی این دستگاه پاک می‌شود؟ | cache size واقعی یا size-error، local device scope، busy/success/failure | توضیح redownload | ادعای حذف account/message/server/session، session panel تکراری، صفر دروغین هنگام خطا |
| `M09` مرکز اعلان | چه چیز تازه یا قابل اقدام است؟ | content، time، مقصد واقعی، یک signal new/unread، tabهای معاملات/سایر | جزئیات ثانویه item | category total از ۵۰ مورد، route خام، چند نشان تکراری، کلیک روی item بدون route، delete/clear |
| `M10` Push | آیا در این browser/device اکنون اقدامی ممکن است؟ | state واقعی و enable فقط در حالت actionable | راهنمای permission/browser متناسب با state | disable/test پشتیبانی‌نشده، تضمین تحویل، Telegram/cross-device claim، preference بازار |

## ماتریس visibility ضروری

| viewer | Keep | Remove/Hide |
| --- | --- | --- |
| self | mobile کامل read-only، address کامل و edit avatar/address | editهای پشتیبانی‌نشده و metadata بی‌اثر |
| normal viewer | phone masked، identity لازم و action مجاز | address و phone کامل، تاریخچه/count پیش‌فرض |
| authorized admin | فقط داده و action مطابق permission backend | افشای فراتر از مجوز یا action مدیریتی خارج دامنه |
| forbidden/unavailable | توضیح کوتاه و recovery امن | هر داده شخصی، success/empty دروغین و spinner نامحدود |

## stateهای recovery

| state | Keep | Remove |
| --- | --- | --- |
| Loading | skeleton هم‌شکل بدون fact فرضی | نام/status/data ساختگی |
| Load error | پیام cause-neutral و retry نزدیک | empty یا cache stale بی‌توضیح |
| True empty | توضیح کوتاه متناسب با حوزه | KPI صفر و کارت تزئینی |
| Category empty | نام category و بازگشت tab | ادعای نبود هیچ اعلان |
| Unavailable/forbidden | علت کوتاه اثرگذار و recovery | افشای policy داخلی یا action نامعتبر |
| Busy/success/failure | feedback کنار همان action و حفظ context | reset زودهنگام و toast مبهم |
| Size error | عدم دسترسی به اندازه و retry | نمایش `0.00 MB` |
| Route-less notification | item غیرکلیک‌پذیر با content/time | route خام یا affordance دروغین |

## قوانین حذف قطعی

- تعداد رابطه، معامله، پروژه، category اعلان یا نشست به‌عنوان KPI؛
- route، path، server و source backend در محتوای محصول؛
- status مثبت تکراری وقتی action را تغییر نمی‌دهد؛
- action یا تنظیمی که API فعلی ندارد؛
- تضمین realtime recovery، Push، Telegram یا cross-channel delivery؛
- UI داخلی بازار، تنظیم اعلان آفر بازار و UI داخلی پیام‌رسان؛
- داده واقعی کاربر در هر artifact مستقیم یا مشتق‌شده.

## نتیجه ممیزی نهایی

- ۱۰ root موبایل `390×844`، پنج proof responsive و یک proof دسکتاپ `1440×900` با همین inventory تطبیق داده شدند؛
- audit مستقیم Figma در `2026-08-08T17:11:05.475Z` هر ۲۷ assertion را پاس کرد؛ صفر noise ممنوع، صفر route/backend metadata، صفر interior بازار/پیام‌رسان و فقط identity synthetic ثبت شد؛
- harness محلی run `2839230-1786210464518` نیز `27 / 27`، صفر failure/page error، content parity هر پنج عرض و desktop بدون fact تازه را ثبت کرد؛
- notification row فقط content/time/destination واقعی و یک signal تازه دارد؛ count دسته‌ای، route خام، delete/clear و click در item بدون route وجود ندارد؛
- account hub مقصدهای یکتا دارد؛ account status مثبت، role chip، back/header تکراری و fact فرضی پیش از load حذف شده‌اند؛
- پروفایل عمومی برای normal viewer شماره masked و آدرس hidden دارد؛ روابط/تاریخچه و اقدام‌های خارج مجوز در نمای پیش‌فرض وجود ندارند؛
- نشست، حافظه و Push stateهای نامعتبر را به success، empty یا action قابل اجرا تبدیل نمی‌کنند.

دو carry-forward غیرمسدودکننده Figma به‌صورت صریح حفظ شده‌اند: avatar initials در component inherited text style محلی exact ندارد و variantهای قدیمی Operations-active Bottom Navigation بدهی focus/layout/style پیش از 0B-5 دارند. این موارد محتوای محصول را متراکم نکرده‌اند و variantهای Account-active و rootهای این Stage همه گیت‌های fit، contrast و interaction را پاس کرده‌اند.

## گیت

این inventory ورودی الزام‌آور طراحی و معیار ثبت‌شده audit است. تطبیق nodeهای Figma، exportهای مستقیم، harness fail-closed و archive/source پیش‌نمایش خصوصی پاس شده و شواهد فنی Stage بسته است. محتوای signed-in Sites بدون bypass token واکشی نشده؛ بنابراین تأیید بصری مالک هنوز لازم است. Stage 0B-5 design-only است، `0B-6` آغاز نشده و تغییر runtime تا تأیید صریح آن ممنوع می‌ماند.
