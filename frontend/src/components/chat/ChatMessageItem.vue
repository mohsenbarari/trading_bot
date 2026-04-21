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
        'selected-message': isSelected,
        'album-bubble': props.isAlbum
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
          @media-click="$emit('media-click', $event)"
          @download="$emit('download', $event)"
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
        <div class="msg-voice">
          <button class="voice-play-btn" @click.stop="toggleVoice">
            <svg v-if="!isPlaying" viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
              <path d="M8 5v14l11-7z"/>
            </svg>
            <svg v-else viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
              <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
            </svg>
          </button>
          
          <div class="voice-body" style="min-width: 0; flex: 1;">
            <div class="voice-waveform" ref="waveformRef" @click.prevent style="width: 100%; height: 24px; position: relative; overflow: hidden; display: block;">
              <!-- WaveSurfer will inject canvas here -->
            </div>
            <div class="voice-time">{{ formattedVoiceTime }}</div>
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
        <div class="msg-document" @click.stop="$emit('download', msg)">
          <div v-if="msg.is_sending" class="doc-icon doc-uploading" @click.stop="$emit('cancel-send', msg)">
            <svg class="progress-ring-small" viewBox="0 0 36 36" style="width:36px;height:36px;">
              <circle class="ring-bg" cx="18" cy="18" r="16" stroke="rgba(255,255,255,0.3)" stroke-width="3" fill="none"></circle>
              <circle class="ring-fg" cx="18" cy="18" r="16" stroke="#fff" stroke-width="3" fill="none" :stroke-dasharray="`${msg.upload_progress || 0}, 100`" transform="rotate(-90 18 18)"></circle>
            </svg>
            <div class="doc-cancel-icon">✕</div>
          </div>
          <div v-else class="doc-icon" :class="docIconClass">
            <svg v-if="docExt === 'pdf'" viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M20 2H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-8.5 7.5c0 .83-.67 1.5-1.5 1.5H9v2H7.5V7H10c.83 0 1.5.67 1.5 1.5v1zm5 2c0 .83-.67 1.5-1.5 1.5h-2.5V7H15c.83 0 1.5.67 1.5 1.5v3zm4-3H19v1h1.5V11H19v2h-1.5V7h3v1.5zM9 9.5h1v-1H9v1zM4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm10 5.5h1v-3h-1v3z"/></svg>
            <svg v-else-if="docExt === 'zip' || docExt === 'rar'" viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-6 10h-4v-1h4v1zm0-2h-4v-1h4v1zm0-2h-4V9h4v3z"/></svg>
            <svg v-else viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>
          </div>
          <div class="doc-info">
            <div class="doc-name">{{ msg.is_sending && msg.local_blob_url ? 'در حال ارسال...' : docFileName }}</div>
            <div class="doc-size" v-if="msg.is_sending">{{ formatBytes(msg.upload_loaded || 0) }} / {{ formatBytes(msg.upload_total || 0) }}</div>
            <div class="doc-size" v-else>{{ docFileSize }}</div>
          </div>
          <div v-if="!msg.is_sending" class="doc-download-icon">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
          </div>
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
             <span class="cancel-text-btn" @click.stop="$emit('cancel-send', msg)" title="لغو ارسال">✕</span>
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
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useAudioStore } from '../../stores/audio'
import WaveSurfer from 'wavesurfer.js'
import type { Message } from '../../types/chat'
import ChatAlbumLayout from './ChatAlbumLayout.vue'


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
}>()

const audioStore = useAudioStore()

// --- Computed State ---
const isSent = computed(() => props.msg.sender_id === props.currentUserId)
const isSending = computed(() => props.msg.id < 0 || props.msg.is_sending)
const isError = computed(() => props.msg.is_error)
const isSelected = computed(() => props.selectedMessages.includes(props.msg.id))

const isCached = computed(() => !!props.imageCache[getFileId(props.msg.content)])
const cachedUrl = computed(() => props.imageCache[getFileId(props.msg.content)])
const thumbnail = computed(() => getImageThumbnail(props.msg.content))
const formattedTime = computed(() => formatTime(props.msg.created_at))
const audioUrl = computed(() => cachedUrl.value || props.msg.local_blob_url)

const mediaStyle = computed(() => {
  const style: any = {
    cursor: 'pointer',
    position: 'relative',
    backgroundSize: 'cover',
  }
  if (thumbnail.value) {
    style.backgroundImage = `url(${thumbnail.value})`
  }
  if (props.msg.message_type === 'image' || props.msg.message_type === 'video') {
    try {
      const content = JSON.parse(props.msg.content)
      if (content.width && content.height) {
        const ratio = content.width / content.height
        const clampedRatio = Math.max(0.6, Math.min(ratio, 2.5))
        style.aspectRatio = `${clampedRatio}`
      } else {
        style.minHeight = '200px'
      }
    } catch {
      style.minHeight = '200px'
    }
  }
  return style
})

// Location parsing
const locationData = computed(() => {
  if (props.msg.message_type === 'location' && props.msg.content) {
    try {
      return JSON.parse(props.msg.content)
    } catch {
      return null
    }
  }
  return null
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
  if (props.msg.message_type === 'document' && props.msg.content) {
    try { return JSON.parse(props.msg.content) } catch { return null }
  }
  return null
})
const docFileName = computed(() => docParsed.value?.file_name || 'فایل')
const docFileSize = computed(() => {
  const size = docParsed.value?.size
  return size ? formatBytes(size) : ''
})
const docExt = computed(() => {
  const name = docFileName.value
  const parts = name.split('.')
  return parts.length > 1 ? parts.pop()?.toLowerCase() || '' : ''
})
const docIconClass = computed(() => {
  const ext = docExt.value
  if (ext === 'pdf') return 'doc-pdf'
  if (ext === 'zip' || ext === 'rar' || ext === '7z') return 'doc-archive'
  if (ext === 'xls' || ext === 'xlsx' || ext === 'csv') return 'doc-excel'
  if (ext === 'doc' || ext === 'docx') return 'doc-word'
  return 'doc-generic'
})

// Voice State
const waveformRef = ref<HTMLElement | null>(null)
let wavesurfer: any = null
const isPlaying = ref(false)
const voiceDuration = ref(0)
const voiceCurrentTime = ref(0)

onMounted(() => {
  if (props.onLoad) props.onLoad()

  if (props.msg.message_type === 'voice' && props.msg.content) {
    try {
      const p = JSON.parse(props.msg.content)
      if (p.durationMs) {
        voiceDuration.value = p.durationMs / 1000
      }
    } catch { }
  }
})

const initWaveSurfer = () => {
  if (!audioUrl.value || props.msg.message_type !== 'voice') return
  nextTick(() => {
    if (wavesurfer) {
      wavesurfer.destroy()
    }
    if (!waveformRef.value) return
    
    wavesurfer = WaveSurfer.create({
      container: waveformRef.value,
      waveColor: isSent.value ? 'rgba(74, 144, 226, 0.4)' : 'rgba(0, 0, 0, 0.15)',
      progressColor: isSent.value ? '#3390ec' : 'var(--primary-color, #4A90E2)',
      cursorWidth: 0,
      barWidth: 2,
      barGap: 1.5,
      barRadius: 2,
      height: 24,
      barAlign: 'bottom',
      normalize: true,
      url: audioUrl.value,
      renderFunction: (channels, ctx) => {
        const { width, height } = ctx.canvas;
        const barWidth = 2;
        const barGap = 2;
        const barCount = Math.floor(width / (barWidth + barGap));
        const channelData = channels[0];
        if (!channelData) {
          ctx.fillStyle = isSent.value ? 'rgba(74, 144, 226, 0.4)' : 'rgba(0,0,0,0.15)';
          ctx.fillRect(0, height - 2, width, 2);
          return;
        }
        const step = Math.floor(channelData.length / barCount);
        const activeIndex = Math.floor(wavesurfer.getCurrentTime() / wavesurfer.getDuration() * barCount || 0);

        ctx.clearRect(0, 0, width, height);

        for (let i = 0; i < barCount; i++) {
          let sum = 0;
          for (let j = 0; j < step; j++) {
            sum += Math.abs(channelData[i * step + j] || 0);
          }
          const avg = sum / step;
          const barHeight = Math.max(2, avg * height * 1.5);
          
          ctx.fillStyle = i <= activeIndex ? (isSent.value ? '#3390ec' : '#4A90E2') : (isSent.value ? 'rgba(74, 144, 226, 0.4)' : 'rgba(0,0,0,0.15)');
          
          // Draw bar from bottom
          const x = i * (barWidth + barGap);
          const y = height - barHeight;
          
          // Rounded rect
          const radius = 2;
          ctx.beginPath();
          ctx.moveTo(x + radius, y);
          ctx.lineTo(x + barWidth - radius, y);
          ctx.quadraticCurveTo(x + barWidth, y, x + barWidth, y + radius);
          ctx.lineTo(x + barWidth, height);
          ctx.lineTo(x, height);
          ctx.lineTo(x, y + radius);
          ctx.quadraticCurveTo(x, y, x + radius, y);
          ctx.closePath();
          ctx.fill();
        }
      }
    })

    wavesurfer.on('ready', () => {
      const d = wavesurfer.getDuration()
      if (d && !isNaN(d) && d !== Infinity) {
        voiceDuration.value = d
      }
    })

    wavesurfer.on('audioprocess', () => {
      voiceCurrentTime.value = wavesurfer.getCurrentTime()
    })
    
    wavesurfer.on('seeking', () => {
      voiceCurrentTime.value = wavesurfer.getCurrentTime()
    })

    wavesurfer.on('finish', () => {
      isPlaying.value = false
      voiceCurrentTime.value = 0
      if (audioStore.currentPlayingId === props.msg.id) {
        audioStore.setCurrentPlaying(null)
      }
    })

    wavesurfer.on('play', () => {
      isPlaying.value = true
    })

    wavesurfer.on('pause', () => {
      isPlaying.value = false
    })
  })
}

watch(audioUrl, initWaveSurfer)

// Make sure to init on mounted if url is already present
onMounted(() => {
  if (audioUrl.value) {
    initWaveSurfer()
  }
})

onUnmounted(() => {
  if (wavesurfer) {
    wavesurfer.destroy()
    wavesurfer = null
  }
})

const formattedVoiceTime = computed(() => {
  const time = isPlaying.value ? voiceCurrentTime.value : (voiceDuration.value || 0)
  const mins = Math.floor(time / 60)
  const secs = Math.floor(time % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
})

// Stop playing if global state changes to another message
watch(() => audioStore.currentPlayingId, (newId) => {
  if (newId !== props.msg.id && isPlaying.value && wavesurfer) {
    wavesurfer.pause()
  }
})

const toggleVoice = () => {
  if (!wavesurfer) return
  if (isPlaying.value) {
    wavesurfer.pause()
    audioStore.setCurrentPlaying(null)
  } else {
    // Set this message as currently playing (will stop others via watch)
    audioStore.setCurrentPlaying(props.msg.id)
    wavesurfer.play()
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
    const parsedContent = (() => {
      try {
        return JSON.parse(message.content)
      } catch {
        return {}
      }
    })()

    return {
      msg: message,
      url: message.local_blob_url || props.imageCache[getFileId(message.content)] || parsedContent.thumbnail,
      type: message.message_type,
      width: parsedContent.width,
      height: parsedContent.height
    }
  })
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
.doc-icon {
  width: 44px;
  height: 44px;
  min-width: 44px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
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
  display: flex;
  align-items: center;
  gap: 12px;
  background: rgba(0,0,0,0.05);
  border-radius: 8px;
  padding: 10px;
  width: 250px;
  direction: ltr; /* Force LTR for audio player */
}
.voice-play-btn {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--primary-color, #4A90E2);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
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
  gap: 8px;
  justify-content: center;
}
.voice-waveform {
  width: 100%;
  height: 24px;
  border-radius: 2px;
  cursor: pointer;
  position: relative;
}
.voice-progress-fill {
  display: none;
}

.voice-time {
  font-size: 0.75rem;
  color: #666;
  line-height: 1;
}
</style>
