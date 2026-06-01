<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { Component } from 'vue'
import { type Conversation } from '../../types/chat'
import { vAutoAnimate } from '@formkit/auto-animate/vue'
import { discardBackState, popBackState, pushBackState } from '../../composables/useBackButton'
import { buildChatFileUrl, getAvatarInitial } from '../../utils/chatFiles'
import { getConversationPreviewText } from '../../utils/chatMessagePreview'
import { isUserOnline } from '../../utils/userPresence'
import { formatIranTime } from '../../utils/iranTime'
import { markMessengerPerformance } from '../../utils/messengerRefactor'
import { recordMessengerDomSnapshot, scheduleMessengerDiagnosticTask } from '../../utils/messengerStage2Metrics'
import {
  MESSENGER_CONVERSATION_INITIAL_WINDOW,
  MESSENGER_CONVERSATION_WINDOW_BATCH,
  selectConversationWindow,
  shouldExpandConversationWindow,
} from '../../utils/messengerStage4Performance'
import {
  ArrowDown,
  ArrowUp,
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
} from 'lucide-vue-next'

type ConversationListAction = 'pin' | 'unpin' | 'move-pin-up' | 'move-pin-down' | 'mute' | 'unmute' | 'mark-unread' | 'delete' | 'leave' | 'unfollow'
type MenuActionTone = 'accent' | 'danger' | 'warning'

type ConversationMenuAction = {
  key: ConversationListAction
  label: string
  description: string
  tone: MenuActionTone
  icon: Component
}

type ConversationRowVm = {
  conv: Conversation
  isRoom: boolean
  isChannel: boolean
  isGroup: boolean
  isManagement: boolean
  isMandatoryPinned: boolean
  isPinned: boolean
  isMuted: boolean
  isActive: boolean
  hasUnread: boolean
  activityText: string
  previewText: string
  avatarUrl: string
  avatarInitial: string
  isOnlineDirectUser: boolean
}

const props = defineProps<{
  conversations: Conversation[]
  selectedUserId: number | null
  typingUsers: Record<number, boolean>
  activityTextByConversation?: Record<number, string>
  apiBaseUrl?: string
  canStartNewConversation?: boolean
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
const conversationWindowLimit = ref(MESSENGER_CONVERSATION_INITIAL_WINDOW)
const conversationMenuBackStateActive = ref(false)
let closingConversationMenuFromBack = false
let conversationListFirstRenderMarked = false

function formatTime(dateStr: string) {
  return formatIranTime(dateStr)
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

function isManagementConversation(conv: Conversation) {
  return isGroupConversation(conv) && conv.is_system === true
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

function comparePinnedConversationOrder(left: Conversation, right: Conversation) {
  const leftOrder = Number(left.pin_order ?? 0)
  const rightOrder = Number(right.pin_order ?? 0)
  if (rightOrder !== leftOrder) return rightOrder - leftOrder

  const leftPinnedAt = left.pinned_at || ''
  const rightPinnedAt = right.pinned_at || ''
  if (rightPinnedAt > leftPinnedAt) return 1
  if (rightPinnedAt < leftPinnedAt) return -1

  const leftLastMessageAt = left.last_message_at || ''
  const rightLastMessageAt = right.last_message_at || ''
  if (rightLastMessageAt > leftLastMessageAt) return 1
  if (rightLastMessageAt < leftLastMessageAt) return -1

  return Number(right.id) - Number(left.id)
}

const reorderablePinnedConversations = computed(() => {
  return displayedConversations.value.filter((conversation) => {
    return isConversationPinned(conversation) && !isMandatoryPinnedConversation(conversation)
  }).slice().sort(comparePinnedConversationOrder)
})

function canMoveConversationPinUp(conv: Conversation) {
  const index = reorderablePinnedConversations.value.findIndex((conversation) => conversation.id === conv.id)
  return index > 0
}

function canMoveConversationPinDown(conv: Conversation) {
  const index = reorderablePinnedConversations.value.findIndex((conversation) => conversation.id === conv.id)
  return index !== -1 && index < reorderablePinnedConversations.value.length - 1
}

function canMarkConversationUnread(conv: Conversation) {
  if (!conv.last_message_at) return false
  if ((conv.unread_count || 0) > 0) return false
  return props.selectedUserId !== conv.other_user_id
}

function getConversationInitial(conv: Conversation) {
  return getAvatarInitial(conv.other_user_name)
}

function getConversationAvatarUrl(conv: Conversation) {
  return buildChatFileUrl(conv.avatar_file_id ?? null, props.apiBaseUrl ?? '')
}

function getPreviewText(conv: Conversation) {
  if (isManagementConversation(conv)) {
    const preview = getConversationPreviewText(conv.last_message_type, conv.last_message_content)
    return preview ? `پیام مدیریت · ${preview}` : 'پیام مدیریت'
  }
  return getConversationPreviewText(conv.last_message_type, conv.last_message_content)
}

function getConversationActivityText(conv: Conversation) {
  const activityText = props.activityTextByConversation?.[conv.other_user_id]
  if (activityText) return activityText

  if (!isRoomConversation(conv) && props.typingUsers[conv.other_user_id]) {
    return 'در حال نوشتن...'
  }

  return ''
}

const conversationRows = computed<ConversationRowVm[]>(() => {
  return displayedConversations.value.map((conv) => {
    const isRoom = isRoomConversation(conv)
    const isChannel = isChannelConversation(conv)
    const isGroup = isGroupConversation(conv)
    const isManagement = isManagementConversation(conv)
    const isMandatoryPinned = isMandatoryPinnedConversation(conv)
    const isPinned = isConversationPinned(conv)
    const isMuted = isConversationMuted(conv)
    const isActive = props.selectedUserId === conv.other_user_id
    const hasUnread = (conv.unread_count || 0) > 0
    const activityText = getConversationActivityText(conv)

    return {
      conv,
      isRoom,
      isChannel,
      isGroup,
      isManagement,
      isMandatoryPinned,
      isPinned,
      isMuted,
      isActive,
      hasUnread,
      activityText,
      previewText: activityText || getPreviewText(conv),
      avatarUrl: getConversationAvatarUrl(conv),
      avatarInitial: getConversationInitial(conv),
      isOnlineDirectUser: !isRoom && isUserOnline(conv.other_user_last_seen_at),
    }
  })
})

const conversationWindow = computed(() => selectConversationWindow(props.conversations, {
  limit: conversationWindowLimit.value,
  selectedUserId: props.selectedUserId,
}))
const displayedConversations = computed(() => conversationWindow.value.items)

function expandConversationWindow() {
  conversationWindowLimit.value = Math.min(
    props.conversations.length,
    conversationWindowLimit.value + MESSENGER_CONVERSATION_WINDOW_BATCH,
  )
}

function handleConversationListScroll(event: Event) {
  if (!conversationWindow.value.hasMore) return
  const target = event.currentTarget as HTMLElement | null
  if (!target) return
  if (shouldExpandConversationWindow(target)) {
    expandConversationWindow()
  }
}

watch(() => props.conversations.length, (rowCount) => {
  if (rowCount <= MESSENGER_CONVERSATION_INITIAL_WINDOW) {
    conversationWindowLimit.value = MESSENGER_CONVERSATION_INITIAL_WINDOW
    return
  }

  conversationWindowLimit.value = Math.min(Math.max(conversationWindowLimit.value, MESSENGER_CONVERSATION_INITIAL_WINDOW), rowCount)
})

watch(() => displayedConversations.value.length, (rowCount) => {
  if (conversationListFirstRenderMarked) {
    return
  }

  conversationListFirstRenderMarked = true
  markMessengerPerformance('conversation-list-first-render')
  nextTick(() => {
    scheduleMessengerDiagnosticTask(() => {
      const root = typeof document !== 'undefined'
        ? document.querySelector('.conversation-list-wrapper') || document.body
        : null
      if (root) {
        recordMessengerDomSnapshot('conversation-list-first-render', root, {
          rowCount,
          totalRowCount: props.conversations.length,
          windowed: conversationWindow.value.hasMore,
        })
      }
    }, { timeoutMs: 750, fallbackDelayMs: 120 })
  })
}, { immediate: true, flush: 'post' })

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
  markMessengerPerformance('conversation-menu-open')
  nextTick(() => {
    scheduleMessengerDiagnosticTask(() => {
      const root = typeof document !== 'undefined'
        ? document.querySelector('.conversation-list-wrapper') || document.body
        : null
      if (root) {
        recordMessengerDomSnapshot('conversation-menu-open', root, { conversationId: conv.id })
      }
    }, { timeoutMs: 750, fallbackDelayMs: 120 })
  })
  try { navigator.vibrate?.(10) } catch { /* noop */ }
}

function closeConversationMenu() {
  menuConversation.value = null
  window.setTimeout(() => {
    suppressClickConversationId.value = null
  }, 0)
}

function closeConversationMenuForAction() {
  if (conversationMenuBackStateActive.value) {
    conversationMenuBackStateActive.value = false
    discardBackState()
  }
  closeConversationMenu()
}

watch(() => Boolean(menuConversation.value), (isOpen) => {
  if (isOpen) {
    if (!conversationMenuBackStateActive.value) {
      conversationMenuBackStateActive.value = true
      pushBackState(() => {
        conversationMenuBackStateActive.value = false
        closingConversationMenuFromBack = true
        closeConversationMenu()
        closingConversationMenuFromBack = false
      })
    }
    return
  }

  if (conversationMenuBackStateActive.value) {
    conversationMenuBackStateActive.value = false
    if (!closingConversationMenuFromBack) {
      popBackState()
    }
  }
})

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
  pointerOrigin.value = { x: event.clientX, y: event.clientY }
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

    if (isConversationPinned(conv)) {
      if (canMoveConversationPinUp(conv)) {
        actions.push({
          key: 'move-pin-up',
          label: 'جابجایی به بالا',
          description: 'این گفتگوی سنجاق‌شده یک پله بالاتر می‌رود.',
          tone: 'accent',
          icon: ArrowUp,
        })
      }

      if (canMoveConversationPinDown(conv)) {
        actions.push({
          key: 'move-pin-down',
          label: 'جابجایی به پایین',
          description: 'این گفتگوی سنجاق‌شده یک پله پایین‌تر می‌رود.',
          tone: 'accent',
          icon: ArrowDown,
        })
      }
    }
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
    if (isManagementConversation(conv)) {
      return actions
    }
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

const menuPosition = computed(() => {
  const menuW = 236
  const actions = activeMenuActions.value
  const dividerCount = actions.reduce((count, _action, index) => count + (shouldShowActionDivider(index) ? 1 : 0), 0)
  const actionCount = Math.max(actions.length, 1)
  const menuH = actionCount * 44 + dividerCount + 8
  const vw = typeof window !== 'undefined' ? window.innerWidth : 400
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800
  const boundedMenuW = Math.min(menuW, vw - 16)

  let x = pointerOrigin.value.x - (boundedMenuW / 2)
  let y = pointerOrigin.value.y - 8

  if (x + boundedMenuW > vw - 8) x = vw - boundedMenuW - 8
  if (x < 8) x = 8
  if (y + menuH > vh - 8) y = vh - menuH - 8
  if (y < 8) y = 8

  return {
    top: `${y}px`,
    left: `${x}px`,
    width: `${boundedMenuW}px`,
  }
})

function emitConversationAction(action: ConversationListAction) {
  if (!menuConversation.value) return
  const conv = menuConversation.value
  closeConversationMenuForAction()
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
      <div class="conversations-list" v-auto-animate @scroll.passive="handleConversationListScroll">
        <div v-if="conversations.length === 0" class="empty-state">
          <span>💬</span>
          <p>گفتگویی وجود ندارد</p>
        </div>

        <div class="conversation-items" v-auto-animate>
            <div
              v-for="row in conversationRows"
              :key="row.conv.id"
              v-memo="[
                row.conv.id,
                row.conv.other_user_id,
                row.conv.other_user_name,
                row.conv.room_kind,
                row.conv.chat_id,
                row.conv.other_user_is_deleted,
                row.conv.other_user_last_seen_at,
                row.conv.last_message_at,
                row.conv.last_message_type,
                row.conv.last_message_content,
                row.conv.unread_count,
                row.conv.unread_mention_count,
                row.conv.is_muted,
                row.conv.is_pinned,
                row.conv.pinned_at,
                row.conv.pin_order,
                row.isActive,
                row.activityText,
              ]"
              class="conversation-card conversation-item"
              v-ripple
              :class="{
                'conversation-card--active': row.isActive,
                'conversation-card--pinned': row.isPinned,
                'conversation-card--mandatory': row.isMandatoryPinned,
                'conversation-card--management': row.isManagement,
                'conversation-card--unread': row.hasUnread,
              }"
              @click="handleConversationClick(row.conv)"
              @contextmenu="handleContextMenu(row.conv, $event)"
              @pointerdown="handlePointerDown(row.conv, $event)"
              @pointermove="handlePointerMove($event)"
              @pointerup="cancelLongPress"
              @pointercancel="cancelLongPress"
              @pointerleave="cancelLongPress"
            >
              <div class="conversation-card-glow"></div>

              <div
                class="conv-avatar"
                :class="{
                  'room-avatar': row.isRoom,
                  'channel-avatar': row.isChannel,
                  'group-avatar': row.isGroup,
                  'management-avatar': row.isManagement,
                }"
              >
                <img v-if="row.avatarUrl" :src="row.avatarUrl" :alt="row.conv.other_user_name" class="conv-avatar-image" />
                <Shield v-else-if="row.isManagement" :size="22" />
                <Megaphone v-else-if="row.isChannel" :size="22" />
                <UsersRound v-else-if="row.isGroup" :size="22" />
                <template v-else>{{ row.avatarInitial }}</template>
                <div v-if="row.isOnlineDirectUser" class="online-indicator-dot"></div>
              </div>

              <div class="conv-content">
                <div class="conv-header">
                  <div class="conv-title-block">
                    <div class="conv-name-row">
                      <span class="conv-name">{{ row.conv.other_user_name }}</span>
                      <span v-if="row.isChannel" class="channel-badge-list" hidden>کانال</span>
                      <span v-else-if="row.isGroup" class="channel-badge-list" hidden>گروه</span>
                    </div>
                    <span class="conv-time" v-if="row.conv.last_message_at">{{ formatTime(row.conv.last_message_at) }}</span>
                  </div>
                </div>

                <div class="conv-preview-row">
                  <span v-if="row.activityText" class="typing-text">
                    {{ row.activityText }}
                  </span>
                  <template v-else>
                    {{ row.previewText }}
                  </template>
                </div>
              </div>

              <div class="conversation-side">
                <div v-if="(row.conv.unread_mention_count ?? 0) > 0" class="unread-badge mention-badge" title="منشن جدید">
                  @
                </div>
                <div v-if="row.conv.unread_count > 0" class="unread-badge">
                  {{ row.conv.unread_count.toLocaleString('fa-IR') }}
                </div>
                <div v-else-if="row.isMuted" class="side-muted-indicator" aria-label="بی‌صدا">
                  <BellOff :size="14" />
                </div>
                <div v-else-if="row.isPinned" class="side-pin-indicator">
                  <Pin :size="14" />
                </div>
              </div>
            </div>
            <button
              v-if="conversationWindow.hasMore"
              type="button"
              class="conversation-window-more"
              @click="expandConversationWindow"
            >
              نمایش بیشتر
              <span>{{ conversationWindow.hiddenCount.toLocaleString('fa-IR') }}</span>
            </button>
        </div>
      </div>
    </div>

    <button v-if="canStartNewConversation !== false" class="fab-new-chat" v-ripple @click="emit('new-conversation')">
      <MessageCirclePlus :size="28" />
    </button>

    <transition name="zoom-fade">
      <div v-if="menuConversation" class="conversation-menu-overlay" @click.self="closeConversationMenu">
        <div class="conversation-menu-popover" :style="menuPosition" role="menu" aria-label="Conversation actions">
          <div class="conversation-menu-panel" @click.stop>
            <div v-if="activeMenuActions.length > 0" class="conversation-menu-actions">
              <template v-for="(action, index) in activeMenuActions" :key="action.key">
                <div v-if="shouldShowActionDivider(index)" class="menu-action-divider"></div>
                <button
                  type="button"
                  class="menu-action"
                  :class="[`tone-${action.tone}`]"
                  @pointerup.stop.prevent="emitConversationAction(action.key)"
                  @click.stop.prevent="emitConversationAction(action.key)"
                >
                  <div class="menu-action-icon">
                    <component :is="action.icon" :size="20" />
                  </div>
                  <div class="menu-action-copy">
                    <strong>{{ action.label }}</strong>
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
      </div>
    </transition>
  </div>
</template>

<style scoped>
.conversation-list-wrapper {
  --surface: var(--messenger-surface-panel, rgba(255, 255, 255, 0.76));
  --line-soft: var(--messenger-border-subtle, rgba(203, 213, 225, 0.82));
  --text-strong: var(--messenger-text-strong, #0f172a);
  --text-muted: var(--messenger-text-muted, #64748b);
  --accent: var(--messenger-accent, #3390ec);
  --teal: #0f766e;
  --blue: #2563eb;
  --danger: var(--messenger-danger, #dc2626);
  --warning: var(--messenger-warning, #c2410c);
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
  min-height: var(--messenger-list-row-min-height, 64px);
  padding: 12px 14px;
  border-radius: 22px;
  border: 1px solid var(--line-soft);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.94));
  cursor: pointer;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.065), 0 1px 0 rgba(255, 255, 255, 0.72) inset;
  transition: transform var(--messenger-motion-standard, 180ms) ease, box-shadow var(--messenger-motion-standard, 180ms) ease, border-color var(--messenger-motion-standard, 180ms) ease;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
  content-visibility: auto;
  contain-intrinsic-size: var(--messenger-list-row-contain-intrinsic-size, 76px);
}

.conversation-window-more {
  width: 100%;
  min-height: 44px;
  border: 1px solid rgba(51, 144, 236, 0.18);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.78);
  color: var(--accent);
  font: inherit;
  font-size: 0.82rem;
  font-weight: 900;
  cursor: pointer;
}

.conversation-window-more span {
  margin-inline-start: 6px;
  color: var(--text-muted);
  font-weight: 800;
}

.conversation-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 16px 30px rgba(15, 23, 42, 0.09), 0 1px 0 rgba(255, 255, 255, 0.8) inset;
}

.conversation-card--pinned {
  border-color: rgba(245, 158, 11, 0.22);
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.98), rgba(255, 255, 255, 0.96));
}

.conversation-card--mandatory {
  border-color: rgba(245, 158, 11, 0.28);
  box-shadow: 0 16px 32px rgba(180, 83, 9, 0.11), 0 1px 0 rgba(255, 255, 255, 0.74) inset;
}

.conversation-card--management {
  border-color: rgba(15, 118, 110, 0.24);
  background: linear-gradient(135deg, rgba(236, 253, 245, 0.98), rgba(255, 251, 235, 0.94));
}

.conversation-card--management .conv-name {
  color: #0f766e;
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

.conv-avatar.management-avatar {
  background: linear-gradient(135deg, #0f766e, #f59e0b);
}

.conv-avatar-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: inherit;
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
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
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

.mention-badge {
  background: #7c3aed !important;
  color: #fff !important;
  box-shadow: 0 4px 10px rgba(124, 58, 237, 0.3) !important;
  animation: pulse-mention calc(var(--messenger-motion-standard, 180ms) * 11) infinite;
}

@keyframes pulse-mention {
  0% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.05);
  }
  100% {
    transform: scale(1);
  }
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
  transition: transform var(--messenger-motion-standard, 180ms) ease, box-shadow var(--messenger-motion-standard, 180ms) ease;
}

.fab-new-chat:hover {
  transform: translateY(-1px) scale(1.02);
}

.conversation-menu-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(15, 23, 42, 0.18);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.conversation-menu-popover {
  position: fixed;
  z-index: 2001;
  direction: rtl;
}

.conversation-menu-panel {
  width: 100%;
  border-radius: 12px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12), 0 2px 8px rgba(0, 0, 0, 0.06);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}

.conversation-menu-actions {
  display: flex;
  flex-direction: column;
  padding: 4px 0;
}

.menu-action-divider {
  height: 1px;
  margin: 0 16px;
  background: rgba(226, 232, 240, 0.92);
}

.menu-action {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 10px 16px;
  box-sizing: border-box;
  border: 0;
  background: transparent;
  text-align: right;
  font: inherit;
  cursor: pointer;
  transition: background var(--messenger-motion-fast, 120ms) ease;
}

.menu-action:active {
  background: rgba(15, 23, 42, 0.08);
}

.menu-action:hover {
  background: rgba(15, 23, 42, 0.04);
}

.menu-action-icon {
  display: inline-flex;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.menu-action-copy {
  min-width: 0;
  flex: 1;
  display: inline-flex;
  align-items: center;
}

.menu-action-copy strong {
  color: inherit;
  font-size: 14px;
  font-weight: 700;
}

.menu-action.tone-accent {
  color: var(--text-strong);
}

.menu-action.tone-accent .menu-action-icon {
  color: #64748b;
}

.menu-action.tone-warning {
  background: linear-gradient(180deg, rgba(255, 247, 237, 0.88), rgba(255, 247, 237, 0.52));
}

.menu-action.tone-warning .menu-action-icon {
  color: var(--warning);
}

.menu-action.tone-danger {
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.9), rgba(254, 242, 242, 0.58));
}

.menu-action.tone-danger .menu-action-icon {
  color: var(--danger);
}

.conversation-menu-empty {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  color: var(--accent);
}

.conversation-menu-empty span {
  color: var(--text-muted);
  font-size: 0.8rem;
}

.zoom-fade-enter-active,
.zoom-fade-leave-active {
  transition: opacity var(--messenger-motion-standard, 180ms) ease;
}

.zoom-fade-enter-active .conversation-menu-popover,
.zoom-fade-leave-active .conversation-menu-popover {
  transition: transform var(--messenger-motion-fast, 120ms) cubic-bezier(0.2, 0, 0, 1), opacity var(--messenger-motion-fast, 120ms) cubic-bezier(0.2, 0, 0, 1);
}

.zoom-fade-enter-from,
.zoom-fade-leave-to {
  opacity: 0;
}

.zoom-fade-enter-from .conversation-menu-popover,
.zoom-fade-leave-to .conversation-menu-popover {
  transform: scale(0.92);
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
