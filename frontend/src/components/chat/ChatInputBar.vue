<template>
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
            <span class="reply-banner-author">{{ replyingToMessage.sender_id === currentUserId ? 'شما' : selectedUserName }}</span>
            <span class="reply-banner-text">
                {{ replyingToMessage.message_type === 'text' ? replyingToMessage.content : (replyingToMessage.message_type === 'image' ? '🖼️ تصویر' : (replyingToMessage.message_type === 'video' ? '📹 ویدیو' : (replyingToMessage.message_type === 'location' ? '📍 موقعیت' : '😊 استیکر'))) }}
            </span>
        </div>
        <button class="close-reply" v-ripple @click="$emit('cancel-reply')">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
    </div>

    <!-- Selection Mode Bottom Bar -->
    <div v-if="isSelectionMode" class="selection-bottom-bar">
      <button v-if="canDeleteSelected" class="selection-action-btn delete" v-ripple @click="$emit('delete-selected')">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6"></polyline>
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
        </svg>
        <span>حذف</span>
      </button>
      <button v-if="selectedMessages.length === 1" class="selection-action-btn" v-ripple @click="$emit('reply-selected')">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 14 4 9 9 4"></polyline><path d="M20 20v-7a4 4 0 0 0-4-4H4"></path>
        </svg>
        <span>پاسخ</span>
      </button>
      <button v-if="canCopySelected" class="selection-action-btn" v-ripple @click="$emit('copy-selected')">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
        </svg>
        <span>کپی</span>
      </button>
      <button class="selection-action-btn" v-ripple @click="$emit('forward-selected')">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 14 20 9 15 4"></polyline>
          <path d="M4 20v-7a4 4 0 0 1 4-4h12"></path>
        </svg>
        <span>هدایت</span>
      </button>
    </div>

    <!-- Disabled Banner -->
    <div v-else-if="isDeleted" class="input-container disabled-banner">
      <span class="disabled-text">امکان ارسال پیام به این کاربر وجود ندارد.</span>
    </div>

    <!-- Input Container -->
    <div v-else class="input-container">
      
      <!-- Recording Overlay state -->
      <template v-if="isRecording">
        <!-- Send Button on the right -->
        <button 
          v-ripple
          class="send-btn-inline" 
          @click="stopVoiceRecording" 
        >
          <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="#3390ec" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transform: rotate(45deg); margin-left: -4px;">
            <line x1="22" y1="2" x2="11" y2="13"></line>
            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
          </svg>
        </button>

        <div class="recording-state">
          <div class="mic-active-pulse"></div>
          <span class="recording-time">{{ recordingDisplay }}</span>
          <span class="recording-hint animate-pulse" style="color: #3390ec;">درحال ضبط...</span>
        </div>

        <!-- Cancel Button on the left -->
        <button 
          v-ripple
          class="cancel-voice-btn" 
          @click="cancelVoiceRecording"
        >
          <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
        </button>
      </template>

      <!-- Left side buttons (Only if not recording) -->
      <template v-if="!messageInput.trim() && !isRecording">
        <button 
          v-ripple 
          class="voice-btn"
          @click="startVoiceRecording"
        >
          <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="#8e8e93" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
            <line x1="12" y1="19" x2="12" y2="23"></line>
            <line x1="8" y1="23" x2="16" y2="23"></line>
          </svg>
        </button>

        <button v-ripple class="attach-btn" @click="$emit('toggle-attachment')" :disabled="isUploading">
          <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="#8e8e93" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
          </svg>
        </button>
      </template>

      <!-- Send Button -->
      <button 
        v-else-if="!isRecording"
        v-ripple
        class="send-btn-inline" 
        @click="sendMessage" 
        @mousedown.prevent
        @touchstart.prevent="sendMessage"
        :disabled="isSending"
      >
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="#3390ec" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transform: rotate(45deg); margin-left: -4px;">
          <line x1="22" y1="2" x2="11" y2="13"></line>
          <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
        </svg>
      </button>

      <!-- Text Input (Hide when recording) -->
      <textarea
        v-show="!isRecording"
        ref="messageInputRef"
        v-model="messageInput"
        rows="1"
        placeholder="پیام..."
        @input="adjustTextareaHeight"
        @keydown.enter="handleEnter"
      ></textarea>

      <!-- Emoji/Sticker Toggle -->
      <button v-if="!isRecording" class="emoji-btn" v-ripple @click="showStickerPicker = !showStickerPicker">
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
</template>

<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import { AudioRecorder } from '../../utils/audioRecorder'

const props = defineProps<{
  replyingToMessage: any | null
  currentUserId: number | null
  selectedUserName: string
  isSelectionMode: boolean
  selectedMessages: any[]
  canDeleteSelected: boolean
  canCopySelected: boolean
  isUploading: boolean
  isSending: boolean
  isDeleted?: boolean
}>()

const emit = defineEmits<{
  (e: 'cancel-reply'): void
  (e: 'delete-selected'): void
  (e: 'reply-selected'): void
  (e: 'copy-selected'): void
  (e: 'forward-selected'): void
  (e: 'toggle-attachment'): void
  (e: 'send-text', content: string): void
  (e: 'send-sticker', sticker: string): void
  (e: 'send-voice', blob: Blob, durationMs: number): void
  (e: 'typing'): void
}>()

const messageInput = ref('')
const messageInputRef = ref<HTMLTextAreaElement | null>(null)
const showStickerPicker = ref(false)

const stickerPacks = [
  { id: 1, name: 'Emoji Icons', stickers: ['😊', '😂', '👍', '❤️', '🔥', '🎉', '🌟', '💔', '😎', '🙏'] }
]

const adjustTextareaHeight = () => {
  const el = messageInputRef.value
  if (!el) return
  el.style.height = '1px'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  emit('typing')
}

// Voice Recording State
const isRecording = ref(false)
const recordingTimeMs = ref(0)
const recordingDisplay = computed(() => {
  const totalSeconds = Math.floor(recordingTimeMs.value / 1000)
  const mins = Math.floor(totalSeconds / 60)
  const secs = totalSeconds % 60
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
})

let recorder: AudioRecorder | null = null

const startVoiceRecording = async () => {
  if (!recorder) {
    recorder = new AudioRecorder((ms) => {
      recordingTimeMs.value = ms
    })
  }

  try {
    // navigator.mediaDevices.getUserMedia removed here to prevent open microphone leak
    await recorder.start()
    isRecording.value = true
  } catch (err) {
    console.error('Failed to start mic', err)
    alert('امکان دسترسی به میکروفون وجود ندارد.')
  }
}

const stopVoiceRecording = async () => {
  if (!isRecording.value || !recorder) return
  isRecording.value = false
  const blob = await recorder.stop()
  
  // Only send if more than 0.5 second recorded
  if (blob && recordingTimeMs.value > 500) {
    emit('send-voice', blob, recordingTimeMs.value)
  }
  recordingTimeMs.value = 0
  
  // reset input height just in case
  nextTick(() => {
    if (messageInputRef.value) messageInputRef.value.style.height = 'auto'
  })
}

const cancelVoiceRecording = () => {
  if (!isRecording.value || !recorder) return
  isRecording.value = false
  recorder.cancel()
  recordingTimeMs.value = 0
}

const handleEnter = (e: KeyboardEvent) => {
  if (e.shiftKey) return
  e.preventDefault()
  sendMessage()
}

const sendMessage = () => {
  const content = messageInput.value.trim()
  if (!content) return
  
  emit('send-text', content)
  messageInput.value = ''
  showStickerPicker.value = false
  
  // reset height
  nextTick(() => {
    if (messageInputRef.value) messageInputRef.value.style.height = 'auto'
  })
}

const sendSticker = (sticker: string) => {
  emit('send-sticker', sticker)
  showStickerPicker.value = false
}
</script>

<style scoped>
.recording-state {
  display: flex;
  align-items: center;
  flex: 1;
  padding: 0 12px;
  gap: 12px;
}
.mic-active-pulse {
  width: 10px;
  height: 10px;
  background-color: #E53935;
  border-radius: 50%;
  animation: pulse-red 1.2s infinite;
}
@keyframes pulse-red {
  0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(229, 57, 53, 0.7); }
  70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(229, 57, 53, 0); }
  100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(229, 57, 53, 0); }
}
.recording-time {
  font-family: monospace;
  font-size: 16px;
  font-weight: 500;
  color: #333;
}
.recording-hint {
  font-size: 14px;
  color: #8E8E93;
  margin-right: auto;
  opacity: 0.8;
}
.voice-btn {
  user-select: none;
  touch-action: none; /* important for preventing scroll while swiping */
}
.voice-btn:active svg {
  transform: scale(1.2);
  transition: transform 0.1s;
  stroke: #3390ec;
}

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
  z-index: 60;
}

.input-container {
  width: 100%;
  gap: 8px;
  flex: 1;
  display: flex;
  align-items: flex-end;
  background: #ffffff;
  border: none;
  border-radius: 20px;
  padding: 8px 4px;
  min-height: 44px;
  transition: background 0.2s;
}
.input-container:focus-within { background: #ffffff; }
.input-container textarea {
  flex: 1; padding: 4px 8px; border: none; background: transparent; outline: none;
  font-size: 16px; color: #000000; resize: none; overflow-y: auto;
  min-height: 24px; line-height: 24px; max-height: 200px; font-family: inherit; direction: rtl; text-align: right;
}
.input-container textarea::placeholder { color: #8E8E93; }

.disabled-banner {
  justify-content: center;
  align-items: center;
  background: #f9fafb;
}
.disabled-text {
  color: #8E8E93;
  font-size: 14px;
}

.emoji-btn, .attach-btn, .voice-btn, .cancel-voice-btn {
  background: none; border: none; padding: 0; margin: 0; cursor: pointer; display: flex;
  align-items: center; justify-content: center; flex-shrink: 0; width: 32px; height: 32px;
}
.emoji-btn svg, .attach-btn svg, .voice-btn svg, .cancel-voice-btn svg { width: 28px; height: 28px; }
.emoji-btn { margin-left: 4px; }
.attach-btn, .voice-btn { margin-right: 4px; }
.cancel-voice-btn { margin-left: 4px; }

.send-btn-inline {
  background: none; border: none; padding: 0; margin: 0; cursor: pointer; display: flex;
  align-items: center; justify-content: center; flex-shrink: 0; width: 32px; height: 32px; margin-right: 4px;
}
.send-btn-inline svg { width: 28px; height: 28px; }
.send-btn-inline:disabled { opacity: 0.5; cursor: not-allowed; }

/* Sticker Picker */
.slide-up-enter-active, .slide-up-leave-active { transition: transform 0.3s cubic-bezier(0.2, 0, 0, 1), opacity 0.3s; }
.slide-up-enter-from, .slide-up-leave-to { transform: translateY(100%); opacity: 0; }
.sticker-picker {
  background: #f4f4f5; border-top: 1px solid rgba(0,0,0,0.05); padding: 16px 12px;
  max-height: 250px; overflow-y: auto; position: absolute; bottom: 0; left: 0; right: 0;
  z-index: 50; transform: translateY(0);
}
.sticker-pack { margin-bottom: 12px; }
.pack-name { font-size: 12px; color: var(--text-secondary); margin-bottom: 8px; }
.stickers-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.sticker-item {
  background: var(--bg-color); border: 1px solid var(--border-color); border-radius: 8px;
  padding: 8px; font-size: 20px; cursor: pointer; transition: transform 0.2s;
}
.sticker-item:hover { transform: scale(1.1); }

/* Reply Banner */
.reply-banner {
  position: relative; display: flex; align-items: center; background: #FFFFFF;
  padding: 8px 16px 8px 12px; border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  animation: slideUp 0.15s ease-out; min-height: 46px; gap: 12px;
}
@keyframes slideUp { from { transform: translateY(100%); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
.reply-banner-icon { display: flex; align-items: center; justify-content: center; color: #3390ec; }
.reply-banner-content { flex: 1; display: flex; flex-direction: column; border-right: 2px solid #3390ec; padding-right: 8px; justify-content: center; overflow: hidden; }
.reply-banner-author { font-size: 14px; font-weight: 500; color: #3390ec; line-height: 1.2; margin-bottom: 2px; }
.reply-banner-text { font-size: 13px; color: #8e8e93; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.close-reply { background: none; border: none; color: #8E8E93; cursor: pointer; padding: 4px; border-radius: 50%; display: flex; align-items: center; justify-content: center; transition: background 0.2s; }
.close-reply:hover { background: rgba(0, 0, 0, 0.05); color: #000; }

/* Selection Bar */
.selection-bottom-bar { display: flex; align-items: center; justify-content: space-around; width: 100%; padding: 8px 0; background: white; min-height: 56px; }
.selection-action-btn { display: flex; flex-direction: column; align-items: center; justify-content: center; background: none; border: none; color: #8e8e93; font-size: 11px; font-weight: 500; gap: 4px; padding: 6px 16px; border-radius: 8px; cursor: pointer; transition: opacity 0.2s, background 0.2s; }
.selection-action-btn:hover { background: rgba(0,0,0,0.05); color: #000; }
.selection-action-btn.delete { color: #ef4444; }
.selection-action-btn.delete:hover { background: rgba(239, 68, 68, 0.1); }
.selection-action-btn svg { margin-bottom: 2px; }
</style>
