# قرارداد runtime هسته روزانه Stage 4

وضعیت: **`stage4_complete`**؛ پذیرش نهایی `passed`

## ۱. route و navigation

| مقصد          | route canonical          | قرارداد                                                              |
| ------------- | ------------------------ | -------------------------------------------------------------------- |
| Home          | `/`                      | standard shell؛ V2 فقط بیرون region محافظت‌شده بازار                 |
| Operations    | `/operations`            | مقصد نقش‌محور، بدون شمارنده ابزار/مسیر و بدون permission copy تکراری |
| Account       | `/account`               | مقصدهای یکتا و هویت فقط پس از load معتبر                             |
| Security      | `/account/security`      | فقط Security؛ بازگشت قطعی به Account                                 |
| Storage       | `/account/storage`       | فقط Storage؛ بازگشت قطعی به Account                                  |
| Notifications | `/account/notifications` | notification center canonical؛ بازگشت قطعی به Account                |

redirectهای compatibility:

- `/settings` → `{ name: "account-security" }`
- `/notifications` → `{ name: "account-notifications" }`

redirect legacy نباید component قبلی را render، state query قدیمی را به قرارداد تازه تحمیل یا history-dependent back تولید کند.

## ۲. Home

- حالت عادی فقط هویت لازم، attention واقعاً مؤثر و اقدام بعدی را نگه می‌دارد.
- KPI عمومی، تعداد معاملات، تعداد اعلان‌ها، تعداد روابط، role chip، سلامت مثبت و Telegram از نمای پیش‌فرض حذف‌اند.
- inactive/restricted یک هشدار و اقدام واقعی نزدیک دارد؛ positive active badge وجود ندارد.
- حسابدار بازار یا deep-link مشتریان/Operations را نمی‌بیند؛ interior بازار برای سایر کاربران فقط طبق authority موجود render می‌شود. CTA مقدماتی `0B-2` با قرارداد متأخر `0B-3` و رد `403` در `ensure_owner_context` supersede شده است.
- شش section محافظت‌شده `home-market-widget` byte/hash frozen هستند و هیچ copy، CSS یا behavior داخلی آن‌ها مجاز به تغییر نیست.
- loading هویت، نام/نقش/status فرضی نمی‌سازد؛ error/retry، stale و offline از هم متمایزند.
- PWA و shell layering قرارداد بسته Stage 3 را حفظ می‌کنند.

## ۳. Operations و نقش

UI فقط action واقعاً مجاز را نشان می‌دهد:

- مالک/سرگروه واجد predicate: مشتریان و حسابداران؛
- مدیر میانی: دعوت‌نامه و کاربران؛
- مدیر ارشد: موارد مدیر میانی به‌علاوه کالاها، پیام‌های مدیریت و تنظیمات سیستم؛
- مشتری یا حسابدار بدون action مالک: empty کوتاه با مقصد واقعی Account؛
- role، تعداد action، تعداد route و توضیح «بر اساس دسترسی شما» محتوای پیش‌فرض نیست.

Backend authority مرجع نهایی است. وجود route یا role label به‌تنهایی permission نمی‌سازد.

## ۴. Account

- قبل از `/api/auth/me` معتبر، هویت یا status فرضی نمایش داده نمی‌شود.
- کاربر غیرحسابدار مقصدهای یکتای Profile، Security، Storage و Notifications را می‌بیند.
- حسابدار دقیقاً Profile، Storage و Notifications را می‌بیند؛ Session، personal logout و Telegram ندارد.
- badge مثبت Active یا badge خنثی Unknown وجود ندارد؛ فقط inactive/restricted اثرگذار نمایش داده می‌شود.
- Telegram فقط در Account، برای non-accountant و فقط با `can_connect_telegram=true` و state قابل اقدام نمایش داده می‌شود؛ linked state اقدام تکراری نمی‌سازد.

## ۵. Security و session authority

endpointهای موجود بدون تغییر semantics:

- `GET /api/sessions/active`
- `DELETE /api/sessions/{sessionId}`
- `POST /api/sessions/logout-all`

قرارداد ارائه:

- فهرست local per-server است و merged/cross-server claim ندارد.
- هر ردیف فقط device، platform، last activity و signal لازم current/primary را نشان می‌دهد.
- `home_server`، server/backend/API copy و IP پیش‌فرض حذف‌اند؛ IP فقط در صورت طراحی on-demand و نیاز تصمیمی آینده قابل بررسی است.
- فقط وقتی session جاری `primary` است، action terminate نشست non-current/non-primary نمایش داده می‌شود.
- نبود current session یا current non-primary به‌صورت truthful و fail-closed نمایش داده می‌شود.
- action یک نشست، `/logout-all` و logout این دستگاه confirm inline، busy محلی و feedback محلی دارند.
- copy `/logout-all` همیشه «نشست‌های دیگر» است و حفظ نشست جاری را می‌گوید.
- receipt معتبر برای اعمال mutation موفق لازم است؛ در failure فهرست قبلی حفظ می‌شود.
- logout محلی علت خام را log یا نمایش نمی‌دهد.
- حسابدار endpointهای session-management را درخواست نمی‌کند.

## ۶. Storage

Storage از cache محلی composable موجود استفاده می‌کند و API تازه نمی‌سازد.

- اندازه cache فقط در route Storage محاسبه می‌شود.
- size-error مقدار `zero` نیست و با copy صریح مستقل نمایش داده می‌شود.
- clear فقط download/cache فایل‌های پیام‌رسان همین browser/device را حذف می‌کند.
- clear حساب، پیام، رکورد server، session یا فایل remote را حذف نمی‌کند.
- confirm، busy، success و failure در card همان action باقی می‌مانند.
- خطا cause-neutral است و raw exception در UI یا console ثبت نمی‌شود.

## ۷. Notifications

endpointها و رفتارهای موجود حفظ می‌شوند؛ Stage 4 فقط ارائه و recovery را منظم می‌کند.

- history یک window محدود با `limit=50` است و طول category total واقعی نیست.
- دسته‌ها `trade → معاملات` و non-trade → `سایر` هستند؛ category count نمایش داده نمی‌شود.
- initial loading، initial error، true empty، retained refresh error و category empty stateهای جدا هستند.
- ورود به center، history موجود را read می‌کند؛ realtime arrival هم‌زمان نباید گم یا به‌اشتباه read شود.
- notification بدون route، article غیرقابل‌کلیک است.
- route فقط پس از validation داخلی، resolve موفق و نبود System Recovery قابل اجراست.
- route/backend/server metadata از body ساختاریافته حذف و route خام نمایش داده نمی‌شود.
- navigation failure context center را حفظ می‌کند.
- delete/clear UI ساخته نمی‌شود.

## ۸. Push

state machine دقیق:

1. `checking`
2. `unsupported`
3. `insecure`
4. `server-disabled`
5. `permission-blocked`
6. `permission-default`
7. `subscribed`
8. `unsubscribed`
9. `error`

- permission فقط پس از action صریح `فعال‌سازی اعلان مرورگر` درخواست می‌شود؛ generic first interaction مجوز درخواست permission نیست.
- state و copy فقط browser/device فعلی را توصیف می‌کنند.
- Telegram، delivery سراسری، cross-server یا cross-device تضمین نمی‌شود.
- retry نزدیک error است؛ state غیرقابل اقدام control خیالی ندارد.

## ۹. adapter و Stage 5

Stage 4 می‌تواند API ظاهری `WorkspaceShell` و پنج primitive همراه را به wrapperهای App\* متصل کند، مشروط به اینکه:

- prop/event و slot contract consumerها حفظ شود؛
- Customer/Accountant regression tests پاس بمانند؛
- route، query، list/detail state، API، permission، mutation و destructive copy آن workspaceها تغییر نکند؛
- حذف adapter تا پایان rollout و اثبات rollback مجاز نیست.

بازطراحی واقعی Customer/Accountant Workspaces فقط در Stage 5 انجام می‌شود.

## ۱۰. failure و privacy

- پیام خطا cause-neutral و نزدیک مبدأ است.
- raw exception، token، route، API، backend/server metadata و IP پیش‌فرض به UI یا log تازه نشت نمی‌کند.
- loading، empty، error، stale و unavailable با هم جایگزین نمی‌شوند.
- action حساس در failure داده/context را حفظ می‌کند و duplicate submit را می‌بندد.

## ۱۱. وضعیت پذیرش

این قرارداد با implementation commit `007f94d170cb02cd69911d9e1f122b83fbacd535`، tree برابر `807a01c76c93489ccce1e5b72cea9c214fd52d31` و parent دقیق comparison base بسته شد. pathset اجرای Stage 4 دقیقاً `67` فایل با SHA-256 برابر `25a5773b2e3ca1f6e45bbf48800dcac4ce3cd8e8125f1913fee674529720739f` است.

پذیرش runtime به شاهدهای مستقل زیر متکی است:

- frontend تجمیعی `34` فایل / `450` تست، guard برابر `3` فایل / `8` suite / `55` تست و backend برابر `11` ماژول / `69` تست، همگی بدون failure؛
- typecheck، production build، diff-check و aggregate guard پاس؛ ESLint و Prettier فقط delta-clean با Stage4-new برابر صفر و debt ارثی صریح؛
- browser acceptance برابر `49/49` در run `uiux-stage4-browser-20260809T180340666Z` با diagnostics غیرمنتظره صفر؛
- Figma authored snapshot روی file `z8jgJxST4O2APzWnlyP9gv`، page `283:18` و root `283:19` با audit مستقیم پاس، `66` instance متصل و detached برابر صفر؛
- evidence محلی `26/26` و بسته frozen دقیقاً `70` فایل / `5863416` بایت با aggregate SHA-256 برابر `8c123a1eeb717f799c0449443f2d8ea76f201a0ae2c31e062b1cff09584a7971`؛
- Sites خصوصی owner-only، source-bound و anonymous-denied طبق [SITES_PROVENANCE](SITES_PROVENANCE.json).

protected source/behavior/visual drift غیرمجاز صفر است. این closure Stage 5 را مجاز یا شروع نمی‌کند: `nextAuthorizedRuntimeStage=null`، `stage5RuntimeImplementationAuthorized=false` و `stage5RuntimeWorkStarted=false`. کار طبق دستور کاربر پس از Stage 4 متوقف است.
