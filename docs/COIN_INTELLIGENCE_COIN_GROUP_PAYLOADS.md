# P2-C-B2 — ورودی JSON گروه‌های سکه

## قرارداد

هر post خصوصی ورودی ممکن است یک object JSON، یک array JSON، یا چند object
کامل با divider افقی مستند باشد. فقط eventهای `coin` با source_key دقیق
`account2_group1` یا `account2_group2` و event type `message_created` یا
`message_updated` پذیرفته می‌شوند. هیچ channel ID، لینک، نام کانال یا outer
message ID به staging یا Market Store انتقال نمی‌یابد.

collector باید `available_at_utc` را از timestamp قابل‌اعتماد **همان post
دریافتی** دهد. زمان `telegram_datetime` داخل event فقط زمان رخداد است و
نمی‌تواند availability را عقب ببرد؛ به‌این‌ترتیب Snapshot بعدی از اطلاعاتی
که هنوز به سرور نرسیده استفاده نمی‌کند.

## ایمنی batch و update

هر inner event به `(group_number,message_id)` map می‌شود. duplicate دقیق
فقط یک بار stage می‌شود. اگر دو version متفاوت از یک message در یک batch
باشند، تنها version با `telegram_edit_datetime` اکیداً جدیدتر انتخاب
می‌شود؛ بدون چنین ترتیب قطعی هر دو version حذف می‌شوند. malformed یا
cross-routed sibling هیچ sibling درست را مسموم نمی‌کند، اما خودش به staging
نمی‌رسد.

reply فقط وقتی parent ID نگه می‌دارد که source آن را `resolved_*` اعلام کرده
باشد. reply مبهم، هرگز non-reply یا parent حدسی نمی‌شود.

## وضعیت runtime

این decoder pure است و collector، credential، scheduler و انتقال سه‌سروره
ندارد. caller بعدی فقط `stage_coin_group_payload` را در transaction staging
فراخوانی می‌کند. P2-C-B3/P2-C-B4 هنوز تنها مراحل مجاز برای resolution قیمت و
تأیید trade هستند.
