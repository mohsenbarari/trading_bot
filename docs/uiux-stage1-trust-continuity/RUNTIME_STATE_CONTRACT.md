# Stage 1 — Runtime state contract

وضعیت: `complete`

## قرارداد بسته‌شده

- درخواست route timeout/abort دارد و retry شبکه نامحدود ایجاد نمی‌کند؛
- `loading`، `ready`، `true empty`، `error`، `offline`، `stale` و `reconnecting` از هم متمایزند؛
- فقط پاسخ موفق با مجموعه خالی empty state می‌سازد؛
- refresh/reconnect داده موفق قبلی را تا نتیجه جدید حفظ می‌کند؛
- پاسخ دیرهنگام قدیمی state جدیدتر را overwrite نمی‌کند؛
- retry کنار failure می‌ماند و query، selection، draft، modal و step معتبر را حفظ می‌کند؛
- mutation هم‌کلید duplicate نمی‌شود؛
- success فقط پس از `2xx` و receipt/entity مورد انتظار نمایش داده می‌شود؛
- failure نتیجه خوش‌بینانه نمی‌سازد و context اصلاح/retry را نمی‌بندد.

## foundation

Commit `88f167d8bfe16b410e5a690a4329c202d27dc24d` قراردادهای additive زیر را فراهم کرد:

- `routeRequest` / `routeRequestJson`: درخواست bounded authenticated/public؛
- `useAsyncResource`: state صریح، retained refresh، latest-wins و freshness فقط در حافظه؛
- `useActionState`: duplicate guard کلیددار و پذیرش نتیجه فقط با response/receipt معتبر؛
- current-user summary: `ready/stale/unauthorized/error` با cache و latest-wins؛
- `AppConfirmDialog`: busy/error/disabled و جلوگیری از cancel/escape هنگام mutation.

## route truth

| حوزه | تعهد Stage 1 | commit | وضعیت |
| --- | --- | --- | --- |
| Home/Profile/Account/Operations/Settings/PublicProfile | حذف identity/access فرضی، blank و loading نامحدود؛ حفظ context در refresh failure | `8e96beef` و `a4828a5a` | `landed` |
| Notifications | error برابر empty نیست؛ retained rows، reconnect و dedupe با ID؛ بدون total ساختگی | `a552e264` | `landed` |
| Customer/Accountant workspace | list/detail failure صریح؛ confirm تا receipt باز؛ duplicate قفل و retry context-preserving | `bea58d4e` و `f897a6fa` | `landed` |
| Admin/UserManager/Invitation/Commodity | حفظ rows/draft؛ direct-detail failure بدون redirect دروغین؛ receipt همان entity | `6f12fbfc` | `landed` |
| UserProfile sensitive actions | confirm کنترل‌شده، serialization سراسری per-user، receipt schema-faithful و truth deadline/lock | `8a3ff751` | `landed` |
| Invite/Login/Register/SetupPassword | درخواست public bounded، snapshot/epoch immutable و stale-response rejection | `43df38fc` | `landed` |
| visual route source truth | fixture دقیق self profile، دعوت pending معتبر و config bot | `c5fc5699` | `landed` |

## ممنوعیت‌های رعایت‌شده

- global token/selectors، shell، navigation یا معماری بصری بازطراحی نشد؛
- Market/Messenger، widget بازار Home و پشتیبان‌های protected آن‌ها تغییر نکردند؛
- total اعلان، delivery success یا quota enforcement از داده ناکافی جعل نشد؛
- snapshot تصویری برای پنهان‌کردن mismatch update نشد.

## carry-forward backend

| محدودیت | حقیقت فعلی | مالک بعدی |
| --- | --- | --- |
| notification history | endpoint فقط آخرین ۵۰ را می‌دهد و total ندارد | Stage 4 |
| freshness/revision | revision/freshness سروری وجود ندارد؛ `lastSucceededAt` فقط in-memory است | Stage 4/8 |
| sessions | inventory محلی per-server است | Stage 4 |
| generic admin receipts | قرارداد receipt بین endpointها یکنواخت نیست | Stage 6 |
| independent warning action | capability مستقل «ارسال هشدار» وجود ندارد | Stage 6/backend |
| permanent quantity quota | null/zero/unbounded ممکن است enforce نشود | Stage 6/backend |
| invitation/SMS delivery | status/accepted فعلی delivery receipt قطعی نیست | Stage 6/backend |

## گیت خروج

گیت Stage 1 با تست‌های fresh، typecheck/build/guard، viewport acceptance، protected source diff صفر و ثبت صریح visual carry-forward پاس شد. Stage 2 مجاز و هنوز شروع‌نشده است.
