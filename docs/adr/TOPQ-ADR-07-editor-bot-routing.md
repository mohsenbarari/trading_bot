# TOPQ-ADR-07 — بات ویرایشگر دوم و routing چند credential

- وضعیت: Accepted
- تاریخ: `2026-07-16`
- owner: Product + Backend + Operations + Security
- چالش‌ها: `TOPQ-C62..C68`, `TOPQ-N19`
- اصلاح‌کننده: بند «یک بات» در `TOPQ-ADR-01` و scope تک‌credential در `TOPQ-ADR-05`

## تصمیم

- محصول همچنان دقیقاً یک کانال و یک پیام مستقل برای هر آفر دارد؛ کانال، batching و publisher دوم اضافه نمی‌شود.
- دو هویت credential در execution plane پیش‌بینی می‌شود:
  - `primary`: انتشار آفر، callback، پیام خصوصی، معامله، مدیریت و تمام رفتار کاربر.
  - `channel_editor`: فقط `editMessageText`/ویرایش‌های مصوب همان کانال برای partial، معامله، انقضا، لغو و repair.
- بات ویرایشگر یک execution owner، صف پایدار، state machine، feeder، scheduler دامنه‌ای یا poller API مستقل نیست. سرویس main queue زیر owner واحد `queue-v1` دو claim/execution lane هم‌زمان دارد: lane editor فقط `bot_identity=channel_editor` و lane primary فقط `bot_identity=primary` را claim می‌کند؛ priority، lease، fencing، retry و feedback همچنان در همان control plane مشترک‌اند.
- lane editor limiter، semaphore/in-flight و token budget خود را دارد و work-conserving است. backlog، sleep، retry یا priority بالاتر primary نباید worker editor را رزرو یا متوقف کند؛ priority فقط میان jobهای رقیب همان lane یا منبع واقعاً مشترک اعمال می‌شود و این رفتار priority inversion نیست.
- editor هیچ پیام بات اصلی را از Telegram کشف نمی‌کند. canonical `(publisher_bot_identity, destination_identity, message_id)` از `offer_publication_states` خوانده و پیش از edit با مقصد/پیام job تطبیق داده می‌شود.
- editor در کانال staging/production فقط کمترین دسترسی لازم `can_edit_messages` را دارد؛ post، delete، promote و سایر دسترسی‌ها پیش‌فرض ممنوع‌اند و preflight باید readback دسترسی مؤثر را ثبت کند.
- fallback خودکار editor به primary یا برعکس ممنوع است. outage، `401/403/429` یا revoke دسترسی editor jobهای edit را پایدار نگه می‌دارد و lane مربوط را متوقف می‌کند؛ هیچ worker دوم همان job را دوباره اجرا نمی‌کند.
- بودجه bot token و ظرفیت اجرای هر `bot_identity` جداست، اما gate نرخ/cooldown مقصد کانال تا اثبات خلاف آن در staging مشترک و محافظه‌کارانه می‌ماند. این gate فقط admission به مقصد را محدود می‌کند و نباید به dispatcher یا claim lock سراسری تبدیل شود. مستندات Telegram افزایش ظرفیت همان chat با بات دوم را تضمین نمی‌کنند.
- token در job، payload، log یا artifact ذخیره نمی‌شود. `bot_identity` فقط به credential allowlisted و fingerprint محیط نگاشت می‌شود.
- فعال‌شدن editor در production منوط به smoke موفق cross-bot edit و benchmark تکرارشونده Stage 4 است. اگر cross-bot edit یا بهبود عملیاتی اثبات نشود، schema/routing multi-bot-ready باقی می‌ماند ولی همه jobها با primary اجرا می‌شوند.

## گزینه‌های ردشده

- poll کردن API پروژه توسط بات دوم برای یافتن editها.
- execution queue مستقل، retry مستقل یا اتصال مستقیم feeder edit به Bot API.
- یک dispatcher/worker تک‌اسلاتی که jobهای editor را پشت backlog پر‌اولویت primary نگه دارد.
- استفاده از بات دوم به‌عنوان publisher یا مسیر پیام خصوصی کاربران.
- fallback خاموش‌نشده میان tokenها، تغییر token job پس از claim و اجرای هم‌زمان دو bot روی یک edit.
- فرض دوبرابرشدن نرخ کانال بدون شاهد staging.

## داده، migration و sync

`telegram_delivery_jobs.bot_identity` routing immutable هر job را نگه می‌دارد و index کمکی `(bot_identity, destination_key, state, next_retry_at)` دارد. token و secret وارد PostgreSQL یا sync نمی‌شوند. `offer_publication_states` message identity ناشر را canonical نگه می‌دارد؛ editor فقط همان message id را ویرایش و هیچ Offer مرجع را mutate نمی‌کند. execution و credential mapping فقط foreign-local هستند.

## failure mode و کنترل

- editor unavailable/revoked: edit pending می‌ماند، alert bot-specific ایجاد می‌شود و publication/message خصوصی primary ادامه دارد.
- backlog اشباع primary: lane editor claim و dispatch را ادامه می‌دهد؛ فقط بازه دارای شاهد destination gate/circuit مشترک مجاز به نگه‌داشتن هر دو lane است.
- `429` editor: cooldown token editor و در صورت انطباق scope، gate مشترک مقصد اعمال می‌شود؛ publication primary حق عبور از cooldown واقعاً مشترک مقصد را ندارد تا گیت staging scope مستقل را ثابت کند، اما private/admin primary ادامه دارد.
- race دو edit: coalescing/freshness فقط آخرین نسخه معتبر را eligible می‌کند و fencing نتیجه token/worker قدیمی را رد می‌کند.
- message id یا publisher identity ناسازگار: job `QUARANTINED` و بدون fallback متوقف می‌شود.
- credential یا destination اشتباه: preflight fingerprint قبل از claim fail-closed است.

## تست و observability

- smoke واقعی staging: primary پیام را می‌فرستد و editor با `can_edit_messages` همان `chat_id/message_id` را ویرایش می‌کند؛ متن و keyboard توسط observer تأیید می‌شوند.
- negative permission: نبود/revoke `can_edit_messages`، token اشتباه و channel اشتباه با fault adapter و preflight تست می‌شوند؛ credential زنده تخریب نمی‌شود.
- benchmark A/B با trace یکسان: primary-only در برابر primary+editor، شامل send/edit ترکیبی، `429` هر token، outage editor، restart، race و backlog.
- تست جداسازی ظرفیت: حداقل ۳۰۰ job آماده `M0/M1` primary که دست‌کم ۲۰۰ مورد آن private/admin و خارج gate کانال‌اند، همراه ۱۰۰ edit آماده editor؛ editor باید پیش از صفرشدن backlog primary پیشروی کند و تست معکوس نیز primary را پشت editor نگه ندارد. زیرآزمون دوم با publication کانال، توقف gate مشترک را جدا اندازه می‌گیرد و هر توقف مشترک باید در ledger دقیقاً به destination gate یا circuit دارای شاهد منتسب شود.
- metric فقط با label کم‌کاردینال `bot_role=primary|channel_editor`، method، priority، destination-class، outcome و wait-reason=`bot_lane|destination_gate|circuit` ثبت می‌شود؛ token و bot id واقعی label نیست.
- معیار فعال‌سازی: صفر duplicate/stale/fallback و صفر cross-lane head-of-line بدون gate، cross-bot receipt کامل، permission readback صحیح و بهبود قابل‌اندازه‌گیری backlog/goodput بدون نقض SLO کانال.

## rollout و rollback

ابتدا schema و routing با editor خاموش deploy می‌شوند. Stage 4 smoke، benchmark و تست head-of-line دو lane را اجرا می‌کند؛ سپس یک flag مستقل `channel_editor_enabled` فقط برای editهای کانال فعال می‌شود. rollback این flag را خاموش و claim جدید lane editor را متوقف می‌کند؛ in-flight/ambiguous قبل از routing دوباره reconcile می‌شوند. pending job موجود خودکار به primary بازنویسی نمی‌شود و migration حذف نمی‌شود.

## الحاقیه ممیزی `2026-07-18`

- channel cooldown در startup نباید private primary را متوقف کند؛ identity-only mode فقط private-capable است و مجوز publication/edit نیست.
- resume برای bot/gateway همانند destination باید saga audit‌شده، full preflight، recheck Redis و crash recovery داشته باشد.
- permission readback زنده فقط روی SHA نهایی و بلافاصله پیش از smoke Stage 4 معتبر است؛ artifact باید redacted و checksumدار باشد و تمام Story rights نیز false باشند.

## الحاقیه scope خطای permission پس از review نهایی `2026-07-19`

- `403` کانال یک hard capability failure برای credential پاسخ‌دهنده است و کلید durable/Redis آن با `(bot_identity,destination)` scope می‌شود. revoke یا نبود `can_edit_messages` editor انتشار سالم primary را مسدود نمی‌کند و editor job نیز هرگز به primary fallback نمی‌شود.
- cadence مقصد و cooldown ناشی از `429` واقعی همان destination همچنان cross-bot و محافظه‌کارانه مشترک‌اند. bot-scoped کردن permission block به معنی دوبرابر فرض‌کردن ظرفیت channel یا عبور از `retry_after` نیست.
