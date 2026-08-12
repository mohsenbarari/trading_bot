# Content Necessity Matrix — Stage 6 delivered scope

| سطح/محتوا | نیاز تصمیم یا عمل | وضعیت | مرز privacy/authority |
| --- | --- | --- | --- |
| Admin landing destinations | رسیدن به workflow مجاز نقش جاری | نگه‌داری | فقط مقصدهای واقعی؛ role filter موجود حفظ شده است. |
| synthetic counters/pending badge | هیچ receipt authoritative ندارد | حذف | نباید وضعیت یا authority ساختگی بسازد. |
| Admin directory list | یافتن user مجاز برای کار مدیریتی | نگه‌داری | list server-authorized و semantic است. |
| directory search | کاهش هزینهٔ یافتن در نشست جاری | نگه‌داری، local-only | `q` در URL/history/storage نمی‌رود. |
| `scroll` context | بازگشت قابل‌پیش‌بینی list/detail | نگه‌داری، نرمال‌شده | تنها context route مجاز است. |
| Admin detail | مشاهدهٔ server-authorized target | نگه‌داری | 403/404 generic recovery دارند. |
| Public profile identity | تشخیص targetِ مجاز | نگه‌داری | route فقط ID است. |
| Peer mobile | تماس ضروری بدون افشای کامل | masked | server projection؛ نه mask صرفاً client. |
| Peer address/presence/membership/relation/trade detail | برای peer عادی ضروری نیست | حذف از projection | مانع PII/relationship leakage. |
| Self contact/address | ویرایش و مدیریت حساب خود | نگه‌داری | فقط selfِ مجاز؛ address affordance حفظ شده است. |
| Sensitive admin actions against self/same level | عمل مدیریتی مجاز نیست | read-only/forbidden | server `403` تعیین‌کننده است. |
| 403/404 state | بازیابی امن پس از عدم دسترسی/عدم وجود | نگه‌داری | جزئیات target و PII نشت نمی‌کند. |
| Messenger/Forward discovery | در این برش نیاز جدیدی ندارد | بدون تغییر | فقط route entry canonical شد؛ discovery بازطراحی/محدود نشد. |
| invitation bearer/link | فقط اقدام صریح copy برای کار عملیاتی | in-memory/copy-only | DOM/URL/history/storage حامل token نیستند؛ 403 دادهٔ حساس را پاک می‌کند. |
| revoke confirmation | جلوگیری از mutation ناخواسته | نگه‌داری | confirm Teleport‌شده؛ cancel/Escape بدون DELETE است. |
| public block/unblock confirmation | جلوگیری از mutation ناخواسته | نگه‌داری | فقط `success:true` state را flip می‌کند؛ failure raw payload ندارد. |
| workspace account deletion | حذف حساب فقط پس از تصمیم آگاهانه | نگه‌داری، double-confirm | نام نمایش‌داده‌شده + acknowledgement؛ receipt همان relation و `deleted` تنها مسیر navigation است. |
| workspace deletion error | بازیابی امن بدون پنهان‌کردن context | نگه‌داری | 400/403/404/malformed/network relation و route را نگه می‌دارند؛ فقط پیام ثابت امن نشان می‌دهند. |
| پایان یک نشست workspace | قطع دسترسی همان نشست بدون حذف رابطه یا نشست‌های دیگر | نگه‌داری، receipt-bound | فقط `terminated_session_id` دقیقاً برابر نشست انتخابی state محلی را تغییر می‌دهد. |
| خطای پایان نشست workspace | recovery قابل بازبینی پس از خطا یا receipt نامعتبر | نگه‌داری | dialog، route، relation و اطلاعات نمایش‌داده‌شدهٔ نشست ثابت می‌مانند؛ raw server detail نمایش داده نمی‌شود. |
| mutationهای رابطهٔ workspace | لغو دعوت یا بستن/حذف همان رابطه پس از تصمیم آگاهانه | نگه‌داری، receipt-bound | فقط `id` همان relation با `revoked` یا `deleted` reconciliation/navigation را فعال می‌کند. |
| خطای mutation رابطهٔ workspace | بازبینی و retry بدون از دست‌دادن context | نگه‌داری | 400/403/404، receipt نادرست یا network dialog، relation، route و query را نگه می‌دارند؛ raw detail/message نمایش یا serialize نمی‌شود. |
| mutation کالا و نام مستعار | ایجاد/ویرایش کالا و alias فقط با outcome قابل‌نسبت‌دادن به درخواست | نگه‌داری، receipt-bound | create/edit فقط status و identity/parent مطابق receipt را می‌پذیرد؛ mismatch یا payload نامعتبر context را تغییر نمی‌دهد. |
| حذف کالا یا نام مستعار | جلوگیری از DELETE ناخواسته و بازیابی قابل‌بازبینی | نگه‌داری، confirm + receipt-bound | dialog body-teleported است؛ cancel/Escape بدون DELETE و فقط `204` خالی اعمال می‌شود؛ خطا یا receipt نامعتبر selected/form/list را با copy امن نگه می‌دارد. |
| حذف کاربر مدیریت | قطع دسترسی حساب فقط پس از تأیید صریح و receipt دقیق | نگه‌داری، confirm + receipt-bound | دیالوگ نام حساب/موبایل ندارد؛ cancel/Escape بدون درخواست است؛ فقط `200` با پیام ثابت موفقیت navigation می‌دهد. |
| پایان همه نشست‌های کاربر مدیریت | قطع نشست‌های فعال همان کاربر بدون نشت detail | نگه‌داری، confirm + receipt-bound | فقط `200` با عدد صحیح `terminated_sessions` پذیرفته می‌شود؛ `detail` خام نمایش داده نمی‌شود. |
| خطای حذف یا پایان نشست کاربر مدیریت | بازبینی و retry بدون از دست‌دادن context | نگه‌داری | 400/403/404، receipt نامعتبر یا network دیالوگ، کاربر نمایش‌داده‌شده و مسیر را نگه می‌دارند؛ raw detail نمایش یا serialize نمی‌شود. |
| پایان نشست حساب در `/account/security` | قطع همان نشست غیرجاری پس از تأیید صریح و receipt دقیق | نگه‌داری، confirm + receipt-bound | دیالوگ نام دستگاه ندارد؛ cancel/Escape بدون درخواست است؛ فقط `200` با متن ثابت موفقیت همان نشست را برمی‌دارد. |
| خروج از نشست‌های دیگر حساب | حفظ نشست جاری و پایان بقیه فقط با receipt دقیق | نگه‌داری، confirm + receipt-bound | فقط `200` با الگوی عددی پایان نشست پذیرفته می‌شود؛ `detail` خام نمایش داده نمی‌شود. |
| خطای نشست حساب | بازبینی و retry بدون از دست‌دادن فهرست | نگه‌داری | 400/403/404، receipt نامعتبر یا network دیالوگ، فهرست نشست‌ها و مسیر را نگه می‌دارند؛ raw detail نمایش یا serialize نمی‌شود. |
| پاک‌سازی فایل‌های محلی در `/account/storage` | حذف فقط نسخه‌های محلی پس از تأیید صریح | نگه‌داری، confirm + local-receipt | دیالوگ جزئیات داخلی حافظه ندارد؛ cancel/Escape بدون پاک‌سازی و reload است؛ موفقیت اندازه را صفر می‌کند. |
| خطای پاک‌سازی حافظه محلی | بازبینی و retry بدون از دست‌دادن اندازه یا فایل‌ها | نگه‌داری | شکست محلی دیالوگ، اندازه و مسیر را نگه می‌دارد؛ علت خام نمایش یا serialize نمی‌شود. |
| تغییر وضعیت حساب کاربر مدیریت | غیرفعال/فعال‌سازی فقط پس از تأیید صریح و receipt دقیق | نگه‌داری، confirm + receipt-bound | دیالوگ نام حساب/موبایل ندارد؛ cancel/Escape بدون درخواست است؛ فقط `200` با وضعیت و مهلت معتبر اعمال می‌شود. |
| رفع مسدودیت یا رفع محدودیت کاربر مدیریت | برداشتن همان محدودیت پس از تأیید صریح | نگه‌داری، confirm + receipt-bound | متن دیالوگ جملهٔ لغو/Escape دارد؛ فقط receipt دقیق همان فیلدها state را تغییر می‌دهد. |
| خطای تغییر وضعیت، رفع مسدودیت یا رفع محدودیت | بازبینی و retry بدون از دست‌دادن کاربر نمایش‌داده‌شده | نگه‌داری | 403/404، receipt نامعتبر یا network دیالوگ و مسیر را نگه می‌دارند؛ raw detail نمایش یا serialize نمی‌شود. |
| منوی تنظیمات و فرم محدودیت/مسدودیت کاربر مدیریت | تشخیص اقدام و تکمیل فرم بدون شکستن ظاهر مشترک | نگه‌داری، visual-unification | کارت اقدام و ورودی/دکمه از primitiveهای `ui-*` می‌آیند؛ فرم confirm نشده و mutation عوض نشده است. |
| ردیف فهرست کاربران مدیریت | باز کردن پروفایل همان کاربر بدون شکستن ظاهر مشترک | نگه‌داری، visual-unification | ردیف `ui-list-item` است؛ جستجو session-local می‌ماند و navigation همان `user_profile` است. |

هر محتوای جدید باید پیش از افزوده‌شدن، یک تصمیم، عمل، state یا risk-prevention مشخص داشته باشد و projection/authority آن را backend تأیید کند.
