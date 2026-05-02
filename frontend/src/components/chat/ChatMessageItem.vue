<template>
  <div
    class="message-wrapper"
    @click="handleWrapperClick($event)"
    @touchstart="handleWrapperTouchStart($event)"
    @touchmove="handleWrapperTouchMove($event)"
    @touchend="handleWrapperTouchEnd()"
    @touchcancel="handleWrapperTouchCancel()"
    @contextmenu.prevent="handleWrapperContextMenu($event)"
  >
    <div 
      v-if="isSwiping" 
      class="swipe-reply-icon"
      :class="{ 
          'sent-side': isSent, 
          'received-side': !isSent,
          'visible': swipeVisualProgress > 0.08,
          'armed': isSwipeReplyArmed
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
      ref="messageBubbleRef"
      class="message-bubble"
      :class="{ 
        'sent': isSent, 
        'received': !isSent,
        'sending': isSending,
        'error': isError,
        'selected-message': isSelected,
        'album-bubble': props.isAlbum
      }"
      @touchstart="handleTouchStart($event)"
      @touchmove="handleTouchMove($event)"
      @touchend="handleTouchEnd()"
      @touchcancel="handleTouchCancel()"
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
            <template v-else-if="msg.reply_to_message.message_type === 'voice'">🎤 پیام صوتی</template>
            <template v-else-if="msg.reply_to_message.message_type === 'sticker'">😊 استیکر</template>
            <template v-else-if="msg.reply_to_message.message_type === 'location'">📍 موقعیت</template>
            <template v-else>{{ msg.reply_to_message.content }}</template>
          </span>
        </div>
      </div>
      
      <!-- Text -->
      <template v-if="msg.message_type === 'text'">
        <p v-html="highlightedContent"></p>
      </template>
      
      <!-- Album -->
      <template v-if="props.isAlbum">
        <ChatAlbumLayout
          :items="albumLayoutItems"
          :currentUserId="currentUserId"
          :isDownloadSelectionMode="props.isAlbumDownloadMode"
          :selectedDownloadMessageIds="props.selectedAlbumDownloadMessageIds"
          @media-click="$emit('media-click', $event)"
          @download="$emit('download', $event)"
          @cancel-send="$emit('cancel-send', $event)"
          @reply-item="$emit('reply-album-item', $event)"
          @forward-item="$emit('forward-album-item', $event)"
          @delete-item="$emit('delete-album-item', $event)"
          @toggle-download-item="$emit('toggle-album-download-item', $event)"
        />
      </template>

      <!-- Media (Image/Video) -->
      <template v-else-if="msg.message_type === 'image' || msg.message_type === 'video'">
        <div class="msg-media-link w-[280px] sm:w-[320px] max-w-full rounded-lg overflow-hidden relative"
             :style="mediaStyle"
             @click.stop="$emit('media-click', msg)">
          
          <!-- 1. Downloaded, Uploading, or Local Render -->
          <template v-if="isCached || msg.local_blob_url">
            <div class="absolute inset-0 w-full h-full flex items-center justify-center">
              <img v-if="msg.message_type === 'image'"
                   v-show="!msg.is_sending || thumbnail"
                    :data-media-msg-id="msg.id"
                   :src="msg.local_blob_url || cachedUrl"
                    alt="تصویر" class="msg-media-content w-full h-full object-cover absolute inset-0 block" />
                   
              <div v-else-if="msg.message_type === 'video'" class="absolute inset-0 w-full h-full">
                <video v-show="!msg.is_sending" :src="msg.local_blob_url || cachedUrl"
                       class="w-full h-full object-cover absolute inset-0 block" autoplay muted loop playsinline></video>
                <div v-if="!msg.is_sending" class="video-play-indicator">
                  <svg viewBox="0 0 24 24" width="24" height="24" fill="white"><path d="M8 5v14l11-7z"/></svg>
                </div>
              </div>
              
              <!-- Overlay for uploading state -->
              <div v-if="msg.is_sending && msg.upload_progress !== undefined" class="msg-media-overlay cancelable-overlay" @click.stop="$emit('cancel-send', msg)" style="cursor:pointer;">
                <div v-if="msg.upload_total > 0" class="telegram-size-badge" :style="{ direction: msg.upload_progress === 100 ? 'rtl' : 'ltr' }">
                  <span v-if="msg.upload_progress === 100">در حال پردازش...</span>
                  <span v-else>{{ formatBytes(msg.upload_loaded) }} / {{ formatBytes(msg.upload_total) }}</span>
                </div>
                <div class="progress-container cancelable" style="background: rgba(0,0,0,0.5); border-radius: 50%; padding: 8px; width: 44px; height: 44px; display: flex; align-items: center; justify-content: center;">
                  <svg class="progress-ring" viewBox="0 0 36 36" style="position: absolute; width: 44px; height: 44px;">
                    <circle class="ring-bg" cx="18" cy="18" r="16"></circle>
                    <circle class="ring-fg" cx="18" cy="18" r="16" :stroke-dasharray="`${msg.upload_progress}, 100`"></circle>
                  </svg>
                  <div class="progress-cancel-icon" style="color: white; font-size: 18px; z-index: 2;">✕</div>
                </div>
              </div>
            </div>
          </template>
          
          <!-- 2. Needs Download State -->
          <template v-else>
            <div class="w-full h-full absolute inset-0 msg-media-overlay z-10" @click.stop>
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
      
      <!-- Voice Message -->
      <template v-else-if="msg.message_type === 'voice'">
        <div class="msg-voice" :class="{ 'is-sent': isSent, 'is-loading': isVoiceLoading, 'is-error': isVoiceErrored }">
          <button class="voice-play-btn" :class="{ 'is-active': isPlaying }" @click.stop="toggleVoice">
            <svg v-if="!isPlaying" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
              <path d="M8 5v14l11-7z"/>
            </svg>
            <svg v-else viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
              <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
            </svg>
          </button>
          
          <div class="voice-body" style="min-width: 0; flex: 1;">
            <div
              ref="waveformRef"
              class="voice-waveform"
              :class="{ 'is-interactive': Boolean(audioUrl) }"
              @click.stop="handleVoiceWaveformClick"
            >
              <div class="voice-wave-bars" aria-hidden="true">
                <span
                  v-for="(barHeight, index) in voiceWaveBars"
                  :key="`${msg.id}-${index}`"
                  class="voice-wave-bar"
                  :class="{ 'is-played': index < playedVoiceBarCount }"
                  :style="{ height: `${barHeight}%` }"
                />
              </div>
            </div>
            <div class="voice-meta-row">
              <div class="voice-time">{{ formattedVoiceTime }}</div>
              <div v-if="isVoiceLoading" class="voice-state-dot is-loading" aria-hidden="true"></div>
              <div v-else-if="isVoiceErrored" class="voice-state-dot is-error" aria-hidden="true"></div>
            </div>
          </div>
          
          <div v-if="msg.is_sending && msg.upload_progress !== undefined" class="msg-voice-uploading" @click.stop="$emit('cancel-send', msg)">
            <svg class="progress-ring-small" viewBox="0 0 36 36">
              <circle class="ring-bg" cx="18" cy="18" r="16"></circle>
              <circle class="ring-fg" cx="18" cy="18" r="16" :stroke-dasharray="`${msg.upload_progress}, 100`"></circle>
            </svg>
            <div class="voice-cancel-icon" style="position: absolute; top:50%; left:50%; transform: translate(-50%, -50%); font-size:12px; color:white;">✕</div>
          </div>
        </div>
      </template>
      
      <!-- Sticker -->
      <template v-else-if="msg.message_type === 'sticker'">
        <div class="msg-sticker">{{ msg.content }}</div>
      </template>

      <!-- Location -->
      <template v-else-if="msg.message_type === 'location'">
        <div class="msg-location" @click="$emit('location-click', msg)">
          <div v-if="mapSnapshotUrl" class="location-snapshot" :style="{ backgroundImage: `url(${mapSnapshotUrl})` }">
            <div class="location-pin">
              <svg viewBox="0 0 24 24" width="32" height="32" fill="#E53935">
                <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/>
              </svg>
            </div>
            <div class="location-label-overlay">📍 موقعیت مکانی</div>
          </div>
          <div v-else class="location-preview fallback">
            <svg viewBox="0 0 24 24" width="32" height="32" fill="#E53935">
              <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/>
            </svg>
            <span class="location-label">📍 موقعیت مکانی</span>
          </div>
        </div>
      </template>

      <!-- Document/File Message -->
      <template v-else-if="msg.message_type === 'document'">
        <div class="msg-document" :class="{ 'is-busy': isDocumentBusy }" @click.stop="handleDocumentOpenClick">
          <div v-if="isDocumentBusy" class="doc-icon doc-uploading" @click.stop="handleDocumentBusyClick">
            <svg class="progress-ring-small" viewBox="0 0 36 36" style="width:36px;height:36px;">
              <circle class="ring-bg" cx="18" cy="18" r="16" stroke="rgba(255,255,255,0.3)" stroke-width="3" fill="none"></circle>
              <circle class="ring-fg" cx="18" cy="18" r="16" stroke="#fff" stroke-width="3" fill="none" :stroke-dasharray="`${docTransferProgress}, 100`" transform="rotate(-90 18 18)"></circle>
            </svg>
            <div class="doc-cancel-icon">✕</div>
          </div>
          <div v-else class="doc-icon" :class="docIconClass">
            <svg v-if="docExt === 'pdf'" viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M20 2H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-8.5 7.5c0 .83-.67 1.5-1.5 1.5H9v2H7.5V7H10c.83 0 1.5.67 1.5 1.5v1zm5 2c0 .83-.67 1.5-1.5 1.5h-2.5V7H15c.83 0 1.5.67 1.5 1.5v3zm4-3H19v1h1.5V11H19v2h-1.5V7h3v1.5zM9 9.5h1v-1H9v1zM4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm10 5.5h1v-3h-1v3z"/></svg>
            <svg v-else-if="docExt === 'zip' || docExt === 'rar'" viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-6 10h-4v-1h4v1zm0-2h-4v-1h4v1zm0-2h-4V9h4v3z"/></svg>
            <svg v-else viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>
            <span v-if="docExtensionLabel" class="doc-extension-badge">{{ docExtensionLabel }}</span>
          </div>
          <div class="doc-info">
            <div class="doc-name">{{ docDisplayName }}</div>
            <div class="doc-size">{{ docStatusText }}</div>
          </div>
          <div v-if="!isDocumentBusy && !isDocumentCached" class="doc-download-icon">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
          </div>
          <button
            v-if="showDocumentShare"
            type="button"
            class="doc-share-btn"
            title="اشتراک‌گذاری"
            @click.stop="handleDocumentShareClick($event)"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="18" cy="5" r="3"></circle>
              <circle cx="6" cy="12" r="3"></circle>
              <circle cx="18" cy="19" r="3"></circle>
              <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
              <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
            </svg>
          </button>
        </div>
      </template>

      <div class="msg-meta">
        <span class="msg-time">
          {{ formattedTime }}
          <span v-if="msg.updated_at" class="edited-label">(ویرایش شده)</span>
        </span>
        <span v-if="isSent" class="msg-status">
          <!-- Sending -->
          <div v-if="isSending" class="sending-status-wrapper">
             <span v-if="!props.isAlbum" class="cancel-text-btn" @click.stop="$emit('cancel-send', msg)" title="لغو ارسال">✕</span>
             <svg viewBox="0 0 24 24" class="icon-clock" width="16" height="16" style="color: #aaa;">
                 <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10 10-4.5 10-10S17.5 2 12 2zm4.2 14.2L11 13V7h1.5v5.2l4.5 2.7-.8 1.3z" fill="currentColor"/>
             </svg>
          </div>
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
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useAudioStore } from '../../stores/audio'
import type { Message } from '../../types/chat'
import ChatAlbumLayout from './ChatAlbumLayout.vue'
import { observeVisibility } from '../../utils/sharedVisibilityObserver'
import {
  handleFileClick as cachedFileClick,
  shareFile as cachedShareFile,
  canShareFiles,
  useChatFileHandler,
  prewarmFileCache,
  isFileCached,
  useFileCacheRegistry,
} from '../../composables/chat/useChatFileHandler'

const { downloadingFiles: cachedDownloadingFiles } = useChatFileHandler()
// Subscribe to the reactive cached-file registry so `isDocumentCached`
// re-evaluates whenever a file id is added to or removed from the cache.
const cachedFileRegistry = useFileCacheRegistry()
const supportsFileShare = canShareFiles()

const messageTimeFormatter = new Intl.DateTimeFormat('fa-IR', {
  hour: '2-digit',
  minute: '2-digit'
})

function parseMessageContent(content: string): Record<string, any> | null {
  if (!content) return null
  try {
    const parsed = JSON.parse(content)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

const formatBytes = (bytes: number, decimals = 2) => {
  if (!+bytes) return "0 Bytes"
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

const props = defineProps<{
  msg: any
  currentUserId: number | null
  selectedUserName: string
  selectedMessages: number[]
  imageCache: Record<string, string>
  isSelectionMode: boolean
  searchQuery?: string
  onLoad?: () => void
  isAlbum?: boolean
  albumItems?: any[]
  isAlbumDownloadMode?: boolean
  selectedAlbumDownloadMessageIds?: number[]
}>()

const emit = defineEmits<{
  (e: 'swipe-reply', msg: any): void
  (e: 'select', msg: any): void
  (e: 'context-menu', event: TouchEvent | MouseEvent, msg: any): void
  (e: 'click-message', event: Event, msg: any): void
  (e: 'scroll-to', msgId: number): void
  (e: 'media-click', msg: any): void
  (e: 'location-click', msg: any): void
  (e: 'download', msg: any): void
  (e: 'cancel-send', msg: any): void
  (e: 'cancel-download', msg: any): void
  (e: 'reply-album-item', msg: any): void
  (e: 'forward-album-item', msg: any): void
  (e: 'delete-album-item', msg: any): void
  (e: 'toggle-album-download-item', msg: any): void
}>()

const audioStore = useAudioStore()

// --- Computed State ---
const isSent = computed(() => props.msg.sender_id === props.currentUserId)
const isSending = computed(() => props.msg.id < 0 || props.msg.is_sending)
const isError = computed(() => props.msg.is_error)
const isSelected = computed(() => props.selectedMessages.includes(props.msg.id))
const parsedContent = computed(() => parseMessageContent(props.msg.content))
const mediaFileId = computed(() => getFileId(props.msg.content, parsedContent.value))

const cachedUrl = computed(() => props.imageCache[mediaFileId.value] || '')
const isCached = computed(() => Boolean(cachedUrl.value))
const thumbnail = computed(() => getImageThumbnail(props.msg.content, parsedContent.value))
const formattedTime = computed(() => formatTime(props.msg.created_at))
const SINGLE_MEDIA_MAX_WIDTH = 320
const SINGLE_MEDIA_MAX_HEIGHT = 420

function getBoundedSingleMediaBox(width: number, height: number) {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return null
  }

  const scale = Math.min(
    SINGLE_MEDIA_MAX_WIDTH / width,
    SINGLE_MEDIA_MAX_HEIGHT / height,
    1,
  )

  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  }
}

const audioUrl = computed(() => {
  if (cachedUrl.value) return cachedUrl.value
  if (props.msg.local_blob_url) return props.msg.local_blob_url

  if (props.msg.message_type !== 'voice' || !mediaFileId.value) {
    return ''
  }

  const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
  const token = localStorage.getItem('auth_token') || ''
  return token ? `${baseUrl}/api/chat/files/${mediaFileId.value}?token=${token}` : ''
})

const mediaStyle = computed(() => {
  const style: any = {
    cursor: 'pointer',
    position: 'relative',
    maxWidth: '100%',
    backgroundSize: 'contain',
    backgroundPosition: 'center',
    backgroundRepeat: 'no-repeat',
    backgroundColor: 'rgba(8, 14, 20, 0.92)',
  }
  if (thumbnail.value) {
    style.backgroundImage = `url(${thumbnail.value})`
  }
  if (props.msg.message_type === 'image' || props.msg.message_type === 'video') {
    if (isSending.value) {
      style.aspectRatio = '1'
      style.minHeight = '200px'
      return style
    }

    const content = parsedContent.value
    if (content?.width && content?.height) {
      const boundedBox = getBoundedSingleMediaBox(Number(content.width), Number(content.height))
      if (boundedBox) {
        style.width = `${boundedBox.width}px`
        style.aspectRatio = `${content.width} / ${content.height}`
      } else {
        style.minHeight = '200px'
      }
    } else {
      style.minHeight = '200px'
    }
  }
  return style
})

// Location parsing
const locationData = computed(() => {
  return props.msg.message_type === 'location' ? parsedContent.value : null
})

const mapSnapshotUrl = computed(() => {
  if (locationData.value && locationData.value.snapshot_id) {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
    const token = localStorage.getItem('auth_token') || ''
    return `${baseUrl}/api/chat/files/${locationData.value.snapshot_id}?token=${token}`
  }
  return null
})

// Document computed properties
const docParsed = computed(() => {
  return props.msg.message_type === 'document' ? parsedContent.value : null
})
const docFileName = computed(() => docParsed.value?.file_name || 'فایل')
const docDisplayName = computed(() => docFileName.value || 'فایل')
const docFileSize = computed(() => {
  const size = docParsed.value?.size
  return size ? formatBytes(size) : ''
})
const docMimeType = computed(() => {
  return typeof docParsed.value?.mime_type === 'string' ? docParsed.value.mime_type : ''
})
const docExt = computed(() => {
  const name = docFileName.value
  const parts = name.split('.')
  return parts.length > 1 ? parts.pop()?.toLowerCase() || '' : ''
})
const docExtensionLabel = computed(() => {
  if (docExt.value) return docExt.value.slice(0, 4).toUpperCase()
  const mimeSubtype = docMimeType.value.split('/')[1] || ''
  return mimeSubtype ? mimeSubtype.slice(0, 4).toUpperCase() : ''
})
const docIconClass = computed(() => {
  const ext = docExt.value
  if (ext === 'pdf') return 'doc-pdf'
  if (ext === 'zip' || ext === 'rar' || ext === '7z') return 'doc-archive'
  if (ext === 'xls' || ext === 'xlsx' || ext === 'csv') return 'doc-excel'
  if (ext === 'doc' || ext === 'docx') return 'doc-word'
  return 'doc-generic'
})
const docFileId = computed(() => {
  const value = docParsed.value?.file_id
  return typeof value === 'string' && value ? value : null
})
const docFileUrl = computed(() => {
  const fileId = docFileId.value
  if (!fileId) return ''
  const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
  const token = localStorage.getItem('auth_token') || ''
  return `${baseUrl}/api/chat/files/${fileId}?token=${token}`
})
const isCachedDownloading = computed(() => Boolean(docFileId.value && cachedDownloadingFiles[docFileId.value!]))
const isDocumentBusy = computed(() => Boolean(
  props.msg.is_sending || props.msg.is_downloading || isCachedDownloading.value
))
const docTransferProgress = computed(() => {
  if (props.msg.is_sending) return props.msg.upload_progress || 0
  if (props.msg.is_downloading) return props.msg.download_progress || 0
  // Cached fetch path doesn't expose granular progress; show indeterminate-ish 60%.
  if (isCachedDownloading.value) return 60
  return 0
})
const showDocumentShare = computed(() => supportsFileShare && !isDocumentBusy.value && !props.msg.is_sending)
const isDocumentCached = computed(() => {
  const id = docFileId.value
  if (!id) return false
  // Touch the reactive registry so this computed re-runs on cache changes.
  void cachedFileRegistry[id]
  return isFileCached(id)
})

function handleDocumentBusyClick() {
  if (props.msg.is_sending) {
    emit('cancel-send', props.msg)
    return
  }

  if (props.msg.is_downloading) {
    emit('cancel-download', props.msg)
  }
  // Cached fetches are short-lived and intentionally non-cancellable.
}

async function handleDocumentOpenClick() {
  if (isDocumentBusy.value) return
  const fileId = docFileId.value
  const url = docFileUrl.value
  if (!fileId || !url) {
    // Fallback to legacy emit-based download flow when file_id is missing.
    emit('download', props.msg)
    return
  }
  try {
    await cachedFileClick(fileId, url, docFileName.value)
  } catch {
    // Fallback to legacy download path on any failure (network, quota, etc.).
    emit('download', props.msg)
  }
}

async function handleDocumentShareClick(event: Event) {
  event.stopPropagation()
  const fileId = docFileId.value
  if (!fileId) return
  // Share button MUST only ever open the OS share sheet, never trigger a
  // device download. If the platform's share API rejects (e.g. Samsung
  // Chrome PWA NotAllowedError on xlsx/heic) we surface a brief toast so
  // the user gets feedback instead of a silent no-op.
  const shared = await cachedShareFile(fileId, docFileName.value, docMimeType.value, docFileUrl.value)
  if (shared) return
  showShareUnavailableToast()
}

function showShareUnavailableToast() {
  // Lightweight inline toast — avoids pulling in a global toast library.
  const existing = document.getElementById('chat-file-share-toast')
  if (existing) { try { existing.remove() } catch { /* noop */ } }
  const toast = document.createElement('div')
  toast.id = 'chat-file-share-toast'
  toast.textContent = 'اشتراک‌گذاری این فایل در این مرورگر پشتیبانی نمی‌شود'
  toast.style.cssText = [
    'position:fixed', 'left:50%', 'bottom:90px', 'transform:translateX(-50%)',
    'background:rgba(40,40,40,0.94)', 'color:#fff', 'padding:10px 16px',
    'border-radius:10px', 'font-size:13px', 'z-index:2147483600',
    'max-width:88vw', 'text-align:center', 'box-shadow:0 4px 18px rgba(0,0,0,0.25)',
    'direction:rtl', 'font-family:inherit', 'pointer-events:none',
    'transition:opacity .25s ease', 'opacity:1',
  ].join(';')
  document.body.appendChild(toast)
  setTimeout(() => { toast.style.opacity = '0' }, 2200)
  setTimeout(() => { try { toast.remove() } catch { /* noop */ } }, 2600)
}

const docStatusText = computed(() => {
  if (props.msg.is_sending) {
    return `${formatBytes(props.msg.upload_loaded || 0)} / ${formatBytes(props.msg.upload_total || docParsed.value?.size || 0)}`
  }
  if (props.msg.is_downloading) {
    const progress = props.msg.download_progress || 0
    return `در حال دانلود... ${progress}%`
  }

  const parts = [] as string[]
  if (docFileSize.value) parts.push(docFileSize.value)
  if (docMimeType.value && !docExt.value) parts.push(docMimeType.value)
  return parts.join(' • ') || 'فایل'
})

// Voice State
const messageBubbleRef = ref<HTMLElement | null>(null)
const waveformRef = ref<HTMLElement | null>(null)
let voiceAudioElement: HTMLAudioElement | null = null
let unobserveVisibility: (() => void) | null = null
const hasTriggeredDeferredLoad = ref(false)
const isPlaying = ref(false)
const isVoiceLoading = ref(false)
const isVoiceErrored = ref(false)
const voiceDuration = ref(0)
const voiceCurrentTime = ref(0)

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function buildVoiceWaveBars(seedSource: string, count = 40) {
  let seed = 0
  for (let index = 0; index < seedSource.length; index += 1) {
    seed = ((seed * 31) + seedSource.charCodeAt(index)) >>> 0
  }
  if (!seed) seed = 0x9e3779b9

  const bars: number[] = []
  let previous = 0.45

  for (let index = 0; index < count; index += 1) {
    seed = (seed * 1664525 + 1013904223) >>> 0
    const random = seed / 0xffffffff
    const envelope = 0.42 + Math.sin((index / Math.max(1, count - 1)) * Math.PI) * 0.26
    const jitter = (random - 0.5) * 0.35
    const next = clampNumber((previous * 0.42) + ((envelope + jitter) * 0.58), 0.15, 1)
    bars.push(Math.round((0.22 + next * 0.78) * 100))
    previous = next
  }

  return bars
}

function normalizeComparableUrl(url: string) {
  try {
    return new URL(url, window.location.href).href
  } catch {
    return url
  }
}

function teardownVoiceAudio() {
  if (!voiceAudioElement) return

  voiceAudioElement.pause()
  voiceAudioElement.removeEventListener('loadedmetadata', handleVoiceLoadedMetadata)
  voiceAudioElement.removeEventListener('timeupdate', handleVoiceTimeUpdate)
  voiceAudioElement.removeEventListener('waiting', handleVoiceWaiting)
  voiceAudioElement.removeEventListener('canplay', handleVoiceCanPlay)
  voiceAudioElement.removeEventListener('play', handleVoicePlay)
  voiceAudioElement.removeEventListener('pause', handleVoicePause)
  voiceAudioElement.removeEventListener('ended', handleVoiceEnded)
  voiceAudioElement.removeEventListener('error', handleVoiceError)
  voiceAudioElement.src = ''
  voiceAudioElement.load()
  voiceAudioElement = null
}

function handleVoiceLoadedMetadata() {
  if (!voiceAudioElement) return
  if (Number.isFinite(voiceAudioElement.duration) && voiceAudioElement.duration > 0) {
    voiceDuration.value = voiceAudioElement.duration
  }
  isVoiceLoading.value = false
  isVoiceErrored.value = false
}

function handleVoiceTimeUpdate() {
  if (!voiceAudioElement) return
  voiceCurrentTime.value = voiceAudioElement.currentTime || 0
  if (Number.isFinite(voiceAudioElement.duration) && voiceAudioElement.duration > 0) {
    voiceDuration.value = voiceAudioElement.duration
  }
}

function handleVoiceWaiting() {
  if (isPlaying.value) {
    isVoiceLoading.value = true
  }
}

function handleVoiceCanPlay() {
  isVoiceLoading.value = false
}

function handleVoicePlay() {
  isPlaying.value = true
  isVoiceLoading.value = false
  isVoiceErrored.value = false
}

function handleVoicePause() {
  isPlaying.value = false
  isVoiceLoading.value = false
}

function handleVoiceEnded() {
  isPlaying.value = false
  isVoiceLoading.value = false
  voiceCurrentTime.value = 0
  if (voiceAudioElement) {
    voiceAudioElement.currentTime = 0
  }
  if (audioStore.currentPlayingId === props.msg.id) {
    audioStore.setCurrentPlaying(null)
  }
}

function handleVoiceError() {
  isPlaying.value = false
  isVoiceLoading.value = false
  isVoiceErrored.value = true
  if (audioStore.currentPlayingId === props.msg.id) {
    audioStore.setCurrentPlaying(null)
  }
}

function ensureVoiceAudio() {
  if (props.msg.message_type !== 'voice' || !audioUrl.value) {
    return null
  }

  const nextUrl = normalizeComparableUrl(audioUrl.value)
  if (voiceAudioElement && normalizeComparableUrl(voiceAudioElement.src) === nextUrl) {
    return voiceAudioElement
  }

  teardownVoiceAudio()

  const audio = new Audio(audioUrl.value)
  audio.preload = 'metadata'
  audio.addEventListener('loadedmetadata', handleVoiceLoadedMetadata)
  audio.addEventListener('timeupdate', handleVoiceTimeUpdate)
  audio.addEventListener('waiting', handleVoiceWaiting)
  audio.addEventListener('canplay', handleVoiceCanPlay)
  audio.addEventListener('play', handleVoicePlay)
  audio.addEventListener('pause', handleVoicePause)
  audio.addEventListener('ended', handleVoiceEnded)
  audio.addEventListener('error', handleVoiceError)
  voiceAudioElement = audio
  return audio
}

const shouldDeferMediaHydration = computed(() => {
  return Boolean(props.onLoad) && (
    props.isAlbum ||
    props.msg.message_type === 'image' ||
    props.msg.message_type === 'video'
  )
})

function cleanupVisibilityObserver() {
  if (unobserveVisibility) {
    unobserveVisibility()
    unobserveVisibility = null
  }
}

function triggerDeferredLoad() {
  if (hasTriggeredDeferredLoad.value || !props.onLoad) return
  hasTriggeredDeferredLoad.value = true
  props.onLoad()
  cleanupVisibilityObserver()
}

function setupDeferredMediaHydration() {
  if (!shouldDeferMediaHydration.value) return

  const target = messageBubbleRef.value
  if (!target) {
    triggerDeferredLoad()
    return
  }

  // Shared singleton observer (see utils/sharedVisibilityObserver.ts).
  // Replaces the previous per-component IntersectionObserver so a chat
  // with many media messages only maintains one intersection pipeline
  // instead of N.
  unobserveVisibility = observeVisibility(target, () => {
    triggerDeferredLoad()
  })
}

onMounted(() => {
  setupDeferredMediaHydration()

  if (props.msg.message_type === 'voice' && parsedContent.value?.durationMs) {
    voiceDuration.value = parsedContent.value.durationMs / 1000
  }

  // Pre-warm the synchronous file cache for document messages so the user's
  // first tap can call navigator.share() inside the click handler without
  // first awaiting an IndexedDB read (which would consume the transient
  // user-activation token on Android Chrome HTTPS).
  if (props.msg.message_type === 'document' && docFileId.value) {
    prewarmFileCache(docFileId.value)
  }
})

watch(audioUrl, (newUrl, oldUrl) => {
  if (props.msg.message_type !== 'voice' || newUrl === oldUrl) return

  teardownVoiceAudio()
  isPlaying.value = false
  isVoiceLoading.value = false
  isVoiceErrored.value = false
  voiceCurrentTime.value = 0
})

onUnmounted(() => {
  cleanupVisibilityObserver()
  teardownVoiceAudio()
})

const voiceWaveBars = computed(() => {
  if (props.msg.message_type !== 'voice') return []
  return buildVoiceWaveBars(`${mediaFileId.value}:${Math.round(voiceDuration.value || 0)}:${props.msg.id}`)
})

const voiceProgress = computed(() => {
  if (!voiceDuration.value) return 0
  return clampNumber(voiceCurrentTime.value / voiceDuration.value, 0, 1)
})

const playedVoiceBarCount = computed(() => {
  return Math.round(voiceWaveBars.value.length * voiceProgress.value)
})

const formattedVoiceTime = computed(() => {
  const hasPlaybackPosition = voiceCurrentTime.value > 0 && voiceCurrentTime.value < (voiceDuration.value || Infinity)
  const time = hasPlaybackPosition ? voiceCurrentTime.value : (voiceDuration.value || 0)
  const mins = Math.floor(time / 60)
  const secs = Math.floor(time % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
})

// Stop playing if global state changes to another message
watch(() => audioStore.currentPlayingId, (newId) => {
  if (newId !== props.msg.id && isPlaying.value && voiceAudioElement) {
    voiceAudioElement.pause()
  }
})

const toggleVoice = async () => {
  if (isPlaying.value) {
    voiceAudioElement?.pause()
    audioStore.setCurrentPlaying(null)
  } else {
    const audio = ensureVoiceAudio()
    if (!audio) return

    if (voiceDuration.value > 0 && voiceCurrentTime.value >= voiceDuration.value - 0.05) {
      audio.currentTime = 0
      voiceCurrentTime.value = 0
    }

    isVoiceLoading.value = true
    isVoiceErrored.value = false
    audioStore.setCurrentPlaying(props.msg.id)
    try {
      await audio.play()
    } catch (error) {
      isVoiceLoading.value = false
      isVoiceErrored.value = true
      isPlaying.value = false
      audioStore.setCurrentPlaying(null)
      console.warn('Voice playback failed:', error)
    }
  }
}

function handleVoiceWaveformClick(event: MouseEvent) {
  if (!waveformRef.value || !audioUrl.value) {
    void toggleVoice()
    return
  }

  const rect = waveformRef.value.getBoundingClientRect()
  if (!rect.width) return

  const nextRatio = clampNumber((event.clientX - rect.left) / rect.width, 0, 1)
  const audio = ensureVoiceAudio()
  if (!audio || !voiceDuration.value) {
    void toggleVoice()
    return
  }

  const nextTime = nextRatio * voiceDuration.value
  if (audio.readyState >= 1) {
    audio.currentTime = nextTime
  } else {
    audio.addEventListener('loadedmetadata', () => {
      if (voiceAudioElement) {
        voiceAudioElement.currentTime = nextTime
      }
    }, { once: true })
  }
  voiceCurrentTime.value = nextTime

  if (!isPlaying.value) {
    void toggleVoice()
  }
}

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

const albumLayoutItems = computed(() => {
  return (props.albumItems || []).map((message: any) => {
    const parsedItemContent = parseMessageContent(message.content)
    const fileId = getFileId(message.content, parsedItemContent)
    const cachedMediaUrl = fileId ? props.imageCache[fileId] : ''
    const resolvedMediaUrl = message.local_blob_url || cachedMediaUrl || ''
    const previewUrl = parsedItemContent?.thumbnail || ''

    return {
      msg: message,
      url: resolvedMediaUrl || previewUrl,
      previewUrl,
      hasResolvedMedia: Boolean(resolvedMediaUrl),
      type: message.message_type,
      width: parsedItemContent?.width,
      height: parsedItemContent?.height
    }
  })
})

// --- Touch & Swipe State ---
const SWIPE_AXIS_LOCK_DISTANCE = 14
const SWIPE_ACTIVATION_DISTANCE = 12
const SWIPE_DIRECTION_BIAS = 1.35
const SWIPE_TRIGGER_DISTANCE = 72
const SWIPE_MAX_TRANSLATE = 72
const SWIPE_RESISTANCE = 0.52
const touchStartX = ref(0)
const touchStartY = ref(0)
const touchCurrentX = ref(0)
const touchCurrentY = ref(0)
const isTouchTracking = ref(false)
const swipeAxis = ref<'horizontal' | 'vertical' | null>(null)
const swipeOffsetX = ref(0)
const longPressTimer = ref<number | null>(null)
const contextTouchStart = ref<{ x: number; y: number } | null>(null)
const suppressContextClickUntil = ref(0)

const isSwiping = computed(() => Math.abs(swipeOffsetX.value) > 0)
const swipeDistance = computed(() => Math.abs(swipeOffsetX.value))
const swipeVisualProgress = computed(() => Math.min(swipeDistance.value / SWIPE_TRIGGER_DISTANCE, 1))
const isSwipeReplyArmed = computed(() => swipeAxis.value === 'horizontal' && swipeDistance.value >= SWIPE_TRIGGER_DISTANCE)

const INTERACTIVE_CONTEXT_TARGET_SELECTOR = [
  '[data-context-ignore]',
  '.msg-media-link',
  '.msg-location',
  '.msg-document',
  '.reply-context',
  '.voice-play-btn',
  '.voice-waveform',
  '.download-btn',
  '.cancel-text-btn',
  '.cancelable-overlay',
  '.msg-voice-uploading',
  '.doc-icon',
  '.doc-download-icon',
  '.album-layout',
  '.album-item',
  'button',
  'a',
  'input',
  'textarea',
  'select',
  'label',
  'video',
  'img'
].join(', ')

const SWIPE_IGNORE_TARGET_SELECTOR = [
  '[data-swipe-ignore]',
  '[data-context-ignore]',
  '.reply-context',
  '.cancel-text-btn',
  '.cancelable-overlay',
  '.msg-voice-uploading',
  'input',
  'textarea',
  'select',
  'label',
  'a'
].join(', ')

function clearLongPressTimer() {
  if (longPressTimer.value) {
    clearTimeout(longPressTimer.value)
    longPressTimer.value = null
  }
}

function shouldIgnoreContextMenuTarget(target: EventTarget | null) {
  const element = target instanceof Element ? target : null
  if (!element) return false
  return Boolean(element.closest(INTERACTIVE_CONTEXT_TARGET_SELECTOR))
}

function shouldIgnoreSwipeTarget(target: EventTarget | null) {
  const element = target instanceof Element ? target : null
  if (!element) return false
  return Boolean(element.closest(SWIPE_IGNORE_TARGET_SELECTOR))
}

const handleWrapperClick = (e: MouseEvent) => {
  if (Date.now() < suppressContextClickUntil.value) {
    return
  }

  if (props.isSelectionMode) {
    emit('select', props.msg)
    return
  }

  if (shouldIgnoreContextMenuTarget(e.target)) {
    return
  }

  emit('click-message', e, props.msg)
}

const handleWrapperTouchStart = (e: TouchEvent) => {
  if (props.isSelectionMode || shouldIgnoreContextMenuTarget(e.target)) return

  const touch = e.touches[0]
  if (!touch) return

  contextTouchStart.value = { x: touch.clientX, y: touch.clientY }
  clearLongPressTimer()

  longPressTimer.value = window.setTimeout(() => {
    suppressContextClickUntil.value = Date.now() + 650
    emit('context-menu', new MouseEvent('contextmenu', {
      clientX: touch.clientX,
      clientY: touch.clientY,
    }), props.msg)
    clearLongPressTimer()
  }, 420)
}

const handleWrapperTouchMove = (e: TouchEvent) => {
  const startPoint = contextTouchStart.value
  const touch = e.touches[0]
  if (!startPoint || !touch) return

  if (Math.abs(touch.clientX - startPoint.x) > 8 || Math.abs(touch.clientY - startPoint.y) > 8) {
    clearLongPressTimer()
  }
}

const handleWrapperTouchEnd = () => {
  contextTouchStart.value = null
  clearLongPressTimer()
}

const handleWrapperTouchCancel = () => {
  contextTouchStart.value = null
  clearLongPressTimer()
}

const handleWrapperContextMenu = (e: MouseEvent) => {
  if (shouldIgnoreContextMenuTarget(e.target)) {
    return
  }

  emit('context-menu', e, props.msg)
}

function resetSwipeState() {
  isTouchTracking.value = false
  swipeAxis.value = null
  touchStartX.value = 0
  touchStartY.value = 0
  touchCurrentX.value = 0
  touchCurrentY.value = 0
  swipeOffsetX.value = 0
}

function getSwipeOffset(rawDeltaX: number) {
  if (isSent.value) {
    if (rawDeltaX >= 0) return 0
    return -Math.min(SWIPE_MAX_TRANSLATE, Math.abs(rawDeltaX) * SWIPE_RESISTANCE)
  }

  if (rawDeltaX <= 0) return 0
  return Math.min(SWIPE_MAX_TRANSLATE, rawDeltaX * SWIPE_RESISTANCE)
}

const handleTouchStart = (e: TouchEvent) => {
  if (props.isSelectionMode || shouldIgnoreSwipeTarget(e.target) || e.touches.length !== 1) return

  const touch = e.touches[0]
  if (!touch) return

  touchStartX.value = touch.clientX
  touchStartY.value = touch.clientY
  touchCurrentX.value = touch.clientX
  touchCurrentY.value = touch.clientY
  isTouchTracking.value = true
  swipeAxis.value = null
  swipeOffsetX.value = 0
}

const handleTouchMove = (e: TouchEvent) => {
  if (!isTouchTracking.value || e.touches.length !== 1) return

  const touch = e.touches[0]
  if (!touch) return

  touchCurrentX.value = touch.clientX
  touchCurrentY.value = touch.clientY

  const deltaX = touchCurrentX.value - touchStartX.value
  const deltaY = touchCurrentY.value - touchStartY.value
  const absDeltaX = Math.abs(deltaX)
  const absDeltaY = Math.abs(deltaY)

  if (!swipeAxis.value) {
    if (absDeltaX < SWIPE_AXIS_LOCK_DISTANCE && absDeltaY < SWIPE_AXIS_LOCK_DISTANCE) {
      return
    }

    swipeAxis.value = absDeltaX > absDeltaY * SWIPE_DIRECTION_BIAS ? 'horizontal' : 'vertical'
  }

  if (swipeAxis.value !== 'horizontal') {
    swipeOffsetX.value = 0
    return
  }

  clearLongPressTimer()

  const nextOffset = getSwipeOffset(deltaX)
  if (Math.abs(nextOffset) < SWIPE_ACTIVATION_DISTANCE) {
    swipeOffsetX.value = 0
    return
  }

  swipeOffsetX.value = nextOffset
}

const handleTouchEnd = () => {
  if (!isTouchTracking.value) return

  const rawDeltaX = touchCurrentX.value - touchStartX.value
  const isValidSwipe = swipeAxis.value === 'horizontal' && (isSent.value
    ? rawDeltaX <= -SWIPE_TRIGGER_DISTANCE
    : rawDeltaX >= SWIPE_TRIGGER_DISTANCE)

  if (isValidSwipe) {
    emit('swipe-reply', props.msg)
  }

  resetSwipeState()
}

const handleTouchCancel = () => {
  if (!isTouchTracking.value) return
  resetSwipeState()
}

function getSwipeStyle() {
  if (!swipeOffsetX.value && !isTouchTracking.value) {
    return {}
  }

  return {
    transform: `translate3d(${swipeOffsetX.value}px, 0, 0)`,
    transition: isTouchTracking.value && swipeAxis.value === 'horizontal'
      ? 'none'
      : 'transform 0.26s cubic-bezier(0.22, 1, 0.36, 1)'
  }
}

function getIconStyle() {
  if (!isSwiping.value) {
    return { opacity: 0, transform: 'scale(0.7)' }
  }

  const progress = swipeVisualProgress.value
  if (progress <= 0) {
    return { opacity: 0, transform: 'scale(0.7)' }
  }

  return {
      opacity: Math.min(1, 0.2 + progress * 0.8),
      transform: `scale(${0.78 + (0.22 * progress)})`,
      transition: isTouchTracking.value ? 'none' : 'all 0.2s ease'
  }
}

// --- Formatting Helpers ---
function formatTime(dateString: string) {
  if (!dateString) return ''
  return messageTimeFormatter.format(new Date(dateString))
}

function getFileId(content: string, parsedContent?: Record<string, any> | null) {
  if (!content) return ''
  if (typeof parsedContent?.file_id === 'string') {
    return parsedContent.file_id
  }
  return content.split('::')[0] || ''
}

function getImageThumbnail(content: string, parsedContent?: Record<string, any> | null) {
  if (!content) return ''
  if (typeof parsedContent?.thumbnail === 'string') {
    return parsedContent.thumbnail
  }
  const parts = content.split('::')
  const base64Data = parts[1]
  if (base64Data) {
    if (base64Data.startsWith('data:image')) return base64Data
    return `data:image/jpeg;base64,${base64Data}`
  }
  return ''
}
</script>

<style scoped>
.message-wrapper {
  position: relative; display: flex; flex-direction: column; width: 100%;
}
.swipe-reply-icon {
  position: absolute; top: 50%; transform: translateY(-50%); display: flex; align-items: center; justify-content: center;
  width: 40px; height: 40px; border-radius: 50%; background: rgba(255, 255, 255, 0.78); color: #7c8793; z-index: 1;
  opacity: 0; transition: opacity 0.18s ease, transform 0.18s ease;
  box-shadow: 0 10px 24px rgba(39, 59, 74, 0.12);
  backdrop-filter: blur(12px);
  pointer-events: none;
}
.swipe-reply-icon.visible { opacity: 1; }
.swipe-reply-icon.armed {
  color: #3390ec;
  box-shadow: 0 12px 28px rgba(51, 144, 236, 0.22);
}
.reply-icon-wrapper { display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; }
.swipe-reply-icon.sent-side { right: 12px; }
.swipe-reply-icon.received-side { left: 16px; }

.message-bubble {
  max-width: 92%; padding: 8px 12px; border-radius: 12px; position: relative; font-size: 15px; line-height: 1.5;
  white-space: pre-wrap; word-wrap: break-word; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  animation: slideIn 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  will-change: transform;
  -webkit-touch-callout: none; -webkit-user-select: none; user-select: none;
  touch-action: pan-y;
}
@keyframes slideIn {
  from { opacity: 0; transform: translateY(20px) scale(0.95); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.message-bubble.album-bubble {
  padding: 4px 4px 6px;
  width: fit-content;
  max-width: min(92%, 336px);
}
.message-bubble.sent { align-self: flex-start; background: #eeffde; color: #000000; border-radius: 12px 12px 4px 12px; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15); }
.message-bubble.received { align-self: flex-end; background: #FFFFFF; color: #000000; border-radius: 12px 12px 12px 4px; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15); }
.message-bubble p { margin: 0; }

.msg-time { font-size: 11px; color: rgba(0, 0, 0, 0.4); }
.message-bubble.received .msg-time { color: #8E8E93; }
.msg-meta { display: flex; align-items: center; justify-content: flex-end; gap: 4px; margin-top: 4px; }
.message-bubble.album-bubble .msg-meta { padding: 0 4px 0 2px; margin-top: 5px; }
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
.msg-location {
  cursor: pointer;
  padding: 4px;
}
.location-snapshot {
  position: relative;
  width: 250px;
  height: 150px;
  background-size: cover;
  background-position: center;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  display: flex;
  align-items: center;
  justify-content: center;
}
.location-pin {
  filter: drop-shadow(0 2px 4px rgba(0,0,0,0.4));
  transform: translateY(-8px); /* Adjust to make pin point to center */
}
.location-label-overlay {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  background: rgba(0,0,0,0.6);
  color: white;
  text-align: center;
  font-size: 12px;
  padding: 4px 0;
  font-weight: 500;
  backdrop-filter: blur(2px);
}
.location-preview.fallback {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: #fef2f2;
  border-radius: 12px;
  border: 1px solid #fecaca;
}
.location-label {
  font-size: 14px;
  color: #374151;
  font-weight: 500;
}

/* Document Message */
.msg-document {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  cursor: pointer;
  min-width: 200px;
}
.msg-document.is-busy {
  cursor: default;
}
.doc-icon {
  width: 44px;
  height: 44px;
  min-width: 44px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  position: relative;
  overflow: hidden;
}
.doc-icon.doc-pdf { background: linear-gradient(135deg, #e53935, #c62828); }
.doc-icon.doc-archive { background: linear-gradient(135deg, #ff9800, #e65100); }
.doc-icon.doc-excel { background: linear-gradient(135deg, #43a047, #2e7d32); }
.doc-icon.doc-word { background: linear-gradient(135deg, #1e88e5, #1565c0); }
.doc-icon.doc-generic { background: linear-gradient(135deg, #78909c, #546e7a); }
.doc-icon.doc-uploading {
  background: var(--primary-color, #3390ec);
  position: relative;
}
.doc-cancel-icon {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-size: 14px;
  font-weight: bold;
}
.doc-extension-badge {
  position: absolute;
  left: 50%;
  bottom: 4px;
  transform: translateX(-50%);
  max-width: calc(100% - 8px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 9px;
  line-height: 1;
  font-weight: 800;
  letter-spacing: 0.4px;
  padding: 2px 4px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.22);
  backdrop-filter: blur(6px);
}
.doc-info {
  flex: 1;
  min-width: 0;
}
.doc-name {
  font-size: 14px;
  font-weight: 500;
  color: #111827;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.doc-size {
  font-size: 12px;
  color: #8e8e93;
  margin-top: 2px;
}
.doc-download-icon {
  color: #3390ec;
  flex-shrink: 0;
}
.doc-share-btn {
  flex-shrink: 0;
  background: transparent;
  border: none;
  color: #3390ec;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color 0.15s ease;
  margin-inline-start: 4px;
}
.doc-share-btn:hover { background-color: rgba(51, 144, 236, 0.12); }
.doc-share-btn:active { background-color: rgba(51, 144, 236, 0.22); }

.edited-label { font-size: 10px; font-style: italic; opacity: 0.7; margin-right: 4px; }

/* Selection */
.selected-message { position: relative; z-index: 10; }
.selected-message::before {
  content: ''; position: absolute; top: -4px; right: -16px; bottom: -4px; left: -16px;
  background-color: rgba(51, 144, 236, 0.15); pointer-events: none; z-index: -1; border-radius: 6px;
}

/* Base Media Styles */
.msg-video-wrapper { position: relative; width: 100%; height: 100%; }
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

.progress-cancel {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  color: white;
  font-size: 16px;
  cursor: pointer;
  background: rgba(0,0,0,0.5);
  border-radius: 50%;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.voice-cancel-icon {
  background: rgba(0,0,0,0.5);
  border-radius: 50%;
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.sending-status-wrapper {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.cancel-text-btn {
  color: #ff4444;
  cursor: pointer;
  font-size: 11px;
  font-weight: bold;
  padding: 0 2px;
}

.telegram-size-badge {
  position: absolute;
  top: 8px;
  left: 8px;
  background: rgba(0, 0, 0, 0.6);
  color: white;
  padding: 4px 8px;
  border-radius: 12px;
  font-size: 11px;
  backdrop-filter: blur(4px);
  direction: ltr; /* English text formatting */
}
.cancelable-overlay {
  transition: background 0.2s;
}
.cancelable-overlay:hover {
  background: rgba(0,0,0,0.3);
}

.telegram-size-badge {
  position: absolute;
  top: 8px;
  left: 8px;
  background: rgba(0, 0, 0, 0.6);
  color: white;
  padding: 4px 8px;
  border-radius: 12px;
  font-size: 11px;
  backdrop-filter: blur(4px);
  direction: ltr; /* English text formatting */
}
.cancelable-overlay {
  transition: background 0.2s;
}
.cancelable-overlay:hover {
  background: rgba(0,0,0,0.3);
}

.telegram-size-badge {
  position: absolute;
  top: 8px;
  left: 8px;
  background: rgba(0, 0, 0, 0.6);
  color: white;
  padding: 4px 8px;
  border-radius: 12px;
  font-size: 11px;
  backdrop-filter: blur(4px);
  direction: ltr; /* English text formatting */
}
.cancelable-overlay {
  transition: background 0.2s;
}
.cancelable-overlay:hover {
  background: rgba(0,0,0,0.3);
}
</style>



<style scoped>
.msg-voice {
  --voice-accent: #4A90E2;
  --voice-accent-top: #8fc0ff;
  --voice-track-bottom: rgba(15, 23, 42, 0.15);
  --voice-track-top: rgba(255, 255, 255, 0.76);
  display: flex;
  align-items: center;
  gap: 12px;
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.42), transparent 55%),
    linear-gradient(180deg, rgba(255,255,255,0.28), rgba(255,255,255,0.04));
  border: 1px solid rgba(255,255,255,0.28);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.18);
  border-radius: 16px;
  padding: 10px 12px;
  width: 250px;
  direction: ltr; /* Force LTR for audio player */
}
.msg-voice.is-sent {
  --voice-accent: #3390ec;
  --voice-accent-top: #9fd0ff;
  --voice-track-bottom: rgba(51, 144, 236, 0.2);
  --voice-track-top: rgba(255, 255, 255, 0.62);
}
.voice-play-btn {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: none;
  background: linear-gradient(180deg, var(--voice-accent-top), var(--voice-accent));
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  box-shadow: 0 10px 24px rgba(32, 92, 160, 0.22);
  transition: transform 0.18s ease, box-shadow 0.18s ease, filter 0.18s ease;
}
.voice-play-btn:hover {
  transform: translateY(-1px);
}
.voice-play-btn.is-active {
  box-shadow: 0 12px 28px rgba(32, 92, 160, 0.3);
}
.msg-voice.is-loading .voice-play-btn {
  filter: saturate(0.92);
}
.msg-voice.is-error .voice-play-btn {
  background: linear-gradient(180deg, #ff9b9b, #ea5455);
}
.voice-play-btn svg {
  width: 24px;
  height: 24px;
  fill: currentColor;
}

.voice-body {
  display: flex;
  flex-direction: column;
  flex-grow: 1;
  gap: 6px;
  justify-content: center;
  min-width: 0;
}
.voice-waveform {
  width: 100%;
  max-width: 100%;
  min-height: 30px;
  border-radius: 12px;
  cursor: default;
  position: relative;
  padding: 4px 0;
  overflow: hidden;
}
.voice-waveform.is-interactive {
  cursor: pointer;
}

.voice-wave-bars {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  width: 100%;
  max-width: 100%;
  height: 22px;
  min-width: 0;
  overflow: hidden;
}

.voice-wave-bar {
  flex: 1 1 0;
  min-width: 0;
  border-radius: 999px;
  background: linear-gradient(180deg, var(--voice-track-top), var(--voice-track-bottom));
  opacity: 0.96;
  transition: background 0.16s ease, opacity 0.16s ease, transform 0.16s ease;
}

.voice-wave-bar.is-played {
  background: linear-gradient(180deg, var(--voice-accent-top), var(--voice-accent));
  opacity: 1;
}

.msg-voice.is-loading .voice-wave-bar {
  animation: voice-bar-pulse 1.4s ease-in-out infinite;
}

.voice-meta-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.voice-state-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  flex-shrink: 0;
}

.voice-state-dot.is-loading {
  background: var(--voice-accent);
  box-shadow: 0 0 0 0 rgba(74, 144, 226, 0.35);
  animation: voice-dot-pulse 1.3s ease-in-out infinite;
}

.voice-state-dot.is-error {
  background: #ea5455;
  box-shadow: 0 0 0 4px rgba(234, 84, 85, 0.14);
}

.voice-time {
  font-size: 0.75rem;
  color: #666;
  line-height: 1;
}

@keyframes voice-bar-pulse {
  0%, 100% {
    opacity: 0.76;
    transform: scaleY(0.92);
  }
  50% {
    opacity: 1;
    transform: scaleY(1.04);
  }
}

@keyframes voice-dot-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(74, 144, 226, 0.35);
  }
  70% {
    box-shadow: 0 0 0 6px rgba(74, 144, 226, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(74, 144, 226, 0);
  }
}
</style>
