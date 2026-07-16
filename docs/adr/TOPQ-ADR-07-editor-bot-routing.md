# TOPQ-ADR-07 — بات ویرایشگر دوم و routing چند credential

- وضعیت: Accepted
- تاریخ: `2026-07-16`
- owner: Product + Backend + Operations + Security
- چالش‌ها: `TOPQ-C62..C67`, `TOPQ-N19`
- اصلاح‌کننده: بند «یک بات» در `TOPQ-ADR-01` و scope تک‌credential در `TOPQ-ADR-05`

## تصمیم

- محصول همچنان دقیقاً یک کانال و یک پیام مستقل برای هر آفر دارد؛ کانال، batching و publisher دوم اضافه نمی‌شود.
- دو هویت credential در execution plane پیش‌بینی می‌شود:
  - `primary`: انتشار آفر، callback، پیام خصوصی، معامله، مدیریت و تمام رفتار کاربر.
  - `channel_editor`: فقط `editMessageText`/ویرایش‌های مصوب همان کانال برای partial، معامله، انقضا، لغو و repair.
- بات ویرایشگر یک execution owner، feeder، scheduler یا poller API مستقل نیست. همان main queue job را با `bot_identity=channel_editor` claim می‌کند و main queue تنها مالک priority، lease، fencing، limiter، retry و feedback است.
- editor هیچ پیام بات اصلی را از Telegram کشف نمی‌کند. canonical `(publisher_bot_identity, destination_identity, message_id)` از `offer_publication_states` خوانده و پیش از edit با مقصد/پیام job تطبیق داده می‌شود.
- editor در کانال staging/production فقط کمترین دسترسی لازم `can_edit_messages` را دارد؛ post، delete، promote و سایر دسترسی‌ها پیش‌فرض ممنوع‌اند و preflight باید readback دسترسی مؤثر را ثبت کند.
- fallback خودکار editor به primary یا برعکس ممنوع است. outage، `401/403/429` یا revoke دسترسی editor jobهای edit را پایدار نگه می‌دارد و lane مربوط را متوقف می‌کند؛ هیچ worker دوم همان job را دوباره اجرا نمی‌کند.
- بودجه bot token برای هر `bot_identity` جداست، اما lane مقصد کانال تا اثبات خلاف آن در staging مشترک و محافظه‌کارانه می‌ماند. مستندات Telegram افزایش ظرفیت همان chat با بات دوم را تضمین نمی‌کنند.
- token در job، payload، log یا artifact ذخیره نمی‌شود. `bot_identity` فقط به credential allowlisted و fingerprint محیط نگاشت می‌شود.
- فعال‌شدن editor در production منوط به smoke موفق cross-bot edit و benchmark تکرارشونده Stage 4 است. اگر cross-bot edit یا بهبود عملیاتی اثبات نشود، schema/routing multi-bot-ready باقی می‌ماند ولی همه jobها با primary اجرا می‌شوند.

## گزینه‌های ردشده

- poll کردن API پروژه توسط بات دوم برای یافتن editها.
- execution queue مستقل، retry مستقل یا اتصال مستقیم feeder edit به Bot API.
- استفاده از بات دوم به‌عنوان publisher یا مسیر پیام خصوصی کاربران.
- fallback خاموش‌نشده میان tokenها، تغییر token job پس از claim و اجرای هم‌زمان دو bot روی یک edit.
- فرض دوبرابرشدن نرخ کانال بدون شاهد staging.

## داده، migration و sync

`telegram_delivery_jobs.bot_identity` routing immutable هر job را نگه می‌دارد و index کمکی `(bot_identity, destination_key, state, next_retry_at)` دارد. token و secret وارد PostgreSQL یا sync نمی‌شوند. `offer_publication_states` message identity ناشر را canonical نگه می‌دارد؛ editor فقط همان message id را ویرایش و هیچ Offer مرجع را mutate نمی‌کند. execution و credential mapping فقط foreign-local هستند.

## failure mode و کنترل

- editor unavailable/revoked: edit pending می‌ماند، alert bot-specific ایجاد می‌شود و publication/message خصوصی primary ادامه دارد.
- `429` editor: cooldown token editor و lane مشترک مقصد اعمال می‌شود؛ publication primary حق عبور از cooldown مقصد را ندارد تا گیت staging scope مستقل را ثابت کند.
- race دو edit: coalescing/freshness فقط آخرین نسخه معتبر را eligible می‌کند و fencing نتیجه token/worker قدیمی را رد می‌کند.
- message id یا publisher identity ناسازگار: job `QUARANTINED` و بدون fallback متوقف می‌شود.
- credential یا destination اشتباه: preflight fingerprint قبل از claim fail-closed است.

## تست و observability

- smoke واقعی staging: primary پیام را می‌فرستد و editor با `can_edit_messages` همان `chat_id/message_id` را ویرایش می‌کند؛ متن و keyboard توسط observer تأیید می‌شوند.
- negative permission: نبود/revoke `can_edit_messages`، token اشتباه و channel اشتباه با fault adapter و preflight تست می‌شوند؛ credential زنده تخریب نمی‌شود.
- benchmark A/B با trace یکسان: primary-only در برابر primary+editor، شامل send/edit ترکیبی، `429` هر token، outage editor، restart، race و backlog.
- metric فقط با label کم‌کاردینال `bot_role=primary|channel_editor`، method، priority، destination-class و outcome ثبت می‌شود؛ token و bot id واقعی label نیست.
- معیار فعال‌سازی: صفر duplicate/stale/fallback، cross-bot receipt کامل، permission readback صحیح و بهبود قابل‌اندازه‌گیری backlog/goodput بدون نقض SLO کانال.

## rollout و rollback

ابتدا schema و routing با editor خاموش deploy می‌شوند. Stage 4 smoke و benchmark را اجرا می‌کند؛ سپس یک flag مستقل `channel_editor_enabled` فقط برای editهای کانال فعال می‌شود. rollback این flag را خاموش و claim جدید editor را متوقف می‌کند؛ in-flight/ambiguous قبل از routing دوباره reconcile می‌شوند. pending job موجود خودکار به primary بازنویسی نمی‌شود و migration حذف نمی‌شود.
