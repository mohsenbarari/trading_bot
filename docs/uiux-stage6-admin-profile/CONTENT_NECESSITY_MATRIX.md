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

هر محتوای جدید باید پیش از افزوده‌شدن، یک تصمیم، عمل، state یا risk-prevention مشخص داشته باشد و projection/authority آن را backend تأیید کند.
