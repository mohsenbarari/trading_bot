# TOPQ-ADR-02 — منبع حقیقت صف، dedupe و مرز Iran/foreign

- وضعیت: Accepted
- تاریخ: `2026-07-16`
- owner: Backend
- چالش‌ها: `TOPQ-C22`, `TOPQ-C23`, `TOPQ-C29`, `TOPQ-C33`, `TOPQ-C38`, `TOPQ-C45`

## تصمیم

- `telegram_delivery_jobs` منبع حقیقت execution، priority، lease، attempt و provider result است.
- `offer_publication_states` نگاشت canonical آفر به bot/destination/message و projection publication است؛ `offers.channel_message_id` فقط mirror سازگاری است.
- outboxها و stateهای فعلی پس از cutover feeder هستند و Bot API یا retry فنی ندارند.
- dedupe identity برابر `feeder_kind + source_natural_id + source_version + action_kind + destination_identity` است.
- `bot_identity` routing immutable job است، ولی token/secret در صف ذخیره نمی‌شود؛ هر identity فقط به credential allowlisted foreign نگاشت می‌شود.
- mutation دامنه و intent روی home server در یک تراکنش ثبت می‌شوند. intent durable sync و main job فقط روی foreign به‌صورت اتمیک یا با outbox بازیاب‌پذیر ساخته می‌شود.
- lease، worker id، attempt، cooldown و Telegram result local-only foreign هستند و به Iran sync نمی‌شوند.
- ترتیب اصلی پس از priority/deadline از `enqueued_seq` افزایشی foreign استفاده می‌کند؛ ساعت و ID محلی دو peer FIFO مشترک نمی‌سازد.

## گزینه‌های ردشده

- استفاده هم‌زمان از `offer_publication_states` و notification outbox به‌عنوان دو execution owner.
- اجرای Telegram روی Iran یا انتقال lease از طریق sync.
- dedupe صرفاً با payload hash یا timestamp محلی.
- overwrite کردن message identity canonical از mirror روی Offer.

## schema و migration

migration افزایشی شامل job identity، source identity/version، `bot_identity`، destination، method، template version، priority، deadline/eligibility، sequence، state، lease fencing، attempts، provider result و timestamps است. unique constraint روی dedupe identity، partial index claim روی state/priority/eligibility/sequence و index routing بر bot/destination/state الزامی است. حذف جدول legacy در این Roadmap مجاز نیست.

## failure mode و کنترل

- قطع peer پس از commit: intent محلی باقی می‌ماند و پس از recovery دقیقاً یک main job می‌سازد.
- replay یا reorder sync: natural-key upsert و source version از downgrade جلوگیری می‌کند.
- message mismatch: هر edit bot/destination/message را validate و مورد ناسازگار را quarantine می‌کند.

## تست و observability

unique/concurrent enqueue، peer outage، replay دوطرفه، clock skew، message mismatch و parity projection تست می‌شوند. metricهای sync lag، orphan intent، duplicate prevented و identity mismatch اجباری‌اند.

## feature flag و rollback

schema پیش از code و با flag خاموش deploy می‌شود. rollback code-forward-compatible است؛ رکوردهای صف حذف نمی‌شوند و ownership فقط پس از drain/reconciliation تغییر می‌کند.

## الحاقیه ممیزی `2026-07-18`

- تغییر NO_SYNC table/field یا fingerprint بدون negotiation نسخه، rolling deploy دو peer را ناسازگار می‌کند. پیش از Stage 4 باید mixed-version matrix، semantics fingerprint و release choreography صریح پیاده و تست شود.
- intent هر دو recipient معامله باید در همان transaction commit معامله ساخته شود؛ BackgroundTask پس از commit فقط مجاز به پردازش intent پایدار است.
- endpoint عمومی ایجاد آفر باید idempotency اجباری یا receipt server-side سازگار داشته باشد؛ تکیه بر رفتار frontend برای clientهای دیگر کافی نیست.
