<template>
  <Teleport to="body">
    <Transition name="zoom-fade">
      <div 
        v-if="menuState.visible" 
        class="context-menu telegram-menu-shadow"
        :style="{ top: menuState.y + 'px', left: menuState.x + 'px' }"
      >
        <div class="menu-item" v-ripple @click="$emit('reply')">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 14 4 9 9 4"></polyline><path d="M20 20v-7a4 4 0 0 0-4-4H4"></path></svg>
          <span style="flex:1;">پاسخ</span>
        </div>
        <div class="menu-item" v-ripple @click="$emit('forward')">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 14 20 9 15 4"></polyline><path d="M4 20v-7a4 4 0 0 1 4-4h12"></path></svg>
          <span style="flex:1;">هدایت پیام</span>
        </div>
        <template v-if="menuState.message?.message_type === 'text'">
            <div class="menu-item" v-ripple @click="$emit('copy')">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
              <span style="flex:1;">کپی کردن</span>
            </div>
        </template>
        <template v-if="canEdit">
            <div class="menu-item" v-ripple @click="$emit('edit')">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
              <span style="flex:1;">ویرایش</span>
            </div>
        </template>
        <template v-if="canDelete">
            <div class="menu-item delete" v-ripple @click="$emit('delete')">
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
const props = defineProps<{
  menuState: {
    x: number
    y: number
    visible: boolean
    message: any | null
  }
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
}>()
</script>

<style scoped>
.context-menu {
  position: fixed; /* Must be fixed since it's teleported to body */
  background: white;
  border-radius: 12px;
  min-width: 180px;
  z-index: 2000;
  overflow: hidden; /* For ripple effect containment */
  padding: 4px 0;
  direction: rtl; /* Guarantee right-to-left layout since teleported */
}

.telegram-menu-shadow {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1), 0 1px 4px rgba(0, 0, 0, 0.05); /* Softer nested shadow */
  transform-origin: top left; /* Menu scales from point of click */
}

/* Telegram Zoom-fade animation classes */
.zoom-fade-enter-active,
.zoom-fade-leave-active {
  transition: opacity 0.15s cubic-bezier(0.2, 0, 0, 1), transform 0.15s cubic-bezier(0.2, 0, 0, 1);
}

.zoom-fade-enter-from,
.zoom-fade-leave-to {
  opacity: 0;
  transform: scale(0.95);
}

.menu-item {
  padding: 10px 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  color: #111827;
  transition: background 0.1s; /* Faster hover transition */
}

.menu-item:hover {
  background: rgba(0,0,0,0.05);
}

.menu-item.delete {
  color: #ef4444;
}

.menu-item.delete:hover {
  background: rgba(239, 68, 68, 0.1);
}

.context-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1999; /* Below context menu */
}
</style>
