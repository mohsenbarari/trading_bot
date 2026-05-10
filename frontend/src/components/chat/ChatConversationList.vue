<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { Component } from 'vue'
import { type Conversation } from '../../types/chat'
import { vAutoAnimate } from '@formkit/auto-animate/vue'
import {
  BellOff,
  LogOut,
  Megaphone,
  MessageCirclePlus,
  Pin,
  PinOff,
  Shield,
  Trash2,
  UsersRound,
  X,
} from 'lucide-vue-next'

type ConversationListAction = 'pin' | 'unpin' | 'delete' | 'leave' | 'unfollow'
type MenuActionTone = 'accent' | 'danger' | 'warning'

type ConversationMenuAction = {
  key: ConversationListAction
  label: string
  description: string
  tone: MenuActionTone
  icon: Component
}

const props = defineProps<{
  conversations: Conversation[]
  selectedUserId: number | null
  typingUsers: Record<number, boolean>
}>()

const emit = defineEmits<{
  (e: 'select-conversation', conv: Conversation): void
  (e: 'new-conversation'): void
  (e: 'conversation-action', payload: { action: ConversationListAction; conv: Conversation }): void
}>()

const menuConversation = ref<Conversation | null>(null)
const suppressClickConversationId = ref<number | null>(null)
const longPressTimer = ref<number | null>(null)
const pointerOrigin = ref({ x: 0, y: 0 })

function formatTime(dateStr: string) {
  const date = new Date(dateStr)
  return date.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })
}

function isChannelConversation(conv: Conversation) {
  return conv.room_kind === 'channel'
}

function isGroupConversation(conv: Conversation) {
  return conv.room_kind === 'group'
}

function isRoomConversation(conv: Conversation) {
  return isChannelConversation(conv) || isGroupConversation(conv)
}

function isMandatoryPinnedConversation(conv: Conversation) {
  return isChannelConversation(conv) && conv.is_mandatory === true
}

function isOptionalChannelConversation(conv: Conversation) {
  return isChannelConversation(conv) && conv.is_mandatory !== true
}

function isConversationPinned(conv: Conversation) {
  return isMandatoryPinnedConversation(conv) || conv.is_pinned === true
}

function getConversationInitial(conv: Conversation) {
  return (conv.other_user_name || '?').charAt(0)
}

function formatMemberCount(conv: Conversation) {
  const count = Number(conv.member_count || 0)
  if (!isRoomConversation(conv) || count <= 0) return ''
  return `${count.toLocaleString('fa-IR')} عضو`
}

function formatEmptyRoomPreview(conv: Conversation) {
  const members = formatMemberCount(conv)
  if (isGroupConversation(conv)) {
    return members || 'گروه'
  }
  if (isChannelConversation(conv)) {
    if (conv.is_mandatory) return members ? `کانال اجباری • ${members}` : 'کانال اجباری'
    return members || 'کانال'
  }
  return '...'
}

function getPreviewText(conv: Conversation) {
  if (!conv.last_message_type) {
    return isRoomConversation(conv) ? formatEmptyRoomPreview(conv) : 'گفتگو هنوز شروع نشده است'
  }
  if (conv.last_message_type === 'image') return 'تصویر'
  if (conv.last_message_type === 'video') return 'ویدئو'
  if (conv.last_message_type === 'voice') return 'پیام صوتی'
  if (conv.last_message_type === 'sticker') return 'استیکر'
  if (conv.last_message_type === 'location') return 'موقعیت'
  if (conv.last_message_type === 'document') return 'فایل'
  return conv.last_message_content?.substring(0, 42) || '...'
}

function isUserOnline(lastSeen: string | null | undefined): boolean {
  if (!lastSeen) return false
  const serverStr = lastSeen.endsWith('Z') ? lastSeen : `${lastSeen}Z`
  const date = new Date(serverStr)
  return (new Date().getTime() - date.getTime()) < 180000
}

const pinnedConversations = computed(() => props.conversations.filter((conv) => isConversationPinned(conv)))
const regularConversations = computed(() => props.conversations.filter((conv) => !isConversationPinned(conv)))

const conversationSections = computed(() => {
  const sections: Array<{ key: string; title: string; subtitle: string; items: Conversation[] }> = []

  if (pinnedConversations.value.length > 0) {
    sections.push({
      key: 'pinned',
      title: 'سنجاق‌شده',
      subtitle: isMandatoryPinnedConversation(pinnedConversations.value[0] || {} as Conversation)
        ? 'کانال اجباری همیشه ابتدای این بخش باقی می‌ماند.'
        : 'گفتگوهای مهم شما اینجا بالای فهرست می‌مانند.',
      items: pinnedConversations.value,
    })
  }

  if (regularConversations.value.length > 0) {
    sections.push({
      key: 'regular',
      title: pinnedConversations.value.length > 0 ? 'گفتگوهای دیگر' : 'همه گفتگوها',
      subtitle: 'برای مدیریت هر گفتگو روی آن کمی نگه دارید.',
      items: regularConversations.value,
    })
  }

  return sections
})

function cancelLongPress() {
  if (longPressTimer.value !== null) {
    window.clearTimeout(longPressTimer.value)
    longPressTimer.value = null
  }
}

function openConversationMenu(conv: Conversation) {
  cancelLongPress()
  suppressClickConversationId.value = conv.id
  menuConversation.value = conv
  try { navigator.vibrate?.(10) } catch { /* noop */ }
}

function closeConversationMenu() {
  menuConversation.value = null
  window.setTimeout(() => {
    suppressClickConversationId.value = null
  }, 0)
}

function handleConversationClick(conv: Conversation) {
  if (suppressClickConversationId.value === conv.id) {
    suppressClickConversationId.value = null
    return
  }
  emit('select-conversation', conv)
}

function handlePointerDown(conv: Conversation, event: PointerEvent) {
  if (event.button !== 0) return
  cancelLongPress()
  pointerOrigin.value = { x: event.clientX, y: event.clientY }
  longPressTimer.value = window.setTimeout(() => {
    longPressTimer.value = null
    openConversationMenu(conv)
  }, 420)
}

function handlePointerMove(event: PointerEvent) {
  if (longPressTimer.value === null) return
  if (Math.abs(event.clientX - pointerOrigin.value.x) > 10 || Math.abs(event.clientY - pointerOrigin.value.y) > 10) {
    cancelLongPress()
  }
}

function handleContextMenu(conv: Conversation, event: MouseEvent) {
  event.preventDefault()
  openConversationMenu(conv)
}

const activeMenuActions = computed<ConversationMenuAction[]>(() => {
  const conv = menuConversation.value
  if (!conv) return []

  const actions: ConversationMenuAction[] = []
  if (!isMandatoryPinnedConversation(conv)) {
    actions.push(
      isConversationPinned(conv)
        ? {
            key: 'unpin',
            label: 'برداشتن سنجاق',
            description: 'گفتگو به ترتیب عادی فهرست برمی‌گردد.',
            tone: 'accent',
            icon: PinOff,
          }
        : {
            key: 'pin',
            label: 'سنجاق کردن',
            description: 'این گفتگو بالاتر از بقیه دیده می‌شود.',
            tone: 'accent',
            icon: Pin,
          }
    )
  }

  if (!isRoomConversation(conv)) {
    actions.push({
      key: 'delete',
      label: 'حذف گفتگو',
      description: 'گفتگو فقط از فهرست شما پنهان می‌شود.',
      tone: 'danger',
      icon: Trash2,
    })
    return actions
  }

  if (isGroupConversation(conv)) {
    actions.push({
      key: 'leave',
      label: 'ترک گروه',
      description: 'دیگر پیام‌های این گروه را دریافت نخواهید کرد.',
      tone: 'warning',
      icon: LogOut,
    })
    return actions
  }

  if (isOptionalChannelConversation(conv)) {
    actions.push({
      key: 'unfollow',
      label: 'لغو دنبال‌کردن',
      description: 'این کانال از فهرست شما خارج می‌شود.',
      tone: 'warning',
      icon: BellOff,
    })
  }

  return actions
})

const menuHint = computed(() => {
  const conv = menuConversation.value
  if (!conv) return ''
  if (isMandatoryPinnedConversation(conv)) {
    return 'این کانال اجباری همیشه در ابتدای گفتگوهای سنجاق‌شده می‌ماند و امکان لغو دنبال‌کردن آن وجود ندارد.'
  }
  if (!isRoomConversation(conv)) {
    return 'حذف در این بخش فقط گفتگو را از فهرست شما پنهان می‌کند و پیام‌ها را برای همیشه پاک نمی‌کند.'
  }
  return ''
})

function emitConversationAction(action: ConversationListAction) {
  if (!menuConversation.value) return
  const conv = menuConversation.value
  closeConversationMenu()
  emit('conversation-action', { action, conv })
}

onBeforeUnmount(() => {
  cancelLongPress()
})
</script>

<template>
  <div class="conversation-list-wrapper">
    <div class="conversation-atmosphere" aria-hidden="true"></div>

    <div class="conversation-panel">
      <div class="conversation-summary-strip">
        <div class="summary-copy">
          <span class="summary-kicker">پیام‌رسان</span>
          <h2>گفتگوها</h2>
          <p>برای بازکردن گفتگو لمس کنید و برای مدیریت، کمی نگه دارید.</p>
        </div>
        <div class="summary-stats">
          <span class="summary-pill accent">{{ conversations.length.toLocaleString('fa-IR') }} گفتگو</span>
          <span v-if="pinnedConversations.length > 0" class="summary-pill warm">
            {{ pinnedConversations.length.toLocaleString('fa-IR') }} سنجاق‌شده
          </span>
        </div>
      </div>

      <div class="conversations-list" v-auto-animate>
        <div v-if="conversations.length === 0" class="empty-state">
          <span>💬</span>
          <p>هنوز گفتگویی ندارید</p>
          <small>از دکمه پایین می‌توانید یک گفتگوی جدید شروع کنید.</small>
        </div>

        <section v-for="section in conversationSections" :key="section.key" class="conversation-section">
          <div class="section-header">
            <div>
              <h3>{{ section.title }}</h3>
              <p>{{ section.subtitle }}</p>
            </div>
            <span class="section-count">{{ section.items.length.toLocaleString('fa-IR') }}</span>
          </div>

          <div class="section-items" v-auto-animate>
            <div
              v-for="conv in section.items"
              :key="conv.id"
              v-memo="[
                conv.id,
                conv.other_user_id,
                conv.other_user_name,
                conv.room_kind,
                conv.chat_id,
                conv.other_user_is_deleted,
                conv.other_user_last_seen_at,
                conv.last_message_at,
                conv.last_message_type,
                conv.last_message_content,
                conv.unread_count,
                conv.member_count,
                conv.is_mandatory,
                conv.is_system,
                conv.is_pinned,
                conv.pinned_at,
                selectedUserId === conv.other_user_id,
                !isRoomConversation(conv) && !!typingUsers[conv.other_user_id],
              ]"
              class="conversation-card"
              v-ripple
              :class="{
                'conversation-card--active': selectedUserId === conv.other_user_id,
                'conversation-card--pinned': isConversationPinned(conv),
                'conversation-card--mandatory': isMandatoryPinnedConversation(conv),
                'conversation-card--unread': conv.unread_count > 0,
              }"
              @click="handleConversationClick(conv)"
              @contextmenu="handleContextMenu(conv, $event)"
              @pointerdown="handlePointerDown(conv, $event)"
              @pointermove="handlePointerMove($event)"
              @pointerup="cancelLongPress"
              @pointercancel="cancelLongPress"
              @pointerleave="cancelLongPress"
            >
              <div class="conversation-card-glow"></div>

              <div
                class="conv-avatar"
                :class="{
                  'room-avatar': isRoomConversation(conv),
                  'channel-avatar': isChannelConversation(conv),
                  'group-avatar': isGroupConversation(conv),
                }"
              >
                <Megaphone v-if="isChannelConversation(conv)" :size="22" />
                <UsersRound v-else-if="isGroupConversation(conv)" :size="22" />
                <template v-else>{{ getConversationInitial(conv) }}</template>
                <div v-if="!isRoomConversation(conv) && isUserOnline(conv.other_user_last_seen_at)" class="online-indicator-dot"></div>
              </div>

              <div class="conv-content">
                <div class="conv-header">
                  <div class="conv-title-block">
                    <div class="conv-name-row">
                      <span class="conv-name">{{ conv.other_user_name }}</span>
                      <span v-if="isConversationPinned(conv)" class="pin-chip">
                        <Pin :size="12" />
                        <span>{{ isMandatoryPinnedConversation(conv) ? 'ثابت' : 'سنجاق' }}</span>
                      </span>
                    </div>
                    <span class="conv-time" v-if="conv.last_message_at">{{ formatTime(conv.last_message_at) }}</span>
                  </div>
                </div>

                <div class="conv-meta-row">
                  <span v-if="isChannelConversation(conv)" class="room-badge-list channel">کانال</span>
                  <span v-else-if="isGroupConversation(conv)" class="room-badge-list group">گروه</span>
                  <span v-if="isChannelConversation(conv) && conv.is_mandatory" class="room-badge-list mandatory">اجباری</span>
                  <span v-if="isChannelConversation(conv) && conv.is_system" class="room-badge-list system">سیستمی</span>
                  <span v-if="formatMemberCount(conv)" class="member-count-list">{{ formatMemberCount(conv) }}</span>
                  <span v-if="conv.other_user_is_deleted" class="deleted-badge-list">غیرفعال</span>
                </div>

                <div class="conv-preview-row">
                  <span v-if="!isRoomConversation(conv) && typingUsers[conv.other_user_id]" class="typing-text">
                    در حال نوشتن...
                  </span>
                  <template v-else>
                    {{ getPreviewText(conv) }}
                  </template>
                </div>
              </div>

              <div class="conversation-side">
                <div v-if="conv.unread_count > 0" class="unread-badge">
                  {{ conv.unread_count.toLocaleString('fa-IR') }}
                </div>
                <div v-else-if="isConversationPinned(conv)" class="side-pin-indicator">
                  <Pin :size="14" />
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <button class="fab-new-chat" v-ripple @click="emit('new-conversation')">
      <MessageCirclePlus :size="28" />
    </button>

    <transition name="sheet-fade">
      <div v-if="menuConversation" class="conversation-menu-overlay" @click.self="closeConversationMenu">
        <div class="conversation-menu-sheet">
          <button class="conversation-menu-close" @click="closeConversationMenu">
            <X :size="18" />
          </button>

          <div class="conversation-menu-header">
            <div
              class="conv-avatar conversation-menu-avatar"
              :class="{
                'room-avatar': isRoomConversation(menuConversation),
                'channel-avatar': isChannelConversation(menuConversation),
                'group-avatar': isGroupConversation(menuConversation),
              }"
            >
              <Megaphone v-if="isChannelConversation(menuConversation)" :size="22" />
              <UsersRound v-else-if="isGroupConversation(menuConversation)" :size="22" />
              <template v-else>{{ getConversationInitial(menuConversation) }}</template>
            </div>

            <div class="conversation-menu-copy">
              <strong>{{ menuConversation.other_user_name }}</strong>
              <span>{{ getPreviewText(menuConversation) }}</span>
            </div>
          </div>

          <p v-if="menuHint" class="conversation-menu-hint">{{ menuHint }}</p>

          <div v-if="activeMenuActions.length > 0" class="conversation-menu-actions">
            <button
              v-for="action in activeMenuActions"
              :key="action.key"
              class="menu-action"
              :class="[`tone-${action.tone}`]"
              @click="emitConversationAction(action.key)"
            >
              <div class="menu-action-icon">
                <component :is="action.icon" :size="20" />
              </div>
              <div class="menu-action-copy">
                <strong>{{ action.label }}</strong>
                <span>{{ action.description }}</span>
              </div>
            </button>
          </div>

          <div v-else class="conversation-menu-empty">
            <Shield :size="20" />
            <span>برای این گفتگو عملیاتی در دسترس نیست.</span>
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.conversation-list-wrapper {
  --surface: rgba(255, 250, 240, 0.92);
  --surface-strong: rgba(255, 255, 255, 0.96);
  --line-soft: rgba(217, 119, 6, 0.14);
  --text-strong: #1f2937;
  --text-muted: #6b7280;
  --accent: #d97706;
  --accent-soft: #fbbf24;
  --teal: #0f766e;
  --blue: #2563eb;
  --danger: #dc2626;
  --warning: #c2410c;
  flex: 1;
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background:
    radial-gradient(circle at top right, rgba(251, 191, 36, 0.22), transparent 26%),
    radial-gradient(circle at top left, rgba(15, 118, 110, 0.14), transparent 24%),
    linear-gradient(180deg, #fff9ef 0%, #fffefb 52%, #fef6e8 100%);
}

.conversation-atmosphere {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(circle at 18% 14%, rgba(217, 119, 6, 0.09), transparent 18%),
    radial-gradient(circle at 82% 10%, rgba(37, 99, 235, 0.08), transparent 16%);
}

.conversation-panel {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  flex-direction: column;
  margin: 10px 12px 0;
  overflow: hidden;
  border-radius: 28px 28px 0 0;
  border: 1px solid rgba(255, 255, 255, 0.68);
  background: var(--surface);
  box-shadow:
    0 18px 45px rgba(161, 98, 7, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
}

.conversation-summary-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 18px 18px 12px;
  border-bottom: 1px solid rgba(217, 119, 6, 0.1);
}

.summary-copy h2 {
  margin: 2px 0 6px;
  color: var(--text-strong);
  font-size: 1.2rem;
  font-weight: 900;
}

.summary-copy p {
  margin: 0;
  color: var(--text-muted);
  font-size: 0.82rem;
}

.summary-kicker {
  display: inline-flex;
  color: var(--accent);
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.summary-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 999px;
  font-size: 0.74rem;
  font-weight: 800;
  border: 1px solid transparent;
  background: rgba(255, 255, 255, 0.8);
}

.summary-pill.accent {
  color: var(--teal);
  border-color: rgba(15, 118, 110, 0.16);
}

.summary-pill.warm {
  color: var(--accent);
  border-color: rgba(217, 119, 6, 0.16);
}

.conversations-list {
  flex: 1;
  overflow-y: auto;
  padding: 10px 12px 112px;
  scrollbar-width: thin;
  scrollbar-color: rgba(180, 83, 9, 0.24) transparent;
}

.conversations-list::-webkit-scrollbar {
  width: 6px;
}

.conversations-list::-webkit-scrollbar-thumb {
  background: rgba(180, 83, 9, 0.24);
  border-radius: 999px;
}

.conversation-section + .conversation-section {
  margin-top: 18px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 6px 10px;
}

.section-header h3 {
  margin: 0;
  color: var(--text-strong);
  font-size: 0.92rem;
  font-weight: 900;
}

.section-header p {
  margin: 3px 0 0;
  color: var(--text-muted);
  font-size: 0.74rem;
}

.section-count {
  min-width: 30px;
  height: 30px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.84);
  color: var(--accent);
  font-size: 0.76rem;
  font-weight: 900;
  box-shadow: inset 0 0 0 1px rgba(217, 119, 6, 0.1);
}

.section-items {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.conversation-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  border-radius: 24px;
  border: 1px solid var(--line-soft);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(255, 249, 240, 0.92));
  cursor: pointer;
  box-shadow: 0 10px 22px rgba(120, 53, 15, 0.06);
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
}

.conversation-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 14px 28px rgba(120, 53, 15, 0.09);
}

.conversation-card--pinned {
  border-color: rgba(217, 119, 6, 0.2);
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.98), rgba(255, 246, 228, 0.95));
}

.conversation-card--mandatory {
  border-color: rgba(245, 158, 11, 0.26);
  box-shadow: 0 14px 30px rgba(180, 83, 9, 0.12);
}

.conversation-card--active {
  background: linear-gradient(135deg, #d97706, #f59e0b);
  border-color: rgba(255, 255, 255, 0.28);
  box-shadow: 0 18px 34px rgba(217, 119, 6, 0.25);
}

.conversation-card--active .conv-name,
.conversation-card--active .conv-time,
.conversation-card--active .conv-preview-row,
.conversation-card--active .typing-text,
.conversation-card--active .room-badge-list,
.conversation-card--active .member-count-list,
.conversation-card--active .deleted-badge-list,
.conversation-card--active .pin-chip {
  color: #fff;
}

.conversation-card--active .room-badge-list,
.conversation-card--active .member-count-list,
.conversation-card--active .deleted-badge-list,
.conversation-card--active .pin-chip {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.18);
}

.conversation-card-glow {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: radial-gradient(circle at top right, rgba(251, 191, 36, 0.18), transparent 34%);
  pointer-events: none;
}

.conv-avatar {
  position: relative;
  z-index: 1;
  width: 54px;
  height: 54px;
  min-width: 54px;
  border-radius: 18px;
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  font-weight: 800;
  box-shadow: 0 10px 20px rgba(15, 118, 110, 0.16);
}

.conv-avatar.channel-avatar {
  background: linear-gradient(135deg, #0f766e, #14b8a6);
}

.conv-avatar.group-avatar {
  background: linear-gradient(135deg, #2563eb, #38bdf8);
}

.online-indicator-dot {
  position: absolute;
  left: -2px;
  bottom: -2px;
  width: 14px;
  height: 14px;
  border-radius: 999px;
  background: #22c55e;
  border: 2px solid #fff;
}

.conversation-card--active .online-indicator-dot {
  border-color: #f59e0b;
}

.conv-content {
  position: relative;
  z-index: 1;
  flex: 1;
  min-width: 0;
}

.conv-header,
.conv-title-block,
.conv-name-row,
.conv-meta-row,
.conv-preview-row {
  display: flex;
}

.conv-title-block {
  width: 100%;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.conv-name-row {
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.conv-name {
  min-width: 0;
  color: var(--text-strong);
  font-size: 0.98rem;
  font-weight: 900;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-time {
  flex-shrink: 0;
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 700;
}

.pin-chip,
.room-badge-list,
.member-count-list,
.deleted-badge-list {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 0.66rem;
  font-weight: 800;
  border: 1px solid transparent;
}

.pin-chip {
  color: var(--accent);
  background: rgba(217, 119, 6, 0.08);
  border-color: rgba(217, 119, 6, 0.12);
}

.conv-meta-row {
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0 6px;
}

.room-badge-list.channel {
  color: var(--teal);
  background: rgba(15, 118, 110, 0.1);
}

.room-badge-list.group {
  color: var(--blue);
  background: rgba(37, 99, 235, 0.1);
}

.room-badge-list.mandatory {
  color: var(--accent);
  background: rgba(245, 158, 11, 0.14);
}

.room-badge-list.system {
  color: #7c3aed;
  background: rgba(124, 58, 237, 0.1);
}

.member-count-list {
  color: #475569;
  background: rgba(148, 163, 184, 0.14);
}

.deleted-badge-list {
  color: var(--danger);
  background: rgba(254, 226, 226, 0.8);
}

.conv-preview-row {
  color: var(--text-muted);
  font-size: 0.83rem;
  font-weight: 600;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.typing-text {
  color: var(--teal);
}

.conversation-side {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 34px;
}

.unread-badge,
.side-pin-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 28px;
  border-radius: 999px;
  padding: 0 8px;
  font-size: 0.72rem;
  font-weight: 900;
}

.unread-badge {
  background: linear-gradient(135deg, #0f766e, #14b8a6);
  color: #fff;
  box-shadow: 0 10px 18px rgba(15, 118, 110, 0.2);
}

.conversation-card--active .unread-badge {
  background: #fff;
  color: var(--accent);
}

.side-pin-indicator {
  background: rgba(217, 119, 6, 0.08);
  color: var(--accent);
}

.empty-state {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--text-muted);
  gap: 8px;
}

.empty-state span {
  font-size: 3rem;
}

.empty-state p,
.empty-state small {
  margin: 0;
}

.fab-new-chat {
  position: absolute;
  right: 22px;
  bottom: 26px;
  z-index: 4;
  width: 58px;
  height: 58px;
  border: none;
  border-radius: 22px;
  background: linear-gradient(135deg, #d97706, #f59e0b);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 18px 30px rgba(217, 119, 6, 0.28);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.fab-new-chat:hover {
  transform: translateY(-1px) scale(1.02);
}

.conversation-menu-overlay {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 20px 16px 24px;
  background: rgba(17, 24, 39, 0.28);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

.conversation-menu-sheet {
  width: min(100%, 460px);
  border-radius: 28px;
  background: rgba(255, 250, 241, 0.98);
  border: 1px solid rgba(255, 255, 255, 0.7);
  box-shadow: 0 28px 60px rgba(17, 24, 39, 0.24);
  padding: 18px;
}

.conversation-menu-close {
  width: 34px;
  height: 34px;
  margin-right: auto;
  margin-bottom: 8px;
  border: none;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
  display: flex;
  align-items: center;
  justify-content: center;
}

.conversation-menu-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.conversation-menu-avatar {
  border-radius: 18px;
}

.conversation-menu-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.conversation-menu-copy strong {
  color: var(--text-strong);
  font-size: 0.98rem;
}

.conversation-menu-copy span,
.conversation-menu-hint,
.menu-action-copy span,
.conversation-menu-empty span {
  color: var(--text-muted);
  font-size: 0.78rem;
  line-height: 1.5;
}

.conversation-menu-hint {
  margin: 0 0 14px;
  padding: 12px 14px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid rgba(217, 119, 6, 0.1);
}

.conversation-menu-actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.menu-action {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 14px 15px;
  border-radius: 20px;
  border: 1px solid transparent;
  background: rgba(255, 255, 255, 0.88);
  text-align: right;
}

.menu-action-icon {
  width: 42px;
  height: 42px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.menu-action-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.menu-action-copy strong {
  color: var(--text-strong);
  font-size: 0.88rem;
}

.menu-action.tone-accent {
  border-color: rgba(217, 119, 6, 0.14);
}

.menu-action.tone-accent .menu-action-icon {
  background: rgba(217, 119, 6, 0.1);
  color: var(--accent);
}

.menu-action.tone-warning {
  border-color: rgba(194, 65, 12, 0.16);
  background: linear-gradient(180deg, rgba(255, 247, 237, 0.98), rgba(255, 237, 213, 0.92));
}

.menu-action.tone-warning .menu-action-icon {
  background: rgba(249, 115, 22, 0.12);
  color: var(--warning);
}

.menu-action.tone-warning .menu-action-copy strong {
  color: var(--warning);
}

.menu-action.tone-danger {
  border-color: rgba(220, 38, 38, 0.18);
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.98), rgba(254, 226, 226, 0.92));
}

.menu-action.tone-danger .menu-action-icon {
  background: rgba(220, 38, 38, 0.12);
  color: var(--danger);
}

.menu-action.tone-danger .menu-action-copy strong {
  color: var(--danger);
}

.conversation-menu-empty {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.84);
  color: var(--accent);
}

.sheet-fade-enter-active,
.sheet-fade-leave-active {
  transition: opacity 0.2s ease;
}

.sheet-fade-enter-active .conversation-menu-sheet,
.sheet-fade-leave-active .conversation-menu-sheet {
  transition: transform 0.24s ease, opacity 0.24s ease;
}

.sheet-fade-enter-from,
.sheet-fade-leave-to {
  opacity: 0;
}

.sheet-fade-enter-from .conversation-menu-sheet,
.sheet-fade-leave-to .conversation-menu-sheet {
  transform: translateY(16px);
  opacity: 0;
}

@media (max-width: 640px) {
  .conversation-panel {
    margin: 6px 8px 0;
    border-radius: 24px 24px 0 0;
  }

  .conversation-summary-strip {
    padding: 16px 14px 10px;
  }

  .conversations-list {
    padding: 10px 10px 108px;
  }

  .conversation-card {
    padding: 13px 14px;
    border-radius: 22px;
  }

  .fab-new-chat {
    right: 18px;
    bottom: 22px;
  }
}
</style>
