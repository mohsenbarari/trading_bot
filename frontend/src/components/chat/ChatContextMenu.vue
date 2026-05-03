<template>
  <Teleport to="body">
    <!-- Radix-based accessible context menu overlay -->
    <Transition name="zoom-fade">
      <div 
        v-if="menuState.visible" 
        class="context-menu telegram-menu-shadow"
        :style="menuPosition"
        role="menu"
        aria-label="Message actions"
      >
        <div v-if="showReactionRow" class="reaction-picker-shell">
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
        <div v-if="showReactionRow" class="menu-divider"></div>
        <div class="menu-item" v-ripple @click="$emit('reply')" role="menuitem">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 14 4 9 9 4"></polyline><path d="M20 20v-7a4 4 0 0 0-4-4H4"></path></svg>
          <span style="flex:1;">پاسخ</span>
        </div>
        <div class="menu-item" v-ripple @click="$emit('forward')" role="menuitem">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 14 20 9 15 4"></polyline><path d="M4 20v-7a4 4 0 0 1 4-4h12"></path></svg>
          <span style="flex:1;">{{ isAlbumSelection ? 'هدایت آلبوم' : 'هدایت پیام' }}</span>
        </div>
        <template v-if="isAlbumSelection">
            <div class="menu-item" v-ripple @click="$emit('save-album')" role="menuitem">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
              <span style="flex:1;">دانلود آلبوم</span>
            </div>
            <div v-if="supportsFileShare" class="menu-item" v-ripple @click="$emit('share-album')" role="menuitem">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="18" cy="5" r="3"></circle>
                <circle cx="6" cy="12" r="3"></circle>
                <circle cx="18" cy="19" r="3"></circle>
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
              </svg>
              <span style="flex:1;">اشتراک‌گذاری آلبوم</span>
            </div>
        </template>
        <template v-if="menuState.message?.message_type === 'text'">
            <div class="menu-item" v-ripple @click="$emit('copy')" role="menuitem">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
              <span style="flex:1;">کپی کردن</span>
            </div>
        </template>
        <!-- Save media option for images/videos -->
        <template v-if="!isAlbumSelection && (menuState.message?.message_type === 'image' || menuState.message?.message_type === 'video')">
            <div class="menu-item" v-ripple @click="$emit('save-media')" role="menuitem">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
              <span style="flex:1;">ذخیره در گالری</span>
            </div>
        </template>
        <!-- Share option for any cacheable media -->
        <template v-if="supportsFileShare && !isAlbumSelection && shareableType">
            <div class="menu-item" v-ripple @click="$emit('share')" role="menuitem">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="18" cy="5" r="3"></circle>
                <circle cx="6" cy="12" r="3"></circle>
                <circle cx="18" cy="19" r="3"></circle>
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
              </svg>
              <span style="flex:1;">اشتراک‌گذاری</span>
            </div>
        </template>
        <template v-if="canEdit">
            <div class="menu-item" v-ripple @click="$emit('edit')" role="menuitem">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
              <span style="flex:1;">ویرایش</span>
            </div>
        </template>
        <template v-if="canDelete">
            <div class="menu-divider"></div>
            <div class="menu-item delete" v-ripple @click="$emit('delete')" role="menuitem">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
              <span style="flex:1;">حذف</span>
            </div>
        </template>
      </div>
    </Transition>
    
    <!-- Click outside to close -->
    <div v-if="menuState.visible" class="context-overlay" @click="$emit('close')"></div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { canShareFiles } from '../../composables/chat/useChatFileHandler'

const supportsFileShare = canShareFiles()

const props = defineProps<{
  menuState: {
    x: number
    y: number
    visible: boolean
    message: any | null
    messageIds?: number[]
  }
  isAlbumSelection: boolean
  currentUserId: number | null
  canEdit: boolean
  canDelete: boolean
  availableReactions: string[]
}>()

const _emit = defineEmits<{
  (e: 'react', emoji: string): void
  (e: 'reply'): void
  (e: 'forward'): void
  (e: 'copy'): void
  (e: 'edit'): void
  (e: 'delete'): void
  (e: 'close'): void
  (e: 'save-media'): void
  (e: 'save-album'): void
  (e: 'share'): void
  (e: 'share-album'): void
}>()

const shareableType = computed(() => {
  const t = props.menuState.message?.message_type
  return t === 'image' || t === 'video' || t === 'voice' || t === 'document'
})

const isReactionPickerExpanded = ref(false)
const quickReactions = computed(() => props.availableReactions.slice(0, 6))
const overflowReactions = computed(() => props.availableReactions.slice(6))
const hasOverflowReactions = computed(() => overflowReactions.value.length > 0)
const currentUserReactionEmoji = computed(() => {
  const reactions = Array.isArray(props.menuState.message?.reactions) ? props.menuState.message.reactions : []
  const match = reactions.find((reaction: any) => Number(reaction?.user_id) === Number(props.currentUserId))
  return typeof match?.emoji === 'string' ? match.emoji : ''
})

const showReactionRow = computed(() => {
  return Boolean(props.menuState.message && !props.menuState.message?.is_deleted && props.availableReactions.length > 0)
})

watch(
  () => props.menuState.visible,
  (visible) => {
    if (!visible) {
      isReactionPickerExpanded.value = false
    }
  },
)

// Smart positioning: keep menu within viewport bounds
const menuPosition = computed(() => {
  const menuW = 296
  const actionCount = [
    true,
    true,
    props.isAlbumSelection,
    props.isAlbumSelection && supportsFileShare,
    props.menuState.message?.message_type === 'text',
    !props.isAlbumSelection && (props.menuState.message?.message_type === 'image' || props.menuState.message?.message_type === 'video'),
    supportsFileShare && !props.isAlbumSelection && shareableType.value,
    props.canEdit,
    props.canDelete,
  ].filter(Boolean).length
  const reactionSectionHeight = showReactionRow.value
    ? hasOverflowReactions.value
      ? (isReactionPickerExpanded.value ? 230 : 100)
      : 68
    : 0
  const dividerCount = (showReactionRow.value ? 1 : 0) + (props.canDelete ? 1 : 0)
  const menuH = reactionSectionHeight + actionCount * 44 + dividerCount * 9 + 24
  const vw = typeof window !== 'undefined' ? window.innerWidth : 400
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800
  
  let x = props.menuState.x
  let y = props.menuState.y
  
  // Prevent overflow right
  if (x + menuW > vw - 8) x = vw - menuW - 8
  // Prevent overflow left
  if (x < 8) x = 8
  // Prevent overflow bottom
  if (y + menuH > vh - 8) y = vh - menuH - 8
  // Prevent overflow top
  if (y < 8) y = 8
  
  return {
    top: y + 'px',
    left: x + 'px'
  }
})
</script>

<style scoped>
.context-menu {
  position: fixed;
  background: white;
  border-radius: 12px;
  width: min(296px, calc(100vw - 16px));
  min-width: 190px;
  z-index: 2000;
  overflow-x: hidden;
  overflow-y: auto;
  max-height: calc(100vh - 16px);
  padding: 4px 0;
  direction: rtl;
  backdrop-filter: blur(16px);
  background: rgba(255, 255, 255, 0.96);
}

.telegram-menu-shadow {
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12), 0 2px 8px rgba(0, 0, 0, 0.06);
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
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  color: #111827;
  transition: background 0.1s;
}

.menu-item:active {
  background: rgba(0,0,0,0.08);
}

.menu-item:hover {
  background: rgba(0,0,0,0.04);
}

.menu-item.delete {
  color: #ef4444;
}

.menu-item.delete:hover {
  background: rgba(239, 68, 68, 0.06);
}

.reaction-picker-shell {
  padding: 10px 12px 8px;
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
</style>
