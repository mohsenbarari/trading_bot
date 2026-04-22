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
import { computed } from 'vue'

const props = defineProps<{
  menuState: {
    x: number
    y: number
    visible: boolean
    message: any | null
    messageIds?: number[]
  }
  isAlbumSelection: boolean
  canEdit: boolean
  canDelete: boolean
}>()

const emit = defineEmits<{
  (e: 'reply'): void
  (e: 'forward'): void
  (e: 'copy'): void
  (e: 'edit'): void
  (e: 'delete'): void
  (e: 'close'): void
  (e: 'save-media'): void
  (e: 'save-album'): void
}>()

// Smart positioning: keep menu within viewport bounds
const menuPosition = computed(() => {
  const menuW = 200
  const menuH = 250
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
  min-width: 190px;
  z-index: 2000;
  overflow: hidden;
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
