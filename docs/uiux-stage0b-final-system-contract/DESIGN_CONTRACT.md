# Stage 0B-6 — قرارداد نهایی طراحی `SYS-01..SYS-14`

وضعیت: `stage0b6_complete_stage1_authorized`؛ مالک قرارداد و ادامه بی‌وقفه roadmap را در `2026-08-08T20:57:28.073Z` تأیید کرد و closure فنی/Sites در `2026-08-08T21:07:38Z` پاس شد. این قرارداد خود runtime را تغییر نمی‌دهد؛ Stage 1 مجاز اما هنوز شروع‌نشده است.

## مبنای قرارداد

- جهت بصری مصوب: **مالی مدرن**؛ آرام، دقیق، premium و بدون KPI یا تزئین بی‌اثر.
- استفاده غالب: mobile-first با حدود ۹۵٪ استفاده موبایل؛ desktop تطبیقی است، نه محصول دوم.
- منبع editable: Figma رسمی `z8jgJxST4O2APzWnlyP9gv`.
- foundation موجود: `65` variable، `9` text style Vazirmatn و `2` effect.
- component inventory frozen: `12` component set و `56` variant؛ delta دو variant برای Home-active navigation ثبت شده است.
- surfaceهای بازار و پیام‌رسان: frozen و خارج از بازطراحی داخلی.

## `SYS-01` — دامنه و سطح‌های محافظت‌شده

داخل دامنه V2 فقط routeها و shellهای صریحاً مجاز هستند. قواعد زیر fail-closed هستند:

- interior `/market`، widget فعلی بازار در خانه و هر flow معامله/آفر تغییر نمی‌کند؛ shell می‌تواند slot را نگه دارد اما واقعیت، copy، status یا CTA تازه داخل widget نمی‌سازد.
- interior `/chat`، `/share-receive` و بخش channel در `/admin/channels` تغییر نمی‌کند.
- حضور label یا icon بازار/پیام‌رسان در navigation صرفاً قرارداد shell است.
- stylesheet، token یا component V2 باید route-scoped باشد؛ global reset یا selector مشترکی که ظاهر protected interior را عوض کند مجاز نیست.
- هر visual regression سطح محافظت‌شده blocker است، حتی اگر unit test سبز باشد.

مرجع machine-readable: [PROTECTED_SURFACE_MANIFEST.json](PROTECTED_SURFACE_MANIFEST.json).

## `SYS-02` — جهت بصری، foundation و typography

- فقط خانواده فونت `Vazirmatn` در product rootها استفاده می‌شود.
- hierarchy با وزن، اندازه، فاصله و سطح ساخته می‌شود؛ رنگ زیاد جای hierarchy را نمی‌گیرد.
- variableها و styleهای موجود reuse می‌شوند؛ alias شکسته، مقدار hard-coded معادل token یا collection تکراری مجاز نیست.
- قرارداد موجود `65 variables / 9 text styles / 2 effects` baseline است. تغییر inventory فقط با دلیل و audit صریح ممکن است.
- شعاع، border، surface و shadow باید آرام و سازگار باشند؛ کارت برای پرکردن فضای خالی ساخته نمی‌شود.
- آیکون باید از خانواده مصوب و دارای معنای مستقل باشد؛ icon تزئینی یا emoji به‌عنوان control مجاز نیست.

## `SYS-03` — خلوتی و ضرورت محتوا

هر واحد همیشه‌نمایان باید یکی از این چهار اثر را داشته باشد:

1. کار جاری را روشن کند؛
2. تصمیم یا انتخاب را تغییر دهد؛
3. اقدام واقعی را ممکن کند؛
4. از ریسک یا خطای مهم جلوگیری کند.

در غیر این صورت عنصر `On demand` یا `Remove` است. route path، backend source، server name، تعداد کل روابط/ابزار/مسیر، role توضیحی، KPI تزئینی، status مثبت بدیهی و متن تکراری عنوان/CTA در product UI جایی ندارند. حریم خصوصی، recovery، deadline و پیامد اقدام به نام خلوتی حذف نمی‌شوند.

مرجع کامل: [CONTENT_NECESSITY_MATRIX.md](CONTENT_NECESSITY_MATRIX.md).

## `SYS-04` — mobile-first، responsive و desktop parity

- root مرجع هر خانواده موبایل دقیقاً `390×844` است.
- width sweep اجباری: `360 / 375 / 390 / 414 / 430` با height مرجع `844`.
- هیچ horizontal overflow، clipping متن، پوشانده‌شدن CTA یا overlap با safe area/bottom navigation مجاز نیست.
- desktop proof دقیق `1440×900` باید یک task پیچیده نماینده را بهتر بچیند، بدون افزودن fact، KPI، فیلتر یا navigation تازه.
- پنج archetype باید در قرارداد پوشش داده شوند: public centered auth، home contained shell، operations/workspace list-detail، admin list-detail و account/security contained detail.
- افزایش فضا فقط برای هم‌زمان‌کردن بخش‌های لازم همان task استفاده می‌شود.

## `SYS-05` — قرارداد route و shell

### مقصدهای shell

ترتیب استاندارد کاربر واجد شرایط:

1. `خانه`
2. `بازار`
3. `پیام‌رسان`
4. `عملیات`
5. `حساب`

مقصد غیرمجاز واقعاً حذف می‌شود و disabled/dead destination ساخته نمی‌شود. حسابدار و کاربر محدود فقط destinationهای معتبر خود را می‌بینند. active state با label، icon و surface قابل تشخیص است و فقط به رنگ وابسته نیست.

### قرارداد ۲۹ route فعلی

تعداد این جدول باید با `frontend/src/router/index.ts` برابر بماند. `catch-all` برنامه‌ریزی‌شده baseline سی‌ام نیست و جداگانه ثبت می‌شود.

| # | route | owner/shell | قرارداد نهایی |
| ---: | --- | --- | --- |
| 1 | `/` | Home / standard authenticated | مقصد canonical خانه؛ shell نقش‌محور و market widget محافظت‌شده |
| 2 | `/setup-password` | Auth / focused authenticated | security gate متمرکز؛ بدون navigation روزانه تا تکمیل |
| 3 | `/login` | Auth / public | بدون authenticated shell یا PWA prompt |
| 4 | `/market` | Protected Market | interior frozen؛ wrapper فعلی و regression lock |
| 5 | `/operations` | Operations / standard | landing خلوت با مقصدهای واقعی نقش |
| 6 | `/operations/customers` | Customer workspace / standard | list موبایل؛ query/filter/scroll قابل بازیابی |
| 7 | `/operations/customers/:relationId` | Customer workspace / standard | detail/deep link با recovery و context |
| 8 | `/operations/accountants` | Accountant workspace / standard | list موبایل و دسترسی permission-bound |
| 9 | `/operations/accountants/:relationId` | Accountant workspace / standard | detail/deep link با recovery و context |
| 10 | `/account` | Account / standard | account hub canonical و خلوت |
| 11 | `/account/security` | Account / standard | مقصد canonical نشست و امنیت |
| 12 | `/account/storage` | Account / standard | مقصد canonical حافظه محلی device/browser |
| 13 | `/account/notifications` | Account / standard | notification center canonical |
| 14 | `/chat` | Protected Messenger | interior frozen؛ wrapper فعلی و regression lock |
| 15 | `/users/:id` | Profile / standard | public profile با PII حداقلی و permission واقعی |
| 16 | `/profile` | Profile / standard | self profile و ویرایش فقط fieldهای پشتیبانی‌شده |
| 17 | `/settings` | Account / legacy | redirect آینده به `/account/security`؛ تا Stage runtime فقط ثبت قرارداد |
| 18 | `/admin` | Admin / standard | landing مدیریت خلوت، permission-bound |
| 19 | `/admin/invitations` | Admin / standard | دعوت استاندارد، بدون نقش/تحویل ساختگی |
| 20 | `/admin/channels` | Protected Messenger/Admin | channel interior frozen و خارج از redesign |
| 21 | `/admin/users` | Admin / standard | directory، search و action permission-bound |
| 22 | `/admin/users/:id` | Admin / standard | detail اختصاصی با PII و action مجاز |
| 23 | `/admin/commodities` | Admin / standard | مدیریت کالا با feedback پایدار |
| 24 | `/admin/messages` | Admin / standard | فقط shell/اعلان عمومی غیر بازار؛ هیچ redesign پیام‌رسان |
| 25 | `/admin/system` | Admin / standard | فقط بخش‌های غیر بازار/پیام‌رسان و permission-bound |
| 26 | `/i/:code` | Auth / public | invitation landing عمومی و state اعتبار روشن |
| 27 | `/register` | Auth / public | ثبت‌نام عمومی چندمرحله‌ای با حفظ داده |
| 28 | `/notifications` | Account / legacy | redirect آینده به `/account/notifications` |
| 29 | `/share-receive` | Protected Messenger | share receiver behavior/interior frozen |

### catch-all برنامه‌ریزی‌شده

`/:pathMatch(.*)*` یک route system-owned در Stage 3 خواهد بود. خروجی آن باید 404/forbidden/deep-link failure را از هم جدا کند، shell مناسب وضعیت احراز هویت را انتخاب کند و حداقل یک recovery معتبر داشته باشد. blank page، redirect loop، افشای route/backend و fallback به interior محافظت‌شده ممنوع است.

### layer order مشترک

1. security/session gate؛
2. blocking permission/identity result؛
3. offline/stale/reconnecting؛
4. contextual result/toast؛
5. optional PWA prompt؛
6. shell navigation.

## `SYS-06` — حقیقت state و feedback

stateهای مشترک:

- `loading`: ساختار بدون fact یا status فرضی؛ timeout/recovery مشخص در runtime.
- `true empty`: درخواست موفق و مجموعه واقعاً خالی.
- `filtered/category empty`: داده وجود دارد اما query/category نتیجه ندارد.
- `error`: failure صریح با retry نزدیک و cause-neutral مگر علت قطعی باشد.
- `offline`: داده cached با اعلام محدودیت؛ action شبکه‌ای بی‌اثر ندارد.
- `stale/reconnecting`: داده قبلی حفظ می‌شود؛ skeleton به‌جای آن نمی‌نشیند.
- `busy`: اقدام تکراری قفل و context حفظ می‌شود.
- `success/failure`: نتیجه کنار همان action و با ادامه روشن.
- `forbidden/unavailable`: با empty یا disabled control بی‌توضیح جایگزین نمی‌شود.

یک واقعیت در یک viewport یک خانه بصری اصلی دارد؛ toast، banner و inline message نباید همان نتیجه را هم‌زمان تکرار کنند.

## `SYS-07` — تداوم context و recovery

- back باید به context قبلی واقعی برگردد، نه همیشه landing.
- در موبایل list و detail هم‌زمان روی هم قرار نمی‌گیرند؛ بازگشت query/filter/scroll و انتخاب قبلی را بازیابی می‌کند.
- deep link در loading/error/forbidden صفحه خالی نمی‌سازد.
- input معتبر کاربر در failure حفظ می‌شود؛ retry action را از صفر مجبور نمی‌کند.
- desktop master/detail همان selection و task را نگه می‌دارد.
- reconnect notification پنجره آخرین ۵۰ را refetch و با شناسه dedupe می‌کند؛ gap بیرون پنجره صادقانه تضمین نمی‌شود.

## `SYS-08` — اقدام‌های حساس

برای محدودیت، مسدودیت، هشدار، پایان نشست، حذف/قطع رابطه و پاک‌سازی:

- backend/bot authority قبل از نمایش نتیجه موفق مرجع است؛
- پیامد دقیق، دامنه و در صورت لزوم مدت/مقدار قبل از confirm دیده می‌شود؛
- confirm عمومی و مبهم مجاز نیست؛
- busy guard از duplicate submit جلوگیری می‌کند؛
- optimistic success بدون receipt/authority مجاز نیست؛
- failure داده و context لازم برای اصلاح/تلاش دوباره را حفظ می‌کند؛
- undo فقط وقتی نمایش داده می‌شود که واقعاً پشتیبانی شود.

## `SYS-09` — permission، نقش و PII

- visibility در UI جای enforcement backend نیست.
- هر action با capability واقعی و scope نقش/رابطه محدود می‌شود؛ self، same-level و middle-manager boundary باید در backend تست شود.
- موبایل عمومی masked و آدرس hidden است؛ detail کامل فقط برای self یا admin اختصاصی واقعاً مجاز.
- route، server، `home_server`، token، session secret یا metadata داخلی product copy نیست.
- نام/شماره واقعی در evidence ممنوع و فقط identity synthetic مجاز است.
- forbidden/unavailable هیچ PII جزئی از داده قبلی باقی نمی‌گذارد.

## `SYS-10` — حقیقت cross-platform، delivery و سهمیه

- محدودیت یا مسدودیت مشترک باید از authority مشترک خوانده شود و در bot/web معنا و زمان یکسان داشته باشد.
- هشدار، صرف‌نظر از مبدأ bot یا web، یک رکورد authoritative مشترک دارد و هر دو پلتفرم همان state را می‌بینند.
- اقدام از web که bot authority می‌خواهد، forward می‌شود یا fail-closed؛ queue محلی موفقیت نیست.
- موفقیت ارسال فقط با receipt معتبر همان کانال اعلام می‌شود. request accepted، database synced یا event emitted به‌تنهایی delivery success نیست.
- محدودیت تعدادی «دائمی» باید یک مقدار **محدود، مثبت و enforceable** داشته باشد. مقدار null/zero/unbounded نمی‌تواند با label دائمی به‌عنوان محدودیت موفق نمایش داده شود.
- نتیجه partial باید partial بماند؛ UI موفقیت همگانی یا cross-channel هم‌زمان ادعا نمی‌کند.

## `SYS-11` — نشست و server truth

- inventory نشست `local per-server` است و فهرست merged ساخته نمی‌شود.
- `home_server`، topology و source server در نمای محصول نمایش داده نمی‌شود.
- فقط primary session مجاز می‌تواند نشست دیگر را پایان دهد.
- «پایان همه نشست‌های دیگر» نشست جاری را حفظ می‌کند.
- current/non-primary/accountant/forbidden stateها distinct هستند.
- runtime باید revocation واقعی را با backend و raceهای failure اثبات کند؛ Figma فقط contract را نشان می‌دهد.

## `SYS-12` — اعلان و Push

- endpoint فعلی آخرین `50` اعلان است؛ طول پاسخ total category یا total history نیست.
- tabها `معاملات` و `سایر` هستند.
- item بدون route غیرتعاملی است و affordance کلیک ندارد.
- ورود به مرکز، موارد موجود را خوانده‌شده می‌کند؛ فقط item تازه realtime می‌تواند signal «جدید» بگیرد.
- reconnect، refetch آخرین ۵۰ + dedupe دارد؛ recovery کامل بیرون پنجره تضمین نمی‌شود.
- delete/clear، Push disable و Push test ساخته نمی‌شوند چون قابلیت پشتیبانی‌شده نیستند.
- ۹ state دقیق Push: `checking`، `unsupported`، `insecure`، `server-disabled`، `permission-blocked`، `permission-default`، `subscribed`، `unsubscribed` و `error`.
- WebSocket، sync رکورد، browser Push و Telegram delivery چهار حقیقت متفاوت‌اند و نباید ادغام ادعایی شوند.

## `SYS-13` — دسترس‌پذیری و motion

### حداقل‌های بصری و تعاملی

- target عمومی: حداقل `44×44px`؛
- CTA اصلی: حداقل `48px` ارتفاع؛
- label ناوبری موبایل: حداقل `11px`؛
- متن عادی: حداقل `4.5:1`؛
- focus/non-text state: حداقل `3:1` با stroke مرجع `3px`؛
- focus visible و مستقل از hover؛
- ترتیب focus، نام accessible و live feedback در runtime باید تست شوند.

### motion

- micro transition مرجع: `140ms`؛
- component/state transition مرجع: `180ms`؛
- حرکت فقط تغییر state، hierarchy یا continuity را روشن می‌کند؛ decorative loop، parallax و وابستگی قابلیت به animation ممنوع است؛
- action destructive یا result مهم با motion نمایشی کند نمی‌شود؛
- در `prefers-reduced-motion: reduce` transform/slide/scale غیرضروری حذف و transition به تغییر فوری یا کوتاه‌ترین fade لازم تبدیل می‌شود؛ progress ضروری همچنان قابل‌درک و غیر وابسته به حرکت است.

## `SYS-14` — evidence، مجوز و rollout

- Figma canonical، export مستقیم، harness محلی و Sites خصوصی باید source-bound ولی از نظر نقش مستقل باشند.
- ۳۲ assertion باید با exact ID/order در audit مستقیم و local metrics پاس شوند.
- pre/post capture assertion و canonical tree/hash باید برابر باشند؛ evidence ناقص atomic promote نمی‌شود.
- screenshot رفتار authorization، mutation، delivery، realtime، focus، keyboard یا screen reader را اثبات نمی‌کند.
- تأیید مالک `0B-6` ادامه بی‌وقفه Stageهای 1 تا 8 را مجاز کرده است، اما Stage 1 فقط پس از closure فنی/Sites باز می‌شود.
- هر Stage از 1 تا 8 همچنان commit، test، protected-surface diff، rollback و گیت فنی مستقل دارد؛ تأیید جداگانه مالک برای هر Stage لازم نیست مگر مالک صریحاً توقف/تغییر مسیر بدهد.
- rollout در Stage 8 محدود، مشاهده‌پذیر، مرحله‌ای و قابل بازگشت است؛ green test جای تأیید انسانی زیبایی و task success را نمی‌گیرد.

## گیت بدهی Figma

| مورد | وضعیت `0B-6` | disposition |
| --- | --- | --- |
| Auth خارج از Figma canonical | `passed` | root `168:2017` با fact parity و binding مرجع |
| Home binding ناشناخته/قدیمی | `passed` | root `168:2018`؛ صفر alias/binding شکسته و صفر detached product instance |
| Operations-active navigation debt | `passed` | root `168:2079`؛ focus/layout/style و `44/11/3:1/3px` پاس |
| avatar initials بدون local text-style exact | `carry_forward_stage2` | Vazirmatn/fit/contrast حفظ شده؛ تصمیم exact style در Stage 2، نه pass صوری |

## وضعیت مجوز

```text
ownerSystemContractApproval.status = approved
ownerSystemContractApproval.approvedAt = 2026-08-08T20:57:28.073Z
continuousProgressionAuthorized = true
runtimeImplementationAuthorized = true
nextAuthorizedRuntimeStage = Stage 1
stage1RuntimeWorkStarted = false
```

Stage 1 مطابق اجازه مالک باز است و این سند ادعای شروع runtime edit آن را ندارد.
