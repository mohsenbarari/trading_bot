# TOPQ-ADR-03 — timeout مبهم، lease و reconciliation

- وضعیت: Accepted
- تاریخ: `2026-07-16`
- owner: Backend + Operations
- چالش‌ها: `TOPQ-C07`, `TOPQ-C24`, `TOPQ-C35`, `TOPQ-C49`, `TOPQ-C55`, `TOPQ-N14`

## تصمیم

- `sendMessage` با احتمال پذیرش و بدون پاسخ قابل‌اعتماد به `AMBIGUOUS` می‌رود و retry خودکار یا دستی کور ندارد.
- شاهد مثبت observer آن را `SENT` می‌کند. نبود receipt به‌تنهایی اثبات عدم ارسال نیست؛ پایان پنجره reconciliation آن را `AMBIGUOUS_UNRESOLVED` می‌کند.
- retry فقط با شاهد audit‌شده‌ای مجاز است که عدم پذیرش side effect را قطعی کند.
- edit idempotent پس از بازخوانی state به `PENDING_RECONCILE` و سپس retry می‌رود؛ callback عبورکرده از deadline `EXPIRED_INTERACTION` است.
- lease حداقل `request_timeout + 15s`، دارای fencing token و heartbeat است. HTTP خارج transaction claim انجام می‌شود و نتیجه token قدیمی پذیرفته نمی‌شود.
- operator فقط ابزار inspect/dry-run/pause/resume/cancel audit‌شده دارد؛ SQL مستقیم ممنوع است.

## گزینه‌های ردشده

- at-least-once کور برای send: duplicate می‌سازد.
- at-most-once با تبدیل فوری timeout به failure: احتمال delivery واقعی را پنهان می‌کند.
- اعتماد به idempotency key داخلی برای Telegram: Bot API آن را enforce نمی‌کند.
- lease recovery بدون fencing یا transaction باز طی HTTP.

## داده، migration و sync

stateهای ambiguity، reconciliation evidence، fencing token، lease timestamps، observer checkpoint و audit actor در schema افزایشی نگهداری می‌شوند. فقط evidence دامنه‌ای لازم sync می‌شود؛ lease، fencing و provider result local foreign می‌مانند. payload و evidence مطابق ADR-06 redacted است.

## failure mode و کنترل

- crash پس از پاسخ Telegram و پیش از commit: job ambiguous و وابستگی‌هایش متوقف می‌شوند.
- observer gap: run یا reconciliation inconclusive است و pass استنباطی گزارش نمی‌شود.
- دو worker: فقط fencing token جاری حق resolve دارد.

## تست و observability

fault injection در مرز send/commit، lease expiry، نتیجه worker قدیمی، observer restart و retry دستی ناامن اجرا می‌شود. شمار ambiguous/unresolved، سن آن‌ها و reconciliation outcome alert دارد.

## feature flag و rollback

در rollback ابتدا claim متوقف و همه leased/ambiguous تعیین تکلیف می‌شوند؛ direct sender پیش از آن روشن نمی‌شود. ابزار break-glass نیز flag و audit مستقل دارد.
