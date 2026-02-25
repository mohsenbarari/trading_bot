<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted, nextTick } from 'vue'
import LoadingSkeleton from './LoadingSkeleton.vue'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import imageCompression from 'browser-image-compression'

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
  message_type: 'text' | 'image' | 'video' | 'sticker'
  is_read: boolean
  is_sending?: boolean
  upload_progress?: number
  download_progress?: number
  is_downloading?: boolean
  local_blob_url?: string
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

// === IndexedDB Image Cache ===
// Maps file_id -> blob URL (reactive, for template binding)
const imageCache = ref<Record<string, string>>({})
const DB_NAME = 'chat_image_cache'
const DB_VERSION = 1
const STORE_NAME = 'images'

function openImageDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = (e) => {
      const db = (e.target as IDBOpenDBRequest).result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME)
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function getFromDB(key: string): Promise<Blob | null> {
  try {
    const db = await openImageDB()
    return new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, 'readonly')
      const req = tx.objectStore(STORE_NAME).get(key)
      req.onsuccess = () => resolve(req.result ?? null)
      req.onerror = () => resolve(null)
    })
  } catch { return null }
}

async function saveToDB(key: string, blob: Blob): Promise<void> {
  try {
    const db = await openImageDB()
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).put(blob, key)
      tx.oncomplete = () => resolve()
      tx.onerror = () => resolve()
    })
  } catch { /* ignore */ }
}

async function loadImageForMessage(content: string): Promise<void> {
  if (!content || !content.startsWith('{')) return
  let fileId = ''
  try {
    const parsed = JSON.parse(content)
    fileId = parsed.file_id
  } catch { return }
  if (!fileId || imageCache.value[fileId]) return // already loaded

  // 1. Check IndexedDB first
  const cached = await getFromDB(fileId)
  if (cached) {
    imageCache.value = { ...imageCache.value, [fileId]: URL.createObjectURL(cached) }
    return
  }

  // 2. Fetch from server
  try {
    const res = await fetch(`${props.apiBaseUrl}/api/chat/files/${fileId}?token=${props.jwtToken}`)
    if (!res.ok) return
    const blob = await res.blob()
    await saveToDB(fileId, blob)
    imageCache.value = { ...imageCache.value, [fileId]: URL.createObjectURL(blob) }
  } catch { /* silently fail */ }
}

function getFileId(content: string): string {
  if (!content || !content.startsWith('{')) return ''
  try { return JSON.parse(content).file_id ?? '' } catch { return '' }
}

function openCachedImage(fileId: string) {
  const url = imageCache.value[fileId]
  if (url) window.open(url, '_blank')
}
// === End IndexedDB Caching ===

// Handle manual media download
async function downloadMedia(msg: Message) {
  const fileId = getFileId(msg.content);
  if (!fileId) return;
  
  // Get reactive proxy of the message
  const targetMsg = messages.value.find(m => m.id === msg.id) || msg;
  targetMsg.is_downloading = true;
  targetMsg.download_progress = 0;
  
  try {
    const res = await fetch(`${props.apiBaseUrl}/api/chat/files/${fileId}?token=${props.jwtToken}`);
    if (!res.ok) throw new Error("Download failed");
    
    const contentType = res.headers.get('content-type') || 'application/octet-stream';
    const contentLength = res.headers.get('content-length');
    const total = contentLength ? parseInt(contentLength, 10) : 0;
    
    if (!total || !res.body) {
      const blob = await res.blob();
      await saveToDB(fileId, blob);
      imageCache.value = { ...imageCache.value, [fileId]: URL.createObjectURL(blob) };
      return;
    }
    
    const reader = res.body.getReader();
    const chunks: Uint8Array[] = [];
    let received = 0;
    
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(value);
        received += value.length;
        targetMsg.download_progress = Math.round((received / total) * 100);
      }
    }
    
    const combinedBlob = new Blob(chunks, { type: contentType });
    await saveToDB(fileId, combinedBlob);
    
    // Update cache with the new blob URL
    const newUrl = URL.createObjectURL(combinedBlob);
    imageCache.value = { ...imageCache.value, [fileId]: newUrl };
    
  } catch (e) {
    console.error("Download failed:", e);
    alert("خطا در دانلود فایل");
  } finally {
    targetMsg.is_downloading = false;
  }
}

// Lightbox State
const lightboxMedia = ref<{ url: string, type: 'image'|'video' } | null>(null);

function handleMediaClick(msg: Message) {
  const fileId = getFileId(msg.content);
  const cacheUrl = imageCache.value[fileId];
  const url = msg.local_blob_url || cacheUrl;
  
  if (url) {
    lightboxMedia.value = { 
      url, 
      type: msg.message_type === 'video' ? 'video' : 'image' 
    };
  }
}

function closeLightbox() {
  lightboxMedia.value = null;
}

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

// Forward State
const showForwardModal = ref(false)

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
async function sendMediaMessage(type: 'image' | 'video' | 'sticker', content: string, localBlobUrl?: string) {
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
    if (localBlobUrl) {
      newMsg.local_blob_url = localBlobUrl
    }
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
  pushBackState(() => {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
  })
}

// Start new chat (from search or profile)
function startNewChat(userId: number, userName: string) {
  selectedUserId.value = userId
  selectedUserName.value = userName
  loadMessages(userId)
  pushBackState(() => {
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
  })
}

// Generate tiny base64 thumbnail for videos locally
async function generateVideoThumbnail(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video')
    video.preload = 'metadata'
    video.src = URL.createObjectURL(file)
    video.muted = true
    video.playsInline = true
    
    // Once metadata is loaded, seek to 0.1 seconds
    video.onloadeddata = () => {
      video.currentTime = 0.1
    }
    
    // Once seeked, draw to canvas
    video.onseeked = () => {
      const canvas = document.createElement('canvas')
      const targetSize = 20 // ultra-low res for blurred preview
      const scale = Math.min(targetSize / (video.videoWidth || 1), targetSize / (video.videoHeight || 1))
      canvas.width = Math.max(1, (video.videoWidth || 1) * scale)
      canvas.height = Math.max(1, (video.videoHeight || 1) * scale)
      const ctx = canvas.getContext('2d')
      if (ctx) {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
        resolve(canvas.toDataURL('image/jpeg', 0.5))
      } else {
        resolve('')
      }
      URL.revokeObjectURL(video.src)
    }
    video.onerror = (e) => reject(e)
  })
}

// Upload image or video
async function handleImageUpload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  
  const file = input.files[0]
  if (!file) return
  
  const isVideo = file.type.startsWith('video/')
  if (!selectedUserId.value) return
  
  isUploading.value = true
  let step = 'start'
  
  // Create an optimistic message to show immediately in UI
  const optimisticId = -Date.now()
  const localUrl = URL.createObjectURL(file)
  const optimisticMsg: Message = {
    id: optimisticId,
    sender_id: props.currentUserId,
    receiver_id: selectedUserId.value,
    content: JSON.stringify({ placeholder: true }), // Will be replaced by actual thumbnail
    message_type: isVideo ? 'video' : 'image',
    is_read: true,
    is_sending: true,
    upload_progress: 0,
    local_blob_url: localUrl,
    created_at: new Date().toISOString()
  }
  messages.value.push(optimisticMsg)
  
  // Create a reactive reference for progress update
  const getOptimisticTarget = () => messages.value.find(m => m.id === optimisticId) || optimisticMsg;
  
  // Auto-scroll to show the uploading item
  await nextTick()
  scrollToBottom()
  
  try {
    let uploadFile = file;
    let thumbBase64 = '';
    
    if (isVideo) {
      step = 'video_thumb'
      try {
        thumbBase64 = await generateVideoThumbnail(file)
      } catch (warn) {
        console.warn("Video thumbnail failed:", warn)
      }
    } else {
      step = 'compress_main'
      try {
        const options = { maxSizeMB: 0.5, maxWidthOrHeight: 1280, useWebWorker: false }
        uploadFile = await imageCompression(file, options)
      } catch (warn) {
        console.warn("Image compression failed, using original:", warn)
      }

      step = 'compress_thumb'
      try {
        const thumbOptions = { maxSizeMB: 0.05, maxWidthOrHeight: 20, useWebWorker: false }
        const thumbFile = await imageCompression(file, thumbOptions)
        thumbBase64 = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onloadend = () => resolve(reader.result as string)
          reader.onerror = (e) => reject(e)
          reader.readAsDataURL(thumbFile)
        })
      } catch (warn) {
        console.warn("Image thumbnail generation failed:", warn)
      }
    }
    
    // Update optimistic message with actual thumbnail so it blanks the background
    const targetMsg = getOptimisticTarget();
    targetMsg.content = JSON.stringify({ thumbnail: thumbBase64 })
  
    step = 'prepare_form'
    const formData = new FormData()
    formData.append('file', uploadFile, file.name)
    formData.append('thumbnail', thumbBase64)
    
    step = 'xhr_upload'
    const data = await new Promise<any>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${props.apiBaseUrl}/api/chat/upload-image`)
      xhr.setRequestHeader('Authorization', `Bearer ${props.jwtToken}`)
      
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          const target = getOptimisticTarget();
          target.upload_progress = Math.round((e.loaded / e.total) * 100)
        }
      }
      
      xhr.onload = () => {
        if (xhr.status === 401) {
          reject(new Error("نشست شما منقضی شده است. لطفاً صفحه را رفرش کنید."))
          return
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText))
          } catch (err) {
            reject(new Error("Invalid JSON response"))
          }
        } else {
          try {
            const parsed = JSON.parse(xhr.responseText)
            if (parsed.detail) {
              reject(new Error(`مشکل سرور (${xhr.status}): ${parsed.detail}`))
              return
            }
          } catch (e) {}
          reject(new Error(`مشکل سرور (${xhr.status}): ${xhr.responseText.substring(0, 100)}`))
        }
      }
      xhr.onerror = () => reject(new Error("Network Error"))
      xhr.send(formData)
    })
    
    step = 'prepare_json'
    const messageContent = JSON.stringify({
      file_id: data.file_id,
      thumbnail: data.thumbnail
    })
    
    step = 'save_local_cache'
    // Instantly cache the local blob so downloading isn't needed
    await saveToDB(data.file_id, uploadFile)
    imageCache.value = { ...imageCache.value, [data.file_id]: localUrl }
    
    step = 'send_ws_message'
    await sendMediaMessage(isVideo ? 'video' : 'image', messageContent, localUrl)
    
  } catch (e: any) {
    console.error(`Upload error at step [${step}]:`, e);
    const errString = e && e.message ? e.message : JSON.stringify(e);
    error.value = `[${step}] ${errString}`;
    alert(`خطا در آپلود: ` + errString);
    optimisticMsg.is_error = true;
  } finally {
    isUploading.value = false
    if (input) input.value = ''
    // Remove the optimistic message so the real one from WebSocket takes its place
    // Or if error, keep it so user sees the error
    if (!optimisticMsg.is_error) {
       messages.value = messages.value.filter(m => m.id !== optimisticId)
    }
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
        pushBackState(() => {
          selectedUserId.value = null
          selectedUserName.value = ''
          messages.value = []
        })
        
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

const handleCopyMessage = () => {
  const msg = contextMenu.value.message;
  if (!msg || msg.message_type !== 'text') {
    closeContextMenu();
    return;
  }
  
  navigator.clipboard.writeText(msg.content).then(() => {
    closeContextMenu();
  }).catch(err => {
    console.error('Failed to copy', err);
    closeContextMenu();
  });
};

const handleDeleteSelected = async () => {
  if (selectedMessages.value.length === 0) return;
  if (!confirm('آیا از حذف پیام‌های انتخاب شده اطمینان دارید؟')) return;

  try {
    for (const msgId of selectedMessages.value) {
      const msg = messages.value.find(m => m.id === msgId)
      if (!msg || msg.sender_id !== props.currentUserId) continue
      const msgTime = new Date(msg.created_at).getTime()
      if (Date.now() - msgTime > 48 * 60 * 60 * 1000) continue
      
      await apiFetch(`/chat/messages/${msgId}`, { method: 'DELETE' });
      const index = messages.value.findIndex(m => m.id === msgId);
      if (index !== -1) messages.value.splice(index, 1);
    }
    clearSelection();
  } catch (err) {
    console.error('Failed to delete selected messages', err);
    alert('خطا در حذف پیام‌ها');
  }
};

const handleCopySelected = () => {
    const textToCopy = selectedMessages.value.map(id => {
        const msg = messages.value.find(m => m.id === id);
        return msg?.message_type === 'text' ? msg.content : '';
    }).filter(Boolean).join('\n\n');
    
    if (textToCopy) {
        navigator.clipboard.writeText(textToCopy).then(() => {
            clearSelection();
        }).catch(err => {
            console.error('Failed to copy', err);
        });
    }
}

const handleReplySelected = () => {
    if (selectedMessages.value.length === 1) {
        const msg = messages.value.find(m => m.id === selectedMessages.value[0]);
        if (msg) {
            handleReply(msg);
            clearSelection();
        }
    }
}

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

function openForwardModal() {
  if (selectedMessages.value.length > 0) {
    showForwardModal.value = true
  }
}

function closeForwardModal() {
  showForwardModal.value = false
}

async function forwardSelectedMessages(targetUserId: number) {
  if (selectedMessages.value.length === 0) return
  
  isSending.value = true
  try {
    for (const msgId of selectedMessages.value) {
      // Find the original message in current chat
      const originalMsg = messages.value.find(m => m.id === msgId)
      if (!originalMsg) continue

      await apiFetch('/chat/send', {
        method: 'POST',
        body: JSON.stringify({
          receiver_id: targetUserId,
          content: originalMsg.content,
          message_type: originalMsg.message_type,
          forwarded_from_id: originalMsg.sender_id
        })
      })
    }
    
    selectedMessages.value = []
    showForwardModal.value = false
    
    // Switch to target chat if different
    if (selectedUserId.value !== targetUserId) {
        const conv = conversations.value.find(c => c.other_user_id === targetUserId)
        if (conv) {
             selectedUserId.value = targetUserId
             selectedUserName.value = conv.other_user_name
             loadMessages(targetUserId)
        }
    } else {
        loadMessages(targetUserId, true)
    }
  } catch (err) {
    console.error('Failed to forward', err)
    alert('خطا در هدایت پیام‌ها')
  } finally {
    isSending.value = false
  }
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
    const translateX = Math.min(diff, 100) // cap at 100px
    return {
      transform: `translateX(-${translateX}px)`,
      // Smooth elastic transition when letting go, tight transition when dragging
      transition: translateX === 0 ? 'transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)' : 'none'
    }
  } else {
    // Received
    if (diff >= 0) return {}
    const translateX = Math.min(Math.abs(diff), 100)
    return {
      transform: `translateX(${translateX}px)`,
      transition: translateX === 0 ? 'transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)' : 'none'
    }
  }
}

// Controls the opacity and scale of the background reply icon
const getIconStyle = (msg: Message) => {
  if (swipedMessageId.value !== msg.id) return { opacity: 0, transform: 'scale(0.5)' }
  const diff = Math.abs(touchStartX.value - touchCurrentX.value)
  
  if (diff < 20) return { opacity: 0, transform: 'scale(0.5)' }
  
  // Max scale/opacity at 80px drag
  const progress = Math.min((diff - 20) / 60, 1)
  
  return {
      opacity: progress,
      transform: `scale(${0.5 + (0.5 * progress)})`,
      transition: diff === 0 ? 'all 0.4s easeOutBounce' : 'none'
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
        safeEl.classList.remove('highlight-message')
        // Force reflow to restart animation
        void safeEl.offsetWidth
        safeEl.classList.add('highlight-message')
        
        // Re-enable auto-scroll after highlight completes, and clean up class
        setTimeout(() => {
            isViewingReply.value = false
            safeEl.classList.remove('highlight-message')
        }, 3000)
        
        // Calculate target position using bounding rects to avoid relative offsetParent issues
        const containerRect = safeContainer.getBoundingClientRect()
        const elRect = safeEl.getBoundingClientRect()
        
        const relativeTop = elRect.top - containerRect.top
        const elHeight = elRect.height
        const containerHeight = containerRect.height
        
        // Scroll amount needed to center the element
        const scrollBy = relativeTop - (containerHeight / 2) + (elHeight / 2)
        const targetScrollTop = safeContainer.scrollTop + scrollBy
        
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

const handleForwardMessage = () => {
  const msg = contextMenu.value.message
  if (!msg) return
  if (!selectedMessages.value.includes(msg.id)) {
      selectedMessages.value.push(msg.id)
  }
  openForwardModal()
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
  
  if (path.startsWith('{')) {
    try {
      const parsed = JSON.parse(path)
      if (parsed.file_id) {
        return `${props.apiBaseUrl}/api/chat/files/${parsed.file_id}?token=${props.jwtToken}`
      }
    } catch (e) {
      // Ignore parse error
    }
  }
  
  // If already full URL, return as is
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path
  }
  // Prepend apiBaseUrl for relative paths
  return `${props.apiBaseUrl}${path}`
}

// Get image thumbnail (base64) from JSON content
function getImageThumbnail(path: string) {
  if (!path) return ''
  
  if (path.startsWith('{')) {
    try {
      const parsed = JSON.parse(path)
      if (parsed.thumbnail) {
        return parsed.thumbnail
      }
    } catch (e) {
      // Ignore parse error
    }
  }
  
  return ''
}

// Go back
function goBack() {
  if (selectedUserId.value) {
    // UI-initiated back — pop the back state (clears selection via callback skip + history.back)
    selectedUserId.value = null
    selectedUserName.value = ''
    messages.value = []
    popBackState()
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

// Check if current context menu message can be edited (own text message, not forwarded + within 48h)
const canEdit = computed(() => {
  const msg = contextMenu.value.message
  if (!msg) return false
  if (msg.sender_id !== props.currentUserId) return false
  if (msg.message_type !== 'text') return false
  // @ts-ignore - forwarded_from_id might not be strictly typed yet but exists
  if (msg.forwarded_from_id || (msg as any).forwarded_from_name) return false
  
  const msgTime = new Date(msg.created_at).getTime()
  const now = Date.now()
  return now - msgTime <= 48 * 60 * 60 * 1000
})

// Check if current context menu message can be deleted (own message + within 48h)
const canDelete = computed(() => {
  const msg = contextMenu.value.message
  if (!msg) return false
  if (msg.sender_id !== props.currentUserId) return false
  const msgTime = new Date(msg.created_at).getTime()
  const now = Date.now()
  return now - msgTime <= 48 * 60 * 60 * 1000
})

const canDeleteSelected = computed(() => {
   if (selectedMessages.value.length === 0) return false;
   return selectedMessages.value.every(id => {
      const msg = messages.value.find(m => m.id === id);
      if (!msg) return false;
      if (msg.sender_id !== props.currentUserId) return false;
      const msgTime = new Date(msg.created_at).getTime();
      return Date.now() - msgTime <= 48 * 60 * 60 * 1000;
   });
})

const canCopySelected = computed(() => {
   if (selectedMessages.value.length === 0) return false;
   return selectedMessages.value.every(id => {
      const msg = messages.value.find(m => m.id === id);
      return msg && msg.message_type === 'text';
   });
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
    pushBackState(() => {
      selectedUserId.value = null
      selectedUserName.value = ''
      messages.value = []
    })
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
  clearBackStack()
})

// Expose for parent to start new chat
defineExpose({ startNewChat })
</script>

<template>
  <div class="chat-view">
    <!-- Header - Telegram Style -->
    <div class="chat-header">
      <template v-if="!isSelectionMode">
        <!-- Back Button -->
      <button class="header-btn back-btn" v-ripple @click="goBack">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M19 12H5M12 19l-7-7 7-7"/>
        </svg>
      </button>
      
      <!-- Avatar + User Info (when in chat) -->
      <template v-if="selectedUserId">
        <div class="header-avatar" @click="viewProfile">{{ selectedUserName.charAt(0) }}</div>
        <div class="header-user-info" @click="viewProfile">
          <span class="header-name">{{ selectedUserName }}</span>
          <span class="header-status" :class="{ 'online': targetUserStatus.includes('آنلاین') || isTyping }">
            <template v-if="isTyping">
              در حال نوشتن<span class="typing-dots"><span>.</span><span>.</span><span>.</span></span>
            </template>
            <template v-else>
              {{ targetUserStatus }}
            </template>
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
         <button class="header-btn" v-ripple @click="toggleSearch">✕</button>
         
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
        <button class="header-btn" v-ripple @click="handleCall">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
             <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
          </svg>
        </button>
        <!-- Three-dot Menu -->
        <div class="header-menu-container" style="position: relative;">
            <button class="header-btn" v-ripple @click.stop="handleHeaderMenu">
              <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="1"></circle>
                <circle cx="12" cy="5" r="1"></circle>
                <circle cx="12" cy="19" r="1"></circle>
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
         <button class="header-btn" v-ripple @click="toggleSearch">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </button>
      </template>
      </template>
      <!-- Selection Mode Header -->
      <template v-else>
        <!-- Close Selection -->
        <button class="header-btn" v-ripple @click="clearSelection">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
        <div class="header-title" style="flex: 1; margin-right: 16px;">
          {{ selectedMessages.length }}
        </div>
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
        v-ripple
        :class="{ 'has-unread': conv.unread_count > 0, 'active': selectedUserId === conv.other_user_id }"
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

            <div class="message-wrapper" v-for="(msg, index) in group.messages" :key="msg.id">
              <div 
                v-if="swipedMessageId === msg.id" 
                class="swipe-reply-icon"
                :class="{ 
                    'sent-side': msg.sender_id === props.currentUserId, 
                    'received-side': msg.sender_id !== props.currentUserId,
                    'visible': Math.abs(touchStartX - touchCurrentX) > 20
                }"
              >
                <div class="reply-icon-wrapper" :style="getIconStyle(msg)">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="9 14 4 9 9 4"></polyline>
                        <path d="M20 20v-7a4 4 0 0 0-4-4H4"></path>
                    </svg>
                </div>
              </div>
              <div 
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
            <!-- Forwarded Banner -->
            <div v-if="msg.forwarded_from_name" class="forwarded-banner">
              <span class="forward-icon">
                 <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="15 14 20 9 15 4"></polyline>
                    <path d="M4 20v-7a4 4 0 0 1 4-4h12"></path>
                 </svg>
              </span>
              <div class="forward-content">
                <span class="forward-title">پیام هدایت شده</span>
                <span class="forward-text">از {{ msg.forwarded_from_name }}</span>
              </div>
            </div>
            
            <!-- Reply Context -->
            <div 
              v-if="msg.reply_to_message" 
              class="reply-context"
              @click.stop="scrollToMessage(msg.reply_to_message.id)"
            >
              <div class="reply-content">
                <span class="reply-author">
                   {{ msg.reply_to_message.sender_id === props.currentUserId ? 'شما' : selectedUserName }}
                </span>
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
            
            <!-- Media (Image/Video) -->
            <template v-else-if="msg.message_type === 'image' || msg.message_type === 'video'">
              <div class="msg-media-link"
                   :style="{ backgroundImage: getImageThumbnail(msg.content) ? `url(${getImageThumbnail(msg.content)})` : 'none', backgroundSize: 'cover' }"
                   @click="handleMediaClick(msg)"
                   style="cursor:pointer; position:relative;">
                
                <!-- 1. Uploading State -->
                <template v-if="msg.is_sending && msg.upload_progress !== undefined">
                  <div class="msg-media-content msg-media-overlay">
                    <div class="progress-container">
                      <svg class="progress-ring" viewBox="0 0 36 36">
                        <circle class="ring-bg" cx="18" cy="18" r="16"></circle>
                        <circle class="ring-fg" cx="18" cy="18" r="16" :stroke-dasharray="`${msg.upload_progress}, 100`"></circle>
                      </svg>
                      <span class="progress-text">{{ msg.upload_progress }}%</span>
                    </div>
                  </div>
                </template>
                
                <!-- 2. Downloaded or Local Render -->
                <template v-else-if="imageCache[getFileId(msg.content)] || msg.local_blob_url">
                  <img v-if="msg.message_type === 'image'"
                       :src="msg.local_blob_url || imageCache[getFileId(msg.content)]"
                       alt="تصویر" class="msg-media-content" />
                       
                  <div v-else-if="msg.message_type === 'video'" class="msg-video-wrapper">
                    <video :src="msg.local_blob_url || imageCache[getFileId(msg.content)]"
                           class="msg-media-content" autoplay muted loop playsinline></video>
                    <!-- Mini play indicator -->
                    <div class="video-play-indicator">
                      <svg viewBox="0 0 24 24" width="24" height="24" fill="white"><path d="M8 5v14l11-7z"/></svg>
                    </div>
                  </div>
                </template>
                
                <!-- 3. Needs Download State -->
                <template v-else>
                  <div class="msg-media-content msg-media-overlay">
                    <div v-if="msg.is_downloading" class="progress-container">
                      <svg class="progress-ring" viewBox="0 0 36 36">
                        <circle class="ring-bg" cx="18" cy="18" r="16"></circle>
                        <circle class="ring-fg" cx="18" cy="18" r="16" :stroke-dasharray="`${msg.download_progress || 0}, 100`"></circle>
                      </svg>
                    </div>
                    <button v-else class="download-btn" @click.stop="downloadMedia(msg)">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                        <polyline points="7 10 12 15 17 10"></polyline>
                        <line x1="12" y1="15" x2="12" y2="3"></line>
                      </svg>
                    </button>
                    <!-- Video duration / type badge placeholder -->
                    <span v-if="msg.message_type === 'video'" class="media-type-badge">
                      <svg viewBox="0 0 24 24" width="12" height="12" fill="white" style="vertical-align: middle;"><path d="M8 5v14l11-7z"/></svg> 
                      ویدئو
                    </span>
                  </div>
                </template>
              </div>
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
            </div> <!-- End .msg-meta -->
          </div> <!-- End .message-bubble -->
            </div> <!-- End .message-wrapper / v-for msg -->
          </div> <!-- End v-for="groupedMessages" message-group -->
        
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
      </div> <!-- End .messages-container -->
      </div> <!-- End .chat-content -->

      <!-- Input Area - Telegram Style -->
      <div class="input-area">
        <!-- Reply Banner -->
        <div v-if="replyingToMessage" class="reply-banner">
            <div class="reply-banner-icon">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#3390ec" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="9 14 4 9 9 4"></polyline>
                <path d="M20 20v-7a4 4 0 0 0-4-4H4"></path>
              </svg>
            </div>
            <div class="reply-banner-content">
                <span class="reply-banner-author">{{ replyingToMessage.sender_id === props.currentUserId ? 'شما' : selectedUserName }}</span>
                <span class="reply-banner-text">
                    {{ replyingToMessage.message_type === 'text' ? replyingToMessage.content : (replyingToMessage.message_type === 'image' ? '🖼️ تصویر' : '😊 استیکر') }}
                </span>
            </div>
            <button class="close-reply" v-ripple @click="cancelReply">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
        </div>

        <!-- Selection Mode Bottom Bar -->
        <div v-if="isSelectionMode" class="selection-bottom-bar">
          <button v-if="canDeleteSelected" class="selection-action-btn delete" v-ripple @click="handleDeleteSelected">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"></polyline>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            </svg>
            <span>حذف</span>
          </button>
          <button v-if="selectedMessages.length === 1" class="selection-action-btn" v-ripple @click="handleReplySelected">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 14 4 9 9 4"></polyline><path d="M20 20v-7a4 4 0 0 0-4-4H4"></path>
            </svg>
            <span>پاسخ</span>
          </button>
          <button v-if="canCopySelected" class="selection-action-btn" v-ripple @click="handleCopySelected">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
            <span>کپی</span>
          </button>
          <button class="selection-action-btn" v-ripple @click="openForwardModal">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 14 20 9 15 4"></polyline>
              <path d="M4 20v-7a4 4 0 0 1 4-4h12"></path>
            </svg>
            <span>هدایت</span>
          </button>
        </div>

        <!-- Input Container -->
        <div v-else class="input-container">
          <!-- Left side buttons - Show voice+attachment when empty, send when has text -->
          <template v-if="!messageInput.trim()">
            <!-- Voice Button -->
            <button v-ripple class="voice-btn">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="#8e8e93" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                <line x1="12" y1="19" x2="12" y2="23"></line>
                <line x1="8" y1="23" x2="16" y2="23"></line>
              </svg>
            </button>
            
            <!-- Attachment Button -->
            <input 
              type="file" 
              ref="imageInput" 
              accept="image/*,video/*" 
              style="display: none" 
              @change="handleImageUpload"
            />
            <button v-ripple class="attach-btn" @click="imageInput?.click()" :disabled="isUploading">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="#8e8e93" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
              </svg>
            </button>
          </template>
          
          <!-- Send Button - Show when has text (same position as voice+attachment) -->
          <button 
            v-else
            v-ripple
            class="send-btn-inline" 
            @click="sendMessage()" 
            @mousedown.prevent
            @touchstart.prevent="sendMessage()"
            :disabled="isSending"
          >
            <!-- Telegram Blue Send Icon -->
            <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="#3390ec" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transform: rotate(45deg); margin-left: -4px;">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
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
          <button class="emoji-btn" v-ripple @click="showStickerPicker = !showStickerPicker">
            <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="#8e8e93" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
              <line x1="9" y1="9" x2="9.01" y2="9"></line>
              <line x1="15" y1="9" x2="15.01" y2="9"></line>
            </svg>
          </button>
        </div>
      </div>

      <!-- Sticker Picker (Slide-up) -->
      <transition name="slide-up">
        <div v-show="showStickerPicker" class="sticker-picker">
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
      </transition>

      <!-- Forward Target Modal -->
      <div v-if="showForwardModal" class="forward-modal-overlay" @click="closeForwardModal">
        <div class="forward-modal" @click.stop>
          <div class="forward-modal-header">
            <h3>ارسال به...</h3>
            <button class="close-btn" @click="closeForwardModal">✕</button>
          </div>
          <div class="forward-modal-body">
            <div 
              v-for="conv in sortedConversations" 
              :key="conv.id"
              class="forward-conv-item"
              @click="forwardSelectedMessages(conv.other_user_id)"
            >
              <div class="conv-avatar">
                {{ conv.other_user_name.charAt(0) }}
              </div>
              <div class="conv-name">{{ conv.other_user_name }}</div>
            </div>
          </div>
        </div>
      </div>

    <!-- Context Menu (Teleport to body to avoid clipping/position issues) -->
    <Teleport to="body">
      <Transition name="zoom-fade">
        <div 
          v-if="contextMenu.visible" 
          class="context-menu telegram-menu-shadow"
          :style="{ top: contextMenu.y + 'px', left: contextMenu.x + 'px' }"
        >
          <div class="menu-item" v-ripple @click="handleReplyMessage">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 14 4 9 9 4"></polyline><path d="M20 20v-7a4 4 0 0 0-4-4H4"></path></svg>
          <span style="flex:1;">پاسخ</span>
        </div>
        <div class="menu-item" v-ripple @click="handleForwardMessage">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 14 20 9 15 4"></polyline><path d="M4 20v-7a4 4 0 0 1 4-4h12"></path></svg>
          <span style="flex:1;">هدایت پیام</span>
        </div>
        <template v-if="contextMenu.message?.message_type === 'text'">
            <div class="menu-item" v-ripple @click="handleCopyMessage">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
              <span style="flex:1;">کپی کردن</span>
            </div>
        </template>
        <template v-if="canEdit">
            <div class="menu-item" v-ripple @click="handleEditMessage">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
              <span style="flex:1;">ویرایش</span>
            </div>
        </template>
        <template v-if="canDelete">
            <div class="menu-item delete" v-ripple @click="handleDeleteMessage">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
              <span style="flex:1;">حذف</span>
            </div>
        </template>
        </div>
      </Transition>
      
      <!-- Click outside to close (Overlay) -->
      <div 
        v-if="contextMenu.visible" 
        class="context-overlay"
        @click="closeContextMenu"
      ></div>
    </Teleport>

    <!-- Lightbox Overlay -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="lightboxMedia" class="lightbox-overlay" @click="closeLightbox">
          <div class="lightbox-content" @click.stop>
            <button class="lightbox-close" @click="closeLightbox">✕</button>
            <img v-if="lightboxMedia.type === 'image'" :src="lightboxMedia.url" />
            <video v-else-if="lightboxMedia.type === 'video'" :src="lightboxMedia.url" controls autoplay></video>
          </div>
        </div>
      </Transition>

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
  /* Telegram classic light background color */
  background-color: #e4eaef;
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
  background: #ffffff; /* Solid white Telegram header */
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  border-bottom: none;
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
  background: linear-gradient(135deg, #fbbf24, #f59e0b);
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
  color: #f59e0b; /* Telegram blue for online status */
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
  padding: 10px 16px; /* slightly tighter padding for Telegram feel */
  border-bottom: none; /* Telegram typically lacks strong dividers here */
  cursor: pointer;
  background: #FFFFFF;
  border-radius: 10px; /* Slight roundness in lists */
  margin: 2px 8px; /* Floating items */
  transition: background 0.1s ease;
}

.conversation-item:hover {
  background: #f4f4f5; /* Very light modern gray hover */
}

.conversation-item:active {
  background: #e4e4e7;
}

.conversation-item.has-unread {
  background: rgba(245, 158, 11, 0.05);
}

.conv-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: linear-gradient(135deg, #fbbf24, #f59e0b);
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
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 2px;
}

.typing-dots span {
  animation: typing-dot 1.4s infinite ease-in-out both;
  display: inline-block;
}

.typing-dots span:nth-child(1) { animation-delay: -0.32s; }
.typing-dots span:nth-child(2) { animation-delay: -0.16s; }

@keyframes typing-dot {
  0%, 80%, 100% {
    transform: scale(0.6);
    opacity: 0.4;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
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
        background-color: rgba(0, 0, 0, 0.2); 
        color: #fff;
        text-shadow: none;
        font-weight: 500;
        border: none;
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
  /* Space for Slide-up sticker picker when open */
  padding-bottom: v-bind('showStickerPicker ? "250px" : "0px"');
  transition: padding-bottom 0.3s cubic-bezier(0.2, 0, 0, 1);
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

.message-wrapper {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
}

.swipe-reply-icon {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: rgba(0,0,0,0.05); /* Gentle circle background like Telegram */
  color: #8e8e93; /* Telegram neutral gray */
  z-index: 1; /* Below the sliding message bubble */
  opacity: 0;
  transition: opacity 0.2s;
}

.swipe-reply-icon.visible {
  opacity: 1;
}

.reply-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}

/* Sent message slides left, so icon is on the right */
.swipe-reply-icon.sent-side {
  right: 12px;
}

/* Received message slides right, so icon is on the left */
.swipe-reply-icon.received-side {
  left: 16px;
}

.message-bubble {
  max-width: 92%;
  padding: 8px 12px;
  border-radius: 12px;
  position: relative;
  font-size: 15px; /* Telegram font size */
  line-height: 1.5;
  white-space: pre-wrap; /* Preserve line breaks */
  word-wrap: break-word;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  /* Snappier Telegram animation curve */
  animation: slideIn 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  /* Smooth transition for swipe/returning */
  transition: transform 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
  
  /* Prevent native text selection on long press */
  -webkit-touch-callout: none;
  -webkit-user-select: none;
  -khtml-user-select: none;
  -moz-user-select: none;
  -ms-user-select: none;
  user-select: none;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(20px) scale(0.95);
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
  background: #eeffde; /* Telegram Sent Green */
  color: #000000;
  border-radius: 12px 12px 4px 12px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}

.message-bubble.received {
  align-self: flex-end;
  background: #FFFFFF; /* White bubble */
  color: #000000;
  border-radius: 12px 12px 12px 4px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}



.msg-time {
  font-size: 11px;
  color: rgba(0, 0, 0, 0.4); /* Telegram time color */
}

/* Override time color for received messages */
.message-bubble.received .msg-time {
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
  fill: #43A047; /* Telegram green read checkmarks */
}

.icon-unread {
  fill: rgba(0, 0, 0, 0.3); /* Translucent unread */
}

/* Forward Styles */
.forwarded-banner {
  font-size: 13px;
  color: #43A047; /* Green highlight for forward header in sent */
  margin-bottom: 2px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.message-bubble.received .forwarded-banner {
  color: #8E8E93;
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

.msg-image-placeholder {
  min-width: 120px;
  min-height: 90px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0,0,0,0.08);
  border-radius: 8px;
  color: #888;
}

.msg-sticker {
  font-size: 48px;
}

/* Input Area - Solid Telegram Style */
.input-area {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  padding: 8px 12px 12px 12px;
  background: #ffffff;
  gap: 0;
  border-top: none;
  box-shadow: 0 -1px 2px rgba(0, 0, 0, 0.05);
  position: relative;
  z-index: 60; /* Above sticker picker */
}

.input-container {
  width: 100%;
  gap: 8px;
  flex: 1;
  display: flex;
  align-items: flex-end;
  background: #ffffff; /* Telegram input sits flat on white */
  border: none;
  box-shadow: none;
  border-radius: 20px;
  padding: 8px 4px;
  min-height: 44px;
  transition: background 0.2s;
}

.input-container:focus-within {
  background: #ffffff;
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
.slide-up-enter-active,
.slide-up-leave-active {
  transition: transform 0.3s cubic-bezier(0.2, 0, 0, 1), opacity 0.3s;
}

.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(100%);
  opacity: 0;
}

.sticker-picker {
  background: #f4f4f5; /* Telegram Light Ash */
  border-top: 1px solid rgba(0,0,0,0.05);
  padding: 16px 12px;
  max-height: 250px;
  overflow-y: auto;
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 50;
  transform: translateY(0);
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
  color: var(--text-color);
  transition: background 0.1s; /* Faster hover transition */
}

/* Base Ripple Setup */
.ripple-container {
  position: relative;
  overflow: hidden;
}

.ripple-effect {
  position: absolute;
  border-radius: 50%;
  background-color: rgba(0, 0, 0, 0.1); /* Default Telegram light-theme ripple */
  transform: scale(0);
  animation: ripple 0.6s linear;
  pointer-events: none; /* Let clicks pass through */
}

@keyframes ripple {
  to {
    transform: scale(4);
    opacity: 0;
  }
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
  border-right: 2px solid #3390ec;
  background: rgba(51, 144, 236, 0.08); /* light blue */
  border-radius: 4px;
  padding: 4px 8px;
  margin-bottom: 6px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  max-width: 100%;
}

.message-bubble.sent .reply-context {
  border-right: 2px solid #43A047;
  background: rgba(67, 160, 71, 0.1);
}

.reply-author {
  font-size: 13px;
  font-weight: 500;
  color: #3390ec;
}

.message-bubble.sent .reply-author {
  color: #2ea043; /* Telegram Sent Reply Author Green */
}

.reply-text {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  opacity: 0.8;
  display: block;
  max-width: 100%;
}

/* Reply Banner (Input Area) */
.reply-banner {
  position: relative;
  display: flex;
  align-items: center;
  background: #FFFFFF;
  padding: 8px 16px 8px 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  animation: slideUp 0.15s ease-out;
  min-height: 46px;
  gap: 12px;
}

@keyframes slideUp {
  from { transform: translateY(100%); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}

.reply-banner-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #3390ec;
}

.reply-banner-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  border-right: 2px solid #3390ec;
  padding-right: 8px;
  justify-content: center;
  overflow: hidden;
}

.reply-banner-author {
  font-size: 14px;
  font-weight: 500;
  color: #3390ec;
  line-height: 1.2;
  margin-bottom: 2px;
}

.reply-banner-text {
  font-size: 13px;
  color: #8e8e93;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.close-reply {
  background: none;
  border: none;
  color: #8E8E93;
  cursor: pointer;
  padding: 4px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s;
}

.close-reply:hover {
  background: rgba(0, 0, 0, 0.05);
  color: #000;
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
  position: relative;
  z-index: 10;
}

.selected-message::before {
  content: '';
  position: absolute;
  top: -4px; right: -16px; bottom: -4px; left: -16px;
  background-color: rgba(51, 144, 236, 0.15); /* Universal Telegram Select Overlay */
  pointer-events: none;
  z-index: -1; /* Behind the bubble but over the background */
  border-radius: 6px;
}

/* Forward Styles */
.forwarded-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  cursor: pointer;
}
.forward-icon {
  color: #3390ec;
}
.message-bubble.sent .forward-icon {
  color: #43A047;
}
.forward-content {
  display: flex;
  flex-direction: column;
}
.forward-title {
  font-size: 13px;
  font-weight: 500;
  color: #3390ec;
  line-height: 1.2;
}
.message-bubble.sent .forward-title {
  color: #43A047;
}
.forward-text {
  font-size: 13px;
  color: inherit;
  opacity: 0.8;
  line-height: 1.2;
}
.forward-modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}
.forward-modal {
  background: white;
  border-radius: 12px;
  width: 90%;
  max-width: 320px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.forward-modal-header {
  padding: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #eee;
}
.forward-modal-header h3 { margin: 0; font-size: 16px; }
.close-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: #8e8e93; }
.forward-modal-body {
  overflow-y: auto;
  padding: 8px 0;
}
.forward-conv-item {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  cursor: pointer;
}
.forward-conv-item:hover { background: #f5f5f5; }
.forward-conv-item .conv-avatar {
  width: 40px; height: 40px;
  border-radius: 20px;
  background: #f59e0b;
  color: white;
  display: flex; align-items: center; justify-content: center;
  font-weight: bold;
  margin-left: 12px;
}
.forward-conv-item .conv-name {
  font-weight: 500;
  color: #000;
}

@media (prefers-color-scheme: dark) {
  .forward-modal { background: #1e1e1e; }
  .forward-modal-header { border-bottom-color: #333; color: white; }
  .forward-conv-item:hover { background: #2a2a2a; }
  .forward-conv-item .conv-name { color: white; }
}
.selection-bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-around;
  width: 100%;
  padding: 8px 0;
  background: white;
  min-height: 56px;
}

.selection-action-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: #8e8e93;
  font-size: 11px;
  font-weight: 500;
  gap: 4px;
  padding: 6px 16px;
  border-radius: 8px;
  cursor: pointer;
  transition: opacity 0.2s, background 0.2s;
}

.selection-action-btn:hover {
  background: rgba(0,0,0,0.05);
  color: #000;
}

.selection-action-btn.delete {
  color: #ef4444;
}

.selection-action-btn.delete:hover {
  background: rgba(239, 68, 68, 0.1);
}

.selection-action-btn svg {
  margin-bottom: 2px;
}

/* Telegram-Style Media UI */
.msg-media-link {
  border-radius: 8px;
  overflow: hidden;
  display: block;
  min-width: 150px;
  min-height: 150px;
}
.msg-media-content {
  width: 100%;
  height: auto;
  max-height: 300px;
  object-fit: cover;
  display: block;
}
.msg-video-wrapper {
  position: relative;
}
.video-play-indicator {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: rgba(0,0,0,0.5);
  border-radius: 50%;
  padding: 12px;
  pointer-events: none;
}
.msg-media-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0,0,0,0.4);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
}
.progress-container {
  position: relative;
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.progress-ring {
  position: absolute;
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}
.ring-bg {
  fill: none;
  stroke: rgba(255,255,255,0.2);
  stroke-width: 3;
}
.ring-fg {
  fill: none;
  stroke: white;
  stroke-width: 3;
  stroke-linecap: round;
  transition: stroke-dasharray 0.3s ease;
}
.progress-text {
  color: white;
  font-size: 11px;
  font-weight: bold;
}
.download-btn {
  background: rgba(0,0,0,0.5);
  border: none;
  border-radius: 50%;
  color: white;
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.2s;
}
.download-btn:hover {
  background: rgba(0,0,0,0.7);
}
.media-type-badge {
  position: absolute;
  bottom: 8px;
  left: 8px;
  background: rgba(0,0,0,0.6);
  color: white;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  gap: 4px;
}

/* Lightbox */
.lightbox-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.9);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
}
.lightbox-content {
  position: relative;
  max-width: 90vw;
  max-height: 90vh;
}
.lightbox-content img, .lightbox-content video {
  max-width: 100%;
  max-height: 90vh;
  object-fit: contain;
  border-radius: 8px;
}
.lightbox-close {
  position: absolute;
  top: -40px;
  right: 0;
  background: none;
  border: none;
  color: white;
  font-size: 24px;
  cursor: pointer;
}

</style>
