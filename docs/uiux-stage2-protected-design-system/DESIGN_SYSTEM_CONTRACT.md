# Stage 2 — قرارداد Design System V2 محافظت‌شده

وضعیت قرارداد: `stage2_complete_stage3_authorized_not_started`

این سند مرز canonical میان Figma و runtime را تعریف می‌کند. اجرای Stage 2 foundation-only و opt-in است؛ هیچ بند این سند مجوز migration route یا تغییر protected interior نیست.

## ۱. منابع canonical و تقدم شواهد

ترتیب تقدم در تعارض:

1. Figma editable frozen در فایل `z8jgJxST4O2APzWnlyP9gv`، صفحه `208:2` و root `208:3`؛
2. قراردادهای machine-readable در `frontend/src/design-system-v2/scope-manifest.json`، `frontend/src/design-system-v2/canonical-token-contract.json` و `frontend/src/router/uiRouteContract.ts`؛
3. tokenهای runtime در `frontend/src/styles/design-system-v2.tokens.css`؛
4. behavior scoped در `frontend/src/styles/design-system-v2.components.css`؛
5. catalog اجرایی خصوصی؛
6. exportها، HTML evidence و Sites به‌عنوان مشتق بازبینی، نه منبع طراحی.

freeze رسمی Stage 2 در `2026-08-09T01:27:37.567Z` و reread canonical آن در `2026-08-09T01:24:59Z` ثبت شده است. root برابر `1440×13028` و بخش `Section/02` با node `213:2` برابر `1280×2092` است. direct audit/exportها، local evidence hash-bound و Sites خصوصی source-bound همگی پاس هستند.

## ۲. قرارداد activation و namespace

دو scope مجاز:

```css
[data-ui-system="v2"]
[data-ui-system="v2-portal"]
```

- scope اول برای subtree معمول محصول یا proof خصوصی است؛
- scope دوم فقط برای hostهای Teleport است و attach/release آن باید reference-counted، idempotent و fail-closed باشد؛
- tokenها فقط با prefix `--ui-v2-*` تعریف و مصرف می‌شوند؛
- تعریف V2 روی `:root`، `html`، `body` یا selector عمومی `*` ممنوع است؛
- sibling escape با `+` یا `~`، selector functional ناقص و activation خارج از subtree ممنوع است؛
- تعریف یا remap `--ds-*` در CSS مربوط به V2 ممنوع است؛
- import stylesheet به‌تنهایی نباید ظاهر legacy یا protected را تغییر دهد.

## ۳. inventory دقیق foundations

### ۳.۱. Primitives — ۲۰ متغیر

| token | مقدار frozen |
| --- | --- |
| `--ui-v2-neutral-ink-950` | `#0f233c` |
| `--ui-v2-neutral-ink-900` | `#12314a` |
| `--ui-v2-neutral-ink-700` | `#52697b` |
| `--ui-v2-neutral-ink-500` | `#94a3b8` |
| `--ui-v2-neutral-border-300` | `#8091a3` |
| `--ui-v2-neutral-surface-100` | `#f4f7fa` |
| `--ui-v2-neutral-surface-50` | `#f8fafc` |
| `--ui-v2-neutral-white` | `#ffffff` |
| `--ui-v2-brand-action-600` | `#2f6fed` |
| `--ui-v2-brand-text-700` | `#2353b5` |
| `--ui-v2-brand-icon-650` | `#315da8` |
| `--ui-v2-brand-subtle-100` | `#e8f0ff` |
| `--ui-v2-danger-strong` | `#b4232c` |
| `--ui-v2-danger-subtle` | `#fdecee` |
| `--ui-v2-warning-strong` | `#8a6110` |
| `--ui-v2-warning-subtle` | `#fff4d8` |
| `--ui-v2-info-strong` | `#176b8c` |
| `--ui-v2-info-subtle` | `#e8f5fa` |
| `--ui-v2-success-strong` | `#0f766e` |
| `--ui-v2-success-subtle` | `#eaf8f3` |

### ۳.۲. Semantic — ۲۶ alias

| token | alias canonical |
| --- | --- |
| `--ui-v2-color-surface-page` | `--ui-v2-neutral-surface-100` |
| `--ui-v2-color-surface-card` | `--ui-v2-neutral-white` |
| `--ui-v2-color-surface-subtle` | `--ui-v2-neutral-surface-50` |
| `--ui-v2-color-surface-brand-soft` | `--ui-v2-brand-subtle-100` |
| `--ui-v2-color-text-primary` | `--ui-v2-neutral-ink-900` |
| `--ui-v2-color-text-strong` | `--ui-v2-neutral-ink-950` |
| `--ui-v2-color-text-secondary` | `--ui-v2-neutral-ink-700` |
| `--ui-v2-color-text-placeholder` | `--ui-v2-neutral-ink-700` |
| `--ui-v2-color-text-on-action` | `--ui-v2-neutral-white` |
| `--ui-v2-color-text-action` | `--ui-v2-brand-text-700` |
| `--ui-v2-color-border-default` | `--ui-v2-neutral-border-300` |
| `--ui-v2-color-border-focus` | `--ui-v2-brand-action-600` |
| `--ui-v2-color-action-primary` | `--ui-v2-brand-action-600` |
| `--ui-v2-color-icon-brand` | `--ui-v2-brand-icon-650` |
| `--ui-v2-color-status-danger-bg` | `--ui-v2-danger-subtle` |
| `--ui-v2-color-status-danger` | `--ui-v2-danger-strong` |
| `--ui-v2-color-status-warning-bg` | `--ui-v2-warning-subtle` |
| `--ui-v2-color-status-warning` | `--ui-v2-warning-strong` |
| `--ui-v2-color-status-info-bg` | `--ui-v2-info-subtle` |
| `--ui-v2-color-status-info` | `--ui-v2-info-strong` |
| `--ui-v2-color-status-success-bg` | `--ui-v2-success-subtle` |
| `--ui-v2-color-status-success` | `--ui-v2-success-strong` |
| `--ui-v2-color-action-secondary` | `--ui-v2-neutral-white` |
| `--ui-v2-color-action-disabled` | `--ui-v2-neutral-surface-50` |
| `--ui-v2-color-action-danger` | `--ui-v2-danger-strong` |
| `--ui-v2-color-text-disabled` | `--ui-v2-neutral-ink-500` |

alias cycle، alias شکسته و alias خارج از namespace مجاز نیست. contrast placeholder روی سفید `5.729:1` است؛ border default روی سفید `3.232:1` و روی سطح صفحه `3.006:1` است. تغییر این مقادیر باید دوباره audit و در manifest ثبت شود.

freeze runtime دقیقاً `65` token canonical و `43` implementation tuple دارد؛ یعنی `108` definition tuple و `106` نام یکتا. دو tuple اضافه همان overrideهای `--ui-v2-motion-micro` و `--ui-v2-motion-state` در `prefers-reduced-motion` هستند. هر تعریف، مقدار یا context خارج از `canonical-token-contract.json` drift و خطای guard است.

### ۳.۳. Dimensions — ۱۹ متغیر

| گروه | tokenها |
| --- | --- |
| spacing | `2 / 4 / 8 / 12 / 16 / 20 / 24 / 32px` |
| radius | `8 / 12 / 14 / 16 / 20 / 9999px` |
| minimum size | target `44px`، CTA `48px`، bottom nav `80px` |
| stroke | standard `1px`، focus `3px` |

نقش‌های radius رسمی `control=12`، `card=14` و `panel=20` هستند؛ `compact=8`، `container=16` و `full=9999` برای سازگاری تخصصی حفظ می‌شوند. مقدار طراحی hard-coded در componentهای V2 مجاز نیست و باید از token استفاده شود.

### ۳.۴. icon scale اجرایی

icon scale از spacing canonical مشتق می‌شود و token مستقل شصت‌وششم ایجاد نمی‌کند:

| نقش | اندازه | node proof | variable binding |
| --- | ---: | --- | --- |
| small | `16px` | `267:24` | `39:28` |
| control | `20px` | `267:29` | `39:29` |
| large | `24px` | `267:34` | `39:30` |

هر سه اندازه باید در catalog runtime به‌صورت computed geometry قابل اندازه‌گیری باشند؛ glyph متنی، width/height hard-coded خارج از mapping و scale دلخواه component مجاز نیست.

## ۴. typography، effect و motion

- تنها font family محصول در V2 برابر `Vazirmatn` با fallbackهای `Tahoma, Arial, sans-serif` است؛
- `10` نقش متن frozen عبارت‌اند از Page، Section، Card، Body Medium، Body Small، Label Medium، Label Small، Action Medium، Caption و Avatar Initial؛
- style تازه `UIUX v2/Avatar/Initial` برابر Vazirmatn Bold `16` با line-height خودکار است و روی nodeهای `51:16` و `51:27` bind شده است؛
- دو effect canonical برابر card و overlay هستند؛
- durationهای motion برابر `140ms` برای micro و `180ms` برای state هستند؛
- در `prefers-reduced-motion: reduce` هر دو duration به `1ms` کاهش می‌یابند، iteration به یک محدود می‌شود، scroll behavior خودکار و transform تزئینی حذف می‌شود؛
- هیچ قابلیت یا فهم state نباید به motion وابسته باشد.

## ۵. component catalog frozen

catalog Figma دقیقاً `12` set و `56` variant دارد و proof Stage 2 شامل `56` reference سطح اول و `6` reference تو‌در‌تو با detached instance برابر صفر است.

| component set | node | variant |
| --- | --- | ---: |
| Button | `48:14` | 6 |
| Status | `49:14` | 4 |
| Relation Row | `50:26` | 3 |
| Authenticated Header | `51:33` | 2 |
| Bottom Navigation | `52:46` | 6 |
| Form Field | `77:610` | 12 |
| Admin User Row | `78:566` | 3 |
| Standard Invitation Row | `80:574` | 2 |
| Decision Panel | `81:566` | 2 |
| Account Action Row | `121:14` | 2 |
| Session Row | `122:1327` | 8 |
| Notification Row | `123:1330` | 6 |

catalog runtime فقط proof خصوصی و synthetic است و باید:

- از primitiveهای عمومی موجود استفاده کند؛
- پنج state مرجع normal/loading/disabled/error/destructive را قابل اجرا نشان دهد؛
- focus، motion، reduced motion، dialog portal و responsive را قابل اندازه‌گیری کند؛
- icon scale را دقیقاً در سه اندازه `16/20/24px` اجرا کند؛
- list را با ساختار native `ul > li` بسازد؛ item تعاملی باید `button` و item غیرتعاملی باید `article` بماند و هیچ `role` جایگزینی مجاز نیست؛
- در `frontend/src/components/ui/index.ts` export نشود؛
- در router تولید route نداشته باشد؛
- هیچ import مستقیم از protected interior یا copy مختص Market/Messenger نداشته باشد.

## ۶. قرارداد route و سطح محافظت

در Stage 2 هر `29` route زیر `v2Scope: off` است:

| protection | routeها |
| --- | --- |
| full | `/market`، `/chat`، `/share-receive`، `/admin/channels` |
| mixed | `/`، `/admin/messages`، `/admin/system` |
| none | `/setup-password`، `/login`، `/operations`، `/operations/customers`، `/operations/customers/:relationId`، `/operations/accountants`، `/operations/accountants/:relationId`، `/account`، `/account/security`، `/account/storage`، `/account/notifications`، `/users/:id`، `/profile`، `/settings`، `/admin`، `/admin/invitations`، `/admin/users`، `/admin/users/:id`، `/admin/commodities`، `/i/:code`، `/register`، `/notifications` |

interiorهای mixed:

| route | interior محافظت‌شده |
| --- | --- |
| `/` | `home-market-widget` |
| `/admin/messages` | `admin-messages-market-delivery` و `admin-messages-messenger-delivery` |
| `/admin/system` | `trading-settings-market-controls` |

فعال‌سازی whole-route روی route mixed، هر فعال‌سازی روی route full، catalog route تو در تو یا مستقیم، و activation helper در source محصول در Stage 2 خطا است.

## ۷. responsive، دسترس‌پذیری و overflow

- عرض‌های مرجع موبایل: `360`، `375`، `390`، `414` و `430`؛
- proof دسکتاپ: دقیقاً `1440×900`؛
- touch target حداقل `44×44` و CTA حداقل `48px` ارتفاع؛
- audit canonical Figma باید `66` target با failure صفر، `10` نمونه focus و product activation/protected interior برابر صفر را نگه دارد؛
- label ناوبری حداقل `11px`؛
- contrast متن عادی حداقل `4.5:1` و non-text/focus حداقل `3:1`؛
- focus ring دقیقاً `3px` با offset `2px` است و باید بر cascade legacy غلبه کند؛
- overflow افقی root، landmark و content در تمام عرض‌های مرجع باید صفر باشد.

وجود قاب در Figma این بندها را برای browser اثبات نمی‌کند؛ browser verifier و metrics واقعی برای closure لازم‌اند.

## ۸. guard fail-closed

guard Stage 2 باید دست‌کم این تخلف‌ها را رد کند:

- selector V2 بدون scope، global selector یا sibling escape؛
- raw color شامل hex، named color و تابع‌های modern color خارج از token source canonical؛
- طول طراحی hard-coded در componentهای V2؛
- class محلی غیرcanonical یا primitive تکراری؛
- style set خالی، token تکراری/تعریف‌نشده و alias cycle؛
- تعریف یا remap legacy `--ds-*`؛
- drift میان router، route contract و scope manifest؛
- route catalog مستقیم/تو‌در‌تو، route expression پویا، computed key یا spread/identifier حل‌نشده؛
- mutation رجیستری با `addRoute/removeRoute/clearRoutes` حتی اگر نام‌ها تکه‌تکه یا dynamic ساخته شوند؛
- فعال‌سازی literal، helper-based، DOM/dataset تکه‌تکه یا computed در product source؛
- import/render مستقیم یا dynamic catalog، حتی با رشته تکه‌تکه و در هر script block فایل Vue؛
- activation در HTML entrypoint.

## ۹. rollback و progression

rollback Stage 2 باید با حذف provider/helper، token/component stylesheet، catalog خصوصی، route/scope/canonical-token contract و guardهای افزایشی ممکن باشد؛ چون هیچ product route فعال نشده، rollback نباید migration صفحه‌ای معکوس کند.

`WorkspaceShell` و خانواده `ds-workspace-*` در Stage 2 تغییر یا فعال نشده‌اند. تبدیل آن‌ها به adapter V2 یک carry-forward صریح Stage 4 است و Stage 3 نیز نباید آن را ضمن تغییر shell عمومی جذب کند.

validation ledger، protected diff، artifact hashها، evidence محلی و Sites همگی بسته شده‌اند. تصمیم progression نهایی:

```text
stage2Status = complete
stage2TechnicalGate = passed_with_preexisting_full_typecheck_parity
stage2EvidenceStatus = passed
stage2SitesStatus = passed
nextAuthorizedRuntimeStage = Stage 3
stage3RuntimeImplementationAuthorized = true
stage3RuntimeWorkStarted = false
```

Stage 3 مجاز اما runtime آن هنوز شروع نشده است. `WorkspaceShell` و `ds-workspace-*` همچنان carry-forward Stage 4 هستند.
