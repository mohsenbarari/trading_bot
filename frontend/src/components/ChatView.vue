<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted } from 'vue'
import LoadingSkeleton from './LoadingSkeleton.vue'

// Props
const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
  currentUserId: number
}>()

// Emits
const emit = defineEmits<{
  (e: 'navigate', view: string, payload?: any): void
  (e: 'back'): void
}>()

// Interfaces
interface Conversation {
  id: number
  other_user_id: number
  other_user_name: string
  last_message_content: string | null
  last_message_type: string | null
  last_message_at: string | null
  unread_count: number
}

interface Message {
  id: number
  sender_id: number
  receiver_id: number
  content: string
  message_type: 'text' | 'image' | 'sticker'
  is_read: boolean
  created_at: string
}

interface StickerPack {
  id: string
  name: string
  stickers: string[]
}

// State
const isLoading = ref(true)
const error = ref('')

// Conversations
const conversations = ref<Conversation[]>([])
const selectedUserId = ref<number | null>(null)
const selectedUserName = ref('')

// Messages
const messages = ref<Message[]>([])
const isLoadingMessages = ref(false)

// Input
const messageInput = ref('')
const isSending = ref(false)

// Stickers
const stickerPacks = ref<StickerPack[]>([])
const showStickerPicker = ref(false)

// Image upload
const imageInput = ref<HTMLInputElement | null>(null)
const isUploading = ref(false)

// Poll timer
let pollTimer: number | null = null
const POLL_INTERVAL = 2000

// API Helper
async function apiFetch(endpoint: string, options: RequestInit = {}) {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {})
  }
  if (props.jwtToken) {
    headers['Authorization'] = `Bearer ${props.jwtToken}`
  }
  const res = await fetch(`${props.apiBaseUrl}/api${endpoint}`, {
    ...options,
    headers
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'خطای سرور' }))
    throw new Error(err.detail || `HTTP Error ${res.status}`)
  }
  if (res.status === 204) return null
  return res.json()
}

// Load conversations
async function loadConversations() {
  try {
    conversations.value = await apiFetch('/chat/conversations')
  } catch (e: any) {
    error.value = e.message
  }
}

// Load messages
async function loadMessages(userId: number, silent = false) {
  if (!silent) isLoadingMessages.value = true
  try {
    messages.value = await apiFetch(`/chat/messages/${userId}`)
    // Mark as read
    await apiFetch(`/chat/read/${userId}`, { method: 'POST' })
    // Update unread count in conversation list
    const conv = conversations.value.find(c => c.other_user_id === userId)
    if (conv) conv.unread_count = 0
  } catch (e: any) {
    if (!silent) error.value = e.message
  } finally {
    if (!silent) isLoadingMessages.value = false
  }
}

// Send message
async function sendMessage(type: 'text' | 'image' | 'sticker' = 'text', content?: string) {
  if (!selectedUserId.value) return
  
  const msgContent = content || messageInput.value.trim()
  if (!msgContent) return
  
  isSending.value = true
  try {
    const newMsg = await apiFetch('/chat/send', {
      method: 'POST',
      body: JSON.stringify({
        receiver_id: selectedUserId.value,
        content: msgContent,
        message_type: type
      })
    })
    messages.value.push(newMsg)
    messageInput.value = ''
    showStickerPicker.value = false
    scrollToBottom()
  } catch (e: any) {
    error.value = e.message
  } finally {
    isSending.value = false
  }
}

// Select conversation
function selectConversation(conv: Conversation) {
  selectedUserId.value = conv.other_user_id
  selectedUserName.value = conv.other_user_name
  loadMessages(conv.other_user_id)
}

// Start new chat (from search or profile)
function startNewChat(userId: number, userName: string) {
  selectedUserId.value = userId
  selectedUserName.value = userName
  loadMessages(userId)
}

// Upload image
async function handleImageUpload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  
  const file = input.files[0]
  if (!file) return
  
  const formData = new FormData()
  formData.append('file', file)
  
  isUploading.value = true
  try {
    const res = await fetch(`${props.apiBaseUrl}/api/chat/upload-image`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${props.jwtToken}`
      },
      body: formData
    })
    if (!res.ok) throw new Error('خطا در آپلود تصویر')
    const data = await res.json()
    await sendMessage('image', data.url)
  } catch (e: any) {
    error.value = e.message
  } finally {
    isUploading.value = false
    if (input) input.value = ''
  }
}

// Load stickers
async function loadStickers() {
  try {
    stickerPacks.value = await apiFetch('/chat/stickers')
  } catch (e) {
    console.warn('Failed to load stickers')
  }
}

// Send sticker
function sendSticker(stickerId: string) {
  sendMessage('sticker', stickerId)
}

// Poll for new messages
async function poll() {
  if (!selectedUserId.value) return
  await loadMessages(selectedUserId.value, true)
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(poll, POLL_INTERVAL)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// Scroll to bottom
function scrollToBottom() {
  setTimeout(() => {
    const container = document.querySelector('.messages-container')
    if (container) container.scrollTop = container.scrollHeight
  }, 50)
}

// Format time
function formatTime(dateStr: string) {
  const date = new Date(dateStr)
  return date.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })
}

// Go back
function goBack() {
  if (selectedUserId.value) {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
    stopPolling()
  } else {
    emit('back')
  }
}

// Computed
const sortedConversations = computed(() => {
  return [...conversations.value].sort((a, b) => {
    if (!a.last_message_at) return 1
    if (!b.last_message_at) return -1
    return new Date(b.last_message_at).getTime() - new Date(a.last_message_at).getTime()
  })
})

const totalUnread = computed(() => {
  return conversations.value.reduce((sum, c) => sum + c.unread_count, 0)
})

// Watchers
watch(selectedUserId, (newVal) => {
  if (newVal) {
    startPolling()
    scrollToBottom()
  } else {
    stopPolling()
  }
})

// Lifecycle
onMounted(async () => {
  isLoading.value = true
  await loadConversations()
  await loadStickers()
  isLoading.value = false
})

onUnmounted(() => {
  stopPolling()
})

// Expose for parent to start new chat
defineExpose({ startNewChat })
</script>

<template>
  <div class="chat-view">
    <!-- Header -->
    <div class="chat-header">
      <button class="back-btn" @click="goBack">
        <span>→</span>
      </button>
      <div class="header-title">
        <template v-if="selectedUserId">
          {{ selectedUserName }}
        </template>
        <template v-else>
          پیام‌ها
          <span v-if="totalUnread > 0" class="badge">{{ totalUnread }}</span>
        </template>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="isLoading" class="loading-state">
      <LoadingSkeleton :count="5" :height="60" />
    </div>

    <!-- Error -->
    <div v-else-if="error" class="error-state">
      <p>{{ error }}</p>
      <button @click="error = ''; loadConversations()">تلاش مجدد</button>
    </div>

    <!-- Conversation List -->
    <div v-else-if="!selectedUserId" class="conversations-list">
      <div v-if="conversations.length === 0" class="empty-state">
        <span>💬</span>
        <p>هنوز گفتگویی ندارید</p>
      </div>
      <div 
        v-for="conv in sortedConversations" 
        :key="conv.id"
        class="conversation-item"
        :class="{ 'has-unread': conv.unread_count > 0 }"
        @click="selectConversation(conv)"
      >
        <div class="conv-avatar">{{ conv.other_user_name.charAt(0) }}</div>
        <div class="conv-content">
          <div class="conv-header">
            <span class="conv-name">{{ conv.other_user_name }}</span>
            <span class="conv-time" v-if="conv.last_message_at">
              {{ formatTime(conv.last_message_at) }}
            </span>
          </div>
          <div class="conv-preview">
            <template v-if="conv.last_message_type === 'image'">🖼️ تصویر</template>
            <template v-else-if="conv.last_message_type === 'sticker'">😊 استیکر</template>
            <template v-else>{{ conv.last_message_content?.substring(0, 30) || '...' }}</template>
          </div>
        </div>
        <div v-if="conv.unread_count > 0" class="unread-badge">
          {{ conv.unread_count }}
        </div>
      </div>
    </div>

    <!-- Messages View -->
    <template v-else>
      <div v-if="isLoadingMessages" class="loading-state">
        <LoadingSkeleton :count="8" :height="50" />
      </div>
      
      <div v-else class="messages-container">
        <div v-if="messages.length === 0" class="empty-state">
          <span>💬</span>
          <p>شروع گفتگو...</p>
        </div>
        
        <div 
          v-for="msg in messages" 
          :key="msg.id"
          class="message-bubble"
          :class="{ 'sent': msg.sender_id === props.currentUserId, 'received': msg.sender_id !== props.currentUserId }"
        >
          <!-- Text -->
          <template v-if="msg.message_type === 'text'">
            <p>{{ msg.content }}</p>
          </template>
          
          <!-- Image -->
          <template v-else-if="msg.message_type === 'image'">
            <img :src="msg.content" alt="تصویر" class="msg-image" />
          </template>
          
          <!-- Sticker -->
          <template v-else-if="msg.message_type === 'sticker'">
            <div class="msg-sticker">{{ msg.content }}</div>
          </template>
          
          <span class="msg-time">{{ formatTime(msg.created_at) }}</span>
        </div>
      </div>

      <!-- Input Area -->
      <div class="input-area">
        <!-- Sticker Picker Toggle -->
        <button class="sticker-toggle" @click="showStickerPicker = !showStickerPicker">
          😊
        </button>
        
        <!-- Image Upload -->
        <input 
          type="file" 
          ref="imageInput" 
          accept="image/*" 
          style="display: none" 
          @change="handleImageUpload"
        />
        <button class="image-btn" @click="imageInput?.click()" :disabled="isUploading">
          🖼️
        </button>

        <!-- Text Input -->
        <input 
          v-model="messageInput"
          type="text"
          placeholder="پیام خود را بنویسید..."
          @keyup.enter="sendMessage()"
          :disabled="isSending"
        />
        
        <!-- Send Button -->
        <button class="send-btn" @click="sendMessage()" :disabled="isSending || !messageInput.trim()">
          ارسال
        </button>
      </div>

      <!-- Sticker Picker -->
      <div v-if="showStickerPicker" class="sticker-picker">
        <div v-for="pack in stickerPacks" :key="pack.id" class="sticker-pack">
          <div class="pack-name">{{ pack.name }}</div>
          <div class="stickers-grid">
            <button 
              v-for="sticker in pack.stickers" 
              :key="sticker"
              class="sticker-item"
              @click="sendSticker(sticker)"
            >
              {{ sticker }}
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-color);
}

/* Header */
.chat-header {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  background: var(--card-bg);
  border-bottom: 1px solid var(--border-color);
  gap: 12px;
}

.back-btn {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  padding: 4px 8px;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}

.badge {
  background: #ff3b30;
  color: white;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
}

/* Loading & Empty States */
.loading-state, .error-state, .empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px;
  color: var(--text-secondary);
}

.empty-state span {
  font-size: 48px;
  margin-bottom: 12px;
}

.error-state button {
  margin-top: 12px;
  padding: 8px 16px;
  background: var(--primary-color);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}

/* Conversations List */
.conversations-list {
  flex: 1;
  overflow-y: auto;
}

.conversation-item {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  transition: background 0.2s;
}

.conversation-item:hover {
  background: rgba(0,0,0,0.02);
}

.conversation-item.has-unread {
  background: rgba(0, 122, 255, 0.05);
}

.conv-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: linear-gradient(135deg, #007aff, #0056b3);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 600;
  margin-left: 12px;
}

.conv-content {
  flex: 1;
  min-width: 0;
}

.conv-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 4px;
}

.conv-name {
  font-weight: 600;
  font-size: 14px;
}

.conv-time {
  font-size: 11px;
  color: var(--text-secondary);
}

.conv-preview {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.unread-badge {
  min-width: 20px;
  height: 20px;
  background: #ff3b30;
  color: white;
  border-radius: 10px;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 6px;
}

/* Messages Container */
.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.message-bubble {
  max-width: 75%;
  padding: 10px 14px;
  border-radius: 16px;
  position: relative;
}

.message-bubble.sent {
  align-self: flex-start;
  background: linear-gradient(135deg, #007aff, #0056b3);
  color: white;
  border-bottom-left-radius: 4px;
}

.message-bubble.received {
  align-self: flex-end;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-bottom-right-radius: 4px;
}

.message-bubble p {
  margin: 0;
  word-break: break-word;
}

.msg-time {
  font-size: 10px;
  opacity: 0.7;
  display: block;
  margin-top: 4px;
  text-align: left;
}

.msg-image {
  max-width: 200px;
  max-height: 200px;
  border-radius: 8px;
}

.msg-sticker {
  font-size: 48px;
}

/* Input Area */
.input-area {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  background: var(--card-bg);
  border-top: 1px solid var(--border-color);
  gap: 8px;
}

.input-area input[type="text"] {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid var(--border-color);
  border-radius: 20px;
  font-size: 14px;
  outline: none;
}

.input-area input:focus {
  border-color: var(--primary-color);
}

.sticker-toggle, .image-btn {
  background: none;
  border: none;
  font-size: 22px;
  cursor: pointer;
  padding: 4px;
}

.send-btn {
  padding: 10px 16px;
  background: linear-gradient(135deg, #007aff, #0056b3);
  color: white;
  border: none;
  border-radius: 20px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Sticker Picker */
.sticker-picker {
  background: var(--card-bg);
  border-top: 1px solid var(--border-color);
  padding: 12px;
  max-height: 200px;
  overflow-y: auto;
}

.sticker-pack {
  margin-bottom: 12px;
}

.pack-name {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.stickers-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
}

.sticker-item {
  background: var(--bg-color);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 8px;
  font-size: 20px;
  cursor: pointer;
  transition: transform 0.2s;
}

.sticker-item:hover {
  transform: scale(1.1);
}
</style>
