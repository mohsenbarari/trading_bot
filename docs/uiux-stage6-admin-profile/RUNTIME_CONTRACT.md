# Stage 6 Runtime Contract — delivered Phase 1/2/3

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

## مرزهای صریح

این contract فقط behavior تحویل‌شده در commit `3283a6e38209cb06d352740dae5b05bce5ba9002` را توصیف می‌کند. invitation management، persistenceهای دیگر، protected Messenger internals، Sites، staging، production و closure کلی Stage 6 را authorize یا اثبات نمی‌کند.
