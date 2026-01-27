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
  other_user_last_seen_at?: string | null
}

interface Message {
  id: number
  sender_id: number
  receiver_id: number
  content: string
  message_type: 'text' | 'image' | 'sticker'
  is_read: boolean
  is_sending?: boolean
  is_error?: boolean
  is_deleted?: boolean
  updated_at?: string
  created_at: string
  reply_to_message?: {
    id: number
    sender_id: number
    content: string
    message_type: string
  }
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
// Selection State
const selectedMessages = ref<number[]>([])
const isSelectionMode = computed(() => selectedMessages.value.length > 0)
const longPressTimer = ref<any>(null)
// Search State
const isSearchActive = ref(false)
const isHeaderMenuOpen = ref(false)
const searchQuery = ref('')
const searchResults = ref<any[]>([])
const isSearching = ref(false)
const searchDebounceTimeout = ref<any>(null)
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

const imageInput = ref<HTMLInputElement | null>(null)
const isUploading = ref(false)

// Reply State
const replyingToMessage = ref<Message | null>(null)
const touchStartX = ref(0)
const touchCurrentX = ref(0)
const swipedMessageId = ref<number | null>(null)
const isViewingReply = ref(false) // Flag to temporarily disable auto-scroll during reply viewing

// UI State
const isMobile = ref(false)
const contextMenu = ref<{ visible: boolean; x: number; y: number; message: Message | null }>({ visible: false, x: 0, y: 0, message: null })

// Poll timer
let pollTimer: number | null = null
const POLL_INTERVAL = 30000

// Status
const targetUserStatus = ref('آخرین بازدید اخیراً')
let statusPollTimer: number | null = null
const typingUsers = ref<Record<number, boolean>>({})
const typingTimeouts = ref<Record<number, number>>({})
const isTyping = computed(() => selectedUserId.value ? !!typingUsers.value[selectedUserId.value] : false)
let lastTypingTime = 0
const TYPING_THROTTLE = 2000

function formatLastSeen(date: Date): string {
  const now = new Date()
  const diffSeconds = Math.floor((now.getTime() - date.getTime()) / 1000)
  
  // Online threshold: 3 minutes (180 seconds)
  if (diffSeconds < 180) {
    return 'آنلاین'
  }
  
  if (diffSeconds < 3600) {
    const mins = Math.floor(diffSeconds / 60)
    return `آخرین بازدید ${mins} دقیقه پیش`
  }
  
  const isToday = now.getDate() === date.getDate() && 
                  now.getMonth() === date.getMonth() && 
                  now.getFullYear() === date.getFullYear()
                  
  const hours = date.getHours().toString().padStart(2, '0')
  const mins = date.getMinutes().toString().padStart(2, '0')
  
  if (isToday) {
    return `آخرین بازدید امروز ${hours}:${mins}`
  }
  
  // Check yesterday
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  const isYesterday = yesterday.getDate() === date.getDate() &&
                      yesterday.getMonth() === date.getMonth() &&
                      yesterday.getFullYear() === date.getFullYear()
                      
  if (isYesterday) {
     return `آخرین بازدید دیروز ${hours}:${mins}`
  }
  
  return `آخرین بازدید ${date.toLocaleDateString('fa-IR')}`
}

function isUserOnline(lastSeen: string | null | undefined): boolean {
  if (!lastSeen) return false
  const date = new Date(lastSeen)
  // 3 minutes threshold (180 seconds)
  return (new Date().getTime() - date.getTime()) < 180000
}

async function fetchTargetUserStatus(userId: number) {
  console.log('Fetching status for user:', userId)
  try {
    const userData = await apiFetch(`/users-public/${userId}`)
    
    if (!userData) {
        console.warn('No user data returned')
        return
    }

    console.log('User Data:', userData)
    
    if (userData.last_seen_at) {
      const serverDate = new Date(userData.last_seen_at)
      targetUserStatus.value = formatLastSeen(serverDate)
    } else {
      targetUserStatus.value = 'آخرین بازدید خیلی وقت پیش'
    }
  } catch (e) {
    console.error("Error fetching status", e)
  }
}

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
async function loadMessages(userId: number, silent = false, aroundId?: number) {
  if (!silent) isLoadingMessages.value = true
  try {
    // Add timestamp to prevent caching
    let url = `/chat/messages/${userId}?limit=200&_t=${Date.now()}`
    
    // Deep Jump Support
    if (aroundId) {
        url = `/chat/messages/${userId}?limit=50&around_id=${aroundId}&_t=${Date.now()}`
        // Clear list to force refresh and correct positioning
        if (!silent) messages.value = [] 
    }

    const loadedMessages = await apiFetch(url)
    
    if (aroundId) {
         messages.value = loadedMessages
         isLoadingMessages.value = false
         return // Skip normal scroll logic
    }
    
    if (silent) {
      // Check for strictly new message (by ID)
      const lastOldMsg = messages.value[messages.value.length - 1]
      const lastNewMsg = loadedMessages[loadedMessages.length - 1]
      const isNewMessage = lastNewMsg && (!lastOldMsg || lastNewMsg.id !== lastOldMsg.id)
      const oldLength = messages.value.length

      // Always update list to ensure consistency
      // Preserve optimistic messages
      const tempParams = messages.value.filter(m => m.id < 0)
      messages.value = [...loadedMessages, ...tempParams]
      
      if (isNewMessage) {
        if (lastNewMsg.sender_id !== props.currentUserId) {
          // Message from other user
          if (isUserAtBottom.value && !isViewingReply.value) {
            // If at bottom, auto-scroll and mark read (unless viewing reply)
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
          if (isUserAtBottom.value && !isViewingReply.value) {
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

function startStatusPolling(userId: number) {
  stopStatusPolling()
  fetchTargetUserStatus(userId) // Immediate fetch
  statusPollTimer = window.setInterval(() => fetchTargetUserStatus(userId), 30000) // Every 30 seconds
}

function stopStatusPolling() {
  if (statusPollTimer) {
    clearInterval(statusPollTimer)
    statusPollTimer = null
  }
}

// Header Actions (placeholders for future implementation)
const handleCall = () => {
  // TODO: Implement call functionality
  alert('قابلیت تماس به زودی اضافه می‌شود')
}

const handleHeaderMenu = () => {
  isHeaderMenuOpen.value = !isHeaderMenuOpen.value
}

const closeHeaderMenu = () => {
  isHeaderMenuOpen.value = false
}

const handleMenuSearch = () => {
   closeHeaderMenu()
   toggleSearch()
}

const toggleSearch = () => {
    isSearchActive.value = !isSearchActive.value
    if (isSearchActive.value) {
        nextTick(() => {
            const input = document.getElementById('search-input')
            if (input) input.focus()
        })
    } else {
        searchQuery.value = ''
        searchResults.value = []
    }
}

const performSearch = () => {
    if (!searchQuery.value.trim()) {
        searchResults.value = []
        return
    }
    
    if (searchDebounceTimeout.value) clearTimeout(searchDebounceTimeout.value)
    
    searchDebounceTimeout.value = setTimeout(async () => {
        isSearching.value = true
        try {
            const params = new URLSearchParams()
            params.append('q', searchQuery.value)
            if (selectedUserId.value) {
                params.append('chat_id', selectedUserId.value.toString())
            }
            
            const response = await apiFetch(`/chat/search?${params.toString()}`)
            searchResults.value = response
        } finally {
            isSearching.value = false
        }
    }, 500)
}

const handleSearchResultClick = async (msg: any) => {
    if (!selectedUserId.value) {
        // Global Search result click logic
        const otherId = msg.sender_id === props.currentUserId ? msg.receiver_id : msg.sender_id;
        
        // Enter chat mode
        selectedUserId.value = otherId;
        // Ideally we fetch user name here, for now use placeholder or try to find in conversations list
        const conv = sortedConversations.value.find(c => c.other_user_id === otherId)
        selectedUserName.value = conv ? conv.other_user_name : 'User'
        
        await loadMessages(otherId, false, msg.id)
        nextTick(() => {
             scrollToMessage(msg.id)
        })
    } else {
    
    // In Chat Jump
    // Check if in list
    const exists = messages.value.find(m => m.id === msg.id)
    if (exists) {
        scrollToMessage(msg.id)
    } else {
        await loadMessages(selectedUserId.value!, false, msg.id)
        nextTick(() => {
            scrollToMessage(msg.id)
        })
    }
    }
    // Close search ? Or keep it open? Telegram keeps it open? 
    // Usually jumps and highlighting remains.
    // We will keep search active but maybe minimize results?
    // Let's close results dropdown but keep header.
}

// Context Menu Logic
const showContextMenu = (event: MouseEvent | TouchEvent, msg: Message) => {
  // Context menu is available for ALL messages (Reply), but Edit/Delete only for own messages + 48h limit

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
  const menuHeight = 150; // Estimate for 3 items
  const padding = 10;

  // Prevent overflow right
  if (clientX + menuWidth > window.innerWidth) {
    clientX = window.innerWidth - menuWidth - padding;
  }
  // Prevent overflow left
  if (clientX < padding) {
    clientX = padding;
  }
  
  // Smart vertical positioning: if not enough space below, open ABOVE the touch point
  if (clientY + menuHeight > window.innerHeight - padding) {
    // Not enough space below, position above
    clientY = clientY - menuHeight - padding;
    // Ensure we don't go above the screen
    if (clientY < padding) {
      clientY = padding;
    }
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
  if (!selectedUserId.value) return;
  const content = messageInput.value;
  const replyTo = replyingToMessage.value;

  // Optimistic UI
  const tempId = -Date.now();
  const tempMsg: Message = {
    id: tempId,
    sender_id: props.currentUserId,
    receiver_id: selectedUserId.value,
    content: content,
    message_type: 'text',
    is_read: false,
    created_at: new Date().toISOString(),
    is_sending: true,
    reply_to_message: replyTo ? {
        id: replyTo.id,
        sender_id: replyTo.sender_id,
        content: replyTo.content,
        message_type: replyTo.message_type
    } : undefined
  };

  messages.value.push(tempMsg);
  
  // Clear UI
  messageInput.value = '';
  replyingToMessage.value = null;
  if (isMobile.value) swipedMessageId.value = null;
  adjustTextareaHeight();
  
  nextTick(() => {
     if (replyTo) forceScrollToBottom();
     else scrollToBottom();
  });

  try {
    const body: Record<string, any> = {
        receiver_id: selectedUserId.value,
        content: content,
        message_type: 'text'
    };
    if (replyTo) body.reply_to_message_id = replyTo.id;

    const serverMsg = await apiFetch('/chat/send', {
      method: 'POST',
      body: JSON.stringify(body)
    });
    
    const idx = messages.value.findIndex(m => m.id === tempId);
    if (idx !== -1) {
        messages.value[idx] = serverMsg;
    }
    
    nextTick(() => messageInputRef.value?.focus());
  } catch (err) {
    console.error('Failed to send message:', err);
    const idx = messages.value.findIndex(m => m.id === tempId);
    if (idx !== -1 && messages.value[idx]) {
        messages.value[idx].is_sending = false;
        messages.value[idx].is_error = true;
    }
  }
};

const handleReply = (msg: Message) => {
    replyingToMessage.value = msg
    nextTick(() => {
        messageInputRef.value?.focus()
    })
}

const cancelReply = () => {
  replyingToMessage.value = null
  if (isMobile.value) {
    swipedMessageId.value = null
  }
}

// Swipe Logic
const SWIPE_THRESHOLD = 100 // Increased to reduce accidental swipes during scroll
const handleTouchStart = (e: TouchEvent, msg: Message) => {
  if (e.touches.length > 0) {
    const touch = e.touches[0]
    if (touch) {
      touchStartX.value = touch.clientX
      touchCurrentX.value = touch.clientX
      swipedMessageId.value = msg.id
    }
  }
}

const handleTouchMove = (e: TouchEvent, msg: Message) => {
  if (swipedMessageId.value !== msg.id) return
  if (e.touches.length > 0) {
    const touch = e.touches[0]
    if (touch) {
      touchCurrentX.value = touch.clientX
      
      // Cancel long press if moved significantly
      if (Math.abs(touchCurrentX.value - touchStartX.value) > 10) {
        if (longPressTimer.value) {
          clearTimeout(longPressTimer.value)
          longPressTimer.value = null
        }
      }
    }
  }
}

const handleTouchEnd = (e: TouchEvent, msg: Message) => {
  if (swipedMessageId.value !== msg.id) return
  
  const diff = touchStartX.value - touchCurrentX.value
  const isSent = msg.sender_id === props.currentUserId
  
  // Sent (Right side): Swipe Left (diff > 0)
  // Received (Left side): Swipe Right (diff < 0)
  const isValidSwipe = isSent ? (diff > SWIPE_THRESHOLD) : (diff < -SWIPE_THRESHOLD)

  if (isValidSwipe) {
    handleReply(msg)
  }
  
  // Reset
  swipedMessageId.value = null
  touchStartX.value = 0
  touchCurrentX.value = 0
}

// Selection Logic
const toggleSelection = (msgId: number) => {
    const index = selectedMessages.value.indexOf(msgId)
    if (index === -1) {
        selectedMessages.value.push(msgId)
    } else {
        selectedMessages.value.splice(index, 1)
    }
}

const clearSelection = () => {
    selectedMessages.value = []
}

// Handle Message Click (Delegated)
const handleMessageClick = (event: MouseEvent | TouchEvent, msg: Message) => {
    if (isSelectionMode.value) {
      event.preventDefault()
      toggleSelection(msg.id)
    } else {
      showContextMenu(event, msg)
    }
}

// Long Press Handlers
const startLongPress = (event: TouchEvent, msg: Message) => {
    longPressTimer.value = setTimeout(() => {
        if (navigator.vibrate) navigator.vibrate(50)
        toggleSelection(msg.id)
        longPressTimer.value = null
    }, 500)
    
    // Pass event to swipe handler too
    handleTouchStart(event, msg)
}

const cancelLongPress = () => {
    if (longPressTimer.value) {
        clearTimeout(longPressTimer.value)
        longPressTimer.value = null
    }
}

const endLongPress = (event: TouchEvent, msg: Message) => {
    cancelLongPress()
    handleTouchEnd(event, msg)
}

const getSwipeStyle = (msg: Message) => {
  if (swipedMessageId.value !== msg.id) return {}
  const diff = touchStartX.value - touchCurrentX.value
  const isSent = msg.sender_id === props.currentUserId

  // Logic:
  // Sent (Right): Allow negative translate (Left) -> diff > 0
  // Received (Left): Allow positive translate (Right) -> diff < 0
  
  if (isSent) {
    if (diff <= 0) return {}
    const translateX = Math.min(diff, 100)
    return { transform: `translateX(-${translateX}px)`, transition: 'none' }
  } else {
    // Received
    if (diff >= 0) return {}
    const translateX = Math.min(Math.abs(diff), 100)
    return { transform: `translateX(${translateX}px)`, transition: 'none' }
  }
}

// Scroll to message with custom slow animation
const scrollToMessage = (msgId: number) => {
    const el = document.getElementById(`msg-${msgId}`)
    const container = messagesContainer.value
    
    if (el && container) {
        // Capture non-null references for closure
        const safeContainer = container
        const safeEl = el
        
        // Temporarily disable auto-scroll during viewing
        isViewingReply.value = true
        
        // Start highlight immediately (runs alongside scroll)
        safeEl.classList.add('highlight-message')
        
        // Re-enable auto-scroll after highlight completes (but don't remove class - let animation end naturally)
        setTimeout(() => {
            isViewingReply.value = false
        }, 3500)
        
        // Calculate target position (center of container)
        const elTop = safeEl.offsetTop
        const elHeight = safeEl.offsetHeight
        const containerHeight = safeContainer.clientHeight
        const targetScrollTop = elTop - (containerHeight / 2) + (elHeight / 2)
        
        // Use custom animation for scroll
        const startScrollTop = safeContainer.scrollTop
        const distance = targetScrollTop - startScrollTop
        const duration = 1000 // 1 second (same as highlight peak at 15% = 0.45s)
        const startTime = performance.now()
        
        function step(currentTime: number) {
            const elapsed = currentTime - startTime
            const progress = Math.min(elapsed / duration, 1)
            
            // Ease out cubic
            const ease = 1 - Math.pow(1 - progress, 3)
            
            safeContainer.scrollTop = startScrollTop + (distance * ease)
            
            if (progress < 1) {
                requestAnimationFrame(step)
            }
        }
        
        requestAnimationFrame(step)
    }
}

const handleReplyMessage = () => {
  const msg = contextMenu.value.message
  if (!msg) return
  handleReply(msg)
  closeContextMenu()
}


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

// Scroll to bottom (smooth)
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
  }, 100)
}

// Force scroll to bottom (instant - for reply cases where smooth fails)
function forceScrollToBottom() {
  if (unreadNewMessagesCount.value > 0) {
    markAsRead()
    unreadNewMessagesCount.value = 0
  }
  
  // Multiple attempts to ensure scroll works after layout changes
  const doScroll = () => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight + 1000
    }
  }
  
  nextTick(doScroll)
  setTimeout(doScroll, 50)
  setTimeout(doScroll, 150)
  setTimeout(doScroll, 300)
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

function shouldShowDateSeparator(index: number): boolean {
    if (index === 0) return true;
    const msgs = messages.value;
    const currentMsg = msgs[index];
    const prevMsg = msgs[index - 1];
    
    if (!currentMsg?.created_at || !prevMsg?.created_at) return false;

    const current = new Date(currentMsg.created_at);
    const prev = new Date(prevMsg.created_at);
    return current.toDateString() !== prev.toDateString();
}

const groupedMessages = computed(() => {
  const groups: { label: string, messages: any[] }[] = []
  
  if (messages.value.length === 0) return groups;
  const firstMsg = messages.value[0];
  if (!firstMsg) return groups;
  
  // Ensure we sort or assume sorted? Assuming sorted by date ascending.
  let currentLabel = formatDateForSeparator(firstMsg.created_at)
  let currentGroup: any[] = [firstMsg]
  
  for (let i = 1; i < messages.value.length; i++) {
      const msg = messages.value[i]
      if (!msg) continue;
      const label = formatDateForSeparator(msg.created_at)
      
      if (label !== currentLabel) {
          groups.push({ label: currentLabel, messages: currentGroup })
          currentLabel = label
          currentGroup = [msg]
      } else {
          currentGroup.push(msg)
      }
  }
  groups.push({ label: currentLabel, messages: currentGroup })
  
  return groups
})

function formatDateForSeparator(dateStr: string): string {
    const date = new Date(dateStr);
    const now = new Date();
    
    // Check Today
    if (date.toDateString() === now.toDateString()) return 'امروز';
    
    // Check Yesterday
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) return 'دیروز';
    
    return date.toLocaleDateString('fa-IR', { year: 'numeric', month: 'long', day: 'numeric' });
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

function viewProfile() {
  if (selectedUserId.value) {
    emit('navigate', 'public_profile', { 
        id: selectedUserId.value, 
        account_name: selectedUserName.value 
    })
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

// Check if current context menu message can be edited/deleted (own message + within 48h)
const canEditDelete = computed(() => {
  const msg = contextMenu.value.message
  if (!msg) return false
  if (msg.sender_id !== props.currentUserId) return false
  const msgTime = new Date(msg.created_at).getTime()
  const now = Date.now()
  return now - msgTime <= 48 * 60 * 60 * 1000
})

// Watchers
watch(selectedUserId, (newVal) => {
  console.log('WATCH selectedUserId:', newVal)
  if (newVal) {
    startStatusPolling(newVal)
    scrollToBottom()
  } else {
    stopStatusPolling()
  }
})

// Typing Logic
async function sendTypingSignal() {
    if (!messageInput.value) return; 
    const now = Date.now();
    if (now - lastTypingTime < TYPING_THROTTLE) return;
    lastTypingTime = now;
    
    if (!selectedUserId.value) return;
    
    try {
        await apiFetch('/chat/typing', {
            method: 'POST',
            body: JSON.stringify({ receiver_id: selectedUserId.value })
        });
    } catch (e) { console.error('Typing signal failed', e); }
}

watch(messageInput, () => {
    sendTypingSignal();
});

function handleTypingEvent(e: Event) {
   const data = (e as CustomEvent).detail;
   const senderId = data.sender_id;
   if (senderId) {
       typingUsers.value[senderId] = true;
       
       if (typingTimeouts.value[senderId]) clearTimeout(typingTimeouts.value[senderId]);
       
       // Use window.setTimeout to ensure number return type
       typingTimeouts.value[senderId] = window.setTimeout(() => {
           typingUsers.value[senderId] = false;
       }, 5000);
   }
}

function handleNewMessageEvent(e: Event) {
  const notif = (e as CustomEvent).detail
  const senderId = notif.sender_id
  
  // Clear typing on message
  if (senderId) {
      typingUsers.value[senderId] = false;
  }
  
  // Always update conversations list (to show new message/count)
  loadConversations();
  
  // Refresh if chat with sender
  if (selectedUserId.value && (senderId === selectedUserId.value)) {
      loadMessages(selectedUserId.value, true)
      markAsRead()
  }
}

function handleReadEvent(e: Event) {
  const data = (e as CustomEvent).detail
  // If the current chat user read our messages, refresh
  if (selectedUserId.value && (data.reader_id === selectedUserId.value)) {
       loadMessages(selectedUserId.value, true)
  }
}

// Lifecycle
onMounted(async () => {
  window.addEventListener('chat-notification', handleNewMessageEvent)
  window.addEventListener('chat-message', handleNewMessageEvent)
  window.addEventListener('chat-typing', handleTypingEvent)
  window.addEventListener('chat-read', handleReadEvent)
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
  window.removeEventListener('chat-message', handleNewMessageEvent)
  window.removeEventListener('chat-notification', handleNewMessageEvent)
  window.removeEventListener('chat-typing', handleTypingEvent)
  window.removeEventListener('chat-read', handleReadEvent)
  window.removeEventListener('resize', updateIsMobile)
  stopPolling()
  stopStatusPolling()
})

// Expose for parent to start new chat
defineExpose({ startNewChat })
</script>

<template>
  <div class="chat-view">
    <!-- Header - Telegram Style -->
    <div class="chat-header">
      <!-- Back Button -->
      <button class="header-btn back-btn" @click="goBack">
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z"/>
        </svg>
      </button>
      
      <!-- Avatar + User Info (when in chat) -->
      <template v-if="selectedUserId">
        <div class="header-avatar" @click="viewProfile">{{ selectedUserName.charAt(0) }}</div>
        <div class="header-user-info" @click="viewProfile">
          <span class="header-name">{{ selectedUserName }}</span>
          <span class="header-status" :class="{ 'online': targetUserStatus.includes('آنلاین') || isTyping }">
            {{ isTyping ? 'در حال نوشتن...' : targetUserStatus }}
          </span>
        </div>
      </template>
      
      <!-- Title (for conversation list) -->
      <!-- Title (for conversation list) -->
      <template v-else>
        <div v-if="!isSearchActive" class="header-title">
          پیام‌ها
          <span v-if="totalUnread > 0" class="badge">{{ totalUnread }}</span>
        </div>
      </template>
      
      <!-- Search Bar Overlay -->
      <div v-if="isSearchActive" class="search-bar-container">
         <input 
            id="search-input"
            v-model="searchQuery" 
            @input="performSearch" 
            placeholder="جستجو..." 
            class="header-search-input"
         />
         <button class="header-btn" @click="toggleSearch">✕</button>
         
         <!-- Search Results Dropdown -->
         <div v-if="searchResults.length > 0" class="search-results-dropdown">
            <div 
               v-for="res in searchResults" 
               :key="res.id" 
               class="search-result-item"
               @click="handleSearchResultClick(res)"
            >
               <span class="search-res-text">{{ res.content.substring(0, 30) }}...</span>
               <span class="search-res-date">{{ formatDateForSeparator(res.created_at) }}</span>
            </div>
         </div>
      </div>
      
      <!-- Spacer -->
      <div class="header-spacer"></div>
      
      <!-- Action Buttons (only in chat view) -->
      <!-- Action Buttons -->
      <template v-if="selectedUserId && !isSearchActive">
        <button class="header-btn" @click="handleCall">
          <svg viewBox="0 0 24 24" fill="currentColor">
             <path d="M20.01 15.38c-1.23 0-2.42-.2-3.53-.56a.977.977 0 00-1.01.24l-1.57 1.97c-2.83-1.35-5.48-3.9-6.89-6.83l1.95-1.66c.27-.28.35-.67.24-1.02-.37-1.11-.56-2.3-.56-3.53 0-.54-.45-.99-.99-.99H4.19C3.65 3 3 3.24 3 3.99 3 13.28 10.73 21 20.01 21c.71 0 .99-.63.99-1.18v-3.45c0-.54-.45-.99-.99-.99z"/>
          </svg>
        </button>
        <!-- Three-dot Menu -->
        <div class="header-menu-container" style="position: relative;">
            <button class="header-btn" @click.stop="handleHeaderMenu">
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/>
              </svg>
            </button>
            <div v-if="isHeaderMenuOpen" class="header-dropdown-menu" v-click-outside="closeHeaderMenu">
               <div class="header-menu-item" @click="handleMenuSearch">
                  <span>جستجو</span>
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                    <path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
                  </svg>
               </div>
               <!-- Other placeholder items -->
               <div class="header-menu-item" @click="closeHeaderMenu">
                  <span>اطلاعات فرد</span>
               </div>
            </div>
            <!-- Overlay to close menu (simple fallback if click-outside directive missing) -->
            <div v-if="isHeaderMenuOpen" class="menu-overlay" @click="closeHeaderMenu"></div>
        </div>
      </template>
      <!-- Conversation List Actions -->
      <template v-else-if="!selectedUserId && !isSearchActive">
         <button class="header-btn" @click="toggleSearch">
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
          </svg>
        </button>
      </template>
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
        <div class="conv-avatar">
          {{ conv.other_user_name.charAt(0) }}
          <div v-if="isUserOnline(conv.other_user_last_seen_at)" class="online-indicator-dot"></div>
        </div>
        <div class="conv-content">
          <div class="conv-header">
            <span class="conv-name">{{ conv.other_user_name }}</span>
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

    <!-- Messages View -->
    <template v-else>
      <div v-if="!selectedUserId" class="no-selection-placeholder">
        <p>لطفاً یک گفتگو را انتخاب کنید</p>
      </div>
      <div class="chat-content">
        <div v-if="isLoadingMessages" class="loading-state">
          <LoadingSkeleton :count="8" :height="50" />
        </div>
        
        <div v-else class="messages-container" ref="messagesContainer" @scroll="handleScroll">
          <div v-if="messages.length === 0" class="empty-state">
            <span>💬</span>
            <p>شروع گفتگو...</p>
          </div>
          
          <div v-for="group in groupedMessages" :key="group.label" class="message-group">
            <div class="date-separator sticky-date">
              <span @click="scrollToMessage(group.messages[0].id)">{{ group.label }}</span>
            </div>

            <div 
              v-for="(msg, index) in group.messages"
              :key="msg.id"
              :id="'msg-' + msg.id"
              class="message-bubble"
              :class="{ 
                'sent': msg.sender_id === props.currentUserId, 
                'received': msg.sender_id !== props.currentUserId,
                'sending': msg.id < 0 || msg.is_sending,
                'error': msg.is_error,
                'selected-message': selectedMessages.includes(msg.id)
              }"
              @click="handleMessageClick($event, msg)"
              @touchstart="startLongPress($event, msg)"
              @touchmove="cancelLongPress(); handleTouchMove($event, msg)"
              @touchend="endLongPress($event, msg)"
              :style="getSwipeStyle(msg)"
            >
            <!-- Reply Context -->
            <div 
              v-if="msg.reply_to_message" 
              class="reply-context"
              @click.stop="scrollToMessage(msg.reply_to_message.id)"
            >
              <div class="reply-line"></div>
              <div class="reply-content">
                <span class="reply-text">
                  <template v-if="msg.reply_to_message.message_type === 'image'">🖼️ تصویر</template>
                  <template v-else-if="msg.reply_to_message.message_type === 'sticker'">😊 استیکر</template>
                  <template v-else>{{ msg.reply_to_message.content }}</template>
                </span>
              </div>
            </div>
            
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
                <!-- Sending -->
                <svg v-if="msg.id < 0 || msg.is_sending" viewBox="0 0 24 24" class="icon-clock" width="16" height="16" style="color: #aaa;">
                    <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10 10-4.5 10-10S17.5 2 12 2zm4.2 14.2L11 13V7h1.5v5.2l4.5 2.7-.8 1.3z" fill="currentColor"/>
                </svg>
                <!-- Read -->
                <svg v-else-if="msg.is_read" viewBox="0 0 24 24" class="icon-read" width="16" height="16">
                  <path d="M18 7l-1.41-1.41-6.34 6.34 1.41 1.41L18 7zm4.24-1.41L11.66 16.17 7.48 12l-1.41 1.41L11.66 19l12-12-1.42-1.41zM.41 13.41L6 19l1.41-1.41L1.83 12 .41 13.41z"/>
                </svg>
                <!-- Unread -->
                <svg v-else viewBox="0 0 24 24" class="icon-unread" width="16" height="16">
                  <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                </svg>
              </span>
            </div>
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
        <!-- Reply Banner -->
        <div v-if="replyingToMessage" class="reply-banner">
            <div class="reply-info">
                <span class="reply-icon">↩️ در پاسخ به:</span>
                <span class="reply-preview">
                    {{ replyingToMessage.message_type === 'text' ? replyingToMessage.content : (replyingToMessage.message_type === 'image' ? '🖼️ تصویر' : '😊 استیکر') }}
                </span>
            </div>
            <button class="close-reply" @click="cancelReply">✕</button>
        </div>

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
        <div class="menu-item" @click="handleReplyMessage">
          <span>↩️</span> پاسخ
        </div>
        <template v-if="canEditDelete">
            <div class="menu-item" @click="handleEditMessage">
              <span>✏️</span> ویرایش
            </div>
            <div class="menu-item delete" @click="handleDeleteMessage">
              <span>🗑️</span> حذف
            </div>
        </template>
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
  /* Light theme background */
  background-color: #E8E5E0;
  /* Telegram-style subtle pattern */
  background-image: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23d5d2cd' fill-opacity='0.4'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
  z-index: 100;
}

/* Header - Telegram Style Glass */
.chat-header {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 56px;
  z-index: 1000;
  display: flex;
  align-items: center;
  padding: 0 8px;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  gap: 8px;
  direction: ltr; /* Force LTR layout as requested */
}

/* Header Buttons */
.header-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 0; /* Minimal padding */
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  transition: background 0.2s;
  color: #000000;
  width: 40px; /* Exact touch target size */
  height: 40px;
  flex-shrink: 0;
}

.header-btn svg {
  width: 24px;
  height: 24px;
}

.header-btn:hover {
  background: rgba(0, 0, 0, 0.05);
}

.header-btn:active {
  background: rgba(0, 0, 0, 0.1);
}

/* Header Avatar */
.header-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: linear-gradient(135deg, #54A3FF, #0088CC);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 600;
  flex-shrink: 0;
  margin: 0; /* Remove margins, rely on gap */
  cursor: pointer;
}

/* Header User Info */
.header-user-info {
  display: flex;
  flex-direction: column;
  justify-content: center;
  margin: 0;
  min-width: 0;
  flex: 1;
  align-items: flex-start; /* Align Left */
  padding-left: 4px; /* Padding on left for LTR */
  cursor: pointer;
}

.header-name {
  font-size: 16px;
  font-weight: 600;
  color: #000000;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
  text-align: left; /* Align Left */
}

.header-status {
  font-size: 13px;
  color: #8E8E93;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
  text-align: left; /* Align Left */
}

.header-status.online {
  color: #0088CC; /* Telegram blue for online status */
}

/* Header Spacer */
.header-spacer {
  display: none; /* We use flex-grow on user-info instead, or keep it if needed for spacing logic */
}

/* Header Title (for conversation list) */
.header-title {
  font-size: 17px;
  font-weight: 600;
  color: #000000;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  flex: 1;
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
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  cursor: pointer;
  background: #FFFFFF;
  transition: background 0.2s;
}

.conversation-item:hover {
  background: #F5F5F5;
}

.conversation-item.has-unread {
  background: rgba(0, 122, 255, 0.05);
}

.conv-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: linear-gradient(135deg, #54A3FF, #0088CC);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 600;
  margin-left: 12px;
  position: relative;
}

.online-indicator-dot {
  position: absolute;
  bottom: 0;
  right: 0;
  width: 13px;
  height: 13px;
  background-color: #4cd964; /* Telegram Green */
  border: 2px solid #fff;
  border-radius: 50%;
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
  color: #8E8E93;
}

.conv-preview {
  font-size: 13px;
  color: #8E8E93;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.typing-text {
  color: #2ea043;
  font-weight: 600;
  font-style: italic;
  display: flex;
  align-items: center;
  gap: 4px;
}

.no-selection-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #8E8E93;
  font-size: 1.1em;
  background-color: #f5f5f7;
}

.date-separator {
  display: flex;
  justify-content: center;
  margin: 16px 0;
  z-index: 5;
}

.sticky-date {
   position: sticky;
   top: 10px;
}

.date-separator span {
  background-color: rgba(0, 0, 0, 0.15);
  color: #fff;
  padding: 4px 12px;
  border-radius: 12px;
  font-size: 12px;
  text-shadow: 0 1px 2px rgba(0,0,0,0.1);
  backdrop-filter: blur(4px);
  cursor: pointer;
  user-select: none;
}
@media (prefers-color-scheme: light) {
    .date-separator span {
        background-color: rgba(0, 0, 0, 0.06); 
        color: #555;
        text-shadow: none;
        font-weight: 500;
        border: 1px solid rgba(0,0,0,0.05);
    }
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
  overflow: hidden;
  min-height: 0;
  position: relative;
  /* No padding-top - messages will scroll UNDER the glass header */
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  overflow-anchor: none;
  /* Extra padding at top/bottom so messages start visible, but scroll under header/input */
  padding: 70px 16px 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.message-group {
  display: flex;
  flex-direction: column;
  width: 100%;
  gap: 6px;
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
  background: #E1FFC7; /* Telegram green bubble */
  color: #000000;
  border-radius: 18px 18px 4px 18px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
}

.message-bubble.received {
  align-self: flex-end;
  background: #FFFFFF; /* White bubble */
  color: #000000;
  border-radius: 18px 18px 18px 4px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
}



.msg-time {
  font-size: 11px;
  color: #8E8E93;
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

/* Input Area - Glass effect */
.input-area {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  padding: 8px 8px 12px 8px;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  gap: 0;
  border-top: 1px solid rgba(0, 0, 0, 0.08);
}

.input-container {
  width: 100%;
  gap: 8px;
  flex: 1;
  display: flex;
  align-items: flex-end;
  background: rgba(255, 255, 255, 0.6);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: none;
  box-shadow: none;
  border-radius: 20px;
  padding: 8px 14px;
  min-height: 44px;
  transition: background 0.2s;
}

.input-container:focus-within {
  background: rgba(255, 255, 255, 0.8);
}

.input-container textarea {
  flex: 1;
  padding: 4px 8px;
  border: none;
  background: transparent;
  outline: none;
  font-size: 16px;
  color: #000000;
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
  color: #8E8E93;
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
  background: #FFFFFF;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #8E8E93;
  z-index: 999;
  transition: all 0.2s;
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
}

.scroll-bottom-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
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

/* Reply Styles */
.reply-context {
  border-left: 2px solid #0088CC;
  background: rgba(0, 136, 204, 0.08);
  border-radius: 4px;
  padding: 4px 8px;
  margin-bottom: 6px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  max-width: 100%;
}

.reply-line {
  display: none; /* Handled by border-left */
}

.reply-name {
  font-size: 11px;
  font-weight: bold;
  color: #0088CC;
}

.reply-text {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  opacity: 0.8;
  display: block;
  max-width: 100%; /* Force truncation */
}

/* Reply Banner (Input Area) */
.reply-banner {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  background: #FFFFFF;
  padding: 8px 12px;
  padding-left: 32px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  animation: slideUp 0.2s ease-out;
  min-height: 40px;
}

@keyframes slideUp {
  from { transform: translateY(100%); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}

.reply-info {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  width: 100%;
}

.reply-icon {
  font-size: 11px;
  color: #0088CC;
  margin-bottom: 2px;
}

.reply-preview {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #000000;
  display: block;
  max-width: 100%;
}

.close-reply {
  position: absolute;
  top: 8px;
  left: 8px;
  background: none;
  border: none;
  color: #8E8E93;
  font-size: 18px;
  width: 20px;
  height: 20px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  line-height: 1;
}

.close-reply:hover {
  color: #000000;
}

/* Highlight Animation */
.highlight-message {
  animation: highlight 3s ease-in-out;
}

@keyframes highlight {
  0% { 
    box-shadow: none;
  }
  15% { 
    box-shadow: 0 0 0 4px rgba(255, 200, 0, 0.5), 0 0 20px 10px rgba(255, 200, 0, 0.3);
  }
  100% { 
    box-shadow: none;
  }
}
/* Search Styles */
.search-bar-container {
  flex: 1;
  display: flex;
  align-items: center;
  position: relative;
  height: 100%;
  margin-right: 8px;
}

.header-search-input {
  flex: 1;
  background: transparent;
  border: none;
  font-size: 16px;
  color: inherit;
  outline: none;
  padding: 0 8px;
  height: 100%;
}

.search-results-dropdown {
  position: absolute;
  top: 100%; /* Below header */
  left: 0;
  right: 0;
  
  /* Glassmorphism */
  background: rgba(255, 255, 255, 0.85); /* Translucent White */
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-top: none;
  max-height: 400px;
  overflow-y: auto;
  z-index: 1000;
  
  /* 3D Shadow */
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15), 0 2px 8px rgba(0,0,0,0.05);
  
  border-radius: 0 0 12px 12px;
}

.search-result-item {
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: background 0.2s;
}

.search-result-item:hover {
  background: #f5f5f5;
}

.search-result-item:last-child {
  border-bottom: none;
}

.search-res-text {
  font-size: 14px;
  color: #000;
}

.search-res-date {
  font-size: 11px;
  color: #8e8e93;
  align-self: flex-end;
}

@media (prefers-color-scheme: dark) {
  .search-results-dropdown {
    background: rgba(30, 30, 32, 0.85); /* Translucent Dark */
    border-color: rgba(255, 255, 255, 0.1);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  }
  
  .search-result-item {
    border-bottom-color: rgba(255, 255, 255, 0.05);
  }
  
  .search-result-item:hover {
    background: #2c2c2e;
  }
  
  .search-res-text {
    color: #fff;
  }
}

/* Header Menu */
.header-menu-container {
  display: flex;
  align-items: center;
}

.header-dropdown-menu {
  position: absolute;
  top: 100%;
  right: 0; 
  left: auto;
  margin-top: 8px;
  
  /* Glassmorphism */
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  
  border-radius: 12px; /* Nicer rounded corners */
  
  /* 3D Shadow */
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15), 0 2px 8px rgba(0,0,0,0.05);
  
  min-width: 160px;
  z-index: 2000;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.3);
}

.header-menu-item {
  padding: 12px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  font-size: 14px;
  color: inherit;
}

.header-menu-item:hover {
  background: #f5f5f5;
}

.menu-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  z-index: 1500;
  background: transparent;
}

@media (prefers-color-scheme: dark) {
  .header-dropdown-menu {
    background: rgba(30, 30, 32, 0.85);
    border-color: rgba(255, 255, 255, 0.1);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  }
  .header-menu-item:hover {
    background: #3a3a3c;
  }
}

.selected-message {
  background-color: rgba(0, 122, 255, 0.2) !important;
  position: relative;
  border: 1px solid #007aff;
}
</style>
