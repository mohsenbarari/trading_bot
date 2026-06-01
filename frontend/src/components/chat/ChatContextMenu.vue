<template>
  <Teleport to="body">
    <!-- Radix-based accessible context menu overlay -->
    <div
      v-if="menuState.visible"
      class="context-menu"
      :class="{ 'has-reactions': showReactionRow }"
      :style="menuStyle"
      role="menu"
      aria-label="Message actions"
    >
      <div v-if="showReactionRow" class="reaction-picker-shell telegram-panel telegram-menu-shadow">
        <div class="reaction-top-grid">
          <button
            v-for="emoji in quickReactions"
            :key="emoji"
            type="button"
            class="reaction-btn"
            :class="{ 'is-active': emoji === currentUserReactionEmoji }"
            @click.stop="$emit('react', emoji)"
          >
            {{ emoji }}
          </button>
        </div>
        <button
          v-if="hasOverflowReactions"
          type="button"
          class="reaction-dropdown-toggle"
          :class="{ 'is-open': isReactionPickerExpanded }"
          @click.stop="isReactionPickerExpanded = !isReactionPickerExpanded"
        >
          <span>واکنش‌های بیشتر</span>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </button>
        <Transition name="reaction-dropdown">
          <div v-if="isReactionPickerExpanded" class="reaction-dropdown-list">
            <button
              v-for="emoji in overflowReactions"
              :key="emoji"
              type="button"
              class="reaction-btn is-secondary"
              :class="{ 'is-active': emoji === currentUserReactionEmoji }"
              @click.stop="$emit('react', emoji)"
            >
              {{ emoji }}
            </button>
          </div>
        </Transition>
      </div>
      <div class="menu-actions-panel telegram-panel telegram-menu-shadow">
        <template v-for="(section, sectionIndex) in menuModel.sections" :key="section.key">
          <div v-if="sectionIndex > 0" class="menu-divider"></div>
          <div class="menu-section-label" :class="{ 'is-danger': section.tone === 'danger' }">
            {{ section.label }}
          </div>
          <div
            v-for="item in section.items"
            :key="item.key"
            class="menu-item"
            :class="{
              'is-warning': item.tone === 'warning',
              'is-danger': item.tone === 'danger',
            }"
            v-ripple
            role="menuitem"
            @click="emitAction(item.key)"
          >
            <span class="menu-item-icon" aria-hidden="true" v-html="ACTION_ICON_SVG[item.key]"></span>
            <span class="menu-item-label">{{ item.label }}</span>
          </div>
        </template>
      </div>
    </div>

    <!-- Click outside to close -->
    <div v-if="menuState.visible" class="context-overlay" @click="$emit('close')"></div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { canShareFiles } from '../../composables/chat/useChatFileHandler'
import { buildQuickMessageReactions } from '../../utils/messageReactions'
import {
  buildMessengerContextMenuModel,
  getMessengerContextMenuStyle,
  type MessengerContextMenuActionKey,
} from '../../utils/messengerStage6ContextMenu'

const supportsFileShare = canShareFiles()

const ACTION_ICON_SVG: Record<MessengerContextMenuActionKey, string> = {
  reply: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 14 4 9 9 4"></polyline><path d="M20 20v-7a4 4 0 0 0-4-4H4"></path></svg>',
  forward: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 14 20 9 15 4"></polyline><path d="M4 20v-7a4 4 0 0 1 4-4h12"></path></svg>',
  'save-media': '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>',
  'save-album': '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>',
  share: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>',
  'share-album': '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>',
  copy: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>',
  edit: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>',
  'pin-message': '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"></path><path d="M5 7V4a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"></path><path d="M4 7h16l-3 6H7z"></path></svg>',
  delete: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>',
}

const props = defineProps<{
  menuState: {
    x: number
    y: number
    visible: boolean
    message: any | null
    messageIds?: number[]
    style?: Record<string, string> | null
  }
  isAlbumSelection: boolean
  currentUserId: number | null
  canEdit: boolean
  canDelete: boolean
  canPin: boolean
  isPinnedMessage: boolean
  availableReactions: string[]
}>()

const emit = defineEmits<{
  (e: 'react', emoji: string): void
  (e: 'reply'): void
  (e: 'forward'): void
  (e: 'copy'): void
  (e: 'edit'): void
  (e: 'delete'): void
  (e: 'pin-message'): void
  (e: 'close'): void
  (e: 'save-media'): void
  (e: 'save-album'): void
  (e: 'share'): void
  (e: 'share-album'): void
}>()

const currentUserReactionEmoji = computed(() => {
  const reactions = Array.isArray(props.menuState.message?.reactions) ? props.menuState.message.reactions : []
  const match = reactions.find((reaction: any) => Number(reaction?.user_id) === Number(props.currentUserId))
  return typeof match?.emoji === 'string' ? match.emoji : ''
})
const isReactionRowMounted = ref(false)
const isReactionPickerExpanded = ref(false)
const quickReactions = computed(() => buildQuickMessageReactions(props.availableReactions, currentUserReactionEmoji.value))
const overflowReactions = computed(() => {
  const quickSet = new Set(quickReactions.value)
  return props.availableReactions.filter((emoji) => !quickSet.has(emoji))
})
const hasOverflowReactions = computed(() => overflowReactions.value.length > 0)

const canShowReactionRow = computed(() => {
  return Boolean(props.menuState.message && !props.menuState.message?.is_deleted && props.availableReactions.length > 0)
})

const showReactionRow = computed(() => canShowReactionRow.value && isReactionRowMounted.value)

const menuModel = computed(() => buildMessengerContextMenuModel({
  messageType: props.menuState.message?.message_type,
  isAlbumSelection: props.isAlbumSelection,
  supportsFileShare,
  canEdit: props.canEdit,
  canDelete: props.canDelete,
  canPin: props.canPin,
  isPinnedMessage: props.isPinnedMessage,
  showReactionRow: showReactionRow.value,
  hasOverflowReactions: hasOverflowReactions.value,
  isReactionPickerExpanded: isReactionPickerExpanded.value,
}))

const menuStyle = computed(() => {
  if (props.menuState.style) {
    return props.menuState.style
  }

  return getMessengerContextMenuStyle({
    x: props.menuState.x,
    y: props.menuState.y,
    menuWidth: menuModel.value.menuWidth,
    menuHeight: menuModel.value.menuHeight,
    viewportWidth: typeof window !== 'undefined' ? window.innerWidth : 400,
    viewportHeight: typeof window !== 'undefined' ? window.innerHeight : 800,
  })
})

watch(
  () => props.menuState.visible,
  (visible) => {
    if (!visible) {
      isReactionPickerExpanded.value = false
      isReactionRowMounted.value = false
      return
    }

    const mountReactionRow = () => {
      if (props.menuState.visible && canShowReactionRow.value) {
        isReactionRowMounted.value = true
      }
    }

    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(() => {
        mountReactionRow()
      })
      return
    }

    mountReactionRow()
  },
  { immediate: true },
)

function emitAction(actionKey: MessengerContextMenuActionKey) {
  switch (actionKey) {
    case 'reply':
      emit('reply')
      return
    case 'forward':
      emit('forward')
      return
    case 'save-media':
      emit('save-media')
      return
    case 'save-album':
      emit('save-album')
      return
    case 'share':
      emit('share')
      return
    case 'share-album':
      emit('share-album')
      return
    case 'copy':
      emit('copy')
      return
    case 'edit':
      emit('edit')
      return
    case 'pin-message':
      emit('pin-message')
      return
    case 'delete':
      emit('delete')
      return
  }
}
</script>

<style scoped>
.context-menu {
  position: fixed;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  align-items: center;
  overflow: visible;
  direction: rtl;
}

.context-menu.has-reactions {
  gap: 6px;
}

.telegram-panel {
  width: 100%;
  background: white;
  border-radius: 12px;
  overflow-x: hidden;
  overflow-y: auto;
  max-height: calc(100vh - 16px);
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: #fff;
}

.telegram-menu-shadow {
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12), 0 1px 4px rgba(15, 23, 42, 0.08);
  transform-origin: top left;
}

/* Telegram Zoom-fade animation */
.zoom-fade-enter-active,
.zoom-fade-leave-active {
  transition: opacity 0.15s cubic-bezier(0.2, 0, 0, 1), transform 0.15s cubic-bezier(0.2, 0, 0, 1);
}

.zoom-fade-enter-from,
.zoom-fade-leave-to {
  opacity: 0;
  transform: scale(0.92);
}

.menu-item {
  padding: 10px 16px;
  width: 100%;
  box-sizing: border-box;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  color: #111827;
  transition: background 0.1s;
}

.menu-item-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.menu-item-label {
  flex: 1;
}

.menu-item:active {
  background: rgba(0,0,0,0.08);
}

.menu-item:hover {
  background: rgba(0,0,0,0.04);
}

.menu-item.is-danger {
  color: #ef4444;
  background: linear-gradient(180deg, rgba(254, 242, 242, 0.78), rgba(254, 242, 242, 0.36));
}

.menu-item.is-danger:hover {
  background: rgba(239, 68, 68, 0.06);
}

.menu-item.is-warning {
  color: #c2410c;
  background: linear-gradient(180deg, rgba(255, 247, 237, 0.82), rgba(255, 247, 237, 0.38));
}

.menu-item.is-warning:hover {
  background: rgba(249, 115, 22, 0.08);
}

.menu-section-label {
  padding: 8px 16px 4px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.2px;
  color: #64748b;
  text-transform: uppercase;
}

.menu-section-label.is-danger {
  color: #b91c1c;
}

.reaction-picker-shell {
  padding: 10px 12px 8px;
}

.menu-actions-panel {
  width: min(220px, 100%);
  padding: 4px 0;
}

.reaction-top-grid,
.reaction-dropdown-list {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}

.reaction-dropdown-list {
  max-height: 164px;
  overflow-y: auto;
  padding-top: 10px;
}

.reaction-dropdown-toggle {
  margin-top: 10px;
  width: 100%;
  min-height: 34px;
  border: none;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.04);
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  cursor: pointer;
  transition: background 0.16s ease, color 0.16s ease;
}

.reaction-dropdown-toggle svg {
  transition: transform 0.18s ease;
}

.reaction-dropdown-toggle.is-open svg {
  transform: rotate(180deg);
}

.reaction-dropdown-toggle:active,
.reaction-dropdown-toggle:hover {
  background: rgba(51, 144, 236, 0.1);
  color: #2563eb;
}

.reaction-btn {
  border: none;
  background: rgba(15, 23, 42, 0.05);
  border-radius: 14px;
  min-width: 0;
  width: 100%;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  cursor: pointer;
  transition: transform 0.12s ease, background 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease;
  border: 1px solid transparent;
}

.reaction-btn.is-secondary {
  background: rgba(15, 23, 42, 0.035);
}

.reaction-btn.is-active {
  background: linear-gradient(180deg, rgba(51, 144, 236, 0.18), rgba(51, 144, 236, 0.12));
  border-color: rgba(51, 144, 236, 0.25);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.45);
}

.reaction-btn:hover {
  background: rgba(15, 23, 42, 0.085);
}

.reaction-btn:active {
  transform: scale(0.94);
  background: rgba(51, 144, 236, 0.14);
}

.reaction-dropdown-enter-active,
.reaction-dropdown-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.reaction-dropdown-enter-from,
.reaction-dropdown-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.menu-divider {
  height: 1px;
  background: rgba(0, 0, 0, 0.06);
  margin: 4px 0;
}

.context-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1999;
}

@media (prefers-reduced-motion: reduce) {
  .zoom-fade-enter-active,
  .zoom-fade-leave-active,
  .reaction-dropdown-enter-active,
  .reaction-dropdown-leave-active {
    transition: none;
  }

  .zoom-fade-enter-from,
  .zoom-fade-leave-to,
  .reaction-dropdown-enter-from,
  .reaction-dropdown-leave-to {
    opacity: 1;
    transform: none;
  }
}
</style>
