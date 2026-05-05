# 🧭 Group & Channel Messenger Specification

> **تاریخ:** 2026-05-05  
> **وضعیت:** Draft for implementation  
> **دامنه:** In-app Messenger (`/api/chat`, `frontend/src/components/ChatView.vue`, related models/services)

---

## فهرست
1. [هدف](#هدف)
2. [تصمیم‌های محصولی نهایی](#تصمیمهای-محصولی-نهایی)
3. [مدل دامنه پیشنهادی](#مدل-دامنه-پیشنهادی)
4. [Schema دیتابیس](#schema-دیتابیس)
5. [Permission Matrix](#permission-matrix)
6. [Business Rules و Edge Cases](#business-rules-و-edge-cases)
7. [API Design](#api-design)
8. [Realtime و Notification Design](#realtime-و-notification-design)
9. [Frontend Refactor Plan](#frontend-refactor-plan)
10. [Migration Strategy](#migration-strategy)
11. [ترتیب پیاده‌سازی کم‌ریسک](#ترتیب-پیادهسازی-کمریسک)
12. [ریسک‌ها و نکات اجرایی](#ریسکها-و-نکات-اجرایی)
13. [Open Question](#open-question)

---

## هدف

هدف این فاز، تبدیل پیام‌رسان فعلی از مدل صرفا `چت دونفره` به یک مدل عمومی‌تر مبتنی بر `chat/room` است، به‌طوری‌که سه نوع فضا پشتیبانی شوند:

1. `direct` - چت دونفره فعلی
2. `group` - گروه چندنفره با نقش‌های `admin/member`
3. `channel` - کانال broadcast که فقط ادمین‌های کانال اجازه ارسال پست دارند

اصل کلیدی:  
**مدل جدید نباید با `if/else`های موقت روی `Conversation.user1/user2` سوار شود؛ بلکه باید یک abstraction جدید برای chat entity ساخته شود و direct chat هم به‌مرور روی همان abstraction منتقل شود.**

---

## تصمیم‌های محصولی نهایی

### گروه

- ساخت گروه برای کاربران آزاد است.
- نقش‌های گروه فقط دو مورد هستند:
  - `admin`
  - `member`
- سازنده گروه، `admin` است.
- همه `admin`ها هم‌سطح هستند.
- فقط `admin` می‌تواند:
  - عضو جدید اضافه کند
  - عضو را اخراج کند
  - `admin` بدهد
  - `admin` بردارد
  - نام گروه را تغییر دهد
- همه اعضای گروه می‌توانند پیام بفرستند.
- تمام قابلیت‌های قابل‌انتقال از چت دونفره باید در گروه هم کار کنند:
  - reaction
  - reply
  - forward
  - album/media upload
  - edit/delete با همان محدودیت‌های موجود
- سقف اعضای گروه در MVP: `50`

### کانال

- ساخت کانال فقط برای `SUPER_ADMIN` مجاز است.
- کانال پیش‌فرض سیستمی با نام اولیه `اطلاع‌رسانی` باید از روز صفر پروژه وجود داشته باشد.
- حتی اگر در لحظه شروع هنوز هیچ `SUPER_ADMIN`ی وجود نداشته باشد، این کانال باید در دیتابیس وجود داشته باشد.
- بعد از ایجاد اولین `SUPER_ADMIN`، این کاربر به کانال اطلاع‌رسانی join می‌شود و نقش `admin` کانال می‌گیرد.
- بعد از ثبت‌نام کامل و فعال شدن هر کاربر، کاربر به کانال اطلاع‌رسانی join می‌شود.
- خروج از کانال اجباری `اطلاع‌رسانی` مجاز نیست.
- کانال‌های بعدی اجباری نیستند.
- بعد از ایجاد هر کانال اختیاری، همان `SUPER_ADMIN` سازنده باید بلافاصله member picker را ببیند.
- member picker باید لیست همه کاربران فعال پروژه را با قابلیت `multi-select` نمایش دهد.
- member picker باید یک گزینه `انتخاب همه` داشته باشد تا همه کاربران فعال پروژه را یکجا برای عضویت انتخاب کند.
- عضویت در کانال‌های اختیاری strictly `invite-only` است.
- هر کاربری که توسط `SUPER_ADMIN` برای یک کانال add/invite نشده باشد، نباید امکان عضویت در آن کانال را داشته باشد.
- در MVP هیچ `join link`، `self-join`، `public discovery` یا `request to join` برای کانال‌های اختیاری وجود ندارد.
- `SUPER_ADMIN` می‌تواند از میان همه کاربران پروژه، بدون توجه به نقش پروژه، `admin` کانال تعیین کند.
- `channel admin` فقط اجازه `post` کردن دارد.
- مدیریت نقش‌های کانال و rename کانال فقط با `SUPER_ADMIN` است.
- اعضای عادی کانال اجازه ارسال پیام ندارند، ولی اجازه گذاشتن `reaction` روی پست‌ها را دارند.

### سیاست‌های سراسری

- `block/unblock` هیچ اثری روی پیام‌رسان ندارد و فقط برای بازار است.
- soft-delete یا اخراج کاربر باید باعث خروج کاربر از همه گروه‌ها و همه کانال‌ها شود.
- پیام‌های قبلی کاربر حذف نمی‌شوند و فقط دسترسی او قطع می‌شود.

---

## مدل دامنه پیشنهادی

مدل فعلی:

- `Conversation(user1_id, user2_id, unread_count_user1, unread_count_user2)`
- `Message(sender_id, receiver_id, ...)`

برای group/channel کافی نیست. مدل پیشنهادی:

### 1. Chat

نماینده یک فضای پیام‌رسانی مستقل:

- direct
- group
- channel

### 2. ChatMember

رابط بین کاربر و chat:

- نقش عضو
- وضعیت عضویت
- زمان join
- آخرین پیام خوانده‌شده
- mute/archive/pin state در صورت نیاز بعدی

### 3. Message

پیام باید به `chat_id` متصل شود، نه فقط `receiver_id`.

### 4. ChatAdminTransfer Policy

قانون انتقال ادمینی باید در service layer enforce شود، نه در UI.

---

## Schema دیتابیس

### جدول `chats`

```text
id                  PK
type                enum('direct', 'group', 'channel')
title               nullable str
description         nullable str
created_by_id       nullable FK users.id
is_system           bool default false
is_mandatory        bool default false
is_deleted          bool default false
deleted_at          nullable datetime
max_members         nullable int
last_message_id     nullable FK messages.id
last_message_at     nullable datetime
created_at          datetime
updated_at          datetime
```

Notes:

- برای `direct`، `title` می‌تواند `null` باشد.
- برای `group`، `title` اجباری است.
- برای `channel`، `title` اجباری است.
- کانال `اطلاع‌رسانی` با `is_system=true` و `is_mandatory=true` ساخته می‌شود.
- `created_by_id` برای کانال سیستمی روز صفر می‌تواند `null` باشد.

### جدول `chat_members`

```text
id                  PK
chat_id             FK chats.id
user_id             FK users.id
role                enum('admin', 'member')
membership_status   enum('active', 'left', 'removed', 'inactive')
joined_at           datetime
left_at             nullable datetime
last_read_message_id nullable FK messages.id
last_read_at        nullable datetime
is_muted            bool default false
created_at          datetime
updated_at          datetime
```

Constraints:

- unique active membership per `(chat_id, user_id)`
- index on `(user_id, membership_status, updated_at)`
- index on `(chat_id, membership_status, role)`

Semantics:

- `active`: عضو فعلی chat
- `left`: کاربر خودش خارج شده
- `removed`: توسط admin یا system خارج شده
- `inactive`: به دلیل soft-delete کاربر یا deactivation سیستم غیرفعال شده

### تغییرات جدول `messages`

ستون‌های جدید:

```text
chat_id             nullable FK chats.id   # مرحله migration: nullable
member_snapshot_name nullable str          # optional, deferred
```

وضعیت نهایی مطلوب:

- `chat_id` مبنای اصلی ارتباط پیام با فضا است.
- `receiver_id` فقط برای migration/compatibility نگه داشته می‌شود و بعدا قابل حذف است.

Indexes جدید پیشنهادی:

```text
ix_messages_chat_window_active (chat_id, created_at, id) where is_deleted = false
ix_messages_chat_sender_active (chat_id, sender_id, created_at)
```

### جدول اختیاری `chat_member_events` (recommended)

برای audit و realtime ساده‌تر، پیشنهاد می‌شود:

```text
id
chat_id
user_id
actor_user_id
event_type enum('joined', 'left', 'removed', 'promoted', 'demoted', 'renamed')
payload json
created_at
```

این جدول در MVP اجباری نیست، اما برای دیباگ و sync مفید است.

---

## Permission Matrix

### Direct Chat

| عملیات | participant |
|---|---|
| مشاهده پیام‌ها | ✅ |
| ارسال پیام | ✅ |
| reaction | ✅ |
| reply/forward/album | ✅ |

### Group

| عملیات | member | admin |
|---|---:|---:|
| مشاهده گروه | ✅ | ✅ |
| ارسال پیام | ✅ | ✅ |
| reaction | ✅ | ✅ |
| reply/forward/album | ✅ | ✅ |
| add member | ❌ | ✅ |
| remove member | ❌ | ✅ |
| promote to admin | ❌ | ✅ |
| demote admin | ❌ | ✅ |
| rename group | ❌ | ✅ |
| leave group | ✅ | ✅* |

`*` آخرین admin اجازه leave ندارد اگر هنوز عضو فعال دیگری در گروه وجود داشته باشد.

### Channel

| عملیات | member | channel admin | SUPER_ADMIN |
|---|---:|---:|---:|
| مشاهده کانال | ✅ | ✅ | ✅ |
| post message | ❌ | ✅ | ✅ |
| reaction | ✅ | ✅ | ✅ |
| reply to channel message | ❌ | ✅ | ✅ |
| forward/album (as post) | ❌ | ✅ | ✅ |
| rename channel | ❌ | ❌ | ✅ |
| assign/remove channel admin | ❌ | ❌ | ✅ |
| create new channel | ❌ | ❌ | ✅ |
| leave mandatory channel | ❌ | ❌ | ❌ |

---

## Business Rules و Edge Cases

### 1. Soft Delete کاربر

وقتی کاربر soft-delete می‌شود:

- تمام `chat_members` فعال او به `inactive` تغییر می‌کند.
- کاربر دیگر در لیست اعضای فعال گروه/کانال دیده نمی‌شود.
- پیام‌های قبلی باقی می‌مانند.
- direct chatها برای طرف مقابل فقط historical باقی می‌مانند.

### 2. تنها admin گروه soft-delete شود

اگر کاربر تنها admin گروه باشد و soft-delete شود:

- از میان اعضای فعال باقی‌مانده، قدیمی‌ترین عضو فعال (`joined_at ASC`, سپس `user_id ASC`) به `admin` ارتقا می‌یابد.
- اگر هیچ عضو فعالی باقی نمانده باشد، گروه `soft-delete/archive` می‌شود.

### 3. آخرین admin گروه بخواهد leave دهد

- اگر هنوز عضو فعال دیگری در گروه وجود دارد، leave ممنوع است.
- پیام خطا:

```text
شما آخرین ادمین گروه هستید. قبل از خروج، یک یا چند ادمین جدید از بین اعضای فعلی انتخاب کنید.
```

- اگر هیچ عضو فعال دیگری باقی نمانده باشد، leave مجاز است و گروه archive می‌شود.

### 4. Remove آخرین admin گروه

- remove آخرین admin توسط admin دیگر مجاز نیست اگر گروه را بی‌ادمین کند.
- باید حداقل یک admin فعال در گروه باقی بماند.

### 5. Leave عضو عادی گروه

- همیشه مجاز است.

### 6. Mandatory channel membership

- کاربر بعد از `register complete + activation` به کانال اطلاع‌رسانی join می‌شود.
- تمام کاربران فعال فعلی نیز در migration اولیه به این کانال اضافه می‌شوند.
- خروج یا leave از این کانال مجاز نیست.

### 7. User deactivation / soft-delete در کانال

- membership کاربر در تمام کانال‌ها به `inactive` تغییر می‌کند.
- اگر کاربر channel admin بوده، role او حذف می‌شود.
- چون `SUPER_ADMIN` همیشه control plane کانال را دارد، کانال orphan نمی‌شود.

### 8. Optional channels

- فقط کانال اطلاع‌رسانی اجباری است.
- کانال‌های بعدی اجباری نیستند.
- عضویت در کانال‌های اختیاری فقط با add/invite مستقیم توسط `SUPER_ADMIN` انجام می‌شود.
- بعد از create channel، flow بعدی باید member picker باشد، نه بازگشت مستقیم به لیست گفتگوها.
- member picker فقط کاربران فعال پروژه (`is_deleted = false`) را نشان می‌دهد.
- گزینه `انتخاب همه` باید همه کاربران فعال پروژه را انتخاب کند، نه فقط آیتم‌های visible در یک صفحه از لیست.
- کاربر invite نشده نباید از هیچ مسیر frontend یا backend بتواند عضو کانال شود.
- اگر بعدا `join link` یا `self-join` اضافه شود، feature جدید و خارج از MVP محسوب می‌شود.

### 9. Block/Unblock

- هیچ اثر backend یا frontend روی messenger ندارد.

---

## API Design

### رویکرد کلان

APIهای جدید باید بر اساس `chat_id` طراحی شوند.  
Endpointهای direct فعلی برای compatibility به‌تدریج روی service جدید سوار می‌شوند.

### Chat CRUD / Listing

#### `GET /api/chat/rooms`

برگشت لیست همه فضاهای کاربر:

- direct
- group
- channel

Projection پیشنهادی:

```json
[
  {
    "id": 123,
    "type": "group",
    "title": "گروه تست",
    "subtitle": "12 عضو",
    "last_message_content": "...",
    "last_message_type": "text",
    "last_message_at": "...",
    "unread_count": 4,
    "can_post": true,
    "is_mandatory": false
  }
]
```

#### `GET /api/chat/rooms/{chat_id}`

جزئیات room + membership current user

#### `GET /api/chat/rooms/{chat_id}/messages`

جایگزین generic برای `messages/{user_id}`

#### `POST /api/chat/rooms/{chat_id}/messages`

ارسال پیام در هر نوع room با permission check بر اساس membership/role

### Group APIs

#### `POST /api/chat/groups`

ورودی:

```json
{
  "title": "گروه خرید سکه",
  "member_ids": [12, 18]
}
```

Behavior:

- creator به صورت خودکار admin می‌شود.
- member_ids اولیه به عنوان member اضافه می‌شوند.
- سقف 50 نفر enforce می‌شود.

#### `POST /api/chat/groups/{chat_id}/members`

admin-only

#### `DELETE /api/chat/groups/{chat_id}/members/{user_id}`

admin-only

#### `POST /api/chat/groups/{chat_id}/admins/{user_id}`

promote

#### `DELETE /api/chat/groups/{chat_id}/admins/{user_id}`

demote with last-admin guard

#### `POST /api/chat/groups/{chat_id}/leave`

member leave / admin leave with last-admin guard

#### `PATCH /api/chat/groups/{chat_id}`

rename group

### Channel APIs

#### `POST /api/chat/channels`

SUPER_ADMIN only

```json
{
  "title": "اطلاعیه‌های مهم"
}
```

Behavior:

- channel را ایجاد می‌کند.
- creator به صورت خودکار member فعال کانال می‌شود.
- response باید اطلاعات کافی برای باز کردن member picker را برگرداند.
- UI باید بلافاصله وارد مرحله انتخاب اعضای قابل دعوت شود.

#### `GET /api/chat/channels/invite-candidates`

SUPER_ADMIN only

Purpose:

- برگرداندن لیست کاربران فعال پروژه برای دعوت به کانال.
- پشتیبانی از جستجو، صفحه‌بندی، و member picker frontend.

Suggested query params:

- `q`: جستجوی نام/شماره/اکانت
- `limit`
- `offset`
- `exclude_chat_id` برای hide کردن اعضای موجود همان کانال در حالت edit

Suggested response shape:

```json
{
  "items": [
    {
      "user_id": 12,
      "account_name": "ali-test",
      "full_name": "علی رضایی",
      "mobile_number": "0912...",
      "is_already_member": false
    }
  ],
  "total": 420,
  "active_total": 420
}
```

#### `POST /api/chat/channels/{chat_id}/members/bulk`

SUPER_ADMIN only

Purpose:

- add/invite چند کاربر با یک request
- پایه backend برای member picker و `انتخاب همه`

Suggested request variants:

```json
{
  "user_ids": [12, 18, 30]
}
```

یا برای انتخاب همه:

```json
{
  "select_all_active_users": true
}
```

Rules:

- فقط کاربران فعال پروژه اضافه شوند.
- membership تکراری idempotent باشد.
- `select_all_active_users` باید کل active user set را پوشش دهد، نه فقط صفحه visible در UI.
- برای کانال mandatory سیستمی، bulk-add عمومی لازم نیست چون membership خودکار دارد.

#### `PATCH /api/chat/channels/{chat_id}`

rename channel, SUPER_ADMIN only

#### `POST /api/chat/channels/{chat_id}/admins/{user_id}`

SUPER_ADMIN only

#### `DELETE /api/chat/channels/{chat_id}/admins/{user_id}`

SUPER_ADMIN only

#### `POST /api/chat/channels/{chat_id}/members/{user_id}`

Recommended for optional channels if membership is manual/admin-managed.

#### `DELETE /api/chat/channels/{chat_id}/members/{user_id}`

SUPER_ADMIN only for optional channels.

#### `POST /api/chat/channels/{chat_id}/posts`

permission: channel admin or SUPER_ADMIN

### Membership enforcement

- تمام endpointهای read/post/reaction در کانال باید active membership را check کنند.
- نبود membership فعال باید `403` یا `404` مناسب بدهد و از هرگونه join ضمنی جلوگیری کند.
- backend نباید هیچ fallbackای برای auto-create membership در optional channels داشته باشد.

### Compatibility endpoints

Endpointهای فعلی مانند:

- `GET /api/chat/conversations`
- `GET /api/chat/messages/{user_id}`
- `POST /api/chat/send`

در فاز migration باید retained شوند، ولی در داخل به `chat_service` جدید delegate کنند.

---

## Realtime و Notification Design

### Realtime channels

به جای event صرفا per-user، دو لایه نیاز داریم:

1. `user-scoped events`
2. `chat-scoped events`

Pattern پیشنهادی:

- `chat:{chat_id}` برای message/reaction/member changes
- `notifications:{user_id}` برای badge/toast سطح کاربر

### Event catalog پیشنهادی

- `chat:message`
- `chat:message_updated`
- `chat:message_deleted`
- `chat:reaction`
- `chat:member_joined`
- `chat:member_left`
- `chat:member_removed`
- `chat:member_promoted`
- `chat:member_demoted`
- `chat:updated`

### Notification policy

#### Group

- unread count فعال
- in-app notification فعال
- browser/system notification: مشابه direct chat

#### Channel

- unread count فعال
- in-app list preview فعال
- browser/system notification به‌صورت پیش‌فرض غیرفعال برای هر post

Reason:

- کانال اطلاع‌رسانی اجباری است و push برای هر پست UX را noisy می‌کند.

---

## Frontend Refactor Plan

### وضعیت فعلی

در فرانت، conversation و message هنوز user-centric هستند:

- `Conversation.other_user_id`
- `Message.receiver_id`
- `ChatView.selectedUserId`

### وضعیت هدف

باید به `selectedChatId` مهاجرت کنیم.

### typeهای جدید پیشنهادی

```ts
type ChatKind = 'direct' | 'group' | 'channel'

interface ChatListItem {
  id: number
  type: ChatKind
  title: string
  subtitle?: string | null
  unread_count: number
  last_message_content: string | null
  last_message_type: string | null
  last_message_at: string | null
  can_post: boolean
  is_mandatory?: boolean
}
```

### Refactor targets

1. `ChatView.vue`
   - `selectedUserId` → `selectedChatId`
   - header rendering by `chat.type`
2. `ChatHeader.vue`
   - direct/group/channel header variants
3. `useChatMessages.ts`
   - send/load by `chat_id`
4. `useChatWebSocket.ts`
   - route realtime by `chat_id`
5. `ChatForwardModal.vue`
   - `ForwardTarget.kind` should support `channel`
6. conversation list item rendering
   - member count / mandatory badge / channel icon
7. channel creation flow
  - after create success, open `ChannelMemberPickerModal`
  - searchable multi-select member list
  - global `انتخاب همه` control
  - confirm invite action backed by bulk invite endpoint

### Channel member picker UX

- فقط برای `SUPER_ADMIN` نمایش داده می‌شود.
- بلافاصله بعد از create channel باز می‌شود.
- لیست شامل کاربران فعال پروژه است.
- هر row یک checkbox دارد.
- یک checkbox سراسری `انتخاب همه` در بالا وجود دارد.
- در صورت active بودن `انتخاب همه`، invite باید تمام کاربران فعال پروژه را عضو کانال کند، نه فقط صفحه فعلی لیست.
- در edit mode همین component می‌تواند برای add member بیشتر reuse شود.

### Feature parity expectations

Group باید full parity with direct chat بگیرد:

- reaction
- reply
- forwarded banner
- album send
- media background upload

Channel intentionally limited است:

- members فقط read + reaction
- admins post/reply/forward as posts

---

## Migration Strategy

### اصل مهم

Migration باید additive باشد، نه destructive.  
یعنی تا زمان stable شدن chat model جدید، direct chat فعلی نباید ناگهان شکسته شود.

### مرحله 1: Schema introduction

- create `chats`
- create `chat_members`
- add `messages.chat_id` nullable

No behavior change yet.

### مرحله 2: Backfill existing direct conversations

برای هر `Conversation` فعلی:

- یک `chat` با `type='direct'` بساز
- دو `chat_member` فعال بساز
- همه `messages` مربوطه را با `chat_id` backfill کن

### مرحله 3: Dual-read / Dual-write service layer

- یک `chat_service` جدید معرفی کن
- endpointهای direct فعلی از این service استفاده کنند
- projection direct فعلی برای UI legacy حفظ شود

### مرحله 4: Frontend generic chat list

- conversation list generic شود، ولی direct UX حفظ شود

### مرحله 5: Group rollout

- create group
- membership management
- messaging in group
- realtime + unread support

### مرحله 6: Mandatory system channel rollout

- create bootstrap job / startup guard for `اطلاع‌رسانی`
- migrate existing active users into mandatory channel
- hook registration-complete flow to auto-join mandatory channel

### مرحله 7: Optional channels

- channel creation by SUPER_ADMIN
- manual membership rules per decided policy

### مرحله 8: Remove dependency on legacy conversation math

وقتی stable شد:

- `Conversation` model را deprecated کن
- `receiver_id` dependency را کاهش بده

---

## ترتیب پیاده‌سازی کم‌ریسک

### Phase A - Foundation

1. Add DB schema (`chats`, `chat_members`, `messages.chat_id`)
2. Add backend service layer (`chat_service.py`)
3. Backfill direct chats from existing conversations/messages
4. Keep old endpoints alive, but route internally to new service

### Phase B - Group MVP

1. Group create/list/detail endpoints
2. Membership management endpoints
3. Group send/load/realtime
4. Group unread + notification handling
5. Frontend chat list + group header + member management UI

### Phase C - Mandatory System Channel

1. Bootstrap mandatory channel on startup/migration
2. Auto-join all active users
3. Auto-join on registration complete
4. Auto-inactivate membership on user soft-delete
5. Admin post flow + member reaction-only flow

### Phase D - Optional Channels

1. SUPER_ADMIN create channel
2. Rename channel
3. Assign/remove channel admins
4. Optional membership management

### Phase E - Cleanup

1. Remove old UI assumptions around `other_user_*`
2. Gradually deprecate direct-specific API surface

---

## ریسک‌ها و نکات اجرایی

### 1. Upload pipeline coupling

`chatUploadBackground.ts` و media pipeline فعلی هنوز receiver/user-centric هستند.  
باید مطمئن شویم queued uploads با `chat_id` bind شوند، نه `selectedUserId`.

### 2. Realtime fan-out

برای group/channel، publish per-member cost افزایش می‌یابد.  
در MVP acceptable است، ولی event schema باید chat-scoped باشد.

### 3. Soft-delete semantics

membershipها حذف فیزیکی نشوند؛ status change کافی است.

### 4. Last-admin rules

این ruleها باید server-side enforce شوند.  
UI فقط پیام راهنما نشان می‌دهد.

### 5. Sync impact

چون پروژه cross-server sync دارد، تمام جدول‌های جدید باید در ordering و dependency chain sync لحاظ شوند:

- chats
- chat_members
- messages.chat_id updates

---

## Open Question

در حال حاضر open question بحرانی برای MVP باقی نمانده است.

تصمیم نهایی این سند برای optional channels:

- membership فقط `invite-only` است.
- دعوت فقط توسط `SUPER_ADMIN` انجام می‌شود.
- بلافاصله بعد از create channel، member picker با امکان multi-select و `انتخاب همه` باز می‌شود.
- کاربر invite نشده، هیچ امکان join ندارد.

---

## نتیجه نهایی

این specification بر اساس تصمیم‌های محصولی فعلی، پیام‌رسان را به یک مدل عمومی `chat/room` مهاجرت می‌دهد که:

- direct chat را نمی‌شکند
- group را با کمترین complexity اضافه می‌کند
- mandatory channel را از روز صفر پروژه تضمین می‌کند
- optional channels را بدون وارد کردن پیچیدگی‌های شبکه اجتماعی پشتیبانی می‌کند

این سند برای شروع فاز عملیاتی کافی است.