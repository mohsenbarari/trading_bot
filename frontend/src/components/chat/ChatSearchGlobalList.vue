<script setup lang="ts">
import { computed } from 'vue'
import { type Conversation } from '../../types/chat'

const props = defineProps<{
  searchResults: any[]
  searchQuery: string
  conversations: Conversation[]
  currentUserId: number | null
}>()

const emit = defineEmits<{
  (e: 'select-result', msg: any): void
}>()

function formatTime(dateStr: string) {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })
}

function getOtherUserInfo(msg: any) {
  const otherId = msg.sender_id === props.currentUserId ? msg.receiver_id : msg.sender_id
  const conv = props.conversations.find(c => c.other_user_id === otherId)
  return {
      name: conv ? conv.other_user_name : (msg.sender_id === props.currentUserId ? 'ناشناس' : (msg.sender?.account_name || 'ناشناس')),
      isDeleted: conv ? conv.other_user_is_deleted : false
  }
}

function highlightText(text: string | undefined) {
  if (!text) return ''
  if (!props.searchQuery) return text
  
  // Basic RegExp to wrap the match in a span
  const escapedQuery = props.searchQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const regex = new RegExp(`(${escapedQuery})`, 'gi')
  return text.replace(regex, '<span class="s-highlight">$1</span>')
}
</script>

<template>
  <div class="conversations-list search-global-list">
    <!-- Header -->
    <div class="search-section-header" v-if="searchResults.length > 0">
       <span class="search-header-title">پیام‌ها</span>
    </div>
    
    <div v-if="searchResults.length === 0" class="empty-state">
      <span>🔍</span>
      <p>نتیجه‌ای یافت نشد</p>
    </div>
    
    <div 
      v-for="msg in searchResults" 
      :key="msg.id"
      class="conversation-item search-result-item"
      v-ripple
      @click="emit('select-result', msg)"
    >
      <div class="conv-avatar">
        {{ getOtherUserInfo(msg).name.charAt(0) }}
      </div>
      <div class="conv-content">
        <div class="conv-header">
          <span class="conv-name">
            {{ getOtherUserInfo(msg).name }}
            <span v-if="getOtherUserInfo(msg).isDeleted" class="deleted-badge-list">غیرفعال</span>
          </span>
          <span class="conv-time">
            {{ formatTime(msg.created_at) }}
          </span>
        </div>
        <div class="conv-preview" v-html="highlightText(msg.content)">
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.conversations-list { flex: 1; overflow-y: auto; background: #ffffff; }
.search-section-header {
  padding: 8px 16px; background: #f4f4f5; font-size: 13px; font-weight: 500; color: #8e8e93; border-bottom: 1px solid #f0f0f0;
}

.conversation-item {
  display: flex; padding: 12px 16px; align-items: center; border-bottom: 1px solid #f0f0f0; cursor: pointer; background: white; transition: background-color 0.2s;
}
.conversation-item:hover { background: #f4f4f5; }

.conv-avatar {
  width: 50px; height: 50px; min-width: 50px; border-radius: 50%;
  background: linear-gradient(135deg, #3b82f6, #2563eb); /* Search results often blueish or default gradient */
  color: white; display: flex; align-items: center; justify-content: center;
  font-size: 20px; font-weight: 500; margin-left: 12px;
}

.conv-content { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; }
.conv-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.conv-name { font-weight: 600; font-size: 16px; color: #000; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: flex; align-items: center; gap: 6px; }
.conv-time { font-size: 12px; color: #8e8e93; white-space: nowrap; }
.conv-preview { font-size: 14px; color: #8e8e93; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* The highlighted text */
:deep(.s-highlight) {
  color: #3390ec;
  font-weight: 500;
}

.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: #79828b; font-size: 16px; }
.empty-state span { font-size: 48px; margin-bottom: 16px; opacity: 0.8; }
.deleted-badge-list { font-size: 10px; background: #fee2e2; color: #ef4444; padding: 2px 6px; border-radius: 4px; }
</style>
