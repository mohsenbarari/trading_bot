<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted, nextTick } from 'vue'
import LoadingSkeleton from './LoadingSkeleton.vue'

// Props
const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
  currentUserId: number
  targetUserId?: number
  targetUserName?: string
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
  is_deleted?: boolean
  updated_at?: string
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
const messagesContainer = ref<HTMLElement | null>(null)
const isUserAtBottom = ref(true)
const unreadNewMessagesCount = ref(0)
const showScrollButton = ref(false)

// Input
const messageInput = ref('')
const isSending = ref(false)
const messageInputRef = ref<HTMLTextAreaElement | null>(null)
const editingMessage = ref<Message | null>(null)

// Stickers
const stickerPacks = ref<StickerPack[]>([])
const showStickerPicker = ref(false)

// Image upload
const imageInput = ref<HTMLInputElement | null>(null)
const isUploading = ref(false)

// UI State
const isMobile = ref(false)
const contextMenu = ref<{ visible: boolean; x: number; y: number; message: Message | null }>({ visible: false, x: 0, y: 0, message: null })
const longPressTimer = ref<number | null>(null)

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
// Load messages
async function loadMessages(userId: number, silent = false) {
  if (!silent) isLoadingMessages.value = true
  try {
    // Add timestamp to prevent caching
    const loadedMessages = await apiFetch(`/chat/messages/${userId}?_t=${Date.now()}`)
    
    if (silent) {
      // Check for strictly new message (by ID)
      const lastOldMsg = messages.value[messages.value.length - 1]
      const lastNewMsg = loadedMessages[loadedMessages.length - 1]
      const isNewMessage = lastNewMsg && (!lastOldMsg || lastNewMsg.id !== lastOldMsg.id)
      const oldLength = messages.value.length

      // Always update list to ensure consistency
      messages.value = loadedMessages
      
      if (isNewMessage) {
        if (lastNewMsg.sender_id !== props.currentUserId) {
          // Message from other user
          if (isUserAtBottom.value) {
            // If at bottom, auto-scroll and mark read
            await nextTick()
            scrollToBottom()
            markAsRead()
          } else {
            // If scrolled up, increment badge (safely handle length changes)
             const diff = loadedMessages.length - oldLength
             unreadNewMessagesCount.value += (diff > 0 ? diff : 1)
          }
        } else if (lastNewMsg.sender_id === props.currentUserId) {
          // Own message (synced), keep bottom if there
          if (isUserAtBottom.value) {
            await nextTick()
            scrollToBottom()
          }
        }
      }
    } else {
      // Initial load
      messages.value = loadedMessages
      unreadNewMessagesCount.value = 0
      isLoadingMessages.value = false
      await nextTick()
      scrollToUnreadOrBottom()
      markAsRead()
    }
  } catch (e: any) {
    if (!silent) error.value = e.message
    if (!silent) isLoadingMessages.value = false 
  } finally {
    if (!silent && isLoadingMessages.value) isLoadingMessages.value = false
  }
}

// Mark current chat as read
async function markAsRead() {
  if (!selectedUserId.value) return
  try {
    await apiFetch(`/chat/read/${selectedUserId.value}`, { method: 'POST' })
    // Update local unread count immediately
    const conv = conversations.value.find(c => c.other_user_id === selectedUserId.value)
    if (conv) conv.unread_count = 0
  } catch (e) {
    console.error('Failed to mark as read', e)
  }
}

// Send message
// Send message
async function sendMediaMessage(type: 'image' | 'sticker', content: string) {
  if (!selectedUserId.value) return
  
  isSending.value = true
  try {
    const newMsg = await apiFetch('/chat/send', {
      method: 'POST',
      body: JSON.stringify({
        receiver_id: selectedUserId.value,
        content: content,
        message_type: type
      })
    })
    messages.value.push(newMsg)
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
  // Clear edit/context state
  contextMenu.value.visible = false;
  editingMessage.value = null;
  messageInput.value = '';
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
    await sendMediaMessage('image', data.url)
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
  sendMediaMessage('sticker', stickerId)
}

// Poll for new messages
// Poll for new messages
async function poll() {
  // Always update conversation list to show new unread counts/chats
  await loadConversations()
  
  if (selectedUserId.value) {
    await loadMessages(selectedUserId.value, true)
  }
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



// Context Menu Logic
const showContextMenu = (event: MouseEvent | TouchEvent, msg: Message) => {
  if (msg.sender_id !== props.currentUserId) return; // Only own messages
  // Check 48h limit
  const msgTime = new Date(msg.created_at).getTime();
  const now = Date.now();
  if (now - msgTime > 48 * 60 * 60 * 1000) return;

  let clientX = 0;
  let clientY = 0;

  if (event instanceof MouseEvent) {
    event.preventDefault();
    clientX = event.clientX;
    clientY = event.clientY;
  } else if (event instanceof TouchEvent && event.touches.length > 0) {
    const touch = event.touches[0];
    if (touch) {
      clientX = touch.clientX;
      clientY = touch.clientY;
    }
  }

  // Initial position (will be adjusted)
  const menuWidth = 160;
  const menuHeight = 100;
  const padding = 10;

  // Prevent overflow right
  if (clientX + menuWidth > window.innerWidth) {
    clientX = window.innerWidth - menuWidth - padding;
  }
  // Prevent overflow bottom
  if (clientY + menuHeight > window.innerHeight) {
    clientY = window.innerHeight - menuHeight - padding;
  }

  contextMenu.value = {
    visible: true,
    x: clientX,
    y: clientY,
    message: msg
  };
};

const closeContextMenu = () => {
  contextMenu.value.visible = false;
};

const handleLongPressStart = (event: TouchEvent, msg: Message) => {
  longPressTimer.value = window.setTimeout(() => {
    showContextMenu(event, msg);
  }, 500);
};

const handleLongPressEnd = () => {
  if (longPressTimer.value) {
    clearTimeout(longPressTimer.value);
    longPressTimer.value = null;
  }
};

const handleEditMessage = () => {
  const msg = contextMenu.value.message;
  if (!msg) return;
  
  editingMessage.value = msg;
  messageInput.value = msg.content;
  closeContextMenu();
  // Focus input
  nextTick(() => {
    messageInputRef.value?.focus();
    adjustTextareaHeight();
  });
};

const handleDeleteMessage = async () => {
  const msg = contextMenu.value.message;
  if (!msg) return;
  
  if (!confirm('آیا از حذف این پیام اطمینان دارید؟')) {
    closeContextMenu();
    return;
  }

  try {
    await apiFetch(`/chat/messages/${msg.id}`, { method: 'DELETE' });
    // Remove from local list
    const index = messages.value.findIndex(m => m.id === msg.id);
    if (index !== -1) {
      messages.value.splice(index, 1); // Remove locally
    }
    closeContextMenu();
  } catch (err) {
    console.error('Failed to delete message:', err);
    alert('خطا در حذف پیام');
  }
};

const cancelEdit = () => {
  editingMessage.value = null;
  messageInput.value = '';
  adjustTextareaHeight(); // Reset height
};

const sendMessage = async () => {
  if (!messageInput.value.trim() && !editingMessage.value) return;

  if (editingMessage.value) {
    const msgToEdit = editingMessage.value
    // Update Mode
    try {
      const updatedMsg = await apiFetch(`/chat/messages/${msgToEdit.id}`, {
        method: 'PUT',
        body: JSON.stringify({ content: messageInput.value })
      });
      // Update local message
      const index = messages.value.findIndex(m => m.id === msgToEdit.id);
      if (index !== -1) {
        messages.value[index] = updatedMsg;
      }
      cancelEdit();
    } catch (err) {
      console.error('Failed to edit message:', err);
      alert('خطا در ویرایش پیام');
    }
    return;
  }

  // Normal Send Mode
  isSending.value = true;
  const content = messageInput.value;
  // Optimistic clear
  messageInput.value = '';
  adjustTextareaHeight();
  
  try {
    const newMessage = await apiFetch('/chat/send', {
      method: 'POST',
      body: JSON.stringify({
        receiver_id: selectedUserId.value,
        content: content,
        message_type: 'text'
      })
    });
    
    messages.value.push(newMessage);
    scrollToBottom();
    // Keep focus
    nextTick(() => messageInputRef.value?.focus());
  } catch (err) {
    console.error('Failed to send message:', err);
    messageInput.value = content; // Restore on error
  } finally {
    isSending.value = false;
  }
};


function updateIsMobile() {
  isMobile.value = window.innerWidth < 768
}

// Handle Enter key
function handleEnter(e: KeyboardEvent) {
  // On mobile, Enter adds new line (default behavior)
  if (isMobile.value) return 
  
  // On desktop, Enter sends (unless Shift is pressed)
  if (!e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

// Auto resize textarea
function adjustTextareaHeight() {
  const el = messageInputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px' // Max 200px height
}

// Scroll to bottom
// Scroll to bottom
function scrollToBottom() {
  // Reset unread count when manually scrolling to bottom
  if (unreadNewMessagesCount.value > 0) {
    markAsRead()
    unreadNewMessagesCount.value = 0
  }
  
  setTimeout(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTo({
        top: messagesContainer.value.scrollHeight,
        behavior: 'smooth'
      })
    }
  }, 50)
}

// Handle Scroll
function handleScroll() {
  const el = messagesContainer.value
  if (!el) return
  
  const threshold = 100 // px from bottom
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  const atBottom = distance < threshold
  
  isUserAtBottom.value = atBottom
  showScrollButton.value = !atBottom
  
  // If arrived at bottom, mark as read
  if (atBottom && unreadNewMessagesCount.value > 0) {
    markAsRead()
    unreadNewMessagesCount.value = 0
  }
}

// Smart scroll - to first unread message or to bottom
function scrollToUnreadOrBottom() {
  if (!messagesContainer.value) return
  
  // Find first unread message (received and not read)
  const firstUnreadIndex = messages.value.findIndex(
    msg => msg.receiver_id === props.currentUserId && !msg.is_read
  )
  
  if (firstUnreadIndex >= 0) {
    // Scroll to first unread message
    const messageElements = messagesContainer.value.querySelectorAll('.message-bubble')
    if (messageElements[firstUnreadIndex]) {
      // Use 'auto' behavior for instant jump on load
      messageElements[firstUnreadIndex].scrollIntoView({ behavior: 'auto', block: 'start' })
    }
  } else {
    // All read - scroll to bottom
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

// Format time
function formatTime(dateStr: string) {
  const date = new Date(dateStr)
  return date.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })
}

// Get full image URL
function getImageUrl(path: string) {
  if (!path) return ''
  // If already full URL, return as is
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path
  }
  // Prepend apiBaseUrl for relative paths
  return `${props.apiBaseUrl}${path}`
}

// Go back
function goBack() {
  if (selectedUserId.value) {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
    // Don't stop polling, we need it for conversation list updates
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
    scrollToBottom()
  }
})

// Lifecycle
onMounted(async () => {
  isLoading.value = true
  await loadConversations()
  await loadStickers()
  isLoading.value = false
  
  // Auto-select target user if provided (e.g., from public profile)
  if (props.targetUserId && props.targetUserName) {
    selectedUserId.value = props.targetUserId
    selectedUserName.value = props.targetUserName
    loadMessages(props.targetUserId)
  }
  
  
  // Start polling for updates (conversations list + messages)
  startPolling()
  
  // Mobile check
  updateIsMobile()
  window.addEventListener('resize', updateIsMobile)
})

onUnmounted(() => {
  window.removeEventListener('resize', updateIsMobile)
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
      <div class="chat-content">
        <div v-if="isLoadingMessages" class="loading-state">
          <LoadingSkeleton :count="8" :height="50" />
        </div>
        
        <div v-else class="messages-container" ref="messagesContainer" @scroll="handleScroll">
          <div v-if="messages.length === 0" class="empty-state">
            <span>💬</span>
            <p>شروع گفتگو...</p>
          </div>
          
          <div 
            v-for="msg in messages" 
            :key="msg.id"
            class="message-bubble"
            :class="{ 'sent': msg.sender_id === props.currentUserId, 'received': msg.sender_id !== props.currentUserId }"
            @contextmenu="showContextMenu($event, msg)"
            @touchstart="handleLongPressStart($event, msg)"
            @touchend="handleLongPressEnd"
          >
            <!-- Text -->
            <template v-if="msg.message_type === 'text'">
              <p>{{ msg.content }}</p>
            </template>
            
            <!-- Image -->
            <template v-else-if="msg.message_type === 'image'">
              <a :href="getImageUrl(msg.content)" target="_blank" class="msg-image-link">
                <img :src="getImageUrl(msg.content)" alt="تصویر" class="msg-image" />
              </a>
            </template>
            
            <!-- Sticker -->
            <template v-else-if="msg.message_type === 'sticker'">
              <div class="msg-sticker">{{ msg.content }}</div>
            </template>
            
            <div class="msg-meta">
              <span class="msg-time">
                {{ formatTime(msg.created_at) }}
                <span v-if="msg.updated_at" class="edited-label">(ویرایش شده)</span>
              </span>
              <span v-if="msg.sender_id === props.currentUserId" class="msg-status">
                <!-- Read (Double Tick) -->
                <svg v-if="msg.is_read" viewBox="0 0 24 24" class="icon-read" width="16" height="16">
                  <path d="M18 7l-1.41-1.41-6.34 6.34 1.41 1.41L18 7zm4.24-1.41L11.66 16.17 7.48 12l-1.41 1.41L11.66 19l12-12-1.42-1.41zM.41 13.41L6 19l1.41-1.41L1.83 12 .41 13.41z"/>
                </svg>
                <!-- Unread (Single Tick) -->
                <svg v-else viewBox="0 0 24 24" class="icon-unread" width="16" height="16">
                  <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                </svg>
              </span>
            </div>
          </div>
        </div>
        
        <!-- Scroll to Bottom Button -->
        <button 
          v-if="showScrollButton" 
          class="scroll-bottom-btn" 
          @click="scrollToBottom"
        >
          <span v-if="unreadNewMessagesCount > 0" class="scroll-badge">{{ unreadNewMessagesCount }}</span>
          <svg viewBox="0 0 24 24" fill="currentColor" width="24" height="24">
            <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/>
          </svg>
        </button>
      </div>

      <!-- Input Area - Telegram Style -->
      <div class="input-area">
        <!-- Input Container -->
        <div class="input-container">
          <!-- Left side buttons - Show voice+attachment when empty, send when has text -->
          <template v-if="!messageInput.trim()">
            <!-- Voice Button -->
            <button class="voice-btn">
              <svg viewBox="0 0 24 24" fill="#8e8e93">
                <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5zm6 6c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
              </svg>
            </button>
            
            <!-- Attachment Button -->
            <input 
              type="file" 
              ref="imageInput" 
              accept="image/*" 
              style="display: none" 
              @change="handleImageUpload"
            />
            <button class="attach-btn" @click="imageInput?.click()" :disabled="isUploading">
              <svg viewBox="0 0 24 24" fill="#8e8e93">
                <path d="M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5c0-1.38 1.12-2.5 2.5-2.5s2.5 1.12 2.5 2.5v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5c0 1.38 1.12 2.5 2.5 2.5s2.5-1.12 2.5-2.5V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z"/>
              </svg>
            </button>
          </template>
          
          <!-- Send Button - Show when has text (same position as voice+attachment) -->
          <button 
            v-else
            class="send-btn-inline" 
            @click="sendMessage()" 
            @mousedown.prevent
            @touchstart.prevent="sendMessage()"
            :disabled="isSending"
          >
            <svg viewBox="0 0 24 24" fill="#007aff">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
            </svg>
          </button>

          <!-- Text Input -->
          <textarea
            ref="messageInputRef"
            v-model="messageInput"
            rows="1"
            placeholder="پیام..."
            @input="adjustTextareaHeight"
            @keydown.enter="handleEnter"
          ></textarea>
          
          <!-- Emoji/Sticker Toggle - Right side inside textbox -->
          <button class="emoji-btn" @click="showStickerPicker = !showStickerPicker">
            <svg viewBox="0 0 24 24" fill="#8e8e93">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-5-6c.78 2.34 2.72 4 5 4s4.22-1.66 5-4H7zm1-4c.55 0 1-.45 1-1s-.45-1-1-1-1 .45-1 1 .45 1 1 1zm8 0c.55 0 1-.45 1-1s-.45-1-1-1-1 .45-1 1 .45 1 1 1z"/>
            </svg>
          </button>
        </div>
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

    <!-- Context Menu (Teleport to body to avoid clipping/position issues) -->
    <Teleport to="body">
      <div 
        v-if="contextMenu.visible" 
        class="context-menu"
        :style="{ top: contextMenu.y + 'px', left: contextMenu.x + 'px' }"
      >
        <div class="menu-item" @click="handleEditMessage">
          <span>✏️</span> ویرایش
        </div>
        <div class="menu-item delete" @click="handleDeleteMessage">
          <span>🗑️</span> حذف
        </div>
      </div>
      
      <!-- Click outside to close (Overlay) -->
      <div 
        v-if="contextMenu.visible" 
        class="context-overlay"
        @click="closeContextMenu"
      ></div>
    </Teleport>

    </template>
  </div>
</template>

<style scoped>
.chat-view {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  background-color: var(--tg-theme-bg-color, #ffffff);
  /* Use a safe gray for pattern that works on both light/dark (e.g. middle gray with low opacity) */
  background-image: radial-gradient(rgba(127, 127, 127, 0.15) 1px, transparent 1px);
  background-size: 20px 20px;
  z-index: 100;
}

/* Header - Absolute for glass effect */
.chat-header {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 60px;
  z-index: 1000;
  display: flex;
  align-items: center;
  padding: 0 16px;
  /* Use color-mix for transparent theme color */
  background: color-mix(in srgb, var(--tg-theme-bg-color, #ffffff) 85%, transparent);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(127, 127, 127, 0.1);
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

/* Chat Content - Main scrollable area */
.chat-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden; /* Scroll handles in messages-container */
  min-height: 0;
  position: relative;
  padding-top: 60px; /* Space for absolute header */
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  padding-bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.message-bubble {
  max-width: 85%;
  padding: 6px 14px;
  border-radius: 12px;
  position: relative;
  font-size: 14px;
  line-height: 1.4;
  white-space: pre-wrap; /* Preserve line breaks */
  word-wrap: break-word;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  animation: slideIn 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(10px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.message-bubble p {
  margin: 0;
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



.msg-time {
  font-size: 10px;
  opacity: 0.7;
}

.msg-meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 4px;
}

.msg-status {
  display: flex;
  align-items: center;
}

.icon-read {
  fill: #4caf50; /* Green for read */
}

.icon-unread {
  fill: #8e8e93; /* Gray for unread */
}

.msg-image-link {
  display: block;
  text-decoration: none;
}

.msg-image {
  max-width: 200px;
  max-height: 200px;
  border-radius: 8px;
  cursor: pointer;
  transition: opacity 0.2s;
}

.msg-image:hover {
  opacity: 0.9;
}

.msg-sticker {
  font-size: 48px;
}

/* Input Area - Telegram Style */
.input-area {
  display: flex;
  align-items: center;
  padding: 10px 8px;
  background: var(--bg-color);
  gap: 6px;
  width: 100%;
  flex-shrink: 0;
}

.input-container {
  flex: 1;
  display: flex;
  align-items: flex-end; /* Align bottom for multi-line support */
  background: var(--bg-color); /* Match background */
  border: 1px solid var(--border-color);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08); /* Soft 3D depth */
  border-radius: 24px;
  padding: 10px 16px;
  min-height: 52px;
  transition: box-shadow 0.2s, border-color 0.2s;
}

.input-container:focus-within {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
  border-color: var(--primary-color);
}

.input-container textarea {
  flex: 1;
  padding: 4px 8px; /* Adjust padding for textarea */
  border: none;
  background: transparent;
  outline: none;
  font-size: 16px;
  color: var(--text-color);
  resize: none;
  overflow-y: auto;
  min-height: 24px;
  line-height: 24px;
  max-height: 200px;
  font-family: inherit;
  direction: rtl;
  text-align: right;
}

.input-container textarea::placeholder {
  color: #8e8e93;
}

.emoji-btn, .attach-btn, .voice-btn {
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
}

.emoji-btn svg, .attach-btn svg, .voice-btn svg {
  width: 28px;
  height: 28px;
}

.emoji-btn {
  margin-left: 4px;
}

.attach-btn, .voice-btn {
  margin-right: 4px;
}

.send-btn-inline {
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  margin-right: 4px;
}

.send-btn-inline svg {
  width: 28px;
  height: 28px;
}

.send-btn-inline:disabled {
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

/* Scroll Bottom Button */
.scroll-bottom-btn {
  position: absolute;
  bottom: 80px;
  right: 20px;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: rgba(40, 40, 40, 0.7); /* Dark semi-transparent */
  backdrop-filter: blur(2px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: white;
  z-index: 999;
  transition: all 0.2s;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

.scroll-bottom-btn:hover {
  transform: translateY(-2px);
  background: rgba(40, 40, 40, 0.9);
}

.scroll-badge {
  position: absolute;
  top: -5px;
  left: -5px;
  background: #ff3b30;
  color: white;
  border-radius: 10px;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: bold;
  min-width: 18px;
  text-align: center;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}

/* Fix layout for absolute header */
.conversations-list, .loading-state, .error-state {
  flex: 1;
  padding-top: 60px; /* Space for absolute header */
  width: 100%;
  overflow-y: auto;
}

.loading-state, .error-state {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}
/* Context Menu */
.context-menu {
  position: fixed;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  min-width: 150px;
  z-index: 2000;
  overflow: hidden;
  padding: 4px;
  border: 1px solid var(--border-color);
}

.menu-item {
  padding: 10px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  border-radius: 8px;
  transition: background 0.2s;
  font-size: 14px;
  color: var(--text-color);
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

.edited-label {
  font-size: 10px;
  font-style: italic;
  opacity: 0.7;
  margin-right: 4px;
}
</style>
