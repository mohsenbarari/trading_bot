# Stage 3 — Content necessity matrix

وضعیت audit: `policy_retained_quantitative_measurement_not_in_frozen_evidence`

این ماتریس برای جلوگیری از بازگشت metadata، summary، badge و کارت بی‌اقدام در shell/Auth است. بسته immutable مرحله سوم، browser acceptance `23/23` و local evidence `21/21` را hash-bound می‌کند، اما هیچ assertion یا projection مستقلی برای شمارش واحدهای همیشه‌نمایان، duplicate fact یا counter بی‌اقدام ندارد. بنابراین closure فنی Stage 3 هیچ عددی برای این audit اختراع نمی‌کند؛ policy باقی می‌ماند و سنجش کمی به evidence اختصاصی بعدی موکول است.

## قاعده تصمیم

هر واحد همیشه‌نمایان باید دست‌کم یکی از این اثرها را داشته باشد:

1. اقدام جاری را ممکن یا سریع‌تر کند؛
2. وضعیت یا نتیجه لازم را روشن کند؛
3. از خطا، پیامد، ریسک امنیتی یا حریم خصوصی جلوگیری کند.

## inventory آغاز

| surface/unit | تصمیم | پرسش یا اثر | قاعده پذیرش |
| --- | --- | --- | --- |
| brand identity محدود | `Keep` | کاربر بداند وارد چه محصولی شده، بدون معرفی محصول فقط به‌عنوان «بازار» | یک خانه بصری؛ بدون tagline، subtitle یا توضیح تکراری |
| عنوان جاری | `Keep` | task/state فعلی را روشن کند | در هر state فقط یک heading اصلی |
| توضیح زیر عنوان | `On demand / conditional` | فقط در صورت اثر مستقیم بر اقدام، وضعیت یا ریسک | helper کلی، متن قراردادی و تکرار heading حذف |
| progress چندمرحله‌ای | `Conditional Keep` | جایگاه و باقی‌مانده فرایند واقعی را روشن کند | فقط با step count واقعی؛ صفر progress ساختگی `REG-02` |
| خطای فیلد | `Keep in context` | علت اصلاح ورودی را همان‌جا روشن کند | نزدیک فیلد؛ بدون alert کلی تکراری |
| consequential disclosure | `Keep in context` | از ریسک حریم خصوصی/امنیتی پیش از اقدام جلوگیری کند | نزدیک آدرس، مدرک یا recovery action؛ نه footer دور |
| deadline/مهلت مؤثر | `Conditional Keep` | تصمیم زمانی کاربر را عوض کند | فقط authoritative؛ بدون وعده cadence داخلی |
| CTA اصلی | `Keep` | اقدام بعدی را ممکن کند | در هر مرحله یک primary، با busy/disabled صادقانه |
| recovery/back واقعی | `Conditional Keep` | از بن‌بست یا ازدست‌رفتن context جلوگیری کند | فقط مقصد معتبر؛ بدون dead action یا loop |
| System Recovery outcome | `Conditional Keep` | تفاوت `not-found`، `forbidden` و `deep-link-failure` را بدون افشای جزئیات داخلی روشن کند | outcome canonical hyphenated؛ shell مهمان/احرازشده متناسب؛ بدون path/target/secret |
| context ثبت‌نام در انتظار | `Conditional Keep` | هویت حساب و مرحله لازم برای ادامه را تأیید کند | response فقط account/mobile mask‌شده و `kind/progress/requires_otp`؛ صفر raw token/handle/exchange ID |
| active Web invitation link | `Keep canonical short link only` | کاربر/پیامک/copy بات باید بدون bearer به Invite Landing برسد | API، SMS و bot-copy فقط pathname دقیق `/i/[A-Za-z0-9]{8}`؛ هیچ query/fragment bearer؛ responseهای create/list/relation بدون `token` و `invitation_token` |
| public invitation lookup bearer | `Never render / memory-only` | short code برای ادامه باید به دعوت authoritative resolve شود | تنها raw-bearer response exception؛ `/api/invitations/lookup/:code` با `no-store/no-referrer/access-log-off`؛ بدون storage/DOM/log؛ فقط تا انتخاب Web/Telegram در memory |
| raw Web registration handoff | `Remove from rendered/persisted content` | bearer secret است، نه محتوای محصول | فقط یک POST body تا exchange authoritative/terminal؛ صفر حضور در Web URL/storage/history/DOM/cookie/log؛ validate raw pre-DB و سه raw mutation endpoint بازنشسته `410/no-store` |
| Telegram invitation deep-link | `Conditional approved exception` | اقدام صریح کاربر دعوت را به bot purpose-bound منتقل می‌کند | تنها raw URL exception: user-initiated `t.me?start=<raw-invitation>`؛ نه محتوای همیشه‌نمایان و نه مجوز fallback/persistence در Web |
| database/log/error secret detail | `Never render or emit` | bind value و bearer نباید از exception یا telemetry نشت کنند | SQLAlchemy `hide_parameters=true`؛ redaction prefix-aware برای `INV/ACCT/CUST/REG` در logging/error tracking؛ focused regression `23/23` و final integration gate پاس |
| exchange idempotency binding | `Never render` | metadata تصادفی tab-local برای response-loss است، نه هویت یا محتوای محصول | record ثابت TTL≤۱۰m فقط ID/timestamp؛ بدون token/code/mobile/route/address؛ پاک‌سازی پس از terminal/navigation معتبر |
| completed registration recovery | `Conditional Keep` | نتیجه durable را بدون درخواست دوباره OTP/address روشن کند | copy cause-neutral؛ marker/cookie opaque؛ navigation معتبر پیش از clear؛ session معتبر پس از `/api/auth/me` به Home |
| Setup navigation retry receipt | `Never render as metadata` | mutation موفق نباید با failure انتقال تکرار شود | receipt در memory نگه داشته می‌شود؛ retry فقط navigation؛ resolved NavigationFailure هم failure؛ `405` بدون Method/API/route metadata |
| route/path/backend/source metadata | `Remove` | اثر محصولی ندارد و می‌تواند افشاگر باشد | count نهایی صفر |
| summary/card/footer تکراری | `Remove` | هیچ اقدام، وضعیت یا ریسک تازه‌ای اضافه نمی‌کند | count نهایی صفر |
| PWA prompt | `Conditional On demand` | فقط پس از Home سالم و در فرصت مناسب | صفر حضور در public/loading/offline/security modal |
| connection status | `Conditional Keep` | توان اعتماد به freshness/اقدام را روشن کند | تمایز offline/stale/reconnecting؛ بدون تکرار در چند بلوک |

## وضعیت اندازه‌گیری در closure

```text
alwaysVisibleUnitCount = not_measured_by_frozen_evidence
justifiedAlwaysVisibleUnitCount = not_measured_by_frozen_evidence
unjustifiedAlwaysVisibleUnitCount = not_measured_by_frozen_evidence
routeOrBackendMetadataCount = not_measured_by_frozen_evidence
duplicateFactCountPerStateViewport = not_measured_by_frozen_evidence
unactionableCounterCount = not_measured_by_frozen_evidence
contentNecessityAuditStatus = policy_retained_measurement_deferred
```

این deferred measurement گیت contract-hard تکمیل Stage 3 نیست. تا زمانی که artifact اختصاصی تولید نشود، هیچ‌کدام از countها—even اگر از screenshot ظاهراً صفر به‌نظر برسد—`0` یا `passed` اعلام نمی‌شود.
