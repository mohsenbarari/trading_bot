# Stage 3 — Runtime contract

وضعیت: `stage3_complete`

مبنای مقایسه: `3822df67a48e7ee3197bc6d67c79aa7ee84a7905`

implementation commit: `bfe4e59192d678eaf4776fbc025d3aa0f431896d`؛ tree: `0b0e1b1e6f615a34622659fca351507e4f7c1404`؛ Stage 4 مرحله runtime بعدیِ مجاز است و هنوز شروع نشده است.

## ۱. قرارداد shell

closure Git-bound دقیقاً `30` route دارد:

```text
v2Scope = route 5 / section 21 / off 4
shell = public 3 / focused-authenticated 1 / standard-authenticated 21 / protected-legacy 4 / system-recovery 1
```

| خانواده | route | انتظار runtime |
| --- | --- | --- |
| public | `/login`، `/i/:code`، `/register` | بدون authenticated header/bottom-nav، PWA prompt یا فضای رزروشده navigation |
| focused authenticated | `/setup-password` | security gate متمرکز، بدون navigation روزانه تا تکمیل |
| standard authenticated | routeهای احرازشده مجاز | header/feedback/navigation نقش‌محور، بدون مهاجرت interior متعلق به Stageهای بعد |
| protected current wrapper | `/market`، `/chat`، `/share-receive`، `/admin/channels` | V2 scope خاموش، wrapper/interior/FAB/behavior بدون drift |
| system-owned recovery | `/:pathMatch(.*)*` | outcomeهای canonical و hyphenated برابر `not-found`، `forbidden` و `deep-link-failure`، shell متناسب auth و حداقل یک recovery معتبر |

disposition دقیق scope برابر است با:

- `route`: `/setup-password`، `/login`، `/i/:code`، `/register` و `/:pathMatch(.*)*`؛
- `off`: `/market`، `/chat`، `/admin/channels` و `/share-receive`؛
- `section`: `/`، `/operations`، `/operations/customers`، `/operations/customers/:relationId`، `/operations/accountants`، `/operations/accountants/:relationId`، `/account`، `/account/security`، `/account/storage`، `/account/notifications`، `/users/:id`، `/profile`، `/settings`، `/admin`، `/admin/invitations`، `/admin/users`، `/admin/users/:id`، `/admin/commodities`، `/admin/messages`، `/admin/system` و `/notifications`.

سه route عمومی، focused route، چهار protected-legacy route و recovery بالا تمام موارد non-standard هستند؛ همین فهرست `section` دقیقاً ۲۱ route `standard-authenticated` را تشکیل می‌دهد. ترتیب authoritative routeها و catch-all آخر در [route/shell manifest](ROUTE_SHELL_MANIFEST.json) ثبت است.

catch-all باید پس از همه routeهای مشخص بیاید، redirect loop نسازد، path/backend را در copy محصول نیاورد و به interior محافظت‌شده fallback نکند.

System Recovery برای مهمان در shell عمومی Auth و بدون navigation احرازشده نمایش داده می‌شود؛ وجود credential محلی آن را داخل shell احرازشده قرار می‌دهد. این تمایز shell به معنی نمایش path، target یا secret نیست و نتیجه ناشناخته روی URL عادی باید `not-found` باقی بماند.

## ۲. قرارداد Auth

- Login، Invite، Register و Setup Password از همان token/component V2 scoped استفاده می‌کنند.
- Login OTP و registration OTP هرکدام یک input معنایی با contract مستقل باقی می‌مانند.
- ورودی موبایل `inputmode` و `autocomplete` مناسب دارد؛ password manager و WebOTP در مرز contract واقعی حفظ می‌شوند.
- progress فقط برای فرایند واقعاً چندمرحله‌ای و با تعداد درست نشان داده می‌شود.
- focus در تغییر مرحله به heading یا اولین فیلد منطقی می‌رود و تغییر state به‌شکل مناسب announce می‌شود.
- Back، Forward، refresh و خطای موقت context و داده غیرحساس لازم را حفظ می‌کنند؛ token خام یا شماره کامل خارج از input ضروری در storage/log/UI افشا نمی‌شود.
- `REG-01` دقیقاً سه مرحله الزامی دارد و Telegram پس از تکمیل، اختیاری است؛ `REG-02` progress یا OTP تکراری ندارد.
- قرارداد endpoint، role، permission، timer، polling و session approval بدون شاهد backend تغییر نمی‌کند.
- تمام Web invitation URLهای فعالِ ساخته‌شده در API، SMS و copy بات باید pathname دقیق هشت‌کاراکتری `/i/[A-Za-z0-9]{8}` داشته باشند؛ `/register?token=<raw>` و هر Web URL دارای query/fragment bearer ممنوع است. responseهای create/list و relation داخلی نیز fieldهای `token` و `invitation_token` را omit می‌کنند.
- short-link عمومی `/i/:code` فقط short code را در URL دارد. تنها استثنای response دارای bearer خام، lookup `/api/invitations/lookup/:code` است؛ پاسخ `no-store`، document/API دارای `Referrer-Policy: no-referrer`، access log این خانواده route در proxy خاموش و bearer تا انتخاب مسیر فقط در memory صفحه است. هیچ response عمومی/داخلی دیگری این استثنا را به ارث نمی‌برد.
- در شاخه Web، bearer lookup/`REG-*` فقط یک‌بار در JSON body درخواست `POST /api/auth/registration-context/exchange` حمل می‌شود و پس از نتیجه authoritative یا terminal از memory قابل‌استفاده پاک می‌شود؛ Web Storage، `history.state`، DOM، cookie و log محل نگهداری آن نیستند. query قدیمی فقط برای مهاجرت در همان mount fail-closed مصرف و فوراً از URL و router state scrub می‌شود. این منع URL مخصوص **Web handoff** است.
- شاخه Telegram تنها استثنای raw URL و purpose-bound است: فقط پس از اقدام صریح کاربر، client لینک `https://t.me/<bot>?start=<raw-invitation>` را باز می‌کند تا Telegram bot همان دعوت را دریافت کند. این deep-link بیرونی مجوز نمایش، log، persistence یا raw-token fallback در شاخه Web نیست.
- exchange موفق یک handle تصادفی ۲۵۶ بیتی می‌سازد؛ Redis فقط state محدود context را زیر کلید مشتق‌شده از SHA-256 handle با TTL حداکثر `600s` نگه می‌دارد. مرورگر handle را فقط در cookie opaque می‌گیرد و هیچ endpoint آن را در JSON نشان نمی‌دهد.
- cookie در production/staging دقیقاً `__Host-web_registration`، `Secure`، `HttpOnly`، `SameSite=Strict`، `Path=/` و بدون `Domain` است؛ dev/test از نام جدا `web_registration` و `Secure=false` استفاده می‌کند. تمام mutation/readهای context فقط `POST`، `no-store` و same-origin هستند؛ `Origin`/`Sec-Fetch-Site` خارجی fail-closed است و `X-Forwarded-Host` client trust نمی‌شود.
- response context فقط account/mobile mask‌شده و factهای لازم `kind`، `progress` و `requires_otp` را دارد؛ raw registration token، invitation token، exchange ID و cookie handle در body نیستند.
- Invite مدرن یک `exchange_id` تصادفی ۲۵۶ بیتی را در یک record ثابت tab-local با TTL حداکثر ۱۰ دقیقه نگه می‌دارد. record فقط `exchangeId/createdAt` دارد و bearer، short code، route، mobile، address یا OTP در آن ممنوع است؛ در مرورگر بدون storage، binding فقط memory می‌ماند. same-ID replay یا replay با cookie دقیق claim می‌تواند ambiguity همان tab را resume کند؛ different-ID بدون/با cookie نادرست `409` و بدون context mint می‌شود.
- OTP request، OTP verify و complete از cookie context استفاده می‌کنند. request وجود OTP فعال را به `otp_requested` reconcile می‌کند؛ verify proof مصرف‌شده را فقط زیر کلید SHA-256 همان opaque handle و با TTL حداکثر برابر زمان باقی‌مانده context نگه می‌دارد و به `otp_verified` reconcile می‌کند؛ complete فقط helper خصوصی را با invitation token گرفته‌شده از همان context verified فراخوانی می‌کند. proof سراسری مبتنی بر raw bearer وجود ندارد و حذف/terminal شدن context proof handle-bound را نیز پاک می‌کند. complete یک receipt/marker محدود نگه می‌دارد و durable DB fact تکمیل Web را نیز به `registration_complete` تبدیل می‌کند؛ marker فقط پس از navigation موفق authoritative ack/clear می‌شود.
- Login OTP در حالت `registration_required` همان cookie context را server-side می‌سازد و response دیگر `registration_token` ندارد؛ Login فقط status را validate و به route نام‌دار `/register` می‌رود. اگر body پاسخ گم شود ولی cookie برسد، probe context ادامه را بازیابی می‌کند؛ بدون cookie fail-closed می‌ماند.
- پس از success عادی، auth credentialها ذخیره و context پاک می‌شود. اگر Telegram اتصال اختیاری باشد، step 4 می‌تواند روی `/register` بماند؛ refresh با credential محلی باید ابتدا `/api/auth/me` را validate و سپس cause-neutral به Home برود، ولی کاربر بدون session و context همچنان terminal است. این disposition و recovery completion-marker در گیت browser نهایی دوباره اثبات می‌شوند.
- endpoint `/api/invitations/validate/{token}` بدون شرط و پیش از هر DB access با `410 Gone` و `Cache-Control: no-store` بازنشسته شده است؛ lookup محدود بالا تنها raw-bearer response exception عمومی است.
- endpoint قدیمی `/api/auth/pending-registration` بازنشسته/حذف شده است. سه route عمومی خام `/api/auth/register-otp-request`، `/api/auth/register-otp-verify` و `/api/auth/register-complete` نیز unconditional و پیش از دسترسی به Redis/DB/OTP/provider با `410 Gone` و `Cache-Control: no-store` بازنشسته شده‌اند؛ compatibility mutation یا raw-bearer proof عمومی باقی نمانده است. منطق موردنیاز فقط helper خصوصیِ بدون decorator است و پس از load/verify کردن opaque context فراخوانی می‌شود.
- engine برنامه SQLAlchemy را با `hide_parameters=true` می‌سازد تا bind value وارد exception نشود. redaction رشته‌ای prefix-aware، bearerهای registration با prefixهای `INV`، `ACCT`، `CUST` و `REG` را پیش از formatterهای logging و payloadهای error tracking می‌پوشاند. focused regression دقیق `tests.test_logging_foundation + tests.test_error_tracking` برابر `23/23` و final serial Vitest برابر `58` فایل / `118` suite / `664` تست پاس است.
- transitionهای نتیجه‌محور، هم exception پرتاب‌شده و هم `NavigationFailure` غیرتهیِ resolveشده توسط Vue Router را failure می‌دانند. Setup Password receipt موفق را هنگام failure انتقال نگه می‌دارد تا retry بدون تکرار mutation سرور انجام شود، و copy خطای `405` علت/Method/API/route را افشا نمی‌کند. Login handoff/intended-route و شاخه مستقیم Register→Home فقط پس از navigation awaitشده و موفق پاک می‌شوند. در شاخه اختیاری Telegram، context/progress پس از receipt تکمیل، `/api/auth/me` معتبر و render مرحله ۴ terminally پاک می‌شوند؛ failure دکمه Skip فقط state انتقال را برای retry و copy cause-neutral نگه می‌دارد.

## ۳. قرارداد لایه‌ها

ترتیب مرجع:

1. security/session gate؛
2. blocking permission/identity result؛
3. offline/stale/reconnecting؛
4. contextual result/toast؛
5. optional PWA prompt؛
6. shell navigation.

PWA فقط داخل Home mount می‌شود و eligibility آن نیازمند user و بارگذاری سالم Home/trades، نبود error/loading/connection recovery، active-account بدون restriction، online بودن، نبود security layer، نصب‌نبودن برنامه، browser prompt معتبر، delay چهارهزار میلی‌ثانیه و quiet period بیست‌وچهارساعته dismissal است. بنابراین روی public/focused/system/protected route، cold loading، offline، error، stale/reconnecting یا security/session blocking layer render نمی‌شود. این Home-only شدن یکی از دقیقاً دو delta مشترک مصوب است.

toast حقیقت نتیجه اقدام را جایگزین نمی‌کند و stateهای connection داده قبلی را بدون دلیل به skeleton برنمی‌گردانند. disposition legacy دقیق است: `SessionApprovalRuntime/SessionApprovalModal`، `AppToasts` و `BottomNav` در false-branch/normal fixture base-identical مانده‌اند؛ فقط Home-only PWA و System Recovery برای denial/unavailable از equivalence مستثنا هستند.

## ۴. مرز protected

- هیچ source در Market/Messenger interior، flow معامله، offer/realtime یا chat behavior به‌عنوان Stage 3 تغییر نمی‌کند.
- routeهای full-protected هرگز V2 scope نمی‌گیرند.
- routeهای mixed هرگز `route` scope نمی‌گیرند؛ هر `section` scope فقط بیرون protected interior و با test مجاز است.
- `WorkspaceShell` و `ds-workspace-*` مالکیت Stage 4 هستند و Stage 3 آن‌ها را جذب نمی‌کند.
- ادعای base-identical فقط برای source/interiorهای محافظت‌شده و fixtureهای normal شاخه legacy معتبر است؛ source diff خالی protected به‌تنهایی کافی نیست و همین مرز باید با behavior/visual QA اثبات شود.
- دو delta مشترک و مصوب Stage 3 از ادعای base-identical مستثنا هستند: PWA روی routeهای protected دیگر render نمی‌شود چون prompt فقط Home-owned است؛ و denial/unavailable در guard مشترک به System Recovery می‌رود. هیچ‌یک مجوز تغییر interior محافظت‌شده نیست.
- legacy false branch مربوط به Session Approval modal، toast، BottomNav و لایه‌های مشترک QA شده و protected legacy normal fixture drift برابر `0` است.
- protected Home market interior با قرارداد composite شش‌بخشی `stage3-dashboard-market-region-v1`، اندازه `4553` byte و SHA-256 `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860` fail-closed می‌شود؛ base، final guard و Git-bound head دقیقاً همین مقدارند. `d037…` extraction قدیمی whole-file/legacy و قرارداد region Stage 3 نیست.

## ۵. قرارداد stale deploy recovery

fallback فایل JS منقضی در Nginx باید با `410` و `Cache-Control: no-store, no-cache, must-revalidate` پاسخ دهد و هیچ script سمت سرور برای reload اجرا نکند. boot-timeout، manual cache recovery و `vite:preloadError` فقط pathname هم‌origin را carry می‌کنند؛ query، fragment، browser/router state و secret قبلی از hard reload عبور نمی‌کنند. HTML پیش از هر script دارای `<meta name="referrer" content="no-referrer">` است و FastAPI/Nginx برای frontend document و surfaceهای `/register`، `/i/` و invitation lookup، `Referrer-Policy: no-referrer` می‌فرستند؛ proxy روی lookup/validate نیز access log را خاموش و پاسخ را `no-store` می‌کند. این کنترل‌ها exposure lookup از short code به raw bearer را محدود می‌کنند؛ استثنای deep-link تلگرام بالا جدا و user-initiated است. retry/recovery سمت client bounded می‌ماند؛ backend/deploy gate این contract را با caveat دقیق G3 بسته است.

## ۶. قرارداد legacy rollout و استثنا

- backend opaque-cookie عمداً raw `registration_token` را در receipt `verify-otp` برنمی‌گرداند. Login JS قدیمیِ ازقبل‌بارگذاری‌شده که آن field را لازم می‌داند در همان tab قابل‌سازگاری بی‌خطر نیست؛ raw fallback ممنوع است.
- پیش از production deploy باید cutover اتمیک/maintenance یا version-gated forced reload فراهم شود. interruption تب قدیمی پذیرفته و با reload قابل‌بازیابی است؛ Stage 3 ادعای zero-downtime compatibility ندارد. این release carry-forward خارج از technical closure است و بدون disposition نباید وارد استقرار شود.
- استثنای ازپیش‌موجود `STAGING_AUTH_VALUE_FOR_TEST_ONLY` تنها با `settings.environment == "staging"` و `staging_log_otp_codes == true` فعال است. default config، staging env example و هر deploy آن را `false` نگه می‌دارند و Stage 6 delivery نیز حالت true را رد می‌کند. این exception فقط test-only legacy است، production را پوشش نمی‌دهد و no-secret-log contract را برای مسیر دیگری ضعیف نمی‌کند؛ disposition نهایی Stage 3 همین مرز محدود است.

## ۷. قرارداد responsive و accessibility

- عرض‌های الزامی موبایل: `360 / 375 / 390 / 414 / 430` با مرجع ارتفاع `844`؛ proof desktop دقیق `1440×900`.
- horizontal overflow، clipping، overlap با safe area/keyboard و obstruction روی CTA صفر است.
- target عمومی حداقل `44×44`، CTA حداقل `48px`، label navigation حداقل `11px`.
- کنتراست متن عادی حداقل `4.5:1` و focus/non-text حداقل `3:1` با indicator پیوسته `3px`.
- reduced-motion، zoom، متن فارسی طولانی، screen-reader smoke و focus restore در گیت implementation اثبات می‌شوند، نه با screenshot ایستا.

## ۸. rollback

rollback باید به commitهای مستقل shell foundation، route/catch-all، Auth screen migration، PWA/layer integration و evidence تقسیم شود. هیچ commit نباید برای rollback به تغییر backend/schema یا پاک‌کردن snapshot protected نیاز داشته باشد.
