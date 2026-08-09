# ماتریس ضرورت محتوا — Stage 4 Daily Core

وضعیت: **`stage4_complete`**؛ policy و assertionهای route/state پاس، inventory کمی مستقل `not_measured_non_contract_hard`

قاعده: هر واحد همیشه‌نمایان باید تصمیم، اقدام، وضعیت ضروری یا ریسک واقعی را روشن کند. داده پرتراکم فقط fixture آزمون تحمل layout است و مجوز نمایش دائمی metadata نیست.

| سطح / state              | Keep                                                                                        | On demand / شرطی                                                | Remove / ممنوع                                                                            | دلیل                                                        |
| ------------------------ | ------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Home عادی                | نام حساب لازم، notification affordance، اقدام بعدی، region بازار بدون تغییر برای کاربر مجاز | PWA فقط پس از Home سالم؛ freshness فقط وقتی تصمیم را عوض می‌کند | greeting، role chip، active badge، KPI، trade count، relation count، Telegram، کارت سلامت | آزمون پنج‌ثانیه‌ای و تمرکز بر امروز/اقدام بعدی              |
| Home inactive/restricted | یک هشدار اصلی، اثر و اقدام Account                                                          | مهلت فقط وقتی اثر تصمیمی دارد                                   | هشدار تکراری، pseudo market control، status مثبت                                          | جلوگیری از تضاد و تراکم هشدار                               |
| Home حسابدار             | هویت لازم                                                                                   | PWA فقط در eligibility مستقل؛ attention ضروری                   | بازار، Customers/Operations deep-link، action مالک، KPI و توضیح نقش                       | `0B-3` و API truth، CTA مقدماتی `0B-2` را supersede می‌کنند |
| Operations مالک/سرگروه   | Customers و Accountants وقتی واقعاً مجازند                                                  | صف نیازمند اقدام در مقصد workspace                              | شمارنده route/tool/relation، role chip، permission paragraph                              | actionهای واقعی تنها محتوای landing هستند                   |
| Operations مدیر          | دعوت/کاربر؛ کالا/پیام/سیستم فقط برای مدیر ارشد                                              | queue/action واقعی                                              | تعداد ابزار و summary دسترسی                                                              | نقش باید در action مجاز دیده شود، نه metadata               |
| Operations بدون action   | empty کوتاه و Account CTA                                                                   | هیچ                                                             | کارت disabled، tab بی‌فایده، مقصد مالک                                                    | ادامه واقعی به‌جای affordance مرده                          |
| Account عادی             | هویت معتبر، Profile، Security، Storage، Notifications                                       | Telegram فقط capability معتبر و non-linked                      | مقصد duplicate، active/unknown badge، count و metadata                                    | هر قابلیت یک مقصد canonical                                 |
| Account حسابدار          | Profile، Storage، Notifications                                                             | inactive/restricted warning                                     | Session، logout، Telegram، کارت محدودیت طولانی                                            | قرارداد دقیق `0B5-M02`                                      |
| Security list            | device، platform، last activity، current/primary                                            | action terminate فقط با current-primary authority               | `home_server`، server/API/backend copy، IP پیش‌فرض                                        | حقیقت per-server بدون افشای زیرساخت                         |
| Security action          | پیامد، confirm inline، busy و receipt/failure محلی                                          | retry                                                           | global toast تنها، optimistic حذف بدون receipt، copy «همه نشست‌ها»                        | context و حفظ نشست جاری                                     |
| Storage                  | اندازه واقعی یا size-error، scope browser/device، clear action                              | retry اندازه، confirm clear                                     | ادعای حذف account/message/server/session؛ یکی‌گرفتن error با zero                         | حریم truth و جلوگیری از برداشت مخرب                         |
| Notifications header     | عنوان، back قطعی Account، tabهای معاملات/سایر                                               | Push state/action                                               | count category از window ۵۰تایی، filter read-count                                        | count bounded total نیست                                    |
| Notification item        | content لازم، time، unread/new لازم                                                         | affordance فقط با route معتبر                                   | route/API/backend/server raw، action جداگانه تکراری، delete                               | notification center قابل اسکن و امن                         |
| Notifications state      | loading، true empty، category empty، initial error، retained refresh error                  | retry نزدیک error                                               | false empty، حذف داده قبلی در refresh failure                                             | اعتماد و recovery                                           |
| Push                     | state دقیق و scope همین browser/device                                                      | enable فقط default/unsubscribed؛ retry فقط error                | auto permission، Telegram/cross-channel claim، control خیالی                              | permission user-initiated و truth محلی                      |
| Workspace adapter        | layout/slot/action semantics مشترک                                                          | V2 wrapper rollback-safe                                        | تغییر workflow Customer/Accountant در Stage 4                                             | مرز صریح Stage 5                                            |
| Error/stale عمومی        | cause-neutral copy، data/context قبلی، retry معتبر                                          | freshness اثرگذار                                               | raw exception، blame قطعی شبکه، صفحه سفید                                                 | قرارداد Stage 1 carry-forward                               |

## نتیجه audit در closure

browser acceptance برابر `49/49` و local evidence برابر `26/26` برای شش route canonical و stateهای role/error/dense مرزهای contract-hard زیر را اثبات کرده‌اند:

- category count مشتق از window محدود اعلان نمایش داده نمی‌شود و route/API/backend metadata خام در Notification UI وجود ندارد؛
- مقصدهای Account یکتا و قرارداد حسابدار دقیق است؛ action مالک برای حسابدار ساخته نمی‌شود؛
- privacy، authority، confirm و recovery در actionهای Security، Storage، Notifications و Push حفظ شده‌اند؛
- Stage 5 interior و Market/Messenger protected surface drift ندارند.

بسته frozen assertion مستقل برای شمارش همهٔ واحدهای همیشه‌نمایان، duplicate fact یا counter بی‌اقدام ندارد. بنابراین هیچ count ظاهراً صفر اختراع نمی‌شود:

```text
alwaysVisibleUnitCount = not_measured_by_frozen_evidence
justifiedAlwaysVisibleUnitCount = not_measured_by_frozen_evidence
unjustifiedAlwaysVisibleUnitCount = not_measured_by_frozen_evidence
routeOrBackendMetadataCount = not_measured_by_frozen_evidence
duplicateFactCountPerStateViewport = not_measured_by_frozen_evidence
unactionableCounterCount = not_measured_by_frozen_evidence
contentNecessityAuditStatus = policy_and_route_assertions_passed_quantitative_inventory_not_contract_hard
```

این deferred measurement گیت contract-hard تکمیل Stage 4 نیست. copy و رفتارهای مشخص مانند نبود category count، metadata خام، route نامعتبر تعاملی، CTA مالک برای حسابدار و drift داخل Market/Messenger با assertionهای صریح browser و evidence پاس شده‌اند. Stage 4 کامل است و Stage 5 مجاز یا آغاز نشده است.
