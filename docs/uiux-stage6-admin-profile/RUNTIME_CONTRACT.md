# Stage 6 Runtime Contract — delivered Phase 1–9

## اصول غیرقابل‌جایگزین

- backend projection و backend authorization منبع حقیقت‌اند؛ hidden/disabled client control مجوز نیست.
- URL، browser history و storage نباید حامل PII یا authority context باشند.
- loading، empty، error، 403 و 404 حالت‌های جدا هستند؛ recovery نباید detail را نشت دهد.
- تغییر Messenger/Forward discovery خارج از این scope است.

## Phase 1 — Admin landing

- `AdminPanel` فقط مقصدهای حقیقی و مجاز نقش جاری را نشان می‌دهد.
- action key، label، icon، role filter و navigation contract موجود حفظ می‌شود.
- heading/access-note تکراری، accordion صرفاً فشرده و count/badge بدون receipt authoritative حذف‌اند.
- هیچ API، route guard، permission contract یا child workflow تازه‌ای به این slice افزوده نشده است.

## Phase 2 — directory و route context

| سطح | قرارداد تحویل‌شده |
| --- | --- |
| `/admin/users` | list semantic و keyboard-accessible با abort/generation safety، empty/error/retry و جست‌وجوی auth-scoped/session-local. |
| `/admin/users/:id` | detail از دادهٔ مجاز server بارگذاری می‌شود؛ 403/404 به recovery عمومی و bounded می‌رسد. |
| context | فقط `scroll` غیرمنفی/integer در URL مجاز است؛ `q`، `account_name` و دادهٔ هویتی در URL/history/storage serialize نمی‌شوند. |
| responsive | زیر breakpoint list/detail به‌شکل جدا و در desktop به‌شکل adaptive master/detail دیده می‌شود؛ query-only transition remount race ایجاد نمی‌کند. |

## Phase 3 — privacy و authority

### Projection privacy

| Viewer/target relation | آنچه تحویل شده است |
| --- | --- |
| peer عادی → public profile | mobile server-masked؛ address، presence، membership، relation و trade detail در projection نیستند. |
| self | contact و address موردنیازِ مجاز حفظ شده و affordance ویرایش address موجود است. |
| administratorِ مجاز | فقط projection موردنیازِ role که server مجاز کرده است؛ client حق بازسازی فیلد حذف‌شده ندارد. |
| 403/404 | پیام و recovery عمومی، retry bounded و بدون شناسه/جزئیات target. |

### Action authority

| Actor → target | outcome سروری |
| --- | --- |
| هر admin → self | forbidden (`403`) برای action حساس. |
| middle admin → هر admin | forbidden (`403`). |
| super admin → super-admin peer | forbidden (`403`). |
| target پایین‌تر و مجاز | تنها پس از check سروری؛ UI به‌تنهایی authority نمی‌دهد. |

### Navigation privacy

- public profile canonical فقط `/users/:id` است؛ direct، notification، toast و browser entry هم همین قرارداد را دارند.
- inbound legacy query قبل از navigation canonical می‌شود؛ account name، highlight، relation و metadata در URL یا history باقی نمی‌ماند.
- directory search در state session-local است؛ تنها `scroll` نرمال‌شده route context است.

### Accessibility و motion

- public profile در 360px reflow می‌شود و controlها target حداقل 44px دارند.
- interactive controlهای profile در `prefers-reduced-motion: reduce` transition مؤثر ندارند.
- browser receipt، focus، overflow، reduced-motion، desktop/mobile و recovery را برای fixtureهای synthetic پوشش می‌دهد؛ این اثبات live backend نیست.

## Phase 4 — invitation management

- invitation URL/token فقط in-memory و با clipboard action صریح است؛ در DOM، URL، history یا storage serialize/render نمی‌شود.
- queue count/KPI ساختگی ندارد؛ `204` receipt حذف row است، `400/404` reconciliation می‌گیرد و `403` queue/dialog/copy state حساس را پاک می‌کند.
- revoke فقط پس از confirm Teleport‌شده به `body` انجام می‌شود؛ focus، Escape، restore-focus و scroll-lock حفظ می‌شوند.

## Phase 5 — public-profile block/unblock

- `window.confirm` و `window.alert` در flow بلاک/رفع‌بلاک وجود ندارند؛ cancel/Escape هیچ mutation ندارد.
- فقط JSON object با `success === true` پس از `POST` یا `DELETE` دقیق `/api/blocks/:id` state محلی را تغییر می‌دهد.
- 400/403/404، network و payload نامعتبر state را حفظ و فقط receipt ثابت، بدون account name/detail/message خام سرور، نشان می‌دهند.

## Phase 6 — workspace account deletion

| سطح | قرارداد تحویل‌شده |
| --- | --- |
| route فعال | فقط `/operations/customers/:relationId` و `/operations/accountants/:relationId`؛ API deletion همچنان `expected_action=delete-account` دقیق دارد. |
| تأیید | dialog به `body` Teleport می‌شود؛ trap-focus، Escape، restore-focus و scroll-lock حفظ می‌شوند؛ نام نمایش‌داده‌شده و acknowledgement لازم‌اند. |
| cancel | cancel یا Escape هیچ DELETE نمی‌فرستد. |
| receipt | فقط receipt همان relation با `status: deleted` navigation/local reconciliation مجاز را فعال می‌کند. |
| recovery | 400/403/404، malformed یا network dialog/relation/route را نگه می‌دارند و فقط متن امن ثابت نمایش می‌دهند؛ raw server detail/message وارد UI نمی‌شود. |

## Phase 7 — پایان امن یک نشست workspace

| سطح | قرارداد تحویل‌شده |
| --- | --- |
| route فعال | همان `/operations/customers/:relationId` و `/operations/accountants/:relationId`؛ API و route contract جدیدی ایجاد نشده است. |
| تأیید | `AppConfirmDialog` body-teleported موجود، trap-focus، Escape، restore-focus و scroll-lock را حفظ می‌کند؛ cancel یا Escape هیچ DELETE نمی‌فرستد. |
| receipt | فقط `terminated_session_id` دقیقاً برابر نشست انتخابی، حذف همان نشست و promotion مجاز نشست باقی‌مانده را فعال می‌کند. |
| recovery | 400/403/404، malformed یا network dialog، route، relation و اطلاعات نمایش‌داده‌شدهٔ نشست را حفظ می‌کنند و فقط پیام ثابت امن نشان می‌دهند؛ `detail`/`message` خام سرور وارد UI، URL، history یا storage نمی‌شود. |

## Phase 8 — بازیابی امن mutation رابطهٔ workspace

| سطح | قرارداد تحویل‌شده |
| --- | --- |
| route فعال | همان `/operations/customers/:relationId` و `/operations/accountants/:relationId`؛ API یا query/route contract تازه‌ای ایجاد نشده است. |
| اقدام‌ها | Customer: `cancel-invitation` و `close-relation`؛ Accountant: `cancel-invitation` و `delete-relation`. |
| تأیید | `AppConfirmDialog` body-teleported موجود، trap-focus، Escape، restore-focus و scroll-lock را حفظ می‌کند؛ cancel یا Escape هیچ DELETE نمی‌فرستد. |
| receipt | cancel فقط با `id` همان relation و `status=revoked`، و close/delete فقط با `id` همان relation و `status=deleted` reconciliation یا navigation را فعال می‌کند. |
| recovery | 400/403/404، wrong-id/wrong-status، malformed یا network dialog، relation، route و query را نگه می‌دارند و فقط متن ثابت امن می‌دهند؛ `detail`/`message` خام سرور در UI، URL، history یا storage نمی‌رود. |

## Phase 9 — mutationهای کالا و نام مستعار با receipt دقیق

| سطح | قرارداد تحویل‌شده |
| --- | --- |
| route فعال | `/admin/commodities`، همان route admin-only موجود از `AdminView` به `CommodityManager`؛ API، router و query contract تازه‌ای ایجاد نشده است. |
| create/update receipt | create کالا فقط `201` + Commodity معتبر با نام درخواست‌شده، edit کالا فقط `200` + همان `id`، create alias فقط `201` + alias درخواست‌شده با parent یکسان، و edit alias فقط `200` + همان alias/parent را می‌پذیرد. |
| حذف | delete کالا یا alias فقط با `204` و body خالی تغییر محلی را اعمال می‌کند. |
| تأیید | `AppConfirmDialog` body-teleported، trap-focus، Escape، restore-focus و scroll-lock را حفظ می‌کند؛ cancel یا Escape هیچ DELETE نمی‌فرستد. |
| recovery | 400/403/404، malformed/mismatch یا network، form/list/selected context و dialog را نگه می‌دارند و فقط feedback ثابت امن می‌دهند؛ detail/message خام سرور وارد UI، URL، history یا storage نمی‌شود. stale detail refresh هنگام بازگشت abort/ignore می‌شود. |

## مرزهای صریح

این contract تا source تحویل‌شدهٔ Phase 9 در commit `2aa32c6d48a8b693de8ff37c310d995a4748efa8` را توصیف می‌کند. persistenceهای دیگر، protected Messenger internals، Sites، staging، production و closure کلی Stage 6 را authorize یا اثبات نمی‌کند.
