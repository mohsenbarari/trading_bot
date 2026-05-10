# برنامه جامع تست UI پیام‌رسان

> تاریخ: 2026-05-10  
> وضعیت: Phase 1 انجام شد  
> نوع تست: E2E UI Only  
> ابزار اجرا: Playwright در مرورگر واقعی و به‌صورت `--headed`

---

## هدف

این سند، نقشه اجرایی کامل تست UI ماژول پیام‌رسان Vue 3 PWA است.  
تمام سناریوهای این برنامه باید فقط از طریق رابط کاربری واقعی مرورگر تست شوند؛ یعنی:

- تعامل با عناصر بصری واقعی
- کلیک، تایپ، اسکرول، انتخاب، باز/بسته کردن modal و menu
- بررسی stateهای دیداری مثل loader، spinner، badge، toast، empty state، error state، selection state
- بررسی تغییرات UI پس از عملیات کاربر
- اجرای تست‌ها فقط در Playwright و فقط در محیط مرورگر واقعی

موارد زیر در این برنامه عمداً خارج از scope هستند مگر وقتی از UI قابل مشاهده باشند:

- assertion مستقیم روی لایه backend بدون عبور از UI
- تست unit یا integration غیر UI
- تست API خام بدون تعامل رابط کاربری

---

## اصول اجرای این برنامه

- هر فاز فقط بعد از تایید کاربر شروع می‌شود.
- برای هر فاز، ابتدا شرح فارسی دقیق از flowهای UI همان فاز ارائه می‌شود.
- سپس فایل spec همان فاز در `frontend/e2e/` ساخته می‌شود.
- اجرای هر فاز با دستور headed انجام می‌شود:

```bash
npx playwright test frontend/e2e/<phase-file>.spec.ts --headed
```

- اگر تست fail شود، locatorها یا منطق تست باید خودکار اصلاح و مجدداً اجرا شود.
- پس از موفقیت هر فاز، checkboxهای همین فایل به `[x]` تبدیل می‌شوند.

---

## پیش‌نیازهای عمومی

- [ ] محیط برنامه بالا و قابل دسترس باشد.
- [ ] مسیر لاگین و session معتبر برای کاربر تست موجود باشد.
- [ ] حداقل یک کاربر تست برای direct chat موجود باشد.
- [ ] حداقل یک group تست و یک channel تست در صورت نیاز فازهای مدیریتی موجود باشد.
- [ ] داده کافی برای badge، unread state، pinned state، media state و empty state در صورت نیاز فاز فراهم باشد.
- [ ] تست‌ها از locatorهای مدرن و پایدار مانند `getByRole` و `getByTestId` استفاده کنند.
- [ ] تست‌ها با `test.step()` به flowهای شفاف و قابل مشاهده شکسته شوند.
- [ ] هر سناریو تا حد ممکن از منظر کاربر نهایی و فقط از روی UI اعتبارسنجی شود.

---

## فهرست فازها

1. Phase 1: Chat List, Boot, Navigation & Header UI
2. Phase 2: Direct Messaging UI, Composer & Message Actions
3. Phase 3: Media Upload, Preview, Download & Native File Handling UI
4. Phase 4: Group / Channel Room UI, Permissions & Manager Screens
5. Phase 5: Search, Reactions, Unread, Pin, Mute & Notification UI
6. Phase 6: Error States, Offline/Retry, Blocked/Read-Only & Recovery UI

---

## Phase 1: Chat List, Boot, Navigation & Header UI

### هدف فاز

اعتبارسنجی کل UI ورود کاربر به پیام‌رسان، لود اولیه، لیست گفتگوها، انتخاب گفتگو، بازگشت، menuهای header و routeهای پایه.

### خروجی این فاز

- فایل پیشنهادی: `frontend/e2e/messenger-phase-1-chat-list-navigation.spec.ts`
- نوع تست: purely visual interaction

### چک‌لیست سناریوها

- [ ] نمایش لودر اولیه پیام‌رسان هنگام باز شدن صفحه گفتگوها
- [ ] حذف صحیح لودر و نمایش conversation list بعد از آماده شدن داده‌ها
- [x] نمایش empty state وقتی گفتگو وجود ندارد یا list خالی است
- [ ] نمایش rowهای گفتگو با نام، preview، timestamp و avatar
- [x] نمایش unread badge روی آیتم‌های دارای پیام نخوانده
- [x] نمایش pinned conversation در جایگاه درست UI
- [x] بررسی واکنش UI به tap روی یک direct chat و باز شدن اتاق گفتگو
- [x] بررسی sync شدن route با گفتگوی انتخاب‌شده
- [x] بررسی بازگشت از یک گفتگو به conversation list از طریق back UI
- [x] بررسی باز و بسته شدن menu سه‌نقطه در header لیست گفتگوها
- [x] بررسی عملکرد آیتم `پروفایل عمومی من` و ورود واقعی به صفحه پروفایل
- [x] بررسی بازگشت از public profile به مسیر درست بدون پرش اشتباه
- [x] بررسی باز شدن modal ساخت گفتگوی جدید از UI مربوطه
- [x] بررسی نمایش صحیح list نتایج کاربرها در modal گفتگوی جدید
- [x] بررسی باز شدن direct chat بعد از انتخاب کاربر از modal شروع گفتگو
- [x] بررسی اینکه close کردن modalها و menuها state بصری را تمیز و بدون overlay باقی‌مانده انجام دهد

### assertionهای UI اجباری

- [ ] visibility لودر، skeleton یا loading surface
- [x] visibility و disappearance صحیح menu / modal / overlay
- [ ] active state روی گفتگوی انتخاب‌شده در لیست
- [x] تغییر title/header بعد از ورود به گفتگو
- [x] عدم بازگشت ناخواسته به صفحه اشتباه بعد از navigation

---

## Phase 2: Direct Messaging UI, Composer & Message Actions

### هدف فاز

اعتبارسنجی UI ارسال پیام، ویرایش، پاسخ، انتخاب، context menu، swipe/tap interactions و stateهای دیداری پیام در direct chat.

### خروجی این فاز

- فایل پیشنهادی: `frontend/e2e/messenger-phase-2-direct-messaging-ui.spec.ts`

### چک‌لیست سناریوها

- [ ] نمایش صحیح اتاق direct chat با history اولیه
- [ ] نمایش textarea / composer و دکمه‌های اصلی پیام
- [ ] تایپ متن و ارسال پیام از UI
- [ ] نمایش optimistic message state یا sending state بلافاصله پس از ارسال
- [ ] جایگزینی sending state با message rendered نهایی
- [ ] بررسی full-width bubble behavior برای message typeهای متنی و غیر media
- [ ] باز شدن context menu با tap یا long-press روی پیام
- [ ] نمایش actionهای مناسب در context menu
- [ ] ورود به edit mode از UI و prefill شدن متن در composer
- [ ] ذخیره edit و مشاهده تغییر متن در bubble
- [ ] ورود به reply mode و نمایش reply banner در composer
- [ ] ارسال reply و نمایش reply context روی bubble جدید
- [ ] ورود به selection mode با long-press
- [ ] انتخاب چند پیام و نمایش selection action bar
- [ ] لغو selection mode از UI/back
- [ ] حذف پیام از UI و مشاهده حذف یا state متناظر در timeline
- [ ] forward یک پیام از طریق modal انتخاب مقصد
- [ ] باز شدن emoji / sticker picker و تغییر وضعیت دیداری آن
- [ ] ارسال sticker-only message و نمایش bubble مناسب
- [ ] نمایش reaction picker و افزودن/حذف reaction از UI
- [ ] به‌روزرسانی visual reaction chips روی پیام

### assertionهای UI اجباری

- [ ] stateهای `sending`, `selected`, `editing`, `replying`
- [ ] نمایش و حذف صحیح bannerهای edit/reply
- [ ] باز و بسته شدن context menu بدون native browser menu
- [ ] به‌روزرسانی شمارش/نمایش reaction در همان bubble

---

## Phase 3: Media Upload, Preview, Download & Native File Handling UI

### هدف فاز

اعتبارسنجی کل flowهای بصری media و file: attachment menu، preview، upload progress، album UI، download state، open/share/download behavior از دید کاربر.

### خروجی این فاز

- فایل پیشنهادی: `frontend/e2e/messenger-phase-3-media-file-ui.spec.ts`

### چک‌لیست سناریوها

- [ ] باز شدن attachment menu از composer
- [ ] نمایش tabهای مربوط به media / file در UI
- [ ] انتخاب یک تصویر از UI و شروع flow ارسال
- [ ] نمایش preview/optimistic bubble برای تصویر
- [ ] نمایش upload progress ring یا spinner
- [ ] حذف progress overlay بعد از completion
- [ ] باز شدن lightbox روی media click
- [ ] بسته شدن lightbox از gesture یا close affordance
- [ ] ارسال چند تصویر و نمایش album layout
- [ ] بررسی stable layout برای album bubble در حین upload
- [ ] ارسال document و نمایش document bubble با metadata
- [ ] نمایش download icon برای file/media حل‌نشده
- [ ] شروع دانلود از UI و مشاهده progress state
- [ ] cancel کردن download از UI اگر affordance موجود است
- [ ] مخفی شدن download icon پس از cache شدن فایل
- [ ] تست share/open UI برای فایل cache شده از طریق دکمه‌های دیداری
- [ ] بررسی اینکه tapping روی فایل cache شده رفتار viewer/share/download صحیح نشان دهد
- [ ] بررسی media error state اگر فایل نامعتبر یا unsupported باشد و UI پیام مناسب بدهد

### assertionهای UI اجباری

- [ ] progress ring / spinner / overlay
- [ ] album grid layout
- [ ] document bubble metadata visibility
- [ ] lightbox toolbar visibility
- [ ] تغییر icon/state بعد از cache/download completion

---

## Phase 4: Group / Channel Room UI, Permissions & Manager Screens

### هدف فاز

اعتبارسنجی UI گروه‌ها و کانال‌ها برای member/admin/owner، read-only stateها، manager flows، member listها، badgeها، role labelها و delete/leave flows.

### خروجی این فاز

- فایل پیشنهادی: `frontend/e2e/messenger-phase-4-groups-channels-ui.spec.ts`

### چک‌لیست سناریوها

- [ ] نمایش group room در conversation list و باز شدن آن از UI
- [ ] نمایش channel room در conversation list و باز شدن آن از UI
- [ ] نمایش header متناسب با نوع room
- [ ] نمایش read-only composer state برای member در channel
- [ ] نمایش writable composer برای admin channel
- [ ] باز شدن manager group از UI header یا menu مربوطه
- [ ] باز شدن manager channel از UI header یا menu مربوطه
- [ ] نمایش overview page با title/description/avatar
- [ ] نمایش member list با rowهای استاندارد و بدون duplicate name
- [ ] نمایش role badgeهای `owner/admin/member` با style کوچک و یکنواخت
- [ ] نمایش members page فقط با actionهای مجاز دیداری
- [ ] نمایش admins page با action buttonهای استاندارد و یکنواخت
- [ ] نمایش promotable members با style یکسان rowها
- [ ] تست add member flow از UI group/channel manager
- [ ] تست promote/demote/remove member از UIهای مجاز
- [ ] تست leave group از UI و مشاهده بسته شدن stateهای مربوطه
- [ ] تست unfollow/delete channel از UI manager
- [ ] بررسی اینکه بعد از حذف/ترک room، کاربر روی room حذف‌شده باقی نماند
- [ ] بررسی پاک شدن overlay / modal / stale state بعد از delete/leave
- [ ] بررسی باز شدن direct public profile از روی member row یا sender link در صورت وجود

### assertionهای UI اجباری

- [ ] read-only banner یا disabled composer state
- [ ] manager modal/page transitions
- [ ] success/error toast یا visual feedback پس از member mutation
- [ ] حذف room از conversation list یا بسته شدن room بعد از leave/delete

---

## Phase 5: Search, Reactions, Unread, Pin, Mute & Notification UI

### هدف فاز

اعتبارسنجی stateهای تعاملی و دیداری گفتگوها و پیام‌ها که به تجربه کاربر در استفاده روزمره مربوط‌اند: جستجو، unread، pin، mute، reactions، badgeها و notification-related UI.

### خروجی این فاز

- فایل پیشنهادی: `frontend/e2e/messenger-phase-5-search-unread-pin-mute-ui.spec.ts`

### چک‌لیست سناریوها

- [ ] باز شدن UI جستجو در داخل chat
- [ ] نمایش نتایج جستجو و navigation بین resultها
- [ ] هایلایت شدن پیام هدف در chat
- [ ] بازگشت از search mode به chat عادی
- [ ] pin کردن گفتگو از long-press menu لیست گفتگوها
- [ ] تغییر جایگاه بصری pinned conversation در list
- [ ] unpin کردن گفتگو و بازگشت به ordering عادی
- [ ] mute کردن گفتگو از UI
- [ ] تغییر indicator دیداری muted state در row گفتگو
- [ ] اطمینان از باقی ماندن unread badge برای گفتگوی mute شده بدون toast مزاحم در UI در صورت امکان مشاهده‌پذیر
- [ ] mark as unread از UI long-press menu
- [ ] ظهور unread badge در row همان گفتگو
- [ ] باز کردن گفتگو و صفر شدن unread badge
- [ ] باز کردن reaction picker و بررسی quick reactions + expanded state
- [ ] تغییر reaction فعلی کاربر و به‌روزرسانی chipها
- [ ] باز شدن notification center از UI global app
- [ ] مشاهده لیست notificationها در view مربوطه
- [ ] mark as read / delete / clear all از UI notification center

### assertionهای UI اجباری

- [ ] badge visibility و count changes
- [ ] pinned ordering visual change
- [ ] muted visual state
- [ ] search result highlight and navigation state
- [ ] notification list mutations via UI

---

## Phase 6: Error States, Offline/Retry, Blocked/Read-Only & Recovery UI

### هدف فاز

اعتبارسنجی resilience UI پیام‌رسان: offline/reconnect، full-screen retry state، denied actionها، blocked/read-only behavior، stale selection cleanup و recovery flowها.

### خروجی این فاز

- فایل پیشنهادی: `frontend/e2e/messenger-phase-6-error-recovery-ui.spec.ts`

### چک‌لیست سناریوها

- [ ] نمایش global connecting/retry UI هنگام قطع موقت ارتباط
- [ ] بازگشت خودکار UI بعد از restore شدن ارتباط
- [ ] نمایش full-screen retry state فقط در شرایط واقعی خطا
- [ ] خروج از retry state با اقدام کاربر یا recovery خودکار
- [ ] نمایش read-only یا blocked state وقتی کاربر اجازه ارسال ندارد
- [ ] جلوگیری دیداری از ارسال در stateهای forbidden
- [ ] تست fail شدن action و نمایش پیام/feedback مناسب در UI
- [ ] بررسی اینکه modal/menu پس از error در حالت گیرکرده باقی نماند
- [ ] تست حذف room و بازگشت/back پس از آن بدون باز شدن room حذف‌شده
- [ ] تست stale route cleanup بعد از leave/delete room
- [ ] تست public profile navigation در حضور menu/back-stack بدون bounce
- [ ] تست empty timeline / missing conversation recovery

### assertionهای UI اجباری

- [ ] reconnect banner / retry affordance
- [ ] disabled composer or restricted action state
- [ ] disappearance صحیح error overlay بعد از recovery
- [ ] عدم navigation اشتباه به room یا route حذف‌شده

---

## ماتریس فایل‌های spec پیشنهادی

- [ ] `frontend/e2e/messenger-phase-1-chat-list-navigation.spec.ts`
- [ ] `frontend/e2e/messenger-phase-2-direct-messaging-ui.spec.ts`
- [ ] `frontend/e2e/messenger-phase-3-media-file-ui.spec.ts`
- [ ] `frontend/e2e/messenger-phase-4-groups-channels-ui.spec.ts`
- [ ] `frontend/e2e/messenger-phase-5-search-unread-pin-mute-ui.spec.ts`
- [ ] `frontend/e2e/messenger-phase-6-error-recovery-ui.spec.ts`

---

## وضعیت اجرای فازها

- [ ] Phase 1 کامل شد
- [ ] Phase 2 کامل شد
- [ ] Phase 3 کامل شد
- [ ] Phase 4 کامل شد
- [ ] Phase 5 کامل شد
- [ ] Phase 6 کامل شد

---

## تعریف موفقیت نهایی

- [ ] تمام فازها به‌صورت headed اجرا و pass شده باشند
- [ ] تمام سناریوها از طریق UI واقعی مرورگر اعتبارسنجی شده باشند
- [ ] برای هر فاز spec جداگانه و قابل نگهداری وجود داشته باشد
- [ ] تمام checkboxهای این سند پس از پایان پروژه تیک خورده باشند
- [ ] suite نهایی بتواند regressionهای اصلی Messenger UI را با اتکا به رفتار بصری کشف کند