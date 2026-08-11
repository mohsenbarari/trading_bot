# قرارداد runtime فضای کاری مشتریان و حسابداران — Stage 5

وضعیت: **`stage5_complete_runtime_browser_figma_sites_proven`**

## ۱. authority و route

چهار route زیر همان `requiresAuth + requiresOwnerAccess` موجود را حفظ می‌کنند و backend authority مرجع نهایی است:

| مقصد | route | scope | قرارداد |
| --- | --- | --- | --- |
| فهرست مشتریان | `/operations/customers` | `SECTION` | list live، جست‌وجو/filter، pending queue و ایجاد |
| پرونده مشتری | `/operations/customers/:relationId` | `SECTION` | detail owner-only، شامل terminal lifecycle |
| فهرست حسابداران | `/operations/accountants` | `SECTION` | list live، جست‌وجو/filter، pending queue و ایجاد |
| پرونده حسابدار | `/operations/accountants/:relationId` | `SECTION` | detail owner-only، شامل terminal lifecycle |

حسابدار در effective accountant context اجازه مدیریت owner-accountants ندارد. route visibility یا role label جای enforcement سمت backend را نمی‌گیرد.

## ۲. responsive، history و context

- mobile/tablet باریک دقیقاً وقتی `width < 900` است و list/detail را هم‌زمان نشان نمی‌دهد.
- desktop دقیقاً از `width >= 900` master/detail است؛ hierarchy و factها همان mobile هستند و metadata یا action تازه از عرض صفحه ایجاد نمی‌شود.
- `q`، `filter`، `scroll` و `tab` فقط با valueهای معتبر در query canonical باقی می‌مانند؛ aliasهای legacy مانند `listScroll`، `panel` و `section` به قرارداد canonical تحمیل نمی‌شوند.
- بازکردن detail و بازگشت، query/filter/selection/scroll فهرست را حفظ می‌کند. scroll detail نباید scroll فهرست را overwrite کند.
- canonical `router.replace` هنگام navigation موفق deletion متوقف می‌شود تا با `router.push` بازگشت رقابت نکند.
- root key فقط برای همین چهار route section-based از `route.path` استفاده می‌کند تا query-only transition، view را وسط fade remount نکند. سایر SECTION/OFF و protected routeها fullPath legacy key خود را حفظ می‌کنند.
- deep link نامعتبر یا relation حذف‌شده blank/loading بی‌پایان نمی‌سازد؛ detail terminal یا missing state و بازگشت معتبر ارائه می‌شود.

## ۳. list/detail lifecycle

- list endpointها فقط relationهای live و capacity-tracked (`pending` یا `active` و حذف‌نشده) را برمی‌گردانند.
- detail endpointهای owner-only terminal lifecycle را پنهان نمی‌کنند تا receipt حذف و deep link truthful باقی بماند.
- row پیش‌فرض فقط identity، disambiguator لازم، status اثرگذار و affordance ورود دارد.
- structural loading، true empty، search/filter empty، initial error، retained refresh error و missing detail stateهای مستقل‌اند.
- refresh failure دادهٔ retained را پاک نمی‌کند و retry نزدیک همان context می‌ماند.

## ۴. دعوت pending

- create submit در busy duplicate نمی‌شود و receipt باید با draft captureشده سازگار باشد.
- pending queue تنها محل مجاز برای count عملیاتی است.
- deadline و SMS delivery state نزدیک copy/cancel، هر کدام یک بار، نمایش داده می‌شوند.
- copy success/failure و cancel success/failure کنار همان action می‌مانند.
- `cancel-pending` فقط برای relation بازِ pending، بدون حساب live و با invitation واقعاً pending معتبر است.

## ۵. ویرایش

Customer:

- تغییرات مالی قبل از PATCH در مرور before/after ارائه می‌شوند.
- اثر قرارداد صریحاً future-only است؛ معاملات تکمیل‌شده و history قبلی بازنویسی نمی‌شوند.
- failure draft و context مرور را حفظ می‌کند؛ success فقط با receipt معتبر state را به‌روز می‌کند.

Accountant:

- شرح وظیفه یک field canonical است و متن فعلی به‌عنوان کارت جداگانه تکرار نمی‌شود.
- normalize به متن trimشده یا `null` انجام می‌شود؛ feedback محلی کنار save باقی می‌ماند.

## ۶. history، statistics و session

- history و customer statistics on-demand هستند و row/list را متراکم نمی‌کنند.
- session list فقط برای relation active با حساب live قابل مدیریت است.
- presentation نشست فقط device/platform، last activity و signal لازم primary را نگه می‌دارد؛ IP و home-server در UI تازه نمایش داده نمی‌شوند.
- terminate یک نشست confirm و busy محلی دارد. receipt باید `terminated_session_id` درخواست‌شده را تأیید کند؛ promotion احتمالی primary از receipt اعمال می‌شود.
- failure لیست retained را حفظ می‌کند و request stale یا tab/relation قدیمی حق نوشتن روی context جدید ندارد.

## ۷. حذف و precondition معنایی

هر DELETE رابطه باید query اجباری `expected_action` داشته باشد:

| مقدار | capability لازم | side effect مجاز |
| --- | --- | --- |
| `cancel-pending` | open + pending + no live account + pending invitation | revoke relation/invitation و release reservation همان دعوت |
| `delete-relation` | open + active + no live linked account | بستن فقط relation و release reservation مرتبط |
| `delete-account` | open + active + live linked account | cascade واقعی user deletion و سپس بستن relation |

قواعد concurrency:

- ابتدا invitation transition/advisory lock و سپس relation row lock گرفته می‌شود.
- relation پس از lock با `populate_existing` بازخوانی می‌شود.
- تغییر invitation token یا capability mismatch پیش از هر side effect با `409` fail-closed است.
- UI نام شخص و پیامدهای واقعی را در strong confirmation نشان می‌دهد. CTA مربوط به حساب live «حذف حساب» است، نه «قطع ارتباط» مبهم.
- dialog حذف حساب، غیرفعال‌شدن وب‌اپ/بات، پایان نشست‌ها، انقضای آفرها، لغو دعوت‌های pending و بسته‌شدن روابط وابسته را بیان می‌کند و تأیید نام لازم دارد.
- success navigation تنها با receipt مربوط به همان relation/action/generation انجام می‌شود؛ unmount یا route change واقعی آن را suppress می‌کند.

## ۸. race، recovery و privacy

- list/detail/session/create/update/delete requestها از AbortController، generation و captured context استفاده می‌کنند.
- پاسخ دیررس relation/tab/route قبلی حق پاک‌کردن busy یا جایگزینی دادهٔ context جدید را ندارد.
- raw exception، token، API/backend/server metadata، route داخلی، IP یا علت حدسی خطا در copy تازه نمایش داده نمی‌شود.
- errorهای کاربر cause-neutral هستند؛ failure destructive داده و context را حفظ می‌کند.

## ۹. accessibility و motion

- target تعاملی حداقل `44×44` و CTA اصلی `48px` است.
- dialog/sheet دارای role، label/description، focus trap، focus return و policy صریح Escape/backdrop است.
- feedback از live region مناسب استفاده می‌کند و busy state با disabled/`aria-busy` بیان می‌شود.
- focus-visible، contrast و reduced-motion contract در V2 scope محلی حفظ می‌شود.
- browser acceptance عرض‌های `360/375/390/414/430/768/899/900/1024/1440` و zoom/reduced-motion/accessibility را پوشش می‌دهد.

## ۱۰. protected boundary و وضعیت پذیرش

Market، Messenger، Home Market و interiorهای admin بسته Stage 4 تغییر نکرده‌اند. shared overlay extensionها opt-in/default-compatible هستند و CSSهای Stage 5 زیر scopeهای workspace قرار دارند. aggregate guard hashهای protected را برابر baseline تأیید می‌کند.

runtime با commit `08c5ae1ea95b3087893146547bed8a220eb83d2b` و tree `96e2f32c46668f37a4753ccaee21216a2b500097` بسته، browser acceptance `23/23` promotable و Figma runtime-delta locally hash-bound است. Sites preview خصوصی owner-only نیز طبق `SITES_PROVENANCE.json` source-bound و passed است؛ بنابراین `stage5CompleteAuthority=true` است. این completion هیچ runtime activation یا product/staging/production deployment ایجاد نکرده و Stage 6 همچنان مجاز نیست.
