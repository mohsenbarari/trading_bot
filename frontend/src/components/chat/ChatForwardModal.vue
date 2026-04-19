<script setup lang="ts">
import { type Conversation } from '../../types/chat'

const props = defineProps<{
  showForwardModal: boolean
  sortedConversations: Conversation[]
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'forward-to', targetUserId: number): void
}>()
</script>

<template>
  <Teleport to="body">
    <Transition name="modal-slide">
      <div v-if="showForwardModal" class="forward-modal-overlay" @click="emit('close')">
        <div class="forward-modal" @click.stop>
          <div class="forward-modal-header">
            <h3>ارسال به...</h3>
            <button class="close-btn" @click="emit('close')">✕</button>
          </div>
          <div class="forward-modal-body">
            <div 
              v-for="conv in sortedConversations" 
              :key="conv.id"
              class="forward-conv-item"
              @click="emit('forward-to', conv.other_user_id)"
            >
              <div class="conv-avatar">
                {{ conv.other_user_name.charAt(0) }}
              </div>
              <div class="conv-name">{{ conv.other_user_name }}</div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.forward-modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.forward-modal {
  background: white;
  width: 90%;
  max-width: 400px;
  max-height: 80vh;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.forward-modal-header {
  padding: 16px;
  border-bottom: 1px solid #e0e0e0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.forward-modal-header h3 {
  margin: 0;
  font-size: 18px;
}

.close-btn {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: #888;
}

.forward-modal-body {
  flex: 1;
  overflow-y: auto;
}

.forward-conv-item {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
}

.forward-conv-item:hover {
  background: #f9f9f9;
}

.forward-conv-item .conv-avatar {
  width: 40px;
  height: 40px;
  min-width: 40px;
  border-radius: 50%;
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  font-size: 16px;
  margin-left: 12px;
}

.forward-conv-item .conv-name {
  font-size: 16px;
  color: #333;
}

/* Modal slide-up transition */
.modal-slide-enter-active,
.modal-slide-leave-active {
  transition: opacity 0.25s ease;
}
.modal-slide-enter-active .forward-modal,
.modal-slide-leave-active .forward-modal {
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.25s ease;
}
.modal-slide-enter-from,
.modal-slide-leave-to {
  opacity: 0;
}
.modal-slide-enter-from .forward-modal {
  transform: translateY(40px) scale(0.95);
  opacity: 0;
}
.modal-slide-leave-to .forward-modal {
  transform: translateY(20px) scale(0.97);
  opacity: 0;
}
</style>
