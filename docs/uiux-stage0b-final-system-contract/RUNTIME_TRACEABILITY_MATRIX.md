# Stage 0B-6 — ماتریس traceability تا Stage 8

وضعیت: `stage0b6_complete_stage1_authorized`؛ Stage 1 next authorized و هنوز runtime edit آن شروع‌نشده است

این ماتریس «مالک بعدی هر تعهد» را مشخص می‌کند. مالک در `2026-08-08T20:57:28.073Z` قرارداد و ادامه بی‌وقفه roadmap را بدون نیاز به تأیید جداگانه هر Stage مجاز کرد و closure Sites/source binding در `2026-08-08T21:07:38Z` پاس شد. ثبت تعهد در اینجا جای گیت فنی هر Stage نیست؛ Stage 1 اکنون مجاز است اما هنوز شروع‌نشده است.

## گیت‌های داخل خود `0B-6`

| شناسه | تعهد | evidence | وضعیت |
| --- | --- | --- | --- |
| `G-0B6-AUTH` | canonical کردن Auth در Figma رسمی | root `168:2017`، fact parity و binding audit | `passed` |
| `G-0B6-HOME` | audit/rebind صفحه Home | root `168:2018`، صفر alias/binding شکسته و detached instance | `passed` |
| `G-0B6-NAV` | رفع بدهی Operations-active navigation | root `168:2079`، focus/layout/style و `44/11/3:1/3px` | `passed` |

## Stage 1 — اعتماد و تداوم کار

| carry-forward | سطح‌ها | گیت runtime |
| --- | --- | --- |
| loading/error/empty/offline/stale/reconnecting distinct | همه routeهای مجاز | هیچ API failure به blank، false empty یا infinite loading ختم نشود |
| retry نزدیک، busy guard و feedback همان context | فرم‌ها، workspace، admin، account | duplicate mutation صفر و result قابل مشاهده |
| حفظ input/context در failure | Auth، workspace، profile/admin forms | test شکست/تلاش دوباره بدون از دست‌رفتن داده |
| notification failure/reconnect | account notifications | آخرین ۵۰ refetch + ID dedupe؛ بدون ادعای total/history کامل |
| home identity failure | `/` | error/retry cause-neutral به‌جای blank |

Stage 1 حق تغییر سیستم بصری global یا protected surface ندارد.

## Stage 2 — Design System V2 محافظت‌شده

| carry-forward | سطح‌ها | گیت runtime |
| --- | --- | --- |
| tokenهای color/type/spacing/radius/effect | routeهای V2 مجاز | parity با Figma و contrast audit |
| component stateها | button/form/list/status/feedback/overlay | normal/loading/disabled/error/destructive قابل تست |
| route-scoped activation | همه routeهای مجاز | هیچ selector/token leak به `/market`، `/chat`، `/share-receive`، `/admin/channels` |
| خانواده component مرجع | `ui-*` و adapterهای موجود | component تکراری تازه و hard-code guard پاس |
| avatar initials text-style debt | account/profile | Vazirmatn/fit/contrast و style disposition قطعی |

## Stage 3 — پوسته، ورود و جریان‌های عمومی

| carry-forward | routeها | گیت runtime |
| --- | --- | --- |
| public/focused/standard shell | `/login`، `/i/:code`، `/register`، `/setup-password` و authenticated routes | shell اشتباه یا navigation نشت‌کرده صفر |
| Auth canonical implementation | `/login`، `/i/:code`، `/register`، `/setup-password` | keyboard/autocomplete/focus/back/refresh/error persistence |
| registration payload minimization | registration endpoints | token/phone حساس فراتر از نیاز در payload/log/UI افشا نشود |
| catch-all system-owned | `/:pathMatch(.*)*` | 404/forbidden/deep-link failure بدون blank/loop |
| PWA timing/layer | `/` پس از load سالم | public/loading/offline/security modal را نمی‌پوشاند |

## Stage 4 — هسته استفاده روزانه

| carry-forward | routeها | گیت runtime |
| --- | --- | --- |
| Home fact/state contract | `/` | widget بازار byte/visual behavior-preserved و فقط shell slot تغییر کند |
| Operations role destinations | `/operations` | dead destination/count/permission explanation صفر |
| canonical account routes | `/account*`، `/settings`، `/notifications` | redirectهای legacy درست و back behavior واحد |
| session/storage action truth | `/account/security`، `/account/storage` | revocation/cleanup واقعی، busy/success/failure و scope صادقانه |
| notification/Push truth | `/account/notifications` | last-50-not-total، route-less noninteractive و ۹ state Push |
| session inventory truth | `/account/security` | local per-server، بدون merge یا `home_server` |

## Stage 5 — workspace مشتریان و حسابداران

| carry-forward | routeها | گیت runtime |
| --- | --- | --- |
| mobile list XOR detail | `/operations/customers*`، `/operations/accountants*` | overlap صفر و back context پایدار |
| query/filter/scroll/selection restore | همان | navigation و retry task را از ابتدا شروع نکند |
| financial before/after + future-only truth | accountant/customer detail | payload و copy با backend contract برابر |
| terminate/unlink/delete cascade | detail actions | confirm scope، authority، result و failure race تست شود |
| desktop master/detail parity | viewport `1440` | fact اضافه نسبت به موبایل صفر |

## Stage 6 — مدیریت و پروفایل

| carry-forward | routeها | گیت runtime |
| --- | --- | --- |
| authoritative pending counts/pagination | `/admin*` | count تزئینی یا ناقص به‌عنوان total صفر |
| permission matrix enforcement | `/admin/users*`، invitations/actions | self/same-level/middle-manager و super-admin invite backend tests |
| هشدار مشترک bot/web | admin user actions + bot | یک authoritative record، forward-or-fail و visibility مشترک |
| delivery receipt truth | warning/invitation/channel delivery | success فقط با receipt؛ partial/pending/failed صادقانه |
| finite permanent quota | restriction action | مقدار محدود، مثبت و enforceable؛ null/zero/unbounded موفق نیست |
| PII visibility | `/profile`، `/users/:id`، `/admin/users/:id` | normal masked/hidden؛ self/admin detail permission-bound |
| protected admin interiors | `/admin/channels` و بخش‌های market/messenger | visual/behavior diff صفر |

## Stage 7 — motion، دسترس‌پذیری و polish

| carry-forward | سطح‌ها | گیت runtime |
| --- | --- | --- |
| motion `140/180ms` | micro/component-state | motion فهم state را بهتر کند و قابلیت به آن وابسته نباشد |
| reduced motion | کل V2 | transform/slide/scale غیرضروری حذف و progress قابل درک بماند |
| keyboard/focus/live region | forms, rows, tabs, dialogs, feedback | ترتیب focus، trap/restore، name و announcement تست شود |
| touch/type/contrast | همه routeهای مجاز | `44/48/11/4.5:1/3:1/stroke 3` پاس |
| zoom/long Persian/dense data | موبایل و desktop | zoom `200%`، overflow/clipping صفر |

## Stage 8 — پذیرش و rollout

| carry-forward | ماتریس | گیت نهایی |
| --- | --- | --- |
| نقش | guest/member/customer/accountant/group lead/middle/super admin | task مجاز و forbidden هر نقش پاس |
| viewport | `360/375/390/414/430/768/1024/1440` | layout، safe area و parity پاس |
| state/network | loading/empty/normal/dense/error/slow/offline/stale | recovery و حقیقت state پاس |
| interaction/environment | touch/keyboard/screen-reader smoke/zoom/reduced-motion/PWA/WebView | blocker دسترس‌پذیری صفر |
| protected freeze | Market/Messenger | baseline visual/behavior diff صفر |
| rollout | cohort محدود → مشاهده → گسترش | telemetry، rollback و تأیید انسانی مالک |

## هشت تصمیم مالک و owner اجرایی

| تصمیم | owner اصلی |
| --- | --- |
| Figma canonical + Auth/Home binding | `0B-6`؛ implementation در Stage 2/3 |
| session local per-server | Stage 4 |
| notification refetch last 50 + dedupe | Stage 1/4 |
| PII masked جز self/admin detail مجاز | Stage 6 |
| bot authority forward-or-fail | Stage 6 |
| delivery success only with receipt | Stage 6 |
| route-scoped V2/no protected leak | Stage 2 و regression تا Stage 8 |
| approval `0B-6` authorizes continuous progression after technical closure | governance و گیت فنی Stageهای 1 تا 8 |

## حدود اثبات Phase 0

Figma، PNG، HTML harness و Sites نمی‌توانند موارد زیر را ثابت کنند: enforcement مجوز، API mutation، revocation، delivery receipt، cross-server sync، reconnect واقعی، keyboard، screen reader، focus restore، race شبکه یا rollback production. این موارد فقط در Stage مالک خود با تست runtime پذیرفته می‌شوند.

## وضعیت مجوز

```text
ownerSystemContractApproval.status = approved
ownerSystemContractApproval.approvedAt = 2026-08-08T20:57:28.073Z
continuousProgressionAuthorized = true
runtimeImplementationAuthorized = true
nextAuthorizedRuntimeStage = Stage 1
stage1RuntimeWorkStarted = false
```

Stage 1 next authorized است. ادامه Stageهای بعدی به گیت فنی خودشان وابسته است، نه تأیید جداگانه مالک؛ مگر اینکه مالک صریحاً توقف کند.
