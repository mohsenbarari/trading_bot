# Offer Overtime - Planning Document

## Branch and Stage Delivery Policy

- All implementation changes defined by this document must be committed only on branch `candidate/offer-overtime`. That branch already exists and currently carries this document only; documentation commits on it are explicitly permitted before implementation approval.
- Every roadmap stage must end with its own commit on `candidate/offer-overtime` before work begins on the next stage.
- Immediately after a stage is completed, this document must be updated with: the commit SHA and title, implemented scope, affected components, test commands and results, retained log/artifact paths, decisions or approved deviations, and the purpose, risks, and prerequisites of the next stage.
- A stage is not complete until its required verification has passed, its completion notes have been recorded here, and its stage commit exists on `candidate/offer-overtime`.
- Unrelated code, configuration, deployment, or infrastructure changes must not be included in any stage commit.

## Document Status

- Phase: implementation started. Stages 0–2 are complete in code; the Stage 1 migration still needs a real-database run before merge/deploy. No open questions remain.
- Implementation status: in progress on `candidate/offer-overtime`. Each stage ends with its own commit and completion notes, per the delivery policy above.
- Rule: implementation was explicitly approved. Deployment and any runtime or production database action remain out of scope until the rollout stages are separately authorized.
- Decision policy: only confirmed decisions are recorded as final. Unresolved items remain explicitly open.
- Verification status: the technical review below was re-verified against the codebase at commit `540b2c0c`. Corrections from that verification are folded into the tables and stages.
- Copy policy: every user-facing message and displayed text in this feature requires explicit product-owner approval of its exact wording before the stage that ships it may be implemented. The message inventory section is the single index of record. All strings call the object a `لفظ` to match existing product vocabulary, and the feature is labelled `وقت اضافه` wherever it is named. All forty-three entries are approved.

## 1. Feature Naming

### Confirmed decision

- Label shown to users, and the only form that appears in the interface: **وقت اضافه**
- Full descriptive Persian name, for internal and documentation use only: **وقت اضافه آفر**
- English working name used only in technical discussions: **Offer Overtime**
- Because every user-facing string of this feature says «لفظ» and never «آفر», the internal full name never collides with anything a user reads.

### Current high-level meaning

After the normal offer lifetime ends, the offer may remain available during its configured overtime. A trade request received during overtime is not executed automatically and requires confirmation from the offer owner.

### Status

- Confirmed by product owner.

## How This Document Is Organized

The discovery topics were not written as separate numbered sections. They are resolved across four places, and this is where to look for each:

- **Decision Log** — every confirmed product and behavioral rule, including time semantics, configuration limits, request lifecycle, confirmation and timeout behavior, concurrency, authority, cross-server rules, and presentation.
- **Draft Product Scenario** and **Draft Exact Product Copy** — the same rules told as a walkthrough, plus the exact user-facing strings.
- **Technical Compatibility and Challenge Review** — the codebase reality behind each rule, the components to reuse, the design work required, cross-cutting risks, the audit payload scope, and the test matrix.
- **Stage-Based Implementation Roadmap** — the delivery order, with per-stage scope, locations, tests, and exit criteria.

Abuse prevention and operational limits are now fully covered: the per-offer lock, the per-owner presented-request limit, the requester cooldown, a cap of three outstanding requests per requester, and a limit of one outstanding request per requester per economic owner.

## Decision Log

| No. | Topic | Decision | Status |
| --- | --- | --- | --- |
| 1 | Feature name | The label users actually see is `وقت اضافه`, and it is the only form that appears anywhere in the interface. `وقت اضافه آفر` remains the full descriptive name for internal and documentation use only; because `آفر` never appears in a user-facing string, the two never conflict on screen. English working name for technical discussion stays `Offer Overtime`. | Confirmed |
| 2 | Request lifetime | Every overtime trade request is valid for 30 seconds. | Confirmed |
| 3 | Approval surface | Approval is shown only on the offer's origin surface and home server. | Confirmed |
| 4 | Telegram approval | Bot-origin offers send an offer-context request with approve/reject buttons to the offer owner. | Confirmed |
| 5 | WebApp approval | WebApp-origin requests appear globally with a server-based 30-second countdown. | Confirmed |
| 6 | Lot request context | Requested quantity is shown for lot-based offers. | Confirmed |
| 7 | Terminal messaging | Success sends normal trade messages to both parties; rejection/expiry sends no separate requester message. | Confirmed |
| 8 | Pre-trade anonymity | Neither party's identity is exposed to the other before the trade is successfully committed. | Confirmed |
| 9 | Near-end request lifetime | A request accepted before overtime closes keeps its full 30-second lifetime, even after the offer stops accepting new requests. | Confirmed |
| 10 | Hard invalidation | Market close, offer cancellation/completion, or either party becoming blocked invalidates a pending request immediately. | Confirmed |
| 11 | One pending request per offer | An offer can have at most one active overtime request, regardless of remaining quantity or distinct available lots. | Confirmed |
| 12 | Requester-offer cooldown | After rejection or timeout, the same requester cannot submit another request for the same offer for 30 seconds; other users remain eligible. | Confirmed |
| 13 | User overtime setting | Overtime is a synchronized per-user default applied to all new offers, with no per-offer override in the initial version. | Confirmed |
| 14 | Fixed range and default | Overtime is an integer from 0 to 10 minutes, is not admin-configurable, and defaults to 0 for all existing and new users. | Confirmed |
| 15 | Owner approval authority | The offer's economic owner, meaning `offer.user_id`, supplies the overtime setting and is the sole approver. No delegated market actor exists to exclude: accountants have no market access anywhere in the product, and a customer's group leader never acts on the customer's market, remaining only an intermediary in the trade chain, a block principal, and a tier-2 commission recipient. | Confirmed |
| 16 | Offline owner behavior | Owner presence is not required; the request remains valid for 30 seconds on its origin surface with no cross-surface fallback. | Confirmed |
| 17 | Overtime presentation | Telegram adds only `⏳` to the post; WebApp restarts the lifetime bar in green and shows an animated `⏳` in the offer-card corner. | Confirmed |
| 18 | Admin normal-lifetime changes | Preserve the current dynamic behavior: the latest admin-configured normal lifetime applies to all still-active offers. Operationally, this rarely changed setting will be edited only while the market is closed so no active offer is affected. | Confirmed |
| 19 | Historical overtime marker | A proportional static `⏳` remains in history only if at least one overtime request produced a committed trade; otherwise it is removed. | Confirmed |
| 20 | Marker coexistence and placement | Telegram keeps `⏳` beside existing trade markers. WebApp places it in the header metadata group at the end side of the RTL text flow, beside and not over the relative time. Its size is set against the adjacent relative-time text, which is `۱۰px`, not against the `۱۳px` card body. | Confirmed |
| 21 | Active-state semantics | An offer remains fully active throughout overtime, counts toward active-offer limits, and enters history/republish flows only after final termination. | Confirmed |
| 22 | User-setting UX | The setting is an explicit-save integer from 0 to 10 minutes, offered only to accounts eligible to own offers; zero is shown as disabled and changes affect only new offers. The two surfaces use different controls by decision. WebApp uses the plus/minus stepper with a one-minute step and an explicit save, reusing the existing number-stepper component. The bot uses a typed value confirmed by explicit accept/cancel, reusing the existing limit-settings pattern; no plus/minus stepper is added to the bot. Zero is entered as the value `0` rather than through a separate disable button. | Confirmed |
| 23 | Origin-scoped sequential presentation | For each offer owner and offer home server, at most one overtime request is actionable at a time; eligible requests for that owner's other offers on the same home server wait in that origin-scoped queue. | Confirmed |
| 24 | Queued-request activation | A queued request's 30-second decision lifetime starts only when it is promoted for presentation to the owner, after full revalidation. | Confirmed |
| 25 | Normal-time boundary | A request received exactly at the end of normal offer time is rejected; automatic execution ends strictly before that boundary and owner approval begins strictly after it. | Confirmed |
| 26 | Final-overtime pending tail | After overtime ends, an already-active request remains actionable for its own 30-second lifetime; the offer accepts no new requests during this tail. | Confirmed |
| 27 | Same-offer request contention | A second request on the same offer, regardless of its requested lot or quantity, never enters the owner queue; it receives the new remaining-time retry feedback defined in the copy section and must be initiated again later. This is a new string, not one of the existing contention messages. | Confirmed |
| 28 | Final-overtime boundary | A request received exactly at the final overtime deadline is rejected. | Confirmed |
| 29 | Presentation and decision-clock start | WebApp starts the 30-second clock when the server promotes the request; Telegram starts it only after successful Telegram acceptance and a recorded message id. | Confirmed |
| 30 | Telegram delivery expiry | A bot-origin request that cannot be delivered by the end of its offer-validity window closes silently and releases the owner queue. | Confirmed |
| 31 | Overtime-setting write authority | Iran is the single writer for this specific synchronized user field, enforced by placing it in the Iran-authoritative field set rather than by any whole-record rule, since write authority on user records is field-level. A bot save is an internal command to Iran and succeeds only after Iran persists it; when the servers are disconnected, it is rejected without a local write or deferred intent. | Confirmed |
| 32 | WebApp prompt priority | A security/session-recovery approval is always shown before an overtime approval. The overtime prompt is shown immediately afterward with its remaining server-authoritative time and may expire while the security prompt is resolved. | Confirmed |
| 33 | Queued bot requester feedback | A valid request queued behind another offer of the same owner and home server receives only: `⏳ درخواست معامله ثبت شد و در صف بررسی است.` This does not apply to a second request for the same offer, which receives the new remaining-time retry feedback instead. | Confirmed |
| 34 | Audit-data completeness | Persist all metadata belonging to each offer and overtime request, including immutable creation snapshots and every lifecycle transition. This internal data is not exposed to counterparties before a committed trade or to public Telegram content. | Confirmed |
| 35 | Uncertain remote request delivery | When a cross-server request has definitely not been sent, reject it with a retry message. When delivery is uncertain after a timeout, retain one idempotency key, show `⏳ در حال بررسی درخواست...`, and recover the authoritative result; never create a local request or a second request. The home-server half of this already exists; the forwarding-server retention and reconciliation is new work, not reuse. | Confirmed |
| 36 | Cross-server normal-time boundary | Follow the current market pattern: use the trusted first-server receipt time for a forwarded request. A receipt strictly before the normal deadline stays automatic, a receipt exactly at the deadline is rejected, and device time is never trusted. | Confirmed |
| 37 | Owner-decision boundary | Follow the current timed-request pattern: an owner decision is valid only when received by the offer home server strictly before the 30-second deadline. Receipt exactly at, or after, the deadline is expired and cannot create a trade. | Confirmed |
| 38 | Telegram approval-delivery priority | A private overtime approval uses Telegram queue priority `M0` with rank `1`: after current immediate callback/expiry work at rank `0`, and before offer publication at rank `2` and all `M1` and lower work. Rank `1` is not exclusive: an overdue `TRADE_RESULT` is dynamically promoted to exactly `(M0, 1)` at claim time, so the two share that rank and are separated by the existing `delivery_deadline_at` then `created_sequence` tie-break. Overtime approvals must therefore carry a delivery deadline so this tie-break is meaningful, and a starved overdue trade result must never be displaced indefinitely. | Confirmed |
| 39 | Requester cancellation | Until the owner has successfully approved the request, the requester may cancel it. Cancellation creates no trade, sends no separate owner message, closes any owner prompt, and releases the offer/owner queue for the next eligible request. | Confirmed |
| 40 | Cancellation-versus-approval race | Cancellation and owner approval are serialized atomically on the offer home server. The first valid command received by that server wins; the later command only observes the terminal result. | Confirmed |
| 41 | Cancellation controls and feedback | Bot request-status messages include `لغو درخواست`; a successful cancellation updates that same message to `درخواست لغو شد.` and removes the button. WebApp shows `لغو درخواست` while waiting, then closes the status with the same short confirmation. The owner receives no separate cancellation message. | Confirmed |
| 42 | Bot cancellation access | The inline `لغو درخواست` button beneath the bot request-status message is the only bot cancellation control. No additional main-menu, user-panel, or pending-request list is added. If that message is unavailable, there is intentionally no secondary cancellation route. | Confirmed |
| 43 | Queued-request promotion feedback | When a queued bot request is presented to the owner, edit the existing requester status message to `⏳ درخواست در حال بررسی است.` and retain its cancellation button. WebApp changes from its queued state to the authoritative 30-second countdown at the same point. | Confirmed |
| 44 | Bot request-status terminal text | Never delete a bot overtime status/approval message. Remove its inline buttons and edit the requester status to `معامله انجام شد.` after approval, `درخواست انجام نشد.` after rejection, timeout, or invalidation, and `درخواست لغو شد.` after requester cancellation. Normal trade messages remain unchanged. | Confirmed |
| 45 | Owner approval-message terminal text | Never delete the owner's bot approval message. Remove its inline buttons and edit it to `معامله انجام شد.` after approval, `درخواست رد شد.` after owner rejection, and `درخواست بسته شد.` after timeout, requester cancellation, or invalidation. | Confirmed |
| 46 | Republished-offer overtime value | A republished offer is a new independent offer and snapshots the economic owner's current persisted overtime setting at its own creation. It never inherits the source offer's overtime snapshot or overtime-history marker. | Confirmed |
| 47 | Final-tail visual state | After overtime ends while one valid approval is still pending, complete the green bar and make WebApp `⏳` static; retain channel `⏳`. The offer is read-only. On terminal completion, retain the marker only if an overtime request committed a trade; otherwise remove it. | Confirmed |
| 48 | Receipt time is the only phase input | The trusted first-server receipt time alone classifies a request into automatic, approval, or rejected. Transit delay, home-server processing time, and the current transit-grace window never move a request between phases. The existing processing-time fallback is removed, so a valid overtime request that arrives slowly reaches owner approval instead of being rejected as expired. | Confirmed |
| 49 | Grace window's remaining role | Transit grace no longer decides automatic versus approval. It survives only to let a request whose receipt was validly inside a phase still be finalized after the expiry worker has already advanced the offer's status, which is the situation the current in-flight allowance handles for normal-time expiry. That allowance must be extended to the overtime phase and the final tail rather than left applying only to normal time. | Confirmed |
| 50 | Worker and request path share one boundary | The expiry worker and the request path evaluate the same strict comparison against the same lifecycle projection. The current asymmetry, where the worker expires an offer exactly at the deadline while the trade path still accepts it, is closed. | Confirmed |
| 51 | Idempotency key is mandatory | Every overtime request carries an idempotency key, because the request ledger's synchronization identity refuses a row without one. | Confirmed |
| 52 | Requester concurrency cap | Two limits apply together. A requester may hold at most **three** simultaneously outstanding overtime requests across distinct offers, and at most **one** outstanding request against any single economic owner. Every nonterminal request counts, including one that is merely queued and whose decision clock has not started, because a queued request already holds its offer's logical lock. Both limits release on every terminal outcome, including approval, rejection, timeout, requester cancellation, and hard invalidation. Neither is scoped per home server. | Confirmed |
| 53 | Owner reachability warning is required | Enabling a nonzero value must warn the owner where approvals will appear. The warning must not promise one fixed surface, because the preference is global while approval is bound per offer to that offer's own origin. Approved wording: `تأیید هر لفظ فقط در همان محل ثبت لفظ نمایش داده می‌شود: لفظ وب در وب‌اپ و لفظ بات در بات.` Repeated silent expiry for one owner is also an operational signal in the diagnostics stage. | Confirmed |
| 54 | One request per requester per owner | The per-owner limit costs the requester nothing, because the owner queue presents one request at a time and a second request against the same owner could never receive a parallel decision anyway. It only removes a reservation that would otherwise block other buyers. Its effect is that a requester's three requests must target three different owners, so all three sit in independent queues, are presented in parallel, and each offer stays locked for its own thirty seconds rather than waiting out the others. | Confirmed |
| 55 | Where the limits are counted | The offer home server enforces both limits locally and atomically against its own request ledger, which already contains synchronized mirror rows of the other server's requests. No cross-server round trip is added to request creation, and a bot-origin request never becomes dependent on Iran being reachable. Synchronization lag makes the count best-effort across servers, and that is accepted: these limits are abuse controls, not correctness invariants. The strictly atomic guarantees remain the per-offer lock and the one-presented-request-per-owner-scope rule, which are what prevent duplicate trades. A transient overshoot of one request is tolerable and must not be treated as a defect. | Confirmed |
| 56 | Message-text approval gate | Every message sent to a user and every text shown to a user by this feature requires explicit product-owner approval of its exact wording before the stage that ships it may be implemented. This covers bot messages and their edits, WebApp prompts and status text, inline button labels, callback answers, validation and error text, the preference-save confirmations, the reachability warning, and any channel post change. All such strings call the object a `لفظ`, never an `آفر`. No stage may introduce, reword, or reuse a user-facing string outside the approved inventory. | Confirmed |
| 57 | Who sees the overtime setting | The setting appears only for accounts that can own their own offers. Accountants never see it, because they are blocked from the market in the API, the bot, and the frontend. Tier-2 customers never see it, because they are refused offer creation and may only request against other people's offers; they also have no bot access at all. Tier-1 customers and ordinary users do see it, and a tier-1 customer's offers are their own, carrying their own `user_id`, so they are their own approver. | Confirmed |
| 58 | Blocking uses trade principals | Wherever this feature revalidates that neither party is blocked, it must use the existing trade-principal resolution, which maps a customer to their group leader, rather than comparing raw user ids. Otherwise a block between two group leaders would not stop a trade routed through their customers. | Confirmed |
| 59 | Ownership is verified server-side on every decision | Every approve, reject, and cancel, on both surfaces and through both servers, re-reads the request and its offer at the offer home server and verifies that the caller is that offer's economic owner, or for cancellation the original requester, before doing anything else. This is never inferred from the fact that only the owner was sent the message or shown the prompt. Delivery is not authorization. The precedent is the existing offer-expiry callback, which performs exactly this check and answers `❌ شما مالک این لفظ نیستید` even though its message is also private. | Confirmed |
| 60 | Request identifiers are opaque and never capabilities | `OfferRequest.id` is a sequential integer, so exposing it to a client would let anyone enumerate other people's requests. The overtime request therefore carries an opaque public identifier generated the same way as `offer_public_id`, that identifier is the only form ever sent to a client or embedded in a callback payload, and possessing it grants nothing on its own. Every endpoint and callback authorizes by the caller's authenticated identity, never by knowledge of an identifier. | Confirmed |

## Draft Product Scenario

> Status: the behavior described here is confirmed and matches the Decision Log. The prose is a walkthrough, not a separate source of truth; where it and the Decision Log ever diverge, the Decision Log governs. Persian prose in this section still says `آفر` in places for historical reasons, which is harmless because it is internal documentation, while every string a user actually sees says `لفظ`.

### مثال پایه

علی یک آفر ۴۰ عددی ثبت می‌کند. زمان اصلی آفر طبق تنظیم مدیر ۲ دقیقه است و علی برای آفر خود ۱۰ دقیقه «وقت اضافه» در نظر گرفته است.

بنابراین:

- از لحظه ثبت تا پایان دقیقه ۲، آفر در **زمان اصلی** است.
- از پایان دقیقه ۲ تا پایان دقیقه ۱۲، آفر در **وقت اضافه** است.
- پس از دقیقه ۱۲، آفر کاملاً منقضی می‌شود.

### 1. ثبت آفر

- اگر وقت اضافه صفر یا غیرفعال باشد، آفر دقیقاً مانند رفتار فعلی پس از زمان اصلی منقضی می‌شود.
- هر کاربر یک مقدار پیش‌فرض مشترک و همگام‌شده بین بات و وب‌اپ برای وقت اضافه آفرهای متعلق به خودش دارد.
- مقدار پیش‌فرض مالک اقتصادی آفر روی تمام آفرهای جدید متعلق به او، مستقل از محل یا شخص ثبت‌کننده، اعمال می‌شود.
- در نسخه اولیه امکان تغییر وقت اضافه به‌صورت جداگانه هنگام ثبت هر آفر وجود ندارد.
- مقدار وقت اضافه فقط می‌تواند یک عدد صحیح از صفر تا ۱۰ دقیقه باشد و سقف آن از تنظیمات مدیر کنترل نمی‌شود.
- مقدار اولیه برای تمام کاربران فعلی و جدید صفر است؛ بنابراین قابلیت برای هر کاربر به‌صورت انتخابی فعال می‌شود.
- در وب‌اپ، تنظیم در صفحه تنظیمات کاربر و بخش بازار با کنترل منفی/مثبت، گام یک دقیقه و دکمه ذخیره صریح نمایش داده می‌شود.
- در بات، دکمه `⏳ وقت اضافه` در همان کیبورد پنل کاربر موجود اضافه می‌شود و هیچ آیتم تازه‌ای به منوی اصلی افزوده نمی‌شود. برچسب دکمه همان برچسب کوتاه تأییدشده فیچر است و با ریتم دکمه‌های همسایه پنل کاربر هم‌خوان می‌ماند.
- تمام متن‌های کاربرپسند این فیچر واژه «لفظ» را به‌کار می‌برند تا با بقیه محصول یکدست بمانند؛ واژه «آفر» در هیچ متن کاربرپسندی ظاهر نمی‌شود.
- کنترل بات ورودی تایپی با تأیید صریح است، نه کنترل منفی/مثبت. کاربر عددی بین صفر تا ده می‌فرستد، بات آن را اعتبارسنجی می‌کند و مقدار را با دکمه‌های تأیید و انصراف می‌پرسد، و ذخیره فقط پس از تأیید و پس از ثبت قطعی در ایران انجام می‌شود. این همان الگوی موجود تنظیم محدودیت‌های کاربر است و استپر منفی/مثبت به بات اضافه نمی‌شود.
- غیرفعال‌کردن در بات با فرستادن مقدار `۰` انجام می‌شود و دکمه مستقل غیرفعال‌سازی ندارد.
- ورودی خارج از بازه یا غیرعددی با یک متن اعتبارسنجی رد می‌شود و مقدار ذخیره‌شده را تغییر نمی‌دهد.
- مقدار صفر در هر دو سطح با عنوان `غیرفعال` نمایش داده می‌شود.
- این تنظیم فقط برای حساب‌هایی نمایش داده می‌شود که مجاز به ثبت لفظ متعلق به خودشان هستند. یعنی کاربران عادی و مشتریان سطح ۱. حسابدار آن را هرگز نمی‌بیند چون اصلاً به بازار دسترسی ندارد، و مشتری سطح ۲ هم نمی‌بیند چون اجازه ثبت لفظ ندارد و فقط می‌تواند روی لفظ دیگران درخواست بدهد. مشتری سطح ۲ به بات هم دسترسی ندارد، پس کنترل باتی برای او در هیچ حالتی ظاهر نمی‌شود.
- مشتری سطح ۲ می‌تواند **درخواست‌دهنده** وقت اضافه باشد. قیمت او با کمیسیون تعدیل می‌شود و این محاسبه همان مسیر فعلی معامله است؛ تأیید وقت اضافه چیزی در آن عوض نمی‌کند.
- مقدار وقت اضافه انتخابی کاربر هنگام ثبت آفر روی همان آفر تثبیت می‌شود؛ تغییر بعدی تنظیم کاربر فقط روی آفرهای جدید اثر می‌گذارد.
- زمان عادی آفر رفتار پویای فعلی پروژه را حفظ می‌کند: آخرین مقدار تنظیم‌شده توسط مدیر روی تمام آفرهای هنوز فعال اعمال می‌شود و زمان ورود به وقت اضافه و پایان نهایی آفر را به همان میزان جابه‌جا می‌کند.
- تغییر زمان عادی توسط مدیر به آفر منقضی‌شده حیات دوباره نمی‌دهد.
- برای جلوگیری از تغییر مرحله آفر فعال، این تنظیم کم‌تغییر فقط در زمان بسته‌بودن بازار و نبود آفر فعال ویرایش می‌شود. این مورد یک رویه عملیاتی است و در نسخه اولیه محدودیت فنی جدیدی برای پنل مدیر ایجاد نمی‌کند.

### 2. درخواست معامله در زمان اصلی

- درخواست‌دهنده مراحل تأیید فعلی خودش را انجام می‌دهد.
- اگر آفر فعال، بازار باز، مقدار یا لات موجود و معامله از نظر بلاک و محدودیت‌های فعلی مجاز باشد، معامله بلافاصله و بدون گرفتن تأیید از لفظ‌دهنده انجام می‌شود.
- اگر هرکدام از قوانین فعلی برقرار نباشد، درخواست رد می‌شود و معامله‌ای ساخته نمی‌شود.
- اگر معامله کل آفر را مصرف کند، آفر تکمیل می‌شود و وارد وقت اضافه نخواهد شد.
- اگر معامله جزئی باشد، فقط مقدار و لات‌های باقی‌مانده در ادامه آفر باقی می‌مانند.

### 3. ورود به وقت اضافه

- اگر در پایان زمان اصلی مقداری از آفر باقی مانده باشد، آفر وارد وقت اضافه می‌شود.
- آفر همچنان در بازار و کانال قابل مشاهده است.
- در کانال تلگرام فقط نشان `⏳` به متن پست اضافه می‌شود و متن توضیحی یا شمارش معکوس جدیدی روی پست قرار نمی‌گیرد.
- در وب‌اپ، نوار عمر عادی آفر در پایان زمان اصلی کامل می‌شود و هنگام ورود به وقت اضافه، یک نوار سبز تازه از ابتدا شروع به حرکت می‌کند.
- در گوشه کارت آفر وب‌اپ، نشان متحرک `⏳` نمایش داده می‌شود.
- متن یا برچسب جداگانه «وقت اضافه» روی کارت وب‌اپ نمایش داده نمی‌شود.
- حرکت نشان شامل چرخش عمودی و بالا‌به‌پایین، قرارگرفتن در وضعیت اصلی و یک لرزش کوتاه افقی است.
- اندازه نشان باید با عنصر مجاور خودش سنجیده شود، نه با متن اصلی کارت. متن اصلی کارت `۱۳px` و توضیحات `۱۱.۵px` است، اما زمان نسبی که ساعت‌شنی کنارش می‌نشیند `۱۳px` نیست و `۱۰px` است. بنابراین اندازه نشان `۱۲px` داخل کادر ثابت `۱۴×۱۴px` تعیین می‌شود تا از متن مجاور بزرگ‌تر و از بج‌های هدر ناهمخوان نشود. مقدار `۱۸px` در کادر `۲۲×۲۲px` که پیش‌تر پیشنهاد شده بود رد می‌شود، چون تقریباً دو برابر متن مجاور و بزرگ‌ترین عنصر هدر می‌شد.
- انیمیشن باید فقط با تبدیل‌های بصری سبک اجرا شود و در حالت کاهش حرکت سیستم‌عامل به شکل ثابت نمایش داده شود. این الگو روی کارت آفر امروز وجود ندارد: انیمیشن `ring-pulse` روی حالت بحرانی تایمر هیچ گارد کاهش حرکتی ندارد. پس افزودن گارد کاهش حرکت برای نشان جدید و اصلاح همین انیمیشن موجود، هر دو کار تازه‌اند.
- اگر حداقل یک درخواست وقت اضافه به معامله قطعی تبدیل شود، پس از نهایی‌شدن آفر نشان `⏳` به‌صورت ثابت و بدون انیمیشن در کارت تاریخچه باقی می‌ماند.
- اگر آفر در وقت اضافه هیچ معامله قطعی نداشته باشد، هنگام پایان یا انقضای آفر نشان `⏳` از نمایش تاریخی حذف می‌شود.
- اگر معامله وقت اضافه جزئی باشد و آفر همچنان فعال بماند، نشان تا زمان فعال‌بودن وقت اضافه متحرک باقی می‌ماند و پس از ورود آفر به تاریخچه ثابت می‌شود.
- ملاک ثبت نشان تاریخی، مرحله درخواست در زمان ایجاد آن است؛ درخواست ثبت‌شده در وقت اضافه که در مهلت معتبر خود تأیید می‌شود، معامله وقت اضافه محسوب می‌شود.
- در نسخه نهایی پست کانال، نشان `⏳` جایگزین استیکرها و علامت‌های فعلی انجام معامله نمی‌شود و در کنار آن‌ها باقی می‌ماند.
- اگر معامله‌ای در وقت اضافه انجام نشود، فقط `⏳` از نسخه نهایی پست حذف می‌شود و سایر علامت‌های فعلی بدون تغییر می‌مانند.
- زمان نسبی آفر در سطر هدر کارت و در سمت مقابل بج‌ها نمایش داده می‌شود. چون رابط کاربری RTL است و هدر با `justify-content: space-between` چیده شده، بج‌ها در سمت شروع (راست) و زمان نسبی در سمت پایان (چپ صفحه) می‌نشیند. ساعت‌شنی نباید به‌صورت لایه‌ای روی زمان نسبی قرار گیرد.
- زمان نسبی و ساعت‌شنی در یک گروه ثابت در همان سمت پایان هدر قرار می‌گیرند؛ ساعت‌شنی در بیرونی‌ترین لبه پایان و زمان در کنار آن. برای پرهیز از اشتباه پیاده‌سازی در چیدمان RTL، مرجع «سمت پایان جریان متن» است، نه واژه چپ یا راست.
- این گروه باید فضای ثابت داشته باشد تا شروع یا توقف انیمیشن، متن، نشان‌های خرید و فروش یا تاریخچه را جابه‌جا نکند.

### 4. درخواست معامله در وقت اضافه

- ابتدا قوانین واضح و فوری مانند فعال بودن بازار، فعال بودن حساب‌ها، بلاک نبودن طرفین و معتبر بودن مقدار یا لات بررسی می‌شوند.
- اگر درخواست از این بررسی‌ها عبور کند، به‌جای ساخت معامله، یک درخواست پایدار با وضعیت «منتظر تأیید لفظ‌دهنده» ثبت می‌شود یا در صف همان مالک قرار می‌گیرد.
- مهلت تصمیم ۳۰ ثانیه‌ای فقط هنگام قابل‌نمایش‌شدن درخواست برای لفظ‌دهنده آغاز می‌شود: در وب‌اپ با ارتقای پایدار روی سرور، و در بات پس از پذیرش موفق پیام توسط تلگرام و ثبت شناسه آن.
- درخواست‌دهنده فقط یک بازخورد لحظه‌ای می‌گیرد که درخواست ارسال شده اما معامله هنوز انجام نشده است.
- لفظ‌دهنده جزئیات دقیق آفر و زمان باقی‌مانده برای تصمیم را می‌بیند، اما هویت درخواست‌دهنده تا پیش از قطعی‌شدن معامله نمایش داده نمی‌شود.
- اگر آفر لات‌بندی شده باشد، مقدار یا لات درخواستی نیز به‌صورت جداگانه نمایش داده می‌شود.

### 5. تصمیم لفظ‌دهنده

مرجع تصمیم فقط مالک اقتصادی خود لفظ است، یعنی همان `user_id` لفظ.

در عمل اینجا ابهامی وجود ندارد، چون ساختار فعلی پروژه اجازه‌ی ثبت نیابتی در بازار را نمی‌دهد. حسابدار در هیچ سطحی به بازار دسترسی ندارد: نه در API آفر و معامله، نه در بات که میدل‌ور اصلاً او را رد می‌کند، و نه در فرانت‌اند که مسیر و تب بازار برایش بسته است. سرگروه هم روی بازار مشتری خودش کاری انجام نمی‌دهد و فقط در زنجیره معامله واسط است، مبنای بررسی بلاک است، و برای مشتری سطح ۲ دریافت‌کننده کمیسیون. مشتری هم لفظ خودش را با شناسه خودش ثبت می‌کند.

نتیجه اینکه روی هر لفظ واقعی بازار، ثبت‌کننده و مالک همیشه یک نفرند و تنها جایی که این دو ستون فرق می‌کنند همانندسازی بین دو سرور است.

#### تأیید

- سیستم هنگام تأیید، تمام قوانین حساس را دوباره بررسی می‌کند.
- اگر آفر و مقدار درخواستی همچنان معتبر باشند، معامله قطعی می‌شود و پیام‌های معمول معامله برای طرفین ارسال می‌شوند.
- اگر شرایط عوض شده باشد، مانند مصرف شدن لات، پایان بازار، بلاک شدن یکی از طرفین یا اتمام آفر، تأیید به معامله تبدیل نمی‌شود؛ نتیجه همان اقدام به لفظ‌دهنده نمایش داده می‌شود اما برای درخواست‌دهنده پیام رد جداگانه ارسال نمی‌شود.
- برای مشتری تحت مدیریت، تأیید مشتری فقط مجوز ادامه معامله است و زنجیره معاملاتی فعلی پروژه را تغییر نمی‌دهد؛ سرگروه همچنان طبق قرارداد فعلی پروژه واسط معامله باقی می‌ماند.
- در بات، پیام تأیید لفظ‌دهنده پس از موفقیت به `معامله انجام شد.` ویرایش می‌شود و دکمه‌های آن حذف می‌شوند.

#### رد

- درخواست بدون ساخت معامله بسته می‌شود.
- آفر تا پایان وقت اضافه یا تا زمانی که مقدار باقی دارد فعال می‌ماند.
- برای درخواست‌دهنده پیام رد جداگانه‌ای ارسال نمی‌شود.
- در بات، پیام تأیید لفظ‌دهنده به `درخواست رد شد.` ویرایش می‌شود و دکمه‌های آن حذف می‌شوند؛ در وب‌اپ پنجره درخواست بسته می‌شود.

#### عدم پاسخ

- اگر لفظ‌دهنده طی ۳۰ ثانیه پاسخ ندهد، درخواست خودکار بسته می‌شود و معامله‌ای ساخته نمی‌شود.
- عدم پاسخ نباید به‌عنوان تأیید ضمنی در نظر گرفته شود.
- برای درخواست‌دهنده پیام انقضای جداگانه‌ای ارسال نمی‌شود.
- در بات، پیام تأیید لفظ‌دهنده به `درخواست بسته شد.` ویرایش و دکمه‌هایش حذف می‌شوند؛ در وب‌اپ پنجره درخواست بسته می‌شود.

### 6. درخواست‌های هم‌زمان و تکراری

- کلیک یا ارسال دوباره یک درخواست نباید دو معامله یا دو درخواست مستقل ناخواسته بسازد.
- هر آفر، فارغ از یکجا یا لات‌بندی بودن و مقدار باقی‌مانده، فقط می‌تواند یک درخواست فعال در وقت اضافه داشته باشد.
- با ثبت اولین درخواست، کل آفر تا تعیین تکلیف همان درخواست برای پذیرش درخواست جدید قفل منطقی می‌شود؛ موجودی واقعی آفر هنوز تغییر نمی‌کند.
- هر درخواست‌دهنده در هر لحظه حداکثر سه درخواست باز روی آفرهای متفاوت دارد. درخواست چهارم پذیرفته نمی‌شود تا یکی از سه درخواست قبلی تعیین تکلیف شود.
- هر درخواست‌دهنده هم‌زمان فقط یک درخواست باز روی آفرهای یک مالک اقتصادی دارد. این قید چیزی از او نمی‌گیرد، چون صف مالک به‌هرحال یکی‌یکی نمایش می‌دهد و درخواست دوم فقط آفرهای دیگر همان مالک را برای سایر خریداران رزرو و مسدود می‌کرد.
- نتیجه این دو قید با هم: سه درخواست یک نفر الزاماً روی سه مالک متفاوت است، پس در سه صف مستقل قرار می‌گیرند، هم‌زمان نمایش داده می‌شوند و هر آفر فقط مهلت سی‌ثانیه‌ای خودش را قفل می‌ماند، نه انتظار پشت درخواست‌های دیگر همان شخص.
- درخواست صف‌شده هم در هر دو قید شمرده می‌شود، چون از لحظه ثبت قفل منطقی آفر خودش را گرفته است، حتی اگر مهلت ۳۰ ثانیه‌ای‌اش هنوز شروع نشده باشد.
- هیچ‌کدام از این دو قید به تفکیک سرور مرجع تقسیم نمی‌شود. شمارش را همان سرور مرجع آفر به‌صورت اتمی از روی لجر محلی خودش انجام می‌دهد که رونوشت سینک‌شده درخواست‌های سرور دیگر را هم دارد؛ تأخیر سینک یعنی دقت تقریبی است و یک واحد تخطی گذرا پذیرفته شده، چون این قیدها کنترل سوءاستفاده‌اند نه ناوردای درستی.
- هر دو قید با هر پایان قطعی آزاد می‌شوند: تأیید، رد، انقضا، لغو درخواست‌دهنده و ابطال سخت.
- هر مالک اقتصادی روی هر سرور مرجع آفر در هر لحظه فقط یک درخواست قابل تصمیم و نمایش‌داده‌شده دارد.
- درخواست معتبر روی آفر دیگری از همان مالک، فقط اگر `offer_home_server` آن با درخواست جاری یکسان باشد، تا تعیین تکلیف یا پایان مهلت درخواست جاری در صف همان سرور مرجع باقی می‌ماند.
- آفرهای یک مالک با `offer_home_server` متفاوت صف‌های مستقل دارند: درخواست آفر وب‌اپی فقط در وب‌اپ صف و نمایش داده می‌شود و درخواست آفر باتی فقط در بات صف و نمایش داده می‌شود؛ این دو می‌توانند هم‌زمان قابل تصمیم باشند.
- درخواست دوم روی همان آفر، مستقل از لات یا مقدار درخواستی، وارد صف مالک نمی‌شود، برای مالک نمایش داده نمی‌شود و همان بازخورد تلاش مجدد را دریافت می‌کند؛ حتی اگر درخواست اول بعداً رد یا منقضی شود.
- ترتیب ارتقای درخواست‌های معتبر صف بر اساس ترتیب ثبت آن‌ها است.
- مهلت ۳۰ ثانیه‌ای درخواست صف‌شده هنگام ثبت در صف شروع نمی‌شود؛ این مهلت فقط هنگام ارتقا و قابل‌نمایش‌شدن درخواست برای مالک آغاز می‌شود.
- برای وب‌اپ، قابل‌نمایش‌شدن همان ارتقای پایدار درخواست روی سرور است و به بازبودن مرورگر یا مشاهده واقعی مالک وابسته نیست.
- برای بات، قابل‌نمایش‌شدن فقط پس از پذیرش موفق ارسال توسط تلگرام و ثبت شناسه پیام رخ می‌دهد؛ صرف ورود به صف ارسال، مهلت تصمیم را آغاز نمی‌کند.
- اگر پیام بات تا پایان بازه معتبر آفر تحویل نشود، درخواست بدون پیام نتیجه بسته می‌شود و صف مالک برای درخواست بعدی آزاد می‌گردد.
- پیش از ارتقای هر درخواست صف‌شده، فعال‌بودن بازار و آفر، باقی‌ماندن وقت اضافه، موجودبودن مقدار یا لات و تمام قوانین حساب و معامله دوباره بررسی می‌شوند.
- اگر آفر در زمان انتظار منقضی، تکمیل، لغو یا نامعتبر شده باشد، درخواست بدون نمایش به مالک بسته می‌شود.
- اگر نتیجه درخواست قبلی باعث شود مقدار، لات یا یکی از قوانین لازم برای درخواست بعدی دیگر معتبر نباشد، درخواست بعدی بدون نمایش بسته می‌شود.
- درخواست‌دهنده وب‌اپ در مدت انتظار متن `در حال ارسال درخواست...` را می‌بیند؛ پس از ارتقای درخواست، وضعیت به متن انتظار تصمیم و شمارش معکوس واقعی تغییر می‌کند.
- درخواست‌دهنده بات برای درخواست معتبرِ صف‌شده فقط این بازخورد را دریافت می‌کند: `⏳ درخواست معامله ثبت شد و در صف بررسی است.` این متن برای درخواست دوم روی همان آفر نیست؛ آن درخواست وارد صف نمی‌شود و بازخورد تلاش مجدد می‌گیرد.
- هنگام ارتقای همان درخواست صف‌شده، پیام وضعیت بات درخواست‌دهنده بدون ارسال پیام جدید به `⏳ درخواست در حال بررسی است.` ویرایش می‌شود و دکمه لغو تا پیش از تأیید موفق مالک باقی می‌ماند. در وب‌اپ در همین نقطه شمارشگر واقعی ۳۰ ثانیه‌ای نمایش داده می‌شود.
- تأیید، رد، انقضای ۳۰ ثانیه‌ای یا ابطال سخت درخواست، قفل منطقی آفر را آزاد می‌کند.
- محدودیت هر آفر و صف تک‌نمایشی هر سرور مرجع باید اتمی باشند تا درخواست‌های هم‌زمان در همان مرجع نتوانند دو درخواست قابل تصمیم بسازند.
- برای کاهش پیچیدگی و بار انتشار، وضعیت موقت صف مالک روی پست کانال یا کارت عمومی آفر منتشر نمی‌شود؛ نگهداری، اعتبارسنجی و ارتقای درخواست صف‌شده فقط در وضعیت پایدار سمت سرور انجام می‌شود.
- پس از رد یا انقضای درخواست، همان درخواست‌دهنده تا ۳۰ ثانیه نمی‌تواند دوباره روی همان آفر درخواست ایجاد کند.
- دوره انتظار فقط برای همان ترکیب کاربر و آفر است؛ سایر کاربران بلافاصله پس از آزادشدن آفر می‌توانند درخواست بدهند.
- پس از معامله موفق، دوره انتظار اعمال نمی‌شود.
- کلیک یا ارسال تکراری همان درخواست فعال باید به همان نتیجه قبلی برگردد و درخواست یا دوره انتظار تازه‌ای نسازد.
- درخواست‌دهنده تا پیش از تأیید موفق مالک می‌تواند درخواست خودش را لغو کند. لغو هیچ معامله یا پیام جداگانه‌ای برای مالک نمی‌سازد، پنجره یا پیام تأیید مالک را می‌بندد و درخواست بعدی صف را بررسی می‌کند.
- در بات، پیام وضعیت درخواست دکمه `لغو درخواست` دارد؛ پس از لغو موفق، همان پیام به `درخواست لغو شد.` تغییر می‌کند و دکمه حذف می‌شود. در وب‌اپ نیز دکمه لغو تا تعیین تکلیف نمایش داده می‌شود و سپس وضعیت بسته و همین تأیید کوتاه نمایش داده می‌شود.
- در بات هیچ پیام وقت اضافه حذف نمی‌شود تا لنگر کیبورد آسیب نبیند. پس از تأیید موفق، پیام وضعیت درخواست‌دهنده به `معامله انجام شد.`؛ پس از رد، انقضا یا ابطال به `درخواست انجام نشد.`؛ و پس از لغو به `درخواست لغو شد.` ویرایش می‌شود. در همه حالت‌های نهایی دکمه‌های شیشه‌ای حذف می‌شوند و پیام‌های معاملاتی استاندارد فقط در معامله موفق ارسال می‌شوند.
- پیام تأیید لفظ‌دهنده نیز حذف نمی‌شود: پس از تأیید به `معامله انجام شد.`، پس از رد به `درخواست رد شد.`، و پس از انقضا، لغو درخواست‌دهنده یا ابطال به `درخواست بسته شد.` ویرایش می‌شود. در همه حالت‌ها دکمه‌های شیشه‌ای حذف می‌شوند.

### 7. پایان یا توقف آفر در وقت اضافه

- آفر در تمام مدت وقت اضافه از نظر سهمیه و قوانین پروژه یک آفر فعال محسوب می‌شود.
- آفر وقت اضافه در سقف تعداد آفرهای فعال مالک محاسبه می‌شود و امکان دورزدن سهمیه با ورود به وقت اضافه وجود ندارد.
- آفر تا پایان نهایی وقت اضافه، تکمیل یا انقضای دستی وارد تاریخچه منقضی‌ها یا فهرست آفرهای قابل تکرار نمی‌شود.
- تکمیل کامل آفر: تمام درخواست‌های منتظر ناسازگار بسته می‌شوند.
- لغو یا انقضای دستی توسط لفظ‌دهنده یا مدیر: آفر و درخواست‌های منتظر بسته می‌شوند.
- پایان وقت اضافه: آفر دیگر درخواست جدید نمی‌پذیرد، اما درخواست معتبر ثبت‌شده پیش از پایان وقت اضافه، مهلت کامل ۳۰ ثانیه‌ای خود را حفظ می‌کند.
- درخواست باقی‌مانده پس از پایان وقت اضافه فقط می‌تواند همان معامله مشخص را قطعی کند و امکان ایجاد درخواست جدید وجود ندارد.
- در این بازه حداکثر ۳۰ ثانیه‌ای، درخواست تأیید برای لفظ‌دهنده و وضعیت انتظار برای درخواست‌دهنده باقی می‌ماند؛ آفر برای سایر کاربران فقط قابل مشاهده و غیرقابل‌تعامل است.
- در وب‌اپ کنترل‌های معامله آفر غیرفعال می‌شوند و در کانال تلگرام کنترل‌های تعاملی آفر حذف یا بی‌اثر می‌شوند؛ این محافظت در سرور نیز مستقل از رابط کاربری اعمال می‌شود.
- در همین بازه، نوار سبز وقت اضافه در وب‌اپ کامل شده و ساعت‌شنی ثابت است؛ نشان `⏳` کانال نیز باقی می‌ماند. اگر درخواست به معامله قطعی نرسد، پس از پایان واقعی آفر نشان‌ها حذف می‌شوند؛ اگر معامله قطعی شود، نشان تاریخی ثابت طبق قانون قبلی باقی می‌ماند.
- اگر درخواست باقی‌مانده تأیید شود و معامله فقط بخشی از آفر را مصرف کند، باقی‌مانده آفر فوراً منقضی می‌شود؛ اگر رد یا منقضی شود نیز آفر فوراً به وضعیت نهایی می‌رود.
- بسته شدن بازار: وقت اضافه بر بسته بودن بازار اولویت ندارد؛ آفر و درخواست‌های منتظر مطابق قانون فعلی پایان بازار بسته می‌شوند.
- بلاک یا غیرفعال شدن یکی از طرفین: هیچ تأیید بعدی نباید معامله ممنوع را قطعی کند.
- رخدادهای نهایی بالا درخواست را فوراً باطل می‌کنند و لازم نیست سیستم تا پایان عمر طبیعی ۳۰ ثانیه‌ای آن منتظر بماند.
- پس از پایان واقعی آفر، رفتار فعلی تاریخچه و تکرار بر اساس مقدار و لات‌های باقی‌مانده اجرا می‌شود.
- آفر تکرارشده یک آفر مستقل است و نشان تاریخی یا وضعیت وقت اضافه آفر منبع را به ارث نمی‌برد.
- آفر تکرارشده در لحظه ایجاد، مقدار فعلی و ذخیره‌شده وقت اضافه مالک اقتصادی را snapshot می‌کند؛ مقدار وقت اضافه آفر منبع به آن منتقل نمی‌شود.

### 8. رفتار بین وب‌اپ، بات و دو سرور

- وب‌اپ و بات فقط محل نمایش و ارسال فرمان هستند؛ سرور مرجع آفر تنها مرجع قطعی تصمیم و ساخت معامله باقی می‌ماند.
- درخواست یا تأییدی که از سرور دیگر می‌آید باید با شناسه یکتا به سرور مرجع ارسال شود.
- درخواست پس از رسیدن به سرور مرجع آفر وارد صف همان سرور می‌شود؛ هیچ صف سراسری مستقلی بر مبنای سرور مرجع کاربر وجود ندارد.
- محل دریافت تأیید فقط از مرجع ثبت آفر پیروی می‌کند:
  - آفر با مرجع وب‌اپ: درخواست تأیید فقط در وب‌اپ مالک آفر نمایش داده می‌شود.
  - آفر با مرجع بات: درخواست تأیید فقط توسط بات برای مالک آفر ارسال می‌شود.
- در وب‌اپ، درخواست باید مستقل از صفحه فعلی کاربر و همراه شمارش معکوس همگام با سرور نمایش داده شود.
- در بات، درخواست باید شامل متن خود آفر، مهلت ۳۰ ثانیه‌ای و دکمه‌های تأیید و رد باشد.
- آنلاین‌بودن مالک شرط ثبت درخواست نیست، زیرا وضعیت حضور کاربر مرجع قابل‌اعتماد برای تصمیم معاملاتی نیست.
- اگر مالک آفر وب‌اپی در طول همان ۳۰ ثانیه وارد یا دوباره متصل شود، درخواست با زمان واقعی باقی‌مانده از سرور نمایش داده می‌شود.
- برای آفر بات، مهلت تصمیم فقط پس از ثبت موفق پیام تلگرام آغاز می‌شود؛ تا پیش از آن ارسال طبق صف تلاش می‌شود، بدون مسیر جایگزین وب‌اپ.
- اگر پیام بات تا پایان اعتبار آفر تحویل نشود، درخواست بی‌صدا بسته و درخواست بعدی صف مالک بررسی می‌شود.
- اگر مالک درخواست را نبیند یا پیام به او نرسد، درخواست بدون تأیید در پایان مهلت منقضی می‌شود و برای درخواست‌دهنده پیام نتیجه ارسال نمی‌شود.
- قطع ارتباط نباید باعث نمایش موفقیت کاذب یا ایجاد معامله تکراری شود.
- پس از اتصال مجدد، نتیجه قطعی باید از سرور مرجع بازیابی و در هر دو محیط همگام شود.
- سطح دیگر نباید به‌عنوان مسیر جایگزین برای تأیید استفاده شود.

### 9. مرز دقیق زمان

- درخواست باید بر اساس زمان معتبر دریافت در لبه سیستم دسته‌بندی شود، نه صرفاً زمان دیرتر پردازش روی سرور مرجع.
- درخواست رسیده پیش از پایان زمان اصلی، در مسیر معامله خودکار قرار می‌گیرد.
- درخواست رسیده دقیقاً در لحظه پایان زمان اصلی رد می‌شود.
- درخواست رسیده بعد از پایان زمان اصلی و در بازه معتبر وقت اضافه، در مسیر تأیید لفظ‌دهنده قرار می‌گیرد.
- درخواست رسیده دقیقاً در لحظه پایان وقت اضافه یا بعد از آن رد می‌شود.

### 10. نتیجه قابل مشاهده

- درخواست‌دهنده هنگام ثبت، پیام وضعیت موجود را می‌بیند؛ فقط در صورت انجام معامله، پیام معاملاتی استاندارد دریافت می‌کند.
- رد یا انقضای درخواست برای درخواست‌دهنده پیام جداگانه ایجاد نمی‌کند؛ همان پیام وضعیت بات مطابق تصمیم ثبت‌شده ویرایش می‌شود و در وب‌اپ وضعیت بسته می‌شود.
- لفظ‌دهنده درخواست‌های منتظر را در محل قابل دسترس می‌بیند و می‌تواند فقط یک‌بار درباره هر درخواست تصمیم بگیرد.
- مدیر امکان مشاهده مسیر کامل درخواست، تصمیم، زمان‌ها و علت رد را برای پشتیبانی و حسابرسی خواهد داشت.

## User-Facing Message Approval Gate

Every message this feature sends to a user, and every text it shows a user, requires explicit product-owner approval of its exact wording before the stage that ships it may be implemented. This covers bot messages and every later edit of them, WebApp prompts and status text, inline and reply button labels, callback answers, validation and error text, preference-save confirmations, the reachability warning, and any change to a channel post. No stage may introduce, reword, or silently reuse a user-facing string that is not in the inventory below with an approved status.

The inventory is the single index of record. If implementation discovers a state that needs a message not listed here, work stops on that path until the text is added and approved.

| No. | Surface | Moment | Exact text | Approval |
| --- | --- | --- | --- | --- |
| M1 | Bot | User-panel entry button | `⏳ وقت اضافه` | Confirmed |
| M2 | Bot | Prompt asking for the value, shown with the current value | `وقت اضافه لفظ‌های جدید شما: {مقدار فعلی}` and `عددی بین ۰ تا ۱۰ دقیقه بفرستید. صفر یعنی غیرفعال.` | Confirmed |
| M2b | Bot | Confirmation question after a valid nonzero value | `وقت اضافه روی {تعداد} دقیقه تنظیم شود؟` | Confirmed |
| M2b-zero | Bot | Confirmation question when the typed value is zero, kept separate so the sentence reads naturally | `وقت اضافه غیرفعال شود؟` | Confirmed |
| M2c | Bot | Confirm and cancel buttons on that question | `✅ تایید` and `❌ انصراف` | Confirmed |
| M3 | Both | Zero value shown as | `غیرفعال` | Confirmed |
| M4 | Both | Save succeeded, nonzero | `✅ وقت اضافه لفظ‌های جدید شما روی {تعداد} دقیقه تنظیم شد.` | Confirmed |
| M5 | Both | Save succeeded, zero | `✅ وقت اضافه برای لفظ‌های جدید شما غیرفعال شد.` | Confirmed |
| M6 | Both | Warning when enabling a nonzero value | `تأیید هر لفظ فقط در همان محل ثبت لفظ نمایش داده می‌شود: لفظ وب در وب‌اپ و لفظ بات در بات.` | Confirmed |
| M7 | Bot | Bot save rejected because Iran is unreachable. Deliberately says nothing about servers, since the two-server topology is not something a user needs to learn. | `تنظیم شما ذخیره نشد. لطفاً کمی بعد دوباره تلاش کنید.` | Confirmed |
| M8 | Both | Value outside the 0–10 range, or non-numeric input in the bot | `لطفاً فقط یک عدد بین ۰ تا ۱۰ بفرستید.` | Confirmed |
| M9 | WebApp | Settings and market section label for the stepper | `وقت اضافه` with helper text `پس از پایان زمان لفظ، تا این مدت درخواست معامله با تأیید شما پذیرفته می‌شود.` | Confirmed |
| M10 | Bot | Requester status, request queued | `⏳ درخواست معامله ثبت شد و در صف بررسی است.` | Confirmed |
| M11 | Bot | Requester status edited at promotion | `⏳ درخواست در حال بررسی است.` | Confirmed |
| M12 | Both | Requester cancellation button | `لغو درخواست` | Confirmed |
| M13 | Bot | Requester status after successful approval | `معامله انجام شد.` | Confirmed |
| M14 | Bot | Requester status after rejection, timeout, or invalidation | `درخواست انجام نشد.` | Confirmed |
| M15 | Both | Requester status after own cancellation | `درخواست لغو شد.` | Confirmed |
| M16 | Both | Second request on a lafz already under review | `درخواست دیگری برای این لفظ در حال بررسی است؛ لطفاً {زمان باقی‌مانده} ثانیه دیگر دوباره تلاش کنید.` | Confirmed |
| M17 | Both | Same requester still in cooldown on that lafz | `برای ارسال مجدد درخواست روی این لفظ، لطفاً {زمان باقی‌مانده} ثانیه دیگر تلاش کنید.` | Confirmed |
| M18 | Both | Cross-server delivery outcome uncertain | `⏳ در حال بررسی درخواست...` | Confirmed |
| M19 | Both | Definite pre-send failure, retry advised | `درخواست ارسال نشد. لطفاً دوباره تلاش کنید.` | Confirmed |
| M20 | Both | Requester already holds three outstanding requests and attempts a fourth | `شما هم‌زمان ۳ درخواست باز دارید. لطفاً تا تعیین تکلیف یکی از آن‌ها صبر کنید.` | Confirmed |
| M20b | Both | Requester already holds a request against another offer of this same economic owner. Worded vaguely on purpose: it does not state the same-owner reason, so it never hints at who owns which offer. The cost is that the requester cannot distinguish it from other temporary blocks, which was accepted. | `فعلاً نمی‌توانید روی این لفظ درخواست بدهید. لطفاً کمی بعد دوباره تلاش کنید.` | Confirmed |
| M21 | WebApp | Requester queued state before promotion | `در حال ارسال درخواست...` | Confirmed |
| M22 | WebApp | Requester countdown display | `۰۰:۳۰` counting to `۰۰:۰۰` | Confirmed |
| M23 | Bot | Owner approval message title | `⏳ **درخواست معامله در وقت اضافه**` | Confirmed |
| M24 | Bot | Owner approval message lead line | `درخواست معامله برای لفظ شما:` | Confirmed |
| M25 | Bot | Owner approval deadline line | `⏱ مهلت پاسخ: ۳۰ ثانیه` | Confirmed |
| M26 | Bot | Owner approval closing line | `در صورت تأیید، معامله پس از بررسی نهایی ثبت می‌شود.` | Confirmed |
| M27 | Both | Requested quantity line, lot-based offers only | `📦 مقدار درخواستی: {تعداد} عدد` | Confirmed |
| M28 | Bot | Owner approval buttons | `✅ تأیید معامله` and `❌ رد درخواست` | Confirmed |
| M29 | Bot | Owner message after own approval | `معامله انجام شد.` | Confirmed |
| M30 | Bot | Owner message after own rejection | `درخواست رد شد.` | Confirmed |
| M31 | Bot | Owner message after timeout, requester cancellation, or invalidation | `درخواست بسته شد.` | Confirmed |
| M32 | Bot | Callback answer when the owner clicks after the deadline | `مهلت پاسخ به این درخواست تمام شده است.` | Confirmed |
| M33 | Bot | Callback answer when the click is a duplicate or the request is already terminal | `این درخواست قبلاً تعیین تکلیف شده است.` | Confirmed |
| M34 | Bot | Callback answer when the clicker is not the economic owner. This is purely a defensive guard, not a role-collision case: no accountant or group leader can reach a market approval at all, so the only realistic triggers are a forwarded message, a stale callback from an earlier session, or a crafted payload. | `فقط صاحب این لفظ می‌تواند درباره این درخواست تصمیم بگیرد.` | Confirmed |
| M35 | WebApp | Owner prompt title | `درخواست معامله در وقت اضافه` | Confirmed |
| M36 | WebApp | Owner prompt buttons | `تأیید معامله` and `رد درخواست` | Confirmed |
| M37 | Both | Owner approved but revalidation failed, so no trade was created. Stays generic so it never leaks which condition changed or anything about the requester. | `شرایط این لفظ تغییر کرده و معامله انجام نشد.` | Confirmed |
| M38 | Channel | Overtime marker added to the public post, and removed or retained at terminal outcome | `⏳` | Confirmed |
| M39 | Both | Standard trade messages to both parties after a successful overtime trade | Existing project text, content contract unchanged | Confirmed to stay unchanged |

**Vocabulary rule.** Every user-facing string in this feature calls the object a `لفظ`, never an `آفر`. This matches every existing message in the product, such as `این لفظ دیگر فعال نیست.` and `شما حداکثر {تعداد} لفظ فعال دارید.`, and it avoids showing the user two different words for one thing on the same screen. `آفر` remains acceptable only in the internal prose of this document. The reachability warning was restated under this rule and reconfirmed, and the feature-name question it raised is closed: users only ever see `وقت اضافه`, so the internal full name never reaches a screen.

**Status.** Every entry has approved text. Any new state discovered during implementation stops that path until its text is added here and approved.

## Draft Exact Product Copy

> Status: behavior requirements are confirmed and every string reproduced below is approved in the inventory above. The inventory is the index of record; this section is the same text shown in context.

### بازخورد وضعیت به درخواست‌دهنده

درخواست صف‌شده در بات با پیام وضعیت زیر و دکمه `لغو درخواست` نمایش داده می‌شود:

> ⏳ درخواست معامله ثبت شد و در صف بررسی است.

هنگام ارتقای درخواست برای تصمیم لفظ‌دهنده، همان پیام به شکل زیر ویرایش می‌شود:

> ⏳ درخواست در حال بررسی است.

در وب‌اپ، وضعیت صف‌شده `در حال ارسال درخواست...` است و فقط پس از ارتقا، شمارشگر واقعی ۳۰ ثانیه‌ای نمایش داده می‌شود.

### بازخورد درخواست دوم روی آفر در حال بررسی

این درخواست ثبت یا برای لفظ‌دهنده ارسال نمی‌شود. فقط پاسخ لحظه‌ای زیر به کاربر دوم نمایش داده می‌شود:

> درخواست دیگری برای این لفظ در حال بررسی است؛ لطفاً `{زمان باقی‌مانده}` ثانیه دیگر دوباره تلاش کنید.

این یک متن **تازه** است و همان «بازخورد تلاش مجدد موجود» نیست. متن موجود پروژه برای رقابت روی یک آفر این است: `درخواست دیگری همزمان روی این لفظ در حال ثبت است. چند لحظه بعد دوباره تلاش کنید.` و در سطح API نیز `این لفظ توسط کاربر دیگری در حال معامله است. لطفاً مجدداً تلاش کنید.` هیچ‌کدام زمان باقی‌مانده را نشان نمی‌دهند. تصمیم ثبت‌شده این است که برای وقت اضافه متن تازه با زمان باقی‌مانده استفاده شود، چون کاربر باید بداند چه‌قدر صبر کند؛ بنابراین در متن تصمیم‌ها هرجا «بازخورد تلاش مجدد موجود» آمده، منظور همین متن تازه است و نه رشته‌های فعلی بات.

### بازخورد دوره انتظار همان درخواست‌دهنده

> برای ارسال مجدد درخواست روی این لفظ، لطفاً `{زمان باقی‌مانده}` ثانیه دیگر تلاش کنید.

### متن ذخیره تنظیم وقت اضافه

برای مقدار غیرصفر:

> ✅ وقت اضافه لفظ‌های جدید شما روی `{تعداد}` دقیقه تنظیم شد.

برای مقدار صفر:

> ✅ وقت اضافه برای لفظ‌های جدید شما غیرفعال شد.

### درخواست ارسالی در بات

برای آفر یکجا:

> ⏳ **درخواست معامله در وقت اضافه**
>
> درخواست معامله برای لفظ شما:
>
> `{متن کامل و استاندارد لفظ همراه توضیحات}`
>
> ⏱ مهلت پاسخ: `۳۰ ثانیه`
>
> در صورت تأیید، معامله پس از بررسی نهایی ثبت می‌شود.

برای آفر لات‌بندی‌شده، خط زیر نیز اضافه می‌شود:

> 📦 مقدار درخواستی: `{تعداد} عدد`

دکمه‌ها:

- `✅ تأیید معامله`
- `❌ رد درخواست`

### درخواست نمایش‌داده‌شده در وب‌اپ

- عنوان: `درخواست معامله در وقت اضافه`
- شمارشگر: `۰۰:۳۰` تا `۰۰:۰۰` بر اساس مهلت اعلام‌شده توسط سرور
- متن آفر: متن کامل و استاندارد آفر همراه توضیحات
- هویت درخواست‌دهنده: نمایش داده نمی‌شود
- مقدار درخواستی: فقط برای آفر لات‌بندی‌شده
- دکمه‌ها: `تأیید معامله` و `رد درخواست`

### پس از تأیید موفق

- پیام یا پنجره درخواست حذف می‌شود.
- هیچ پیام جداگانه‌ای با عنوان «درخواست تأیید شد» ساخته نمی‌شود.
- پیام‌های معاملاتی استاندارد فعلی، بدون تغییر در قرارداد محتوایی، برای هر دو طرف ارسال می‌شوند.

نمونه در سناریوی فروش آفر و خرید درخواست‌دهنده:

لفظ‌دهنده:

> 🔴 فروش
>
> 💰 فی: `{قیمت}`
>
> 📦 تعداد: `{تعداد معامله‌شده}`
>
> 🏷️ کالا: `{نام کالا}`
>
> 🗓️ تسویه: `{نقد حاضر یا فردایی}`
>
> 👤 طرف معامله: `{نام درخواست‌دهنده}`
>
> 🔢 شماره معامله: `{شماره معامله}`
>
> 🕐 زمان معامله: `{زمان معامله}`
>
> 📝 توضیحات: `{در صورت وجود}`

درخواست‌دهنده:

> 🟢 خرید
>
> 💰 فی: `{قیمت}`
>
> 📦 تعداد: `{تعداد معامله‌شده}`
>
> 🏷️ کالا: `{نام کالا}`
>
> 🗓️ تسویه: `{نقد حاضر یا فردایی}`
>
> 👤 طرف معامله: `{نام لفظ‌دهنده}`
>
> 🔢 شماره معامله: `{شماره معامله}`
>
> 🕐 زمان معامله: `{زمان معامله}`
>
> 📝 توضیحات: `{در صورت وجود}`

### پس از رد یا انقضا

- به درخواست‌دهنده هیچ پیام جدیدی ارسال نمی‌شود.
- پیام درخواست در بات حذف می‌شود یا پنجره درخواست در وب‌اپ بسته می‌شود.
- هیچ معامله یا پیام معاملاتی ساخته نمی‌شود.

## Confirmed Requirements

1. Each overtime trade request has a 30-second lifetime.
2. Approval is presented only on the offer's origin surface and home server.
3. WebApp-origin offers show approval requests globally across authenticated WebApp pages.
4. Bot-origin offers send approval requests to the offer owner through the bot.
5. Telegram approval requests include the offer text, request lifetime, useful context, and approve/reject buttons.
6. Lot-based offers include the requested quantity in the approval request.
7. Successful approval produces the normal trade messages for both parties.
8. Rejected or expired requests produce no separate message for the requester.
9. Requester identity remains hidden from the offer owner until the trade is successfully committed.
10. A request accepted before overtime closes remains actionable for its complete 30-second lifetime.
11. Market close, offer cancellation/completion, or a blocking state invalidates pending requests immediately.
12. Only one overtime request may be active per offer, even when another independent lot remains available.
13. Rejection or timeout starts a 30-second requester-offer cooldown without blocking other users.
14. Overtime is configured as a synchronized per-user default, snapshotted onto each new offer, with no initial per-offer override.
15. The user setting is an integer from 0 to 10 minutes, is not admin-configurable, and defaults to 0 for every user.
16. The offer's economic owner provides the overtime setting and is the sole approval authority; existing customer-manager trade-chain rules remain unchanged.
17. Owner presence is not required; requests remain valid only on their origin surface for 30 seconds and silently expire if unseen.
18. Telegram marks overtime with only `⏳`; WebApp restarts a green lifetime bar and shows a lightweight animated hourglass on the offer card.
19. A proportional static `⏳` remains in history only when at least one committed trade originated from an overtime request.
20. Telegram overtime and trade markers coexist; WebApp integrates `⏳` beside the existing upper-left relative time without overlap.
21. Overtime offers remain active, count against active-offer limits, and do not enter history or republish eligibility until final termination.
22. WebApp and bot both expose an explicit-save 0-10 minute setting to eligible offer owners, with different controls by decision: a plus/minus stepper in the WebApp, and a typed value confirmed by explicit accept/cancel in the bot. Zero means disabled and changes apply only to new offers.
23. The admin-configured normal offer lifetime preserves the current dynamic behavior for still-active offers; operational changes are made only while the market is closed, and expired offers are never revived.
24. Each owner receives only one actionable overtime request per offer home server at a time; valid requests for the owner's other offers on that same server wait in FIFO order, while WebApp- and bot-origin queues remain independent.
25. A queued request receives its full 30-second decision lifetime only when promoted for presentation, after full business revalidation.
26. WebApp requesters see a queued state before promotion and the authoritative countdown only after promotion.
27. A request received exactly at the normal-time deadline is rejected; automatic execution ends before that boundary and approval begins after it.
28. An already-active request keeps its full 30-second lifetime after overtime ends, while the offer becomes read-only and rejects all new requests.
29. A second request for the same offer, regardless of lot or requested quantity, never enters the owner queue or reaches the owner, even if the first request is later rejected or times out.
30. A request received exactly at the final overtime deadline is rejected.
31. WebApp begins the decision clock when the server promotes the request; Telegram begins it only after Telegram accepts the approval message and its id is recorded.
32. A bot-origin request that remains undelivered through the offer-validity window closes silently and releases the owner queue.
33. The trusted first-server receipt time is the only input that classifies a request into automatic, approval, or rejected. Transit delay, home-server processing time, and the current transit-grace window never move a request between phases.
34. The expiry worker and the request path evaluate the same strict boundary comparison against the same lifecycle projection, closing the current asymmetry at the exact deadline.
35. Every overtime request carries an idempotency key without exception, because the request ledger's synchronization identity requires one.
36. One requester may hold at most three simultaneously outstanding requests across distinct offers, and at most one against any single economic owner. Queued requests count toward both, neither is scoped per home server, and both release on every terminal outcome. The offer home server counts them locally against its own ledger including synchronized mirror rows, with best-effort cross-server accuracy accepted.

## Technical Compatibility and Challenge Review (2026-08-04)

### Review Scope

- This review is read-only and is based on `main` at commit `540b2c0c`, which was the branch head at the time of review.
- No source code, deployment configuration, branch, database, or running service was changed during this review.
- The feature can be implemented within the current two-server architecture. It does not require Object Storage, CDN, a second bot, or a new cross-server queue.
- One structural fact underpins several decisions and is worth stating plainly: the offer home server is derived directly from the origin surface, so a bot-created offer is always foreign-home and a WebApp-created offer is always Iran-home. "Origin surface" and `offer_home_server` are therefore the same axis, which is what makes the origin-scoped queue rule coherent.

### Existing Components That Must Be Reused

| Existing component | Relevant locations | Reuse decision |
| --- | --- | --- |
| Durable trade-request ledger | `models/offer_request.py`, `core/services/offer_request_ledger_service.py` | Extend the existing `OfferRequest` ledger. A parallel request table would split idempotency, auditing, and sync authority. |
| Offer-home authority and forwarding | `api/routers/trades.py` | Keep `Offer.home_server` as the sole authority. A remote request is forwarded to that authority before it is classified as automatic, queued, or approval-required. |
| Offer expiry transitions | `core/offer_expiry.py`, `core/services/offer_expiry_service.py`, `core/services/market_transition_service.py` | Replace the current one-deadline interpretation with a shared normal/overtime lifecycle; all expiry callers must use it. |
| Telegram publication queue | `core/telegram_delivery_queue_contract.py`, `core/telegram_delivery_freshness_router.py` | Add a dedicated, deadline-aware private approval action. Do not bypass the queue with a direct bot send. |
| Global WebApp approval runtime | `frontend/src/composables/useSessionApprovalRuntime.ts`, `frontend/src/components/SessionApprovalModal.vue`, `frontend/src/components/AppAuthenticatedShell.vue` | Reuse its authenticated-shell mount point, WebSocket-plus-HTTP-fallback refresh, reconnect/visibility recovery, and server-seeded countdown; create an overtime-specific state and UI rather than duplicating that infrastructure. Note that only login requests are primary-session gated today and there is no cross-tab coordination, so multi-tab behavior for overtime must be specified rather than inherited. |
| User and request synchronization | `core/events.py`, `core/sync_metadata.py`, `api/routers/sync.py` | Add new fields to the existing explicit sync payload and allow-list contracts on both servers. Do not rely on implicit ORM replication. |
| Channel rendering | `core/services/telegram_offer_channel_service.py` | Extend the central renderer and its queued publication flow for `⏳`; do not independently edit channel messages from the overtime workflow. |

### Required Technical Design Work

| No. | Challenge | What exists now | Required low-risk design |
| --- | --- | --- | --- |
| 1 | One authoritative offer lifecycle | No offer row stores a deadline. Every caller recomputes `created_at + current offer_expiry_minutes`, reading that setting through a cache with a 60-second TTL, a local fallback, and its own sync path. Roughly fifteen independent computation sites exist across API, worker, trade path, publication queue, web push, and frontend. | Create one server-side lifecycle projection returning normal deadline, overtime deadline, phase, interaction availability, and terminal reason. The offer home server owns that projection and is the single authoritative answer for its own offers; all other surfaces display it rather than recomputing. Expose the read-only result through the REST responses and the existing realtime WebSocket events. The settings cache is only a read mechanism and must not be treated as a cross-server convergence guarantee. |
| 2 | Exact time boundaries across two servers | Three separate behaviors exist today and they disagree. The trade path forgives transit delay: when the trusted edge receipt is `<= deadline` and transit is within `trade_forward_grace_seconds` (config default `3`, raised to `max(grace, 8)` on the bot path), a request that lands after the deadline still trades automatically. When that branch does not apply, classification falls back to the home server's own processing time via `now > expiry_at`. The expiry worker instead uses `created_at <= now - minutes`, so it expires an offer exactly at the deadline while the trade path still accepts it. Separately, `allow_in_flight_after_time_limit_expiry` lets an offer already flipped to `EXPIRED`/`time_limit` still finalize a trade when the edge grace applies. | Replace all three with one rule: the trusted first-server receipt time alone determines the phase, and transit delay never changes the phase. Compare strictly against that receipt: `< normal deadline` is automatic, `== normal deadline` rejects, `> normal deadline && < final deadline` enters approval, and `>= final deadline` rejects. The numeric grace window is therefore removed from phase classification; its only remaining job is to let a request whose receipt was validly inside a phase still be finalized after the worker has advanced the offer's status. The processing-time fallback must be deleted so a slow but valid overtime request reaches owner approval instead of being rejected as expired. The home server remains the final clock authority and device time is never trusted. |
| 3 | Durable request state and queue | `OfferRequest` has a durable ledger but no queued/presented/owner-decision lifecycle, decision deadline, delivery reference, or overtime marker. | Extend this ledger with explicit nonterminal and terminal approval states, immutable timestamps, queue ordering, home-server/owner snapshots, decision deadline, Telegram message reference, and rejection/invalidation reason. Add targeted database indexes and PostgreSQL-enum migrations. |
| 4 | Atomic one-request-per-offer rule | Current direct trade processing locks the offer, but does not model a pending owner decision. | At the offer home server, lock the offer and active request state in one transaction. A second request for the same offer must be rejected immediately, regardless of lot or requested quantity, and must never join the owner queue. |
| 5 | Independent owner queues by offer home server | There is no current owner-approval queue. On the ownership side there is less ambiguity than it first appears: for every market offer created through a user-facing path, `actor_user_id` equals `user_id`. Accountants are hard-blocked from the market in the API, the bot middleware, and the frontend router, and customers and group leaders always act as themselves. The only path where the two columns differ is cross-server replication. | Scope ordering and exclusivity to `(economic_owner_id, offer_home_server)`. WebApp- and bot-origin offers of one owner may each show one request concurrently; offers with the same home server are FIFO. Use `offer.user_id` as the owner key. Reading `user_id` rather than `actor_user_id` is correct by definition rather than a defence against delegation, since no delegated market actor exists in production. |
| 6 | Finalization without a second trade implementation | The current direct path creates the ledger and commits a trade in one authorization flow. | Separate request creation/classification from final approval, then reuse the current authoritative trade-validation/commit core for approval. This preserves lot, block, customer-manager, price, idempotency, and accounting rules instead of recreating them. |
| 7 | User overtime preference and immediate consistency | `User` has no overtime field. Write authority on `users` is field-level, not whole-record: `allowed_user_fields_for_source` grants the foreign server a small subset, so "Iran is the only writer" is only true per field. Emitting a forbidden foreign write already raises `foreign_user_write_authority_forbidden`. | Add a user-level integer default with migration default `0`, self-service save endpoints/handlers, explicit sync fields, and a per-offer snapshot at creation. Place the field in the Iran-authoritative identity field set, never the foreign-writable set, so the existing authority guard is what mechanically enforces single-writer. A bot save is forwarded to Iran and is shown as successful only after Iran persists it; during an Iran/foreign outage it is rejected without a local write or deferred intent. A new offer uses its persisted local snapshot, never an unsaved browser/bot value. Because bot-origin offers are always foreign-home, a bot-created offer snapshots the foreign mirror of this Iran-authoritative value; that snapshot is accepted as-is even when the mirror is briefly stale, and the staleness window must be bounded and logged rather than blocking offer creation. |
| 8 | Terminal invalidation fan-out | Manual expiry, automatic expiry, market close, cancel-all, completion, account blocking, and deactivation have separate callers. | Centralize `invalidate_overtime_requests_for_offer(...)` in the existing terminal-transition service. It must atomically close active/queued requests, prevent later callbacks from trading, remove/obsolete Telegram deliveries, close WebApp prompts, and promote the next eligible request only where the offer remains valid. |
| 9 | Telegram 30-second clock and stale delivery | The current queue is fail-closed: the freshness router refuses to build an incomplete lane and raises on a missing validator at dispatch, so a new action cannot ship without one. Private delivery, per-entry `delivery_deadline_at`/`freshness_deadline_at`, durable `telegram_message_id` capture, and edit-with-empty-keyboard are all already supported. There is no overtime-approval action, and `(M0, 1)` is already reachable by promoted overdue trade results. | Add a dedicated, deadline-aware private approval action at priority `M0`/rank `1`, register its freshness validator in the primary lane, and define its tie-break against promoted trade results. The clock begins only after Telegram accepts the message and its id is persisted. Retry only while the offer and queue entry are valid; otherwise silently close and release that same home-server queue. Callback payloads must be opaque and every click must be idempotent. |
| 10 | Channel marker and public interactivity | Channel renderer currently derives text/buttons from `Offer.status == ACTIVE`. | Render `⏳` from the shared lifecycle state, preserve normal trade markers, and remove/disable public trade interaction during the final pending tail. Route all edits through the publication queue to avoid races with expiry, trades, and retries. |
| 11 | WebApp global approval UX | The WebApp has a global security/session-approval modal mounted in the authenticated shell, but there is no cross-modal priority or queueing mechanism: competing overlays are independent teleported components. Market cards depend on the single `expires_at_ts` value plus a separate `expiryMinutes` prop for the bar denominator. | Add a dedicated overtime approval coordinator to the authenticated shell, server-authoritative countdown/reconnect recovery, and one owner/home-server prompt. The priority rule below must be built as new shared arbitration, not inherited. Update offer REST responses and realtime WebSocket events, and give the overtime bar a server-provided total duration instead of reusing the `expiryMinutes` prop. |
| 12 | Sync and rolling deployment | `OfferRequest` and `User` are synchronized through hand-maintained payloads; request status is a PostgreSQL enum crossing the wire as a plain string. The receiver already drops payload keys that are not persisted columns, so a newer peer's extra fields are generally tolerated rather than rejected. | Deliver additive migrations to both servers before enabling the feature. Then deploy backward-compatible code to both, keep the feature disabled by default (`0`), and only enable after schema/sync parity checks. Old code tolerates the new columns because of the existing column filter, but an old receiver will still reject an unknown enum *value* at write time, so the enum migration must land on both servers strictly before either server can emit a new status. |
| 13 | Privacy and auditability | Requester identity is intentionally hidden before a committed trade; channel-monitoring data is sensitive. | Never put requester identity, mobile number, or approval callback data in public/channel content. Persist all offer and request metadata, including immutable snapshots and lifecycle transitions, as internal data. Restrict any later admin view to existing admin authorization. |
| 14 | Regression coverage | Current tests cover direct trade, expiry, sync, Telegram delivery, and WebApp session approval separately. | Add focused tests for each lifecycle boundary and integration point, then execute a two-server staging matrix covering WebApp and bot origins, remote forwarding, customer ownership, queue ordering, terminal invalidations, retry/freshness, sync recovery, and UI reconnect behavior. |
| 15 | Ambiguous cross-server delivery | Only half of this pattern exists. On the offer home server the durable ledger, its unique idempotency key, and authoritative replay of a completed request are all real and reusable. On the forwarding server a timeout simply returns `504` to the caller: no intent is retained, no reconciliation worker exists, and recovery depends entirely on the user retrying with the same key. | Reuse the home-server idempotency and replay half unchanged. Build the forwarding-server half as new work: retain the idempotency key with a pending marker, show `⏳ در حال بررسی درخواست...` instead of the current retry error, and reconcile the authoritative home-server result in the background without creating a local queue entry or a second request. A definite pre-send failure keeps returning a short retry error and retains nothing. |
| 16 | Owner decision at the deadline | Timed project requests are active only while their expiry is strictly later than server time. | Require owner approval to reach the offer home server strictly before its stored 30-second deadline. At or after the deadline, atomically expire the request and reject the click/callback without creating a trade. |
| 17 | Requester cancellation | A queued or presented request may otherwise lead to a trade after the requester has changed their mind. | Allow only the original economic requester to cancel before owner approval. Finalize cancellation at the offer home server, remove/obsolete any owner prompt through the existing queue, and promote the next eligible request. |
| 18 | Cancellation-versus-approval race | Owner approval and requester cancellation may arrive concurrently from different surfaces/servers. | Lock the request and offer at the home server. The first valid mutation wins atomically; the later command is idempotent and reports the already-final state without creating a second trade or reopening the queue. |
| 19 | Cancellation-control delivery | A bot requester may lose or delete the private status message containing the cancellation control. | Use only the inline `لغو درخواست` button beneath that status message. Do not add a new persistent menu, panel item, or pending-request list; a missing message has no secondary cancellation route. |
| 20 | Queued-request requester status | A bot status saying that a request is queued becomes inaccurate once the owner is asked to decide. | Edit the same requester status message to `⏳ درخواست در حال بررسی است.` at promotion, retain cancellation until approval, and never create a second status message or alter the reply-keyboard anchor. |
| 21 | Terminal bot-status cleanup | Deleting a business message can disturb the reply-keyboard anchor, while leaving its old cancellation button exposes a stale action. | Never delete overtime status/approval messages. Edit the existing status to the confirmed terminal text and remove its inline buttons through the queue; retain normal trade delivery unchanged. |
| 22 | Republish snapshot semantics | Existing republish creates an independent replacement offer but has no overtime value to select. | Snapshot the economic owner's current persisted overtime setting when the replacement is created. Never copy the source offer's overtime configuration or historical marker. |
| 23 | Final-tail visual consistency | Existing UI has no state between active and terminal expiry. | When one accepted request outlives overtime, complete the overtime bar, make WebApp `⏳` static, retain channel `⏳`, disable all new interaction, and resolve the marker only at the final request outcome. |
| 24 | Requester concurrency across offers | One request per offer and one presented request per owner scope both bound the owner side. Nothing bounds how many distinct offers a single requester may hold under logical lock at once, so one requester could lock a large share of the market for thirty seconds at a time. Worse, several of those could target one owner and sit queued, holding their offers locked far longer than thirty seconds while waiting their turn. | Enforce two limits per requester: at most three outstanding requests across distinct offers, and at most one against any single economic owner. Queued requests count, because they already hold their offer's lock. The second limit is what bounds duration: it forces the three requests onto three owners, so they occupy independent queues and are presented in parallel. Count both locally and atomically on the offer home server against its own ledger, which already mirrors the other server's rows. Accept best-effort accuracy across servers; a transient overshoot of one is tolerable because these are abuse controls, not correctness invariants. |
| 25 | Ledger sync preconditions | The `offer_requests` sync natural identity requires a non-null idempotency key, so a row without one never synchronizes. The table is also in the quick parity set, with an explicit local-only field list. No naming problem exists here: the registry's `offer_home_server` text is a free-form authority description, like the authority strings of every other table, not a column name. The real column is `request_home_server`, the sync authority resolver reads exactly that column, and ledger creation deliberately populates it from `Offer.home_server`, so it already carries offer-home semantics. | Require an idempotency key on every overtime request without exception. Keep `request_home_server` as-is and continue populating it from the offer's home server; no rename is needed and none should be introduced. Register every new local reference column, such as a Telegram delivery job reference, in the parity local-only list so hash comparison does not report false drift. Confirm that the many nonterminal transitions an overtime request goes through are compatible with the registry's stated terminal-row immutability. |
| 26 | Migration surface | `alembic.ini` sets the script location to `migrations`, and the separate `alembic/` tree is a stale leftover with its own disconnected revision root. The safe enum-extension pattern already used in this repository is `ALTER TYPE ... ADD VALUE IF NOT EXISTS`, with values intentionally retained on downgrade. | Author every migration in `migrations/versions` only. Extend `offerrequeststatus` with the established `ADD VALUE IF NOT EXISTS` pattern and do not attempt to remove values on downgrade. |
| 27 | Owner reachability on the wrong surface | Approval is bound to the offer's origin surface with no cross-surface fallback, while the overtime preference is a single global per-user value applied to every new offer. A user who registers offers on a surface they do not actually monitor therefore never sees any request, and every request against their offers expires silently with no signal to anyone. | Show the confirmed warning at the moment the user enables a nonzero value, stating the per-offer rule rather than promising one surface. Treat persistent silent expiry for one owner as an operational signal in the diagnostics stage. |

### Cross-Cutting Risks and Controls

1. **Unauthorized decision on someone else's request:** this is the sharpest risk in the feature and it is not hypothetical. The WebApp approval is an ordinary authenticated endpoint that receives a request identifier, and `OfferRequest.id` is a sequential integer, so without an explicit check any logged-in user could approve or reject a stranger's trade by incrementing a number. The endpoint and the callback must both load the request and its offer at the home server, compare the caller against the offer's economic owner for approve and reject and against the original requester for cancel, and refuse otherwise. Authorization is never implied by the fact that the prompt or message was only delivered to one person. An analogous existing endpoint in this project, session login approval, checks that the caller has a primary session but never compares the request's `user_id` to the caller, leaving only identifier unguessability between users; this feature must not repeat that shape.
2. **Duplicate trade:** the approval endpoint and callback must lock the request and offer on the home server, revalidate all business rules, and be idempotent for repeated clicks or network retries.
3. **A request survives an invalid offer:** no caller may transition an offer to cancelled, completed, expired, blocked, or market-closed without invalidating its pending overtime requests in the same transactional boundary or an explicitly ordered outbox action.
4. **Different displays report different time:** WebApp, bot, channel, expiry worker, and trade API must consume server-provided lifecycle fields. Browser or Telegram clock is presentation only.
5. **Queue starvation or a late Telegram message:** use FIFO per owner/home server, a delivery deadline, freshness checks immediately before delivery, and a durable recorded Telegram message id before exposing an actionable deadline.
6. **A user's new setting is silently stale on the other server:** do not show a save success before canonical persistence; use the persisted synchronized value at offer creation and log a retriable failure when the required authority is unavailable.
7. **Migration incompatibility:** PostgreSQL enum additions and explicit sync payload changes require migration-first deployment, staged health checks on both servers, and a feature flag/default of zero until rollout completes. New columns are tolerated by an old receiver because non-column payload keys are already filtered out, but a new enum value is not, so the enum migration must land on both servers strictly before either can emit a new status.
8. **Market-wide lock by one requester:** without a per-requester cap on outstanding requests across distinct offers, a single user can hold many offers under a thirty-second logical lock at once and stall the market for everyone else. The cap must be enforced in the same atomic boundary as the per-offer lock, and released on every terminal outcome including cancellation, timeout, and hard invalidation.
9. **Silent expiry for an unreachable owner:** because approval never crosses surfaces while the preference is global, an owner can accumulate expired requests without any party noticing. Persistent silent expiry for one owner must be visible in the diagnostics stage rather than only in per-request records.

### Confirmed Audit Payload Scope

All metadata belonging to the offer and its overtime requests is retained as durable internal data. The implementation must preserve, at minimum:

1. **Offer snapshot:** public identity, home server, both the economic owner and the recorded actor reference even though the two are always identical for market offers, commodity, side, settlement type, price, quantity, remaining quantity, wholesale/lot structure, notes, creation time, normal-lifetime calculation inputs, overtime-minute snapshot, channel publication references, and every terminal/phase status transition.
2. **Request snapshot:** requester and actor references, source surface/server, idempotency key, requested quantity/lot, authoritative receipt time, queue sequence, presentation and decision timestamps, decision deadline, approver, approval/rejection/timeout/invalidation reason, Telegram delivery/message reference when applicable, and resulting trade reference.
3. **Privacy boundary:** these records are internal. Requester identity and personal data are not shown to the offer owner before a committed trade, and none of this audit payload is added to public or monitoring-channel posts.
4. **User data:** the offer/request records retain stable user references and relation snapshots needed to reconstruct the decision. Unrelated duplicate profile copies are not created merely for audit; the existing authoritative user record remains the source for profile details.

### Required Test Matrix Before Activation

1. **Lifecycle boundaries:** zero overtime; normal-only offers; one and ten minute overtime; strictly before, exactly at, and strictly after both deadlines; dynamic normal-lifetime change while market is closed; no revival of expired offers.
2. **Request creation:** direct normal trade; overtime request; duplicate idempotency replay; concurrent requests for one offer with different lots/quantities; same requester cooldown; concurrent requests for different offers under the same and different home servers.
3. **Decision outcome:** approve, reject, timeout, double click, delayed callback, partial lot completion, full completion, and final-tail approval that causes remaining quantity to expire immediately.
4. **Invalidation:** manual expiry, automatic final expiry, cancellation, cancel-all, market close, account block/deactivation, and completion while requests are queued, presented, or being delivered.
5. **Origin and ownership:** WebApp-home and bot-home offers; request issued locally and remotely; ordinary users; tier-1 customers owning and approving their own offers; tier-2 customers as requesters only, including their commission-adjusted price; accountants confirmed to see neither the setting nor any approval on any surface; and block checks resolved through trade principals so a block between two group leaders stops a trade routed through their customers.
6. **Telegram:** successful send, 429/retry, failed delivery before deadline, stale queued delivery, callback replay, bot restart, message cleanup, and coexistence with the existing publication queue.
7. **WebApp:** any authenticated page, reconnect, visibility restore, primary/secondary tabs, server countdown, modal priority, reduced motion, card timers, final-tail read-only controls, and historical marker placement.
8. **Synchronization and rollout:** user-setting propagation, offer snapshot propagation, request transition parity, temporary cross-server failure/recovery, migration compatibility, and feature-disabled behavior.
9. **Staging acceptance:** a real two-server functional matrix with isolated test users and offers, recording complete offer/request metadata, request ids, offer ids, home-server logs, queue transitions, sync evidence, and before/after database state. No production data or production trades are used.
10. **Ambiguous forwarding:** definite connection failure, timeout after remote acceptance, timeout before remote acceptance, idempotent retry, authoritative recovery, and no duplicate local or remote request.
11. **Forwarded normal boundary:** trusted first-server receipt strictly before, exactly at, and strictly after the normal deadline; device-time tampering; delayed arrival at the offer home server; a receipt validly inside overtime that arrives late and must reach approval rather than rejection; and confirmation that the removed transit-grace window no longer changes phase.
12. **Owner-decision boundary:** approve/reject strictly before, exactly at, and after the 30-second server deadline; delayed WebApp request, delayed Telegram callback, duplicate click, and concurrent expiry worker.
12b. **Decision authorization:** a second authenticated user attempting approve, reject, and cancel on another user's request through the WebApp endpoint and through a replayed Telegram callback, on both home servers, with and without a valid session, verifying refusal every time, no state change, no trade, and no information disclosed about the request or its parties.
13. **Telegram priority:** approval delivery follows `M0`/rank `1`, remains behind existing rank-`0` callback/expiry actions, and cannot be starved by publication or normal-result jobs.
14. **Requester cancellation:** queued and presented cancellation from WebApp and bot, unauthorized cancellation, duplicate cancellation, owner-prompt cleanup, queue promotion, and races with owner approval, expiry, invalidation, and market close.
15. **Cancellation-versus-approval race:** concurrent owner approval and requester cancellation from same/different servers, exact transaction outcome, stale response, idempotent replay, and no duplicate trade.
16. **Cancellation access:** inline bot cancellation, delivered and failed/deleted status message, restart/reconnect, no secondary cancellation route, and no unintended main-menu or user-panel change.
17. **Requester status transition:** queued bot request promotion edits the existing message, retains/removes cancellation at the correct point, shows the WebApp countdown only after promotion, and leaves the reply-keyboard anchor untouched.
18. **Terminal message safety:** approve, reject, timeout, invalidation, cancellation, stale callback, and queue retry edit the existing bot status/approval message, remove inline buttons, preserve the reply-keyboard anchor, and emit normal trade messages only on successful approval.
19. **Republish semantics:** source and replacement with different current overtime settings, republish by an ordinary user and by a tier-1 customer, historical marker isolation, remaining lots, and two-server synchronization.
20. **Final-tail visuals:** end of overtime with an active approval, completed green bar, static WebApp marker, retained channel marker, disabled public interaction, approval/rejection/timeout, and historical marker removal/retention.
21. **Requester limits:** a requester holding one, two, and three outstanding requests and being refused the fourth; a second request against an owner they already hold one against being refused; three requests across three owners all being presented in parallel rather than queued behind each other; queued requests counting toward both limits; release on approval, rejection, timeout, cancellation, and hard invalidation; requests spread across both home servers, including behavior under synchronization lag where a transient overshoot of one is accepted rather than treated as a failure; interaction with the per-offer lock and the one-presented-per-owner-scope rule; and concurrent attempts racing each limit boundary.
22. **Deadline agreement:** worker and request path evaluated at the same exact deadline instant, the in-flight allowance extended to the overtime phase and the tail, and no path where a worker-advanced status silently drops a validly received request.
23. **Telegram rank contention:** an overtime approval and an overdue trade result both resolving at `M0` rank `1`, verifying the delivery-deadline and sequence tie-break and that neither starves the other.
24. **Message conformance:** every user-facing string emitted on either surface matches its approved inventory entry exactly, no state produces an unlisted message, and no path silently reuses an existing project string in place of an approved overtime string.
25. **Sync preconditions:** a request without an idempotency key never reaching the wire, parity hashing with the new local reference columns registered as local-only, repeated nonterminal transitions against the registry's immutability rule, and an old receiver encountering a new enum value before its own migration has landed.

## Open Questions Requiring a Decision

None. Every product and technical decision this feature depends on is recorded in the Decision Log, and all forty-three user-facing strings are approved in the message inventory. Detailed schema, migration, API, queue, and test design will be derived from the confirmed requirements without changing any stated behavior.

Resolved during review, listed here so the reasoning is not lost:

- The owner reachability warning is required, with approved wording that states the per-offer rule rather than promising one surface.
- Requester limits are three outstanding requests overall and one per economic owner, counted locally on the offer home server with best-effort cross-server accuracy explicitly accepted.
- The feature name question is closed: users only ever see `وقت اضافه`, so the internal full name never reaches a screen.
- The bot preference control is a typed value with explicit confirmation, not a stepper; the stepper is WebApp-only.
- Phase classification uses the trusted first-server receipt time alone, and the transit-grace window no longer moves a request between phases.
- Every decision is authorized server-side against the caller's identity, and the request carries an opaque public identifier so no identifier is ever a capability.

The next gate is not a question but an approval: the roadmap below may begin only when implementation is explicitly authorized.

## Stage-Based Implementation Roadmap

> Status: planning only. No code, migration, deployment, or runtime action may start until this roadmap is explicitly approved. Branch creation is not part of that prohibition: `candidate/offer-overtime` already exists for documentation work and is where implementation will later land.

### Global Delivery Rules

1. Work continues on the existing `candidate/offer-overtime` branch, rebased on the then-current `main`; unrelated worktree changes are never included.
2. Every stage has focused automated tests and a recorded result before the next stage starts. A failing regression blocks progress.
3. All database changes are additive first. Both servers receive compatible schema before any code can emit a new request state or enable user overtime.
4. The feature is inert by default: every user starts at `0` minutes. No existing offer is altered by migration.
5. `Offer.home_server` remains the only authority for request creation, cancellation, decision, expiry, and trade commit. No local mirror may make an overtime decision.
6. Existing normal-time trade behavior, customer-manager settlement chain, limits, blocks, fair-price behavior, notifications, and Telegram queue contracts are preserved unless a stage explicitly extends them.
7. Staging uses isolated test users and offers. No production trade, production user, or production data mutation is used as a test fixture.
8. No stage may ship a user-facing string that is not approved in the message inventory. A stage whose scope includes a message with missing or unapproved text is blocked until that text is written and approved, and each stage's exit criteria include confirming that every string it introduced matches the approved inventory entry exactly.

### Stage 0 — Baseline and Delivery Contract

**Goal:** freeze a reproducible starting point before any feature code exists.

- Record the exact `main` commit, current migrations, server schema versions, sync health, and Telegram queue contract version.
- Run the existing market, expiry, request-ledger, sync, Telegram queue, and relevant WebApp test suites unchanged.
- The feature branch already exists and carries this document; begin committing implementation to it only after explicit approval, and collect per-stage logs under an ignored `tmp/` evidence directory.
- Define the feature-disabled acceptance baseline: user setting is absent before migration and, after migration, defaults to `0` with unchanged market behavior.

**Exit criteria:** clean baseline evidence and no untriaged failure in existing behavior.

#### Stage 0 completion notes

**Status:** complete. No code, schema, or runtime state was touched; this stage only recorded facts and ran existing suites.

**Baseline recorded:** `main` at `540b2c0c`; this branch at `75e4f700`, which differs from `main` by this document only. Migration graph in `migrations/versions` is healthy with 113 revisions, a single root `d339d8abee2f` and a single head `a274f5a6b8c9`, so Stage 1 adds onto one linear head rather than resolving a branch. Contract versions at baseline: sync protocol `2`, sync registry `4`, sync field policy `2`, sync parity schema `1`.

**Suites run:** the 241 test modules matching offer, expiry, market, sync, Telegram delivery, and trade. Command: `python3 -m unittest` over the module list retained at `tmp/offer-overtime-evidence/stage0-modules.txt`. Result: **1826 tests, all passing, 178 skipped**. Skips are Postgres-gated integration tests that require `MARKET_STAGEN_TEST_DATABASE_URL`, which is expected outside a database-backed environment.

**Triaged finding, no product defect:** the first run reported 23 failures across nine sync-receiver modules. The cause is environmental, not behavioral. `core/config.py` defaults `registration_sync_v2_enabled` to `False` and those tests are written against that default, but the local `.env` sets `REGISTRATION_SYNC_V2_ENABLED=true`, which routes user inserts down the v2 path and changes the expected outcome. Re-running the identical module list with the flag at its code default gives a fully green suite. This is worth knowing before Stage 1: anyone running these suites on a machine with that `.env` will see the same 23 phantom failures and must not read them as regressions introduced by this feature.

**Evidence retained:** `tmp/offer-overtime-evidence/stage0-modules.txt`, `tmp/offer-overtime-evidence/stage0-targeted.log` (local `.env`, 23 failures), and `tmp/offer-overtime-evidence/stage0-targeted-default-env.log` (code-default flag, green). The directory is inside the ignored `tmp/` tree as the delivery rules require.

**Deviations:** none. The WebApp suite was not run in this stage because no frontend behavior is in scope before Stage 10; it is covered by the Stage 11 and 12 entries instead.

**Next stage prerequisites:** Stage 1 is the first stage that writes code and migrations, so it may not begin until implementation is explicitly authorized. When it does, pin the run to the code-default flag value so the baseline stays comparable, and add onto head `a274f5a6b8c9`.

### Stage 1 — Additive Data Model and Safe Migrations

**Goal:** create durable storage without changing live behavior.

- Add `User.offer_overtime_minutes` with default `0`, range validation `0..10`, and explicit source/sync metadata.
- Add immutable overtime snapshot fields to `Offer`, plus a durable historical marker recording whether an overtime request committed a trade.
- Extend the existing `OfferRequest` ledger, not a parallel table, with an opaque public identifier generated like `offer_public_id`, workflow kind, queue/presentation/decision state, immutable offer/request snapshots, owner/home-server references, timestamps, deadline, terminal reason, Telegram delivery/message reference, and resulting trade reference.
- Add PostgreSQL enum values through safe, ordered migrations and targeted partial indexes for one nonterminal request per offer, queued FIFO lookup, one presented request per `(economic_owner, offer_home_server)`, and the per-requester concurrency cap.
- Author all migrations in `migrations/versions` only; the separate `alembic/` tree is a stale leftover and must not be touched. Extend `offerrequeststatus` with the established `ALTER TYPE ... ADD VALUE IF NOT EXISTS` pattern and retain values on downgrade.
- Keep the existing `request_home_server` column and its current derivation from `Offer.home_server`. It is already the offer-home key that the sync authority resolver reads, so no rename or new home column is introduced.
- Ensure migration downgrade/old-code compatibility is defined before deployment; no new state is emitted in this stage.

**Primary locations:** `models/user.py`, `models/offer.py`, `models/offer_request.py`, `migrations/versions`, `core/events.py`.

**Tests:** migration upgrade on empty and representative databases; default-zero backfill; enum compatibility; index/concurrency probes; existing request-ledger tests.

**Exit criteria:** both schemas can store the new fields while feature-disabled code behaves exactly as before.

#### Stage 1 completion notes

**Status:** complete in code, with one prerequisite still outstanding: the migration has not been executed against a real database, because no PostgreSQL instance is reachable from this environment. That is recorded as a deviation below and must be closed before Stage 2.

**Scope delivered.** `User.offer_overtime_minutes` defaults to `0` with a `0..10` check constraint. `Offer` gains `overtime_minutes_snapshot`, also range-checked, and `overtime_trade_committed` for the historical marker. The `OfferRequest` ledger is extended rather than duplicated, with an opaque `request_public_id`, a `workflow_kind` discriminator defaulting to `direct`, an `offer_owner_user_id` snapshot so the owner queue never has to join offers, `queue_sequence` for FIFO promotion, `presented_at` and `decision_deadline_at`, `decided_by_user_id`, `terminal_reason`, and the two Telegram delivery references. Nine overtime statuses were added to `offerrequeststatus`; success deliberately reuses `completed_trade` so there is one meaning of a committed trade across both workflows.

**Four partial indexes carry the concurrency rules into the schema** rather than leaving them to application code: one live overtime request per `(home server, offer)`, one owner-occupying request per `(home server, economic owner)`, a FIFO lookup on the queued set, and a requester lookup for the outstanding-request limits. Their predicates compare `result_status::text`, not the enum, so a later type rebuild cannot invalidate them.

**Inertness.** Every new column is nullable or server-defaulted to the disabled value, no code writes any new status, and `workflow_kind` defaults to `direct`, so a row written by code that predates this stage remains valid. Tests assert this directly rather than assuming it.

**Affected components:** `models/user.py`, `models/offer.py`, `models/offer_request.py`, new `core/offer_request_identity.py`, `core/registration_sync_policy.py`, `core/sync_parity.py`, and `migrations/versions/b5d1c7e93f04_add_offer_overtime_data_model.py` on head `a274f5a6b8c9`, which remains the single head.

**Sync decisions.** `offer_overtime_minutes` is registered in the Iran-authoritative user field set and never in the foreign set, so the existing write-authority guard is what enforces single-writer. `telegram_delivery_job_id` is registered as parity local-only, because the two peers legitimately point at different local delivery rows. The remaining new columns are deliberately absent from the sync payloads at this stage; nothing writes them, so both peers hold identical defaults and parity cannot drift. Wiring them into the payloads belongs to Stage 7 and must not be forgotten.

**Tests and results.** New suite `tests/test_offer_overtime_data_model.py`, 27 tests, covering inertness, the state groups, index shape and uniqueness scoping, the sync wiring, identifier opacity, and a drift guard that reads the migration with `ast` and compares its status and predicate constants against the models. `tests/test_offer_request_ledger_model.py` was updated because it pins the status set as a contract. Regression: the Stage 0 module list re-run green at 1826 tests, and a further 1058 tests across user, registration, session, contract, parity, auth, and migration modules also green, both with the environment flags at their code defaults.

**Deviations and known gaps.** First, no database was available, so `alembic upgrade head`, the downgrade guard, and the partial-index concurrency probes are untested against PostgreSQL; the migration is verified only by static analysis and model agreement. Second, two additional environment-dependent failures appeared alongside the Stage 0 finding: `.env` sets `TELEGRAM_DIRECT_REGISTRATION_ENABLED` and `TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED` to true while the tests assert the code defaults of false. Third, `tests/test_registration_identity_property` and `tests/test_registration_stateful_fuzz` cannot load because `hypothesis` is not installed. None of the three is caused by this stage.

**Evidence retained:** `tmp/offer-overtime-evidence/stage1-regression.log`, `stage1-extra.log`, and `stage1-extra-default-env.log`.

**Next stage prerequisites.** This migration must be run up and down on a real database, with the default-zero backfill confirmed on a representative dataset and the partial indexes probed under concurrent writers. That gate is on **deploying or merging** the feature, not on writing later stages: subsequent stages may be developed against the models while it stays open, since nothing they add is deployable either until the schema is proven. The gate is tracked here until a database-backed environment is available, and Stage 16 cannot be entered while it remains open.

### Stage 2 — Canonical User Preference and Offer Snapshot

**Goal:** implement the confirmed single-writer preference contract before any offer can rely on it.

- Add a self-service preference service with validation and explicit save semantics.
- WebApp writes on Iran. Bot writes are signed internal commands to Iran and acknowledge success only after Iran persists the value, reusing the existing signed internal-command mechanism.
- During Iran/foreign disconnection, reject bot saves without a local write, deferred intent, or false success. Note that disconnection behavior in this project is not uniform: the trade forward rejects, the registration reconciler retries, and the sync receiver defers. This preference follows the reject pattern explicitly.
- Add the field to explicit user serialization, event payload, sync allow lists, versioning, and cache invalidation. Place it in the Iran-authoritative identity field set and never in the foreign-writable set, so the existing write-authority guard is what enforces single-writer.
- When the user saves a nonzero value, show the reachability warning: `تأیید هر لفظ فقط در همان محل ثبت لفظ نمایش داده می‌شود: لفظ وب در وب‌اپ و لفظ بات در بات.` It must not name one fixed surface for the user, because the preference is global while approval is bound per offer to that offer's own origin.
- At creation of any new offer, including a republished offer, snapshot the economic owner’s current persisted value. Never copy the source offer’s snapshot during republish.

**Primary locations:** `models/user.py`, user API/service layer, bot internal-command client, `api/routers/sync.py`, `core/events.py`, `core/sync_metadata.py`, offer creation services.

**Tests:** Iran save, bot-forwarded save, outage rejection, stale-sync recovery, range validation, eligibility across ordinary users, tier-1 customers, tier-2 customers, and accountants, new offer snapshot, and republish using the current preference.

**Exit criteria:** both servers converge on the preference and every newly created offer has an immutable, correct snapshot.

#### Stage 2 completion notes

**Status:** complete in code across two commits on `candidate/offer-overtime`: `92ec83ee` (canonical preference helpers, offer-creation snapshot, user sync payload field) and `e0a7ada4` (WebApp/Iran save, signed bot forward, serialization, offer sync payload fields).

**Scope delivered.** A single preference service owns range normalization (including Persian/Arabic-Indic digits), eligibility, approved success/warning/outage copy, Iran-local persist, and the bot-facing save that forwards to Iran. WebApp saves through `PUT /api/auth/me/offer-overtime` on Iran only; a call that lands elsewhere is refused with inventory M7 and no local write. Bot saves go through `save_overtime_preference_from_bot` → signed `POST /api/auth/internal/offer-overtime/update` on Iran via `core/offer_overtime_preference_transport.py`. On transport failure the bot path raises with M7 and leaves the foreign row untouched—no deferred intent and no false success. `UserRead` and the update schemas expose `offer_overtime_minutes`. Nonzero saves return inventory M4 plus reachability warning M6; zero returns M5. Offer creation continues to snapshot from the locked owner row. Because Stage 2 now writes `overtime_minutes_snapshot`, that field and `overtime_trade_committed` were added to `build_offer_sync_payload` ahead of the broader Stage 7 sync pass so offer sync cannot converge peers onto the column default of `0`.

**Affected components:** `core/services/offer_overtime_preference_service.py`, `core/offer_overtime_preference_transport.py`, `core/services/offer_creation_service.py`, `core/events.py` (`build_user_sync_payload`), `core/offer_sync_payload.py`, `schemas.py`, `api/routers/auth.py`, `main.py` (internal isolation prefix), and tests `tests/test_offer_overtime_preference_service.py`, `tests/test_offer_overtime_preference_save.py`, plus updates to the auth current-user and offer sync payload suites.

**Tests and results.** Targeted overtime/auth/sync-payload suites green at 65 tests via `make test-unit MODULES="tests.test_offer_overtime_preference_save tests.test_offer_overtime_preference_service tests.test_offer_sync_payload tests.test_auth_router_current_user_contract tests.test_offer_overtime_data_model"`. Coverage includes Iran save, foreign WebApp refusal, bot forward without local write, outage rejection with M7, range validation, eligibility for ordinary/tier-1/tier-2/accountant, new-offer snapshot, user sync authority, and offer sync payload defaults.

**Deviations and known gaps.** First, the Stage 1 real-database migration gate remains open; it still blocks merge/deploy, not further coding. Second, defensive copy `این تنظیم برای حساب شما در دسترس نیست.` is used when an ineligible account reaches a save path the UI should already hide; it is not yet an inventory entry and must be approved or replaced before any user-visible surface relies on it. Third, bot panel UI and WebApp stepper remain Stage 9 / Stage 10; this stage only ships the authoritative save contract they will call. Fourth, full stale-sync recovery matrix stays with Stage 7; this stage relies on the existing user sync path once Iran persists the field.

**Next stage prerequisites.** Stage 3 may begin. It must not change behavior for snapshot `0`, and it must treat the offer home server's lifecycle projection as the only authoritative answer.

### Stage 3 — Shared Offer Lifecycle Projection

**Goal:** replace scattered expiry calculations with one authoritative lifecycle calculation.

- Implement a server-side lifecycle projection that returns normal deadline, final overtime deadline, phase, public interaction availability, and terminal transition eligibility.
- Preserve current dynamic normal-lifetime behavior for still-active offers; combine it with the immutable offer overtime snapshot. Read the admin lifetime only through the existing settings accessor, and treat that accessor purely as a settings read mechanism: its 60-second TTL, its local fallback, and the separate settings sync path mean the cache is not a convergence guarantee between the two servers. The offer home server's projection is the single authoritative answer for that offer's lifecycle, and every other surface displays that answer rather than recomputing one.
- Enforce confirmed strict boundaries using the trusted first-server receipt time as the only phase input: automatic trade only strictly before the normal deadline; exact normal and exact final boundaries reject; approval only strictly within overtime. Delete the current home-server processing-time fallback and remove the transit-grace window from phase classification.
- Close the current worker-versus-request asymmetry so both evaluate the same comparison against this projection.
- Keep an accepted request actionable in the 30-second final tail, while disabling all new public interaction. Extend the existing in-flight allowance, which today only covers normal-time expiry, to cover the overtime phase and the tail.
- Update automatic-expiry scheduling to defer terminal expiry until final overtime/tail resolution without changing non-overtime offers.
- Migrate every existing deadline computation site onto the projection. The known sites are: the three computations in `core/offer_expiry.py` (stale expiry cutoff, next-delay scheduling, remote stale channel state); the trade guard in `api/routers/trades.py`; the four in `api/routers/offers.py` (private response, public response, creation realtime payload, market history stale-active expression) plus the expired filter in the my-offers listing; the realtime payload in `api/routers/sync.py`; the live-offer check in `core/web_push.py`; the publication freshness deadline in `core/services/telegram_offer_queue_service.py`, which carries its own five-second safety margin; the settings read in `core/telegram_offer_queue_feeder.py`; and on the client, the timer math in the offers list component and the active-row filter in the offers composable. This list is the completion checklist for the stage.

**Primary locations:** `core/offer_expiry.py`, `core/services/offer_expiry_service.py`, `api/routers/trades.py`, `api/routers/offers.py`, `api/routers/sync.py`, `core/web_push.py`, `core/services/telegram_offer_queue_service.py`, `core/telegram_offer_queue_feeder.py`, `core/utils.py`.

**Tests:** zero/one/ten-minute snapshots; normal and final boundary triplets; trusted forwarded receipt versus delayed home arrival, including a receipt validly inside overtime that arrives late and must reach approval rather than rejection; removal of the transit-grace effect on classification; worker and request path agreeing at the exact deadline; dynamic normal setting including a change landing inside the settings cache window; final-tail behavior; no revival of an already-expired offer; worker timing and clock-skew guards.

**Exit criteria:** one server-side answer drives all lifecycle decisions, with no active behavior change for snapshot `0`.

### Stage 4 — Durable Overtime Request State Machine

**Goal:** build the persistent workflow and concurrency rules without wiring public UI yet.

- Define explicit nonterminal and terminal overtime states in `OfferRequest` and legal state transitions.
- Create requests atomically at the offer home server; enforce one nonterminal request per offer regardless of lot or quantity.
- Implement independent FIFO queues per `(economic_owner, offer_home_server)`, with at most one presented/delivering request per scope.
- Revalidate offer, market, accounts, blocks, quantity/lot, ownership, and business rules before promoting every queued request.
- Add requester-offer cooldown after owner rejection or timeout; never apply it after a completed trade.
- Enforce both requester limits in the same atomic boundary as the per-offer lock: three outstanding requests across distinct offers, and one per economic owner. Count every nonterminal request including queued ones, release on every terminal outcome, and evaluate the count locally against the home server's own ledger including synchronized mirror rows, accepting best-effort cross-server accuracy.
- Require an idempotency key on every overtime request, because the ledger's sync natural identity refuses a row without one.
- Implement requester cancellation, owner decision, timeout, and their atomic first-valid-command-wins race.

**Primary locations:** `models/offer_request.py`, `core/services/offer_request_ledger_service.py`, new focused overtime workflow service, transaction/locking helpers.

**Tests:** duplicate replay, same-offer different-lot contention, FIFO promotion, separate Iran/foreign owner queues, cancellation, cancellation-versus-approval race, timeout, cooldown, and database-level concurrent writers.

**Exit criteria:** all request states are durable, idempotent, and mutually exclusive under concurrent access.

### Stage 5 — Authoritative Trade Integration

**Goal:** connect overtime approval to the existing trade engine without duplicating trade rules.

- Keep the existing direct automatic path for normal-time requests.
- Route overtime requests into the durable state machine rather than committing a trade immediately.
- On approved request, re-enter the existing authoritative validation/commit core under the request/offer lock; do not create a second trade implementation.
- Preserve existing lot, remaining quantity, customer chain legs, tier-2 commission pricing, limits, principal-resolved blocks, idempotency, trade messages, and accounting behavior.
- Mark the offer historical overtime flag only after a trade actually commits.

**Primary locations:** `api/routers/trades.py`, current trade service/validation helpers, `models/offer.py`, `models/offer_request.py`.

**Tests:** normal regression, overtime approval/rejection, partial/full lot trade, unavailable lot at approval, tier-1 customer owning and approving, tier-2 customer requesting with commission pricing, price and limit revalidation, block revalidation through trade principals, and exactly-once trade commit.

**Exit criteria:** approved overtime requests produce the same authoritative trade result as an equivalent valid normal request.

### Stage 6 — Terminal Events, Final Tail, and Queue Release

**Goal:** ensure no pending request survives an invalid offer or account.

- Centralize overtime-request invalidation and call it from automatic expiry, manual expiry, cancellation, cancel-all, completion, market close, account block/deactivation, and relevant user deletion flows.
- Implement final-tail behavior: one already-presented request may finish its own 30 seconds; no new request is accepted; partial approval immediately expires the remainder.
- Remove or obsolete pending Telegram delivery jobs and WebApp prompts through durable state transitions, never through best-effort-only cleanup.
- Promote the next request only after the previous terminal transition commits and only if its own offer remains valid.

**Primary locations:** `core/services/offer_expiry_service.py`, `core/services/market_transition_service.py`, `core/offer_expiry.py`, cancellation/block services, overtime workflow service.

**Tests:** every terminal event against queued, delivering, presented, and tail requests; queue release; partial tail trade; market close; account block; stale callback after invalidation.

**Exit criteria:** terminal offer/account state can never yield a later trade from a stale overtime request.

### Stage 7 — Cross-Server Commands, Sync, and Recovery

**Goal:** preserve home-server authority through both directions of the existing topology.

- Extend signed internal trade/request commands with an idempotency key, trusted first-server receipt time, request source surface/server, and overtime workflow result.
- Keep the existing home-server half of ambiguous-forward recovery, which already retains the unique key and replays a completed request. Build the forwarding-server half as new work, since a timeout there currently returns `504` with no retained intent and no reconciler: retain the key with a pending marker, show `⏳ در حال بررسی درخواست...`, and reconcile the authoritative result in the background. A definite pre-send failure still returns a short retry error and retains nothing.
- Extend explicit `OfferRequest`, `Offer`, and `User` sync payloads, field policies, natural identities, localization, version guards, and parity checks for every new field/state. Register every new local reference column in the parity local-only field list so hash comparison does not report false drift, and confirm that repeated nonterminal transitions of an overtime request are compatible with the registry's stated terminal-row immutability.
- Verify foreign mirrors never authorize, queue, or decide an Iran-home request, and vice versa.

**Primary locations:** `core/trade_forwarding.py`, `api/routers/trades.py`, `api/routers/sync.py`, `core/events.py`, `core/sync_metadata.py`, `core/sync_field_policy.py`, `core/sync_parity.py`.

**Tests:** both forwarding directions; before-send failure; ambiguous timeout with recovery; replay; temporary disconnect/reconnect; stale sync event; payload parity; requests from tier-1 and tier-2 customers; no foreign local decision.

**Exit criteria:** one logical request and one terminal result survive retries, outages, and sync reordering.

### Stage 8 — Telegram Delivery Queue Contract

**Goal:** add owner approval delivery without bypassing the queue or harming current Telegram flows.

- Add a dedicated durable private approval action to `TelegramDeliveryAction` with priority `M0` / rank `1`.
- Register a fail-closed freshness validator and execution route; define delivery deadline, retry, rate-limit, ambiguous send, expiry, and terminal cleanup semantics.
- Begin the owner’s 30 seconds only after Telegram accepts the approval message and its message id is persisted.
- Use opaque callback payloads carrying the request's opaque public identifier, never its sequential primary key. Verify owner identity, home server, request state, deadline, and idempotency for every approve/reject click, and treat a mismatch as a refusal rather than a no-op.
- Ensure queue actions edit existing business messages and never delete a reply-keyboard anchor.

**Primary locations:** `core/telegram_delivery_queue_contract.py`, `core/telegram_delivery_freshness_router.py`, Telegram worker/executor, callback contracts.

**Tests:** priority ordering behind rank-`0` work; rate limit; retry; ambiguous send; stale delivery; callback replay; invalid owner; bot restart; final-tail expiry; message-id persistence; no anchor deletion.

**Exit criteria:** Telegram approval is timely, durable, safe under retries, and isolated from normal channel publication behavior.

### Stage 9 — Bot Overtime Interaction Flow

**Goal:** expose the confirmed bot behavior without changing the persistent keyboard layout.

- Add the `⏳ وقت اضافه` entry to the existing eligible user panel. The control is a typed value confirmed by explicit accept/cancel, reusing the limit-settings pattern; do not build a plus/minus stepper in the bot. Zero is entered as a value, not a separate disable button. The save result is Iran-authoritative and is reported only after Iran persists it.
- Add bot-origin request creation and source-side status messages with the existing inline `لغو درخواست` button only; add no new main-menu, panel, or pending-request-list button.
- Implement queued-to-presented requester status edit, confirmed terminal texts, owner approval texts/buttons, and button removal through the queue.
- Keep requester identity hidden in every pre-trade bot message.

**Primary locations:** `bot/keyboards.py`, relevant trade handlers/callbacks, `bot/states.py`, bot delivery runtime helpers.

**Tests:** setting authorization and the typed-input flow including non-numeric input, out-of-range values, zero entered as a value, cancel at each step, and a save attempt while Iran is unreachable; queued/presented/terminal text; cancellation; owner approve/reject; late, duplicate, and non-owner callback clicks; requester/owner identity privacy; remote-home flow; no reply-keyboard/anchor regression; normal bot trading regression; every string emitted matches its approved inventory entry exactly.

**Exit criteria:** bot behavior matches every confirmed message, control, queue, and cancellation decision in this document.

### Stage 10 — WebApp Server API and Realtime Contract

**Goal:** make lifecycle and overtime-request state available to WebApp without client-side inference.

- Extend offer read models, public/private list responses, history responses, and the existing realtime WebSocket events with server-derived lifecycle fields, interaction availability, overtime marker state, and request status. The market UI consumes WebSocket events plus one-second HTTP polling; the separate SSE endpoints are not part of this surface and must not be assumed.
- Add authenticated endpoints for preference save, current owner approval request, decision, requester cancellation, and reconnect recovery.
- Enforce the same home-server authority, locks, deadlines, and idempotency as the bot paths; no WebApp-only shortcut is allowed.
- Authorize every decision endpoint against the caller's authenticated identity: approve and reject require the offer's economic owner, cancel requires the original requester. Accept only the request's opaque public identifier, never its sequential primary key, and never treat knowledge of an identifier as permission.
- Preserve compatibility for feature-disabled offers and old clients during rolling deployment.

**Primary locations:** `api/routers/offers.py`, `api/routers/trades.py`, realtime router/events, schemas, response serializers.

**Tests:** API contracts; realtime WebSocket event order and interaction with one-second polling; a different authenticated user attempting approve, reject, and cancel on someone else's request and being refused on every endpoint and both servers; enumeration attempts against sequential ids being impossible because only opaque identifiers are accepted; reconnect; remote home; disabled feature; stale request; decision/cancel race; backward-compatible response parsing.

**Exit criteria:** WebApp receives authoritative phase and request state from the server and cannot create a conflicting decision.

### Stage 11 — WebApp Approval Runtime and User Setting UI

**Goal:** add the global, authenticated WebApp interaction surface.

- Reuse the authenticated-shell mount point and the session-approval runtime pattern; do not create a competing global modal system.
- Build the confirmed priority as new shared arbitration: no cross-modal priority or queueing mechanism exists today, and competing overlays are currently independent teleported components at the same stacking level. Security and session-recovery approval comes first, overtime approval immediately afterward with remaining server time.
- Specify multi-tab behavior explicitly rather than inheriting it: today only login requests are primary-session gated and there is no cross-tab coordination.
- Show one request per owner/home-server scope, full offer/notes, lot quantity where applicable, anonymous requester, authoritative countdown, approve/reject, and requester cancellation state.
- Add the explicit-save 0–10 minute preference stepper in the confirmed WebApp settings and market locations.

**Primary locations:** `frontend/src/components/AppAuthenticatedShell.vue`, `frontend/src/composables/useSessionApprovalRuntime.ts`, approval components/composables, market settings components.

**Tests:** authenticated routes, multi-tab primary-session behavior, reconnect/visibility restore, security-modal priority, countdown drift, owner/requester authorization, setting save/error, accessibility, and reduced motion preference.

**Exit criteria:** an eligible owner receives a safe approval prompt on every authenticated WebApp page without disturbing existing session controls.

### Stage 12 — WebApp Market Cards and History Presentation

**Goal:** render the confirmed lifecycle visibly and without layout regressions.

- Preserve normal timer behavior; restart the green overtime bar only when server phase changes.
- Render animated `⏳` in the fixed upper-left metadata group during overtime, static during final tail, and static in history only after a committed overtime trade.
- Respect reduced-motion preferences. The offer card has no such guard today and its existing critical-timer pulse animation is unguarded, so add the guard for the new marker and fix that existing animation in the same change.
- Size the marker against the adjacent relative-time text rather than the card body, and place it at the end side of the RTL text flow beside the relative time in a fixed-width group so starting or stopping the animation shifts nothing.
- Ensure no overlap, text shift, stale interactive control, or incorrect history marker.
- Make final-tail cards read-only while the single valid approval is pending.

**Primary locations:** market list/card components, timer composables, CSS, history/dashboard recent-trade views.

**Tests:** desktop/mobile visual regression; normal/overtime/tail/terminal states; partial/full trade; history; repeated offer; reduced motion; filter/list refresh; no active-offer count or republish regression.

**Exit criteria:** visual state accurately follows the server lifecycle on every market view.

### Stage 13 — Telegram Channel Rendering

**Goal:** update public posts through the established renderer and publication queue.

- Render `⏳` when an offer enters overtime; retain it statically during the final tail.
- Preserve current cash/future text and existing trade markers; never replace them with the overtime marker.
- Remove public trade interaction in final tail; restore nothing after terminal expiry.
- On terminal outcome, retain historical `⏳` only if an overtime request committed a trade; otherwise remove it.

**Primary locations:** `core/services/telegram_offer_channel_service.py`, offer publication state/service, Telegram delivery queue producers.

**Tests:** active overtime edit; final-tail edit; completed/partial/expired rendering; coexistence of markers; queue race with expiry/trade; stale edit prevention; normal channel regression.

**Exit criteria:** channel and WebApp present the same lifecycle without direct-send or message-edit races.

### Stage 14 — Audit, Reconciliation, and Operational Diagnostics

**Goal:** make every overtime decision diagnosable without exposing private data.

- Persist and expose internally all confirmed offer/request metadata and transition reasons.
- Add structured logs/metrics for classification, queue wait/presentation, decision, cancellation, invalidation, forwarding recovery, Telegram delivery, and sync conflict. Include a signal for an owner whose requests repeatedly expire unseen, and for a bot-created offer that snapshotted a stale mirror of the Iran-authoritative preference.
- Add a reconciliation job/report that detects impossible nonterminal requests, mismatched delivery references, overdue queue entries, or lifecycle inconsistencies and repairs only through authoritative state transitions.
- Do not add a new public or admin UI in this version; use existing authorization for any internal inspection path.

**Tests:** metadata completeness; privacy redaction; reconciliation dry-run; no destructive repair without authority; metric/log assertions; sync parity evidence.

**Exit criteria:** a production incident can be diagnosed from durable records without exposing requester identity before trade.

### Stage 15 — Full Automated Regression Matrix

**Goal:** prove the feature across all covered combinations before staging activation.

- Run all focused tests from Stages 1–14 plus existing full market, offer, trade, expiry, queue, sync, bot, and WebApp suites.
- Execute concurrency tests with controlled parallel requests, not load testing: same offer, different lots, different offers/same owner/home, different homes, cancel/approve/expire races.
- Run cross-server contract tests in both directions, migration compatibility tests, and browser tests across supported engines/viewports.
- Archive machine-readable results, logs, seed identities, request/offer ids, and failure diagnostics per suite.

**Exit criteria:** all suites pass; any known non-feature failure is documented and explicitly accepted before staging.

### Stage 16 — Two-Server Staging Acceptance

**Goal:** validate the complete behavior against real staging Iran/foreign topology.

- Deploy migration-first, then compatible application code, while all staging users remain at overtime `0`.
- Use isolated staging users to validate user-setting save from WebApp and bot, WebApp/bot offer origins, both request directions, customer ownership, queue ordering, cancellation, final tail, channel markers, sync interruption/recovery, Telegram delivery retry, and UI reconnect.
- Compare authoritative and mirrored database rows, sync receipts, queue records, channel message ids, and WebApp/browser evidence for every scenario.
- Produce a zipped staging evidence package with logs and test results organized by roadmap stage and scenario.

**Exit criteria:** every acceptance case passes, no data/sync mismatch remains, and feature-disabled users show no regression.

### Stage 17 — Controlled Production Rollout and Rollback Gates

**Goal:** activate with no unplanned impact on existing market behavior.

- Preflight production schema parity, sync health, Telegram queue health, backups, and current market state; deploy migration-first and compatible code to both servers.
- Keep all users at `0` after deployment. Enable only selected test users by their own preference, monitor durable audit/queue/sync evidence, then expand deliberately.
- Do not alter global normal offer lifetime or existing active offers during rollout.
- Define rollback: disable further overtime admission without deleting requests/trades; let already-valid requests finish or close through authoritative terminal rules; preserve every audit record and do not downgrade schema destructively.

**Exit criteria:** monitored rollout is stable, rollback gates are proven, and the feature can be expanded without affecting users who keep overtime disabled.
