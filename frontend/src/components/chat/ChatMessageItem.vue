<template>
  <div class="message-wrapper">
    <div 
      v-if="isSwiping" 
      class="swipe-reply-icon"
      :class="{ 
          'sent-side': isSent, 
          'received-side': !isSent,
          'visible': Math.abs(touchStartX - touchCurrentX) > 20
      }"
    >
      <div class="reply-icon-wrapper" :style="getIconStyle()">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 14 4 9 9 4"></polyline>
              <path d="M20 20v-7a4 4 0 0 0-4-4H4"></path>
          </svg>
      </div>
    </div>
    
    <div 
      :id="'msg-' + msg.id"
      class="message-bubble"
      :class="{ 
        'sent': isSent, 
        'received': !isSent,
        'sending': isSending,
        'error': isError,
        'selected-message': isSelected
      }"
      @click="handleClick($event)"
      @touchstart="handleTouchStart($event)"
      @touchmove="handleTouchMove($event)"
      @touchend="handleTouchEnd($event)"
      @contextmenu.prevent="handleContextMenu($event)"
      :style="getSwipeStyle()"
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
        @click.stop="$emit('scroll-to', msg.reply_to_message.id)"
      >
        <div class="reply-content">
          <span class="reply-author">
             {{ msg.reply_to_message.sender_id === currentUserId ? 'شما' : selectedUserName }}
          </span>
          <span class="reply-text">
            <template v-if="msg.reply_to_message.message_type === 'image'">🖼️ تصویر</template>
            <template v-else-if="msg.reply_to_message.message_type === 'video'">📹 ویدیو</template>
            <template v-else-if="msg.reply_to_message.message_type === 'sticker'">😊 استیکر</template>
            <template v-else>{{ msg.reply_to_message.content }}</template>
          </span>
        </div>
      </div>
      
      <!-- Text -->
      <template v-if="msg.message_type === 'text'">
        <p v-html="highlightedContent"></p>
      </template>
      
      <!-- Media (Image/Video) -->
      <template v-else-if="msg.message_type === 'image' || msg.message_type === 'video'">
        <div class="msg-media-link"
             :style="{ backgroundImage: thumbnail ? `url(${thumbnail})` : 'none', backgroundSize: 'cover' }"
             @click.stop="$emit('media-click', msg)"
             style="cursor:pointer; position:relative;">
          
          <!-- 1. Downloaded, Uploading, or Local Render -->
          <template v-if="isCached || msg.local_blob_url">
            <div style="position: relative; display: inline-block; width: 100%;">
              <img v-if="msg.message_type === 'image'"
                   :src="msg.local_blob_url || cachedUrl"
                   alt="تصویر" class="msg-media-content" />
                   
              <div v-else-if="msg.message_type === 'video'" class="msg-video-wrapper">
                <video :src="msg.local_blob_url || cachedUrl"
                       class="msg-media-content" autoplay muted loop playsinline></video>
                <div v-if="!msg.is_sending" class="video-play-indicator">
                  <svg viewBox="0 0 24 24" width="24" height="24" fill="white"><path d="M8 5v14l11-7z"/></svg>
                </div>
              </div>
              
              <!-- Overlay for uploading state -->
              <div v-if="msg.is_sending && msg.upload_progress !== undefined" class="msg-media-overlay" @click.stop>
                <div class="progress-container">
                  <svg class="progress-ring" viewBox="0 0 36 36">
                    <circle class="ring-bg" cx="18" cy="18" r="16"></circle>
                    <circle class="ring-fg" cx="18" cy="18" r="16" :stroke-dasharray="`${msg.upload_progress}, 100`"></circle>
                  </svg>
                  <span class="progress-text">{{ msg.upload_progress }}%</span>
                </div>
              </div>
            </div>
          </template>
          
          <!-- 2. Needs Download State -->
          <template v-else>
            <div class="msg-media-content msg-media-overlay" @click.stop>
              <div v-if="msg.is_downloading" class="progress-container">
                <svg class="progress-ring" viewBox="0 0 36 36">
                  <circle class="ring-bg" cx="18" cy="18" r="16"></circle>
                  <circle class="ring-fg" cx="18" cy="18" r="16" :stroke-dasharray="`${msg.download_progress || 0}, 100`"></circle>
                </svg>
              </div>
              <button v-else class="download-btn" @click.stop="$emit('download', msg)">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                  <polyline points="7 10 12 15 17 10"></polyline>
                  <line x1="12" y1="15" x2="12" y2="3"></line>
                </svg>
              </button>
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
          {{ formattedTime }}
          <span v-if="msg.updated_at" class="edited-label">(ویرایش شده)</span>
        </span>
        <span v-if="isSent" class="msg-status">
          <!-- Sending -->
          <svg v-if="isSending" viewBox="0 0 24 24" class="icon-clock" width="16" height="16" style="color: #aaa;">
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
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'

const props = defineProps<{
  msg: any
  currentUserId: number | null
  selectedUserName: string
  selectedMessages: number[]
  imageCache: Record<string, string>
  isSelectionMode: boolean
  searchQuery?: string
}>()

const emit = defineEmits<{
  (e: 'swipe-reply', msg: any): void
  (e: 'select', msg: any): void
  (e: 'context-menu', event: TouchEvent | MouseEvent, msg: any): void
  (e: 'click-message', event: Event, msg: any): void
  (e: 'scroll-to', msgId: number): void
  (e: 'media-click', msg: any): void
  (e: 'download', msg: any): void
}>()

// --- Computed State ---
const isSent = computed(() => props.msg.sender_id === props.currentUserId)
const isSending = computed(() => props.msg.id < 0 || props.msg.is_sending)
const isError = computed(() => props.msg.is_error)
const isSelected = computed(() => props.selectedMessages.includes(props.msg.id))

const isCached = computed(() => !!props.imageCache[getFileId(props.msg.content)])
const cachedUrl = computed(() => props.imageCache[getFileId(props.msg.content)])
const thumbnail = computed(() => getImageThumbnail(props.msg.content))
const formattedTime = computed(() => formatTime(props.msg.created_at))

function escapeHtml(unsafe: string) {
  return (unsafe || '').replace(/[&<"'>]/g, function (m) {
    switch (m) {
      case '&': return '&amp;';
      case '<': return '&lt;';
      case '>': return '&gt;';
      case '"': return '&quot;';
      case "'": return '&#039;';
      default: return m;
    }
  });
}

const highlightedContent = computed(() => {
  const content = props.msg.content || ''
  if (!props.searchQuery) return escapeHtml(content)
  
  const escapedContent = escapeHtml(content)
  const escapedQuery = props.searchQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const regex = new RegExp(`(${escapedQuery})`, 'gi')
  return escapedContent.replace(regex, '<mark class="in-bubble-highlight">$1</mark>')
})

// --- Touch & Swipe State ---
const SWIPE_THRESHOLD = 100
const touchStartX = ref(0)
const touchCurrentX = ref(0)
const isSwiping = ref(false)
const longPressTimer = ref<number | null>(null)

const handleTouchStart = (e: TouchEvent) => {
  if (props.isSelectionMode) return
  if (e.touches.length > 0) {
    const touch = e.touches[0]
    if (touch) {
      touchStartX.value = touch.clientX
      touchCurrentX.value = touch.clientX
      isSwiping.value = true
    }
  }

  if (longPressTimer.value) clearTimeout(longPressTimer.value)
  longPressTimer.value = window.setTimeout(() => {
    emit('context-menu', e, props.msg)
  }, 500)
}

const handleTouchMove = (e: TouchEvent) => {
  if (!isSwiping.value) return
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

const handleTouchEnd = (e: TouchEvent) => {
  if (longPressTimer.value) {
    clearTimeout(longPressTimer.value)
    longPressTimer.value = null
  }
  
  if (!isSwiping.value) return
  
  const diff = touchStartX.value - touchCurrentX.value
  const isValidSwipe = isSent.value ? (diff > SWIPE_THRESHOLD) : (diff < -SWIPE_THRESHOLD)

  if (isValidSwipe) {
    emit('swipe-reply', props.msg)
  }
  
  // Reset
  isSwiping.value = false
  touchStartX.value = 0
  touchCurrentX.value = 0
}

const handleClick = (e: Event) => {
  if (props.isSelectionMode) {
    emit('select', props.msg)
  } else {
    emit('click-message', e, props.msg)
  }
}

const handleContextMenu = (e: MouseEvent) => {
  emit('context-menu', e, props.msg)
}

function getSwipeStyle() {
  if (!isSwiping.value) return {}
  const diff = touchStartX.value - touchCurrentX.value

  if (isSent.value) {
    if (diff <= 0) return {}
    const translateX = Math.min(diff, 100)
    return {
      transform: `translateX(-${translateX}px)`,
      transition: translateX === 0 ? 'transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)' : 'none'
    }
  } else {
    if (diff >= 0) return {}
    const translateX = Math.min(Math.abs(diff), 100)
    return {
      transform: `translateX(${translateX}px)`,
      transition: translateX === 0 ? 'transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)' : 'none'
    }
  }
}

function getIconStyle() {
  if (!isSwiping.value) return { opacity: 0, transform: 'scale(0.5)' }
  const diff = Math.abs(touchStartX.value - touchCurrentX.value)
  if (diff < 20) return { opacity: 0, transform: 'scale(0.5)' }
  const progress = Math.min((diff - 20) / 60, 1)
  return {
      opacity: progress,
      transform: `scale(${0.5 + (0.5 * progress)})`,
      transition: diff === 0 ? 'all 0.4s easeOutBounce' : 'none'
  }
}

// --- Formatting Helpers ---
function formatTime(dateString: string) {
  if (!dateString) return ''
  const date = new Date(dateString)
  return new Intl.DateTimeFormat('fa-IR', {
    hour: '2-digit', minute: '2-digit'
  }).format(date)
}

function getFileId(content: string) {
  if (!content) return ''
  try {
     const data = JSON.parse(content)
     return data.file_id || ''
  } catch {
      return content.split('::')[0] || ''
  }
}

function getImageThumbnail(content: string) {
  if (!content) return ''
  try {
      const data = JSON.parse(content)
      return data.thumbnail || ''
  } catch {
      const parts = content.split('::')
      const base64Data = parts[1]
      if (base64Data) {
          if (base64Data.startsWith('data:image')) return base64Data
          return `data:image/jpeg;base64,${base64Data}`
      }
      return ''
  }
}
</script>

<style scoped>
.message-wrapper {
  position: relative; display: flex; flex-direction: column; width: 100%;
}
.swipe-reply-icon {
  position: absolute; top: 50%; transform: translateY(-50%); display: flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: 50%; background: rgba(0,0,0,0.05); color: #8e8e93; z-index: 1;
  opacity: 0; transition: opacity 0.2s;
}
.swipe-reply-icon.visible { opacity: 1; }
.reply-icon-wrapper { display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; }
.swipe-reply-icon.sent-side { right: 12px; }
.swipe-reply-icon.received-side { left: 16px; }

.message-bubble {
  max-width: 92%; padding: 8px 12px; border-radius: 12px; position: relative; font-size: 15px; line-height: 1.5;
  white-space: pre-wrap; word-wrap: break-word; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  animation: slideIn 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  transition: transform 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
  -webkit-touch-callout: none; -webkit-user-select: none; user-select: none;
}
@keyframes slideIn {
  from { opacity: 0; transform: translateY(20px) scale(0.95); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.message-bubble.sent { align-self: flex-start; background: #eeffde; color: #000000; border-radius: 12px 12px 4px 12px; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15); }
.message-bubble.received { align-self: flex-end; background: #FFFFFF; color: #000000; border-radius: 12px 12px 12px 4px; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15); }
.message-bubble p { margin: 0; }

.msg-time { font-size: 11px; color: rgba(0, 0, 0, 0.4); }
.message-bubble.received .msg-time { color: #8E8E93; }
.msg-meta { display: flex; align-items: center; justify-content: flex-end; gap: 4px; margin-top: 4px; }
.msg-status { display: flex; align-items: center; }
.icon-read { fill: #43A047; }
.icon-unread { fill: rgba(0, 0, 0, 0.3); }

/* Forward Styles */
.forwarded-banner { font-size: 13px; color: #43A047; margin-bottom: 2px; display: flex; align-items: center; gap: 4px; }
.message-bubble.received .forwarded-banner { color: #8E8E93; }
.forward-icon { color: #3390ec; }
.message-bubble.sent .forward-icon { color: #43A047; }
.forward-content { display: flex; flex-direction: column; }
.forward-title { font-size: 13px; font-weight: 500; color: #3390ec; line-height: 1.2; }
.message-bubble.sent .forward-title { color: #43A047; }
.forward-text { font-size: 13px; color: inherit; opacity: 0.8; line-height: 1.2; }

/* Reply Context */
.reply-context {
  border-right: 2px solid #3390ec; background: rgba(51, 144, 236, 0.08); border-radius: 4px;
  padding: 4px 8px; margin-bottom: 6px; cursor: pointer; display: flex; flex-direction: column; max-width: 100%; overflow: hidden;
}
.message-bubble.sent .reply-context { border-right: 2px solid #43A047; background: rgba(67, 160, 71, 0.1); }
.reply-content { display: flex; flex-direction: column; overflow: hidden; }
.reply-author { font-size: 13px; font-weight: 500; color: #3390ec; }
.message-bubble.sent .reply-author { color: #2ea043; }
.reply-text { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: 0.8; display: block; max-width: 100%; }

.msg-sticker { font-size: 48px; }
.edited-label { font-size: 10px; font-style: italic; opacity: 0.7; margin-right: 4px; }

/* Selection */
.selected-message { position: relative; z-index: 10; }
.selected-message::before {
  content: ''; position: absolute; top: -4px; right: -16px; bottom: -4px; left: -16px;
  background-color: rgba(51, 144, 236, 0.15); pointer-events: none; z-index: -1; border-radius: 6px;
}

/* Base Media Styles */
.msg-media-link { border-radius: 8px; overflow: hidden; display: block; min-width: 150px; min-height: 150px; }
.msg-media-content { width: 100%; height: auto; max-height: 300px; object-fit: cover; display: block; }
.msg-video-wrapper { position: relative; }
.video-play-indicator { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(0,0,0,0.5); border-radius: 50%; padding: 12px; pointer-events: none; }
.msg-media-overlay {
  position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.4); backdrop-filter: blur(5px);
  display: flex; align-items: center; justify-content: center;
}
.progress-container { position: relative; width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; }
.progress-ring { position: absolute; width: 100%; height: 100%; transform: rotate(-90deg); }
.ring-bg { fill: none; stroke: rgba(255,255,255,0.2); stroke-width: 3; }
.ring-fg { fill: none; stroke: white; stroke-width: 3; stroke-linecap: round; transition: stroke-dasharray 0.3s ease; }
.progress-text { color: white; font-size: 11px; font-weight: bold; }
.download-btn {
  background: rgba(0,0,0,0.5); border: none; border-radius: 50%; color: white; width: 48px; height: 48px; display: flex;
  align-items: center; justify-content: center; cursor: pointer; transition: background 0.2s;
}
.download-btn:hover { background: rgba(0,0,0,0.7); }
.media-type-badge { position: absolute; bottom: 8px; left: 8px; background: rgba(0,0,0,0.6); color: white; font-size: 10px; padding: 2px 6px; border-radius: 12px; display: flex; align-items: center; gap: 4px; }

/* Highlight */
.highlight-message::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  border-radius: inherit;
  pointer-events: none;
  animation: highlight 2.5s ease-in-out forwards;
}

@keyframes highlight {
  0% { box-shadow: none; background-color: transparent; }
  15% { box-shadow: 0 0 0 3px rgba(255, 200, 0, 0.6), 0 0 15px 5px rgba(255, 200, 0, 0.2); background-color: rgba(255, 200, 0, 0.1); }
  100% { box-shadow: none; background-color: transparent; }
}

:deep(.in-bubble-highlight) {
  background-color: rgba(255, 200, 0, 0.5);
  color: inherit;
  border-radius: 2px;
  padding: 0 2px;
}
</style>
