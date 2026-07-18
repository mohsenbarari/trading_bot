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

## الحاقیه ممیزی `2026-07-18`

- pure transition به‌تنهایی reconciliation نیست. یک observer/reconciler اجرایی، evidence ledger، checkpoint بادوام، alert سن و ابزار inspect/dry-run برای `AMBIGUOUS/PENDING_RECONCILE/AMBIGUOUS_UNRESOLVED` اجباری است.
- provider outcome قطعی باید ابتدا در inbox append-only مستقل commit شود. سپس state job، cooldown و domain feedback با یک saga idempotent و replayable اعمال شوند؛ شکست feedback نباید `429/retry_after` یا پاسخ قطعی provider را rollback کند.
- هر پاسخ `2xx` برای `sendMessage` که `ok=true` و message id کامل و معتبر ندارد، ambiguous است و retry کور نمی‌شود.

## الحاقیه پیاده‌سازی `2026-07-19`

- migration افزایشی `fc18b9d0e2f3` دو جدول foreign-local و `NO_SYNC` می‌سازد: inbox نتیجه provider با identity یکتای `(job_id, lease_token)` و evidence ledger reconciliation. provider factهای ثبت‌شده immutable هستند؛ فقط lifecycle اعمال آن‌ها `pending/applied/quarantined` تغییر می‌کند.
- worker ابتدا provider fact redacted را در transaction مستقل commit می‌کند و سپس job، cooldown و feedback دامنه را در یک transaction دوم اعمال می‌کند. failure همان نتیجه را با backoff replay می‌کند و هرگز Telegram API را دوباره برای آن fence صدا نمی‌زند؛ lease recovery نیز job دارای outcome pending را لمس نمی‌کند.
- reconciler برای edit/delete/callback فقط پس از freshness بازخوانی‌شده retry امن می‌سازد. `sendMessage` بدون شاهد مثبت پس از grace به `AMBIGUOUS_UNRESOLVED` می‌رود. نبود شاهد، مجوز retry نیست.
- resolution دستی فقط از service ممیزی‌شده، با `dry_run`، actor/evidence اجباری و ذخیرهٔ hash آن‌ها انجام می‌شود. «تحویل قطعی» message id و feedback دامنه می‌خواهد؛ «عدم تحویل قطعی» job موجود را retryable می‌کند. SQL مستقیم همچنان ممنوع است.
- upgrade→downgrade تا والد→upgrade و faultهای `429 + feedback rollback + restart/replay` روی PostgreSQL scratch واقعی پاس شدند؛ runtime اصلی همچنان code-disabled است.
