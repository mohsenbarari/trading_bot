<script setup lang="ts">
import { type Conversation } from '../../types/chat'

const props = defineProps<{
  conversations: Conversation[]
  selectedUserId: number | null
  typingUsers: Record<number, boolean>
}>()

const emit = defineEmits<{
  (e: 'select-conversation', conv: Conversation): void
  (e: 'new-conversation'): void
}>()

function formatTime(dateStr: string) {
  const date = new Date(dateStr)
  return date.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })
}

function isUserOnline(lastSeen: string | null | undefined): boolean {
  if (!lastSeen) return false
  const date = new Date(lastSeen)
  return (new Date().getTime() - date.getTime()) < 180000
}
</script>

<template>
  <div class="conversation-list-wrapper">
    <div class="conversations-list">
    <div v-if="conversations.length === 0" class="empty-state">
      <span>💬</span>
      <p>هنوز گفتگویی ندارید</p>
    </div>
    <div 
      v-for="conv in conversations" 
      :key="conv.id"
      class="conversation-item"
      v-ripple
      :class="{ 'has-unread': conv.unread_count > 0, 'active': selectedUserId === conv.other_user_id }"
      @click="emit('select-conversation', conv)"
    >
      <div class="conv-avatar">
        {{ conv.other_user_name.charAt(0) }}
        <div v-if="isUserOnline(conv.other_user_last_seen_at)" class="online-indicator-dot"></div>
      </div>
      <div class="conv-content">
        <div class="conv-header">
          <span class="conv-name">
            {{ conv.other_user_name }}
            <span v-if="conv.other_user_is_deleted" class="deleted-badge-list">غیرفعال</span>
          </span>
          <span class="conv-time" v-if="conv.last_message_at">
            {{ formatTime(conv.last_message_at) }}
          </span>
        </div>
        <div class="conv-preview">
          <span v-if="typingUsers[conv.other_user_id]" class="typing-text">
             🖊️ در حال نوشتن...
          </span>
          <template v-else>
              <template v-if="conv.last_message_type === 'image'">🖼️ تصویر</template>
              <template v-else-if="conv.last_message_type === 'sticker'">😊 استیکر</template>
              <template v-else>{{ conv.last_message_content?.substring(0, 30) || '...' }}</template>
          </template>
        </div>
      </div>
      <div v-if="conv.unread_count > 0" class="unread-badge">
        {{ conv.unread_count }}
      </div>
    </div>
    </div>
    
    <!-- Floating Action Button for New Chat -->
    <button class="fab-new-chat" v-ripple @click="emit('new-conversation')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        <line x1="12" y1="9" x2="12" y2="13"></line>
        <line x1="10" y1="11" x2="14" y2="11"></line>
      </svg>
    </button>
  </div>
</template>

<style scoped>
.conversation-list-wrapper {
  flex: 1;
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.conversations-list {
  flex: 1;
  overflow-y: auto;
  background: #ffffff;
  /* Telegram classic sidebar scrollbar styling */
  scrollbar-width: thin;
  scrollbar-color: rgba(0,0,0,0.2) transparent;
}

.conversations-list::-webkit-scrollbar {
  width: 6px;
}

.conversations-list::-webkit-scrollbar-thumb {
  background: rgba(0,0,0,0.2);
  border-radius: 4px;
}

.conversation-item {
  display: flex;
  padding: 12px 16px;
  align-items: center;
  border-bottom: 1px solid #f0f0f0; /* Subtle separator */
  cursor: pointer;
  background: white;
  transition: background-color 0.2s, background-image 0.2s; /* Add smooth hover */
}

/* Telegram hover effect */
.conversation-item:hover {
  background: #f4f4f5;
}

.conversation-item.active {
  background: #3390ec; /* Classic Telegram blue */
  color: white; /* Important for contrast */
}

/* Override child text colors when active for contrast */
.conversation-item.active .conv-name {
  color: white;
}
.conversation-item.active .conv-time {
  color: rgba(255,255,255,0.8);
}
.conversation-item.active .conv-preview {
  color: rgba(255,255,255,0.9);
}
.conversation-item.active .typing-text {
  color: #fff;
}

.conv-avatar {
  width: 50px;
  height: 50px;
  min-width: 50px;
  border-radius: 50%;
  background: linear-gradient(135deg, #10b981, #059669); /* Rich green gradient */
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 500;
  margin-left: 12px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  position: relative;
}

.online-indicator-dot {
  position: absolute;
  bottom: 0px;
  left: 0px;
  width: 14px;
  height: 14px;
  background-color: #4CAF50; /* Bright online Green */
  border: 2.5px solid #ffffff; /* White stroke matching background */
  border-radius: 50%;
  z-index: 2;
}
/* Ensure the border matches the blue background if selected */
.conversation-item.active .online-indicator-dot {
  border-color: #3390ec;
}

.conv-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.conv-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.conv-name {
  font-weight: 600; /* Bold sender name */
  font-size: 16px;
  color: #000;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: flex;
  align-items: center;
  gap: 6px;
}

.deleted-badge-list {
  font-size: 10px;
  background: #fee2e2;
  color: #ef4444;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: normal;
}

.conversation-item.active .deleted-badge-list {
    background: rgba(255,255,255,0.2) !important;
    color: white !important;
}

.conv-time {
  font-size: 12px;
  color: #8e8e93; /* Classic light gray */
  white-space: nowrap;
}

.conv-preview {
  font-size: 14px;
  color: #8e8e93;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-right: 2px;
}

/* Make preview blue/highlighted if unread */
.conversation-item.has-unread .conv-preview {
  /* No bold/color change for preview in standard Telegram usually, 
     but keeping it if desired via standard styles */
}

/* Special formatting for typing indicator */
.typing-text {
  color: #3390ec;
  font-weight: 500;
}


.unread-badge {
  background: #c6cdd3; /* Telegram neutral gray badge for muted/normal */
  color: white;
  border-radius: 12px;
  min-width: 24px;
  height: 24px;
  padding: 0 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: bold;
  margin-right: 8px; /* Push to left boundary (RTL) */
}

/* If active chat, change badge color to inverse */
.conversation-item.active .unread-badge {
  background: #ffffff;
  color: #3390ec;
}

/* We don't have muted state yet, but if it's unread, Telegram uses a blue or gray badge. 
   We'll stick to a noticeable default, e.g. blueish */
.conversation-item.has-unread:not(.active) .unread-badge {
   background: #3390ec; 
}


.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #79828b;
  font-size: 16px;
}

.empty-state span {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.8;
}

/* FAB */
.fab-new-chat {
  position: absolute;
  bottom: 24px;
  right: 24px;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background-color: #3390ec;
  color: white;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px rgba(51, 144, 236, 0.4);
  cursor: pointer;
  z-index: 10;
  transition: transform 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}

.fab-new-chat:hover {
  transform: scale(1.05);
}

.fab-new-chat:active {
  transform: scale(0.95);
}

.fab-new-chat svg {
  width: 26px;
  height: 26px;
}
</style>
