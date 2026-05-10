<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { Component } from 'vue'
import { type Conversation } from '../../types/chat'
import { vAutoAnimate } from '@formkit/auto-animate/vue'
import {
  Bell,
  BellOff,
  CircleDot,
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

type ConversationListAction = 'pin' | 'unpin' | 'mute' | 'unmute' | 'mark-unread' | 'delete' | 'leave' | 'unfollow'
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

function isConversationMuted(conv: Conversation) {
  return conv.is_muted === true
}

function canMarkConversationUnread(conv: Conversation) {
  if (!conv.last_message_at) return false
  if ((conv.unread_count || 0) > 0) return false
  return props.selectedUserId !== conv.other_user_id
}

function getConversationInitial(conv: Conversation) {
  return (conv.other_user_name || '?').charAt(0)
}

function getPreviewText(conv: Conversation) {
  if (!conv.last_message_type) {
    return ''
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

const displayedConversations = computed(() => props.conversations)

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

    if (canMarkConversationUnread(conv)) {
      actions.push({
        key: 'mark-unread',
        label: 'علامت‌گذاری به‌عنوان خوانده‌نشده',
        description: 'این گفتگو دوباره با یک نشان نخوانده در فهرست دیده می‌شود.',
        tone: 'accent',
        icon: CircleDot,
      })
    }

    if (!isMandatoryPinnedConversation(conv)) {
      actions.push(
        isConversationMuted(conv)
          ? {
              key: 'unmute',
              label: 'خروج از حالت بی‌صدا',
              description: 'اعلان‌های این گفتگو دوباره نمایش داده می‌شود.',
              tone: 'accent',
              icon: Bell,
            }
          : {
              key: 'mute',
              label: 'بی‌صدا کردن گفتگو',
              description: 'پیام‌های جدید همچنان می‌رسند اما اعلان ارسال نمی‌شود.',
              tone: 'accent',
              icon: BellOff,
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
  return ''
})

function emitConversationAction(action: ConversationListAction) {
  if (!menuConversation.value) return
  const conv = menuConversation.value
  closeConversationMenu()
  emit('conversation-action', { action, conv })
}

function shouldShowActionDivider(index: number) {
  if (index <= 0) return false
  const current = activeMenuActions.value[index]
  const previous = activeMenuActions.value[index - 1]
  if (!current || !previous) return false
  return current.tone !== previous.tone && (current.tone === 'warning' || current.tone === 'danger')
}

onBeforeUnmount(() => {
  cancelLongPress()
})
</script>

<template>
  <div class="conversation-list-wrapper">
    <div class="conversation-atmosphere" aria-hidden="true"></div>

    <div class="conversation-panel">
      <div class="conversations-list" v-auto-animate>
        <div v-if="conversations.length === 0" class="empty-state">
          <span>💬</span>
          <p>گفتگویی وجود ندارد</p>
        </div>

        <div class="conversation-items" v-auto-animate>
            <div
              v-for="conv in displayedConversations"
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
                conv.is_muted,
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
                    </div>
                    <span class="conv-time" v-if="conv.last_message_at">{{ formatTime(conv.last_message_at) }}</span>
                  </div>
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
                <div v-else-if="isConversationMuted(conv)" class="side-muted-indicator" aria-label="بی‌صدا">
                  <BellOff :size="14" />
                </div>
                <div v-else-if="isConversationPinned(conv)" class="side-pin-indicator">
                  <Pin :size="14" />
                </div>
              </div>
            </div>
        </div>
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
            <template v-for="(action, index) in activeMenuActions" :key="action.key">
              <div v-if="shouldShowActionDivider(index)" class="menu-action-divider"></div>
              <button
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
            </template>
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
  --surface: rgba(255, 255, 255, 0.76);
  --surface-strong: rgba(255, 255, 255, 0.96);
  --line-soft: rgba(203, 213, 225, 0.82);
  --text-strong: #0f172a;
  --text-muted: #64748b;
  --accent: #3390ec;
  --accent-soft: #93c5fd;
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
    radial-gradient(circle at top right, rgba(51, 144, 236, 0.16), transparent 26%),
    radial-gradient(circle at top left, rgba(245, 158, 11, 0.1), transparent 22%),
    linear-gradient(180deg, #edf2f7 0%, #f8fafc 54%, #eef4f8 100%);
}

.conversation-atmosphere {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(circle at 18% 14%, rgba(255, 255, 255, 0.28), transparent 18%),
    radial-gradient(circle at 82% 12%, rgba(255, 255, 255, 0.2), transparent 16%);
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
  border: 1px solid rgba(255, 255, 255, 0.72);
  background: var(--surface);
  box-shadow:
    0 18px 45px rgba(15, 23, 42, 0.08),
    inset 0 1px 0 rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
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
  scrollbar-color: rgba(51, 144, 236, 0.24) transparent;
}

.conversations-list::-webkit-scrollbar {
  width: 6px;
}

.conversations-list::-webkit-scrollbar-thumb {
  background: rgba(51, 144, 236, 0.24);
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
  gap: 12px;
  padding: 12px 14px;
  border-radius: 22px;
  border: 1px solid var(--line-soft);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.94));
  cursor: pointer;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
}

.conversation-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 14px 28px rgba(15, 23, 42, 0.08);
}

.conversation-card--pinned {
  border-color: rgba(245, 158, 11, 0.22);
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.98), rgba(255, 255, 255, 0.96));
}

.conversation-card--mandatory {
  border-color: rgba(245, 158, 11, 0.28);
  box-shadow: 0 14px 30px rgba(180, 83, 9, 0.1);
}

.conversation-card--active {
  background: linear-gradient(135deg, #3390ec, #2563eb);
  border-color: rgba(255, 255, 255, 0.28);
  box-shadow: 0 18px 34px rgba(37, 99, 235, 0.22);
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
  background: radial-gradient(circle at top right, rgba(255, 255, 255, 0.22), transparent 36%);
  pointer-events: none;
}

.conv-avatar {
  position: relative;
  z-index: 1;
  width: 54px;
  height: 54px;
  min-width: 54px;
  border-radius: 20px;
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  font-weight: 800;
  box-shadow: 0 10px 22px rgba(15, 118, 110, 0.14);
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
  font-weight: 800;
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

.room-badge-list.muted {
  color: #475569;
  background: rgba(148, 163, 184, 0.16);
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
.side-pin-indicator,
.side-muted-indicator {
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
  background: linear-gradient(135deg, #3390ec, #2563eb);
  color: #fff;
  box-shadow: 0 10px 18px rgba(37, 99, 235, 0.2);
}

.conversation-card--active .unread-badge {
  background: #fff;
  color: var(--blue);
}

.side-pin-indicator {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.side-muted-indicator {
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
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
  background: linear-gradient(135deg, #3390ec, #2563eb);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 18px 30px rgba(37, 99, 235, 0.28);
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
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}

.conversation-menu-sheet {
  width: min(100%, 380px);
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.97);
  border: 1px solid rgba(226, 232, 240, 0.92);
  box-shadow: 0 24px 54px rgba(15, 23, 42, 0.2);
  padding: 14px;
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}

.conversation-menu-close {
  width: 32px;
  height: 32px;
  margin-right: auto;
  margin-bottom: 10px;
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
  margin-bottom: 14px;
  padding: 2px 2px 0;
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
  gap: 0;
  border-radius: 18px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(226, 232, 240, 0.86);
}

.menu-action-divider {
  height: 1px;
  margin: 0 14px;
  background: rgba(226, 232, 240, 0.92);
}

.menu-action {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 12px 14px;
  border: 0;
  background: transparent;
  text-align: right;
  transition: background 0.12s ease;
}

.menu-action:hover {
  background: rgba(15, 23, 42, 0.04);
}

.menu-action-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
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
  font-size: 0.9rem;
}

.menu-action.tone-accent {
  color: var(--text-strong);
}

.menu-action.tone-accent .menu-action-icon {
  background: rgba(51, 144, 236, 0.12);
  color: var(--accent);
}

.menu-action.tone-warning {
  background: linear-gradient(180deg, rgba(255, 247, 237, 0.88), rgba(255, 247, 237, 0.52));
}

.menu-action.tone-warning .menu-action-icon {
  background: rgba(249, 115, 22, 0.12);
  color: var(--warning);
}

.menu-action.tone-warning .menu-action-copy strong {
  color: var(--warning);
}

.menu-action.tone-danger {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.9), rgba(254, 242, 242, 0.58));
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
  background: rgba(248, 250, 252, 0.96);
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

  .conversation-menu-sheet {
    width: min(100%, 100%);
  }

  .fab-new-chat {
    right: 18px;
    bottom: 22px;
  }
}
</style>
