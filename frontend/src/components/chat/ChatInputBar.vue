<template>
  <div class="input-area" :class="{ 'picker-open': isStickerPickerOpen }">
    <!-- Edit Banner -->
    <div v-if="editingMessage" class="reply-banner edit-banner">
        <div class="reply-banner-icon edit-banner-icon">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 20h9"></path>
            <path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"></path>
          </svg>
        </div>
        <div class="reply-banner-content">
            <span class="reply-banner-author">ویرایش پیام</span>
            <span class="reply-banner-text">{{ editingBannerText }}</span>
        </div>
        <button class="close-reply" v-ripple @click="$emit('cancel-edit')">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
    </div>

    <!-- Reply Banner -->
    <div v-else-if="replyingToMessage" class="reply-banner">
        <div class="reply-banner-icon">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#3390ec" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="9 14 4 9 9 4"></polyline>
            <path d="M20 20v-7a4 4 0 0 0-4-4H4"></path>
          </svg>
        </div>
        <div class="reply-banner-content">
            <span class="reply-banner-author">{{ replyingToMessage.sender_id === currentUserId ? 'شما' : selectedUserName }}</span>
            <span class="reply-banner-text">{{ replyBannerText }}</span>
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
      <template v-if="!messageInput.trim() && !isRecording && !editingMessage">
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

        <button v-ripple class="attach-btn" @click="handleToggleAttachment">
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
        :class="{ 'edit-mode': !!editingMessage }"
        @click="sendMessage" 
        @mousedown.prevent
        @touchstart.prevent="sendMessage"
        :disabled="isSending || !canSubmit"
      >
        <svg v-if="editingMessage" viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="#3390ec" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        <svg v-else viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="#3390ec" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transform: rotate(45deg); margin-left: -4px;">
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
        :placeholder="editingMessage ? 'ویرایش پیام...' : 'پیام...'"
        @input="handleInput"
        @keydown.enter="handleEnter"
        @mousedown="prepareTextareaFocus"
        @touchstart="prepareTextareaFocus"
        @focus="handleTextareaFocus"
        @click="captureSelection"
        @keyup="captureSelection"
        @select="captureSelection"
      ></textarea>

      <!-- Emoji/Sticker Toggle -->
      <button
        v-if="!isRecording"
        class="emoji-btn"
        :class="{ 'is-active': isStickerPickerOpen }"
        v-ripple
        :aria-label="isStickerPickerOpen ? 'بازگشت به کیبورد' : 'باز کردن پنل استیکر'"
        @mousedown.prevent="prepareStickerToggle"
        @touchstart="prepareStickerToggle"
        @click="toggleStickerPicker"
      >
        <svg
          v-if="isStickerPickerOpen"
          viewBox="0 0 24 24"
          width="26"
          height="26"
          fill="none"
          stroke="#3390ec"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <rect x="2" y="5" width="20" height="14" rx="2"></rect>
          <path d="M6 9h.01"></path>
          <path d="M10 9h.01"></path>
          <path d="M14 9h.01"></path>
          <path d="M18 9h.01"></path>
          <path d="M6 13h8"></path>
          <path d="M16 13h2"></path>
        </svg>
        <svg v-else viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="#8e8e93" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
          <line x1="9" y1="9" x2="9.01" y2="9"></line>
          <line x1="15" y1="9" x2="15.01" y2="9"></line>
        </svg>
      </button>
    </div>
    <EmojiStickerPicker
      :open="pickerSlotActive"
      :currentUserId="currentUserId"
      :currentStickerCount="stickerCount"
      :maxStickerCount="MAX_STICKERS_PER_MESSAGE"
      :closeOnSelect="false"
      :panelHeight="stickerPickerHeight"
      :disableTransition="disablePickerTransition"
      @update:open="setStickerPickerOpen"
      @insert="insertSticker"
      @backspace="deleteComposerBackward"
    />
    <!--
      Hidden probe whose height tracks env(keyboard-inset-height) exactly.
      We measure THIS element via ResizeObserver instead of inferring keyboard height
      from visualViewport, guaranteeing the picker target matches env() pixel-for-pixel
      so the panel and the keyboard are always exactly the same height.
    -->
    <div ref="keyboardInsetProbeRef" class="keyboard-inset-probe" aria-hidden="true"></div>
    <div
      v-if="pickerTransitionSpacerHeight > 0"
      class="picker-transition-spacer"
      :style="{ height: `${pickerTransitionSpacerHeight}px` }"
      aria-hidden="true"
    ></div>
    <div v-if="isChatDebugEnabled" class="chat-debug-panel">
      <div class="chat-debug-grid">
        <div class="chat-debug-entry">event={{ debugState.lastEvent }}</div>
        <div class="chat-debug-entry">focus={{ debugState.hasFocus ? 1 : 0 }}</div>
        <div class="chat-debug-entry">vv={{ debugState.visualViewportHeight }}</div>
        <div class="chat-debug-entry">offset={{ debugState.visualViewportOffsetTop }}</div>
        <div class="chat-debug-entry">inner={{ debugState.innerHeight }}</div>
        <div class="chat-debug-entry">doc={{ debugState.documentHeight }}</div>
        <div class="chat-debug-entry">base={{ debugState.viewportBaseHeight }}</div>
        <div class="chat-debug-entry">keyboard={{ debugState.keyboardHeight }}</div>
        <div class="chat-debug-entry">last={{ debugState.lastKnownKeyboardHeight }}</div>
        <div class="chat-debug-entry">locked={{ debugState.lockedComposerInsetHeight }}</div>
        <div class="chat-debug-entry">panel={{ debugState.panelHeight }}</div>
        <div class="chat-debug-entry">spacer={{ debugState.spacerHeight }}</div>
        <div class="chat-debug-entry">open={{ debugState.isPickerOpen ? 1 : 0 }}</div>
        <div class="chat-debug-entry">waitOpen={{ debugState.pendingPickerOpen ? 1 : 0 }}</div>
        <div class="chat-debug-entry">waitKb={{ debugState.pendingKeyboardReturn ? 1 : 0 }}</div>
        <div class="chat-debug-entry">width={{ debugState.visualViewportWidth }}</div>
      </div>
      <div v-if="debugTrail.length > 0" class="chat-debug-trail">
        <div v-for="entry in debugTrail" :key="entry" class="chat-debug-trail-item">{{ entry }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, onBeforeUnmount } from 'vue'
import { AudioRecorder } from '../../utils/audioRecorder'
import {
  countEmojiStickerOccurrences,
  MAX_STICKERS_PER_MESSAGE,
  splitTextGraphemes,
} from '../../utils/emojiStickerCatalog'
import EmojiStickerPicker from './EmojiStickerPicker.vue'

const props = defineProps<{
  modelValue: string
  editingMessage: any | null
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
  stickerPickerOpen?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'cancel-edit'): void
  (e: 'cancel-reply'): void
  (e: 'delete-selected'): void
  (e: 'reply-selected'): void
  (e: 'copy-selected'): void
  (e: 'forward-selected'): void
  (e: 'toggle-attachment'): void
  (e: 'send-text', content: string): void
  (e: 'send-voice', blob: Blob, durationMs: number): void
  (e: 'typing'): void
  (e: 'update:stickerPickerOpen', value: boolean): void
}>()

const messageInputRef = ref<HTMLTextAreaElement | null>(null)
const keyboardInsetProbeRef = ref<HTMLElement | null>(null)
const composerSelectionStart = ref(0)
const composerSelectionEnd = ref(0)
const DEFAULT_PICKER_HEIGHT = 336
const MIN_PICKER_FALLBACK_HEIGHT = 260
const MAX_PICKER_FALLBACK_HEIGHT = 420
const KEYBOARD_OPEN_THRESHOLD = 120
const KEYBOARD_CLOSE_THRESHOLD = 24
const keyboardHeight = ref(0)
const lastKnownKeyboardHeight = ref(0)
const envKeyboardInset = ref(0)
const envKeyboardInsetMax = ref(0)
const viewportBaseHeight = ref(0)
const viewportBaseWidth = ref(0)
const pendingPickerOpenAfterKeyboardClose = ref(false)
const lockedComposerInsetHeight = ref(0)
const pendingKeyboardReturn = ref(false)
const disablePickerTransition = ref(false)
const keyboardInsetEnvSupported = ref(false)
let pendingPickerOpenTimer: number | null = null
let pendingTextareaFocusScrollSnapshot: ScrollSnapshotEntry[] | null = null
let keyboardInsetResizeObserver: ResizeObserver | null = null

type VisualViewportWithEvents = VisualViewport & {
  addEventListener: (type: 'resize' | 'scroll', listener: EventListenerOrEventListenerObject) => void
  removeEventListener: (type: 'resize' | 'scroll', listener: EventListenerOrEventListenerObject) => void
}

type ViewportMetrics = {
  fullHeightCandidate: number
  layoutViewportHeight: number
  width: number
}

type ScrollSnapshotEntry = {
  target: Window | HTMLElement
  top: number
  left: number
}

type ChatInputDebugState = {
  lastEvent: string
  hasFocus: boolean
  visualViewportHeight: number
  visualViewportWidth: number
  visualViewportOffsetTop: number
  innerHeight: number
  documentHeight: number
  viewportBaseHeight: number
  keyboardHeight: number
  lastKnownKeyboardHeight: number
  lockedComposerInsetHeight: number
  panelHeight: number
  spacerHeight: number
  isPickerOpen: boolean
  pendingPickerOpen: boolean
  pendingKeyboardReturn: boolean
}

type ChatInputDebugWindow = Window & {
  __chatInputDebug?: {
    getSnapshot: () => ChatInputDebugState
    getTrail: () => string[]
  }
}

const isChatDebugEnabled = ref(false)
const debugTrail = ref<string[]>([])
const debugState = ref<ChatInputDebugState>({
  lastEvent: 'idle',
  hasFocus: false,
  visualViewportHeight: 0,
  visualViewportWidth: 0,
  visualViewportOffsetTop: 0,
  innerHeight: 0,
  documentHeight: 0,
  viewportBaseHeight: 0,
  keyboardHeight: 0,
  lastKnownKeyboardHeight: 0,
  lockedComposerInsetHeight: 0,
  panelHeight: 0,
  spacerHeight: 0,
  isPickerOpen: false,
  pendingPickerOpen: false,
  pendingKeyboardReturn: false,
})

const messageInput = computed({
  get: () => props.modelValue ?? '',
  set: (value: string) => emit('update:modelValue', value)
})

const canSubmit = computed(() => Boolean(messageInput.value.trim()))
const isStickerPickerOpen = computed(() => Boolean(props.stickerPickerOpen))
const stickerCount = computed(() => countEmojiStickerOccurrences(messageInput.value))
// Numeric target height for the picker panel (used as the locked keyboard slot size).
// Prefers the env(keyboard-inset-height) probe value when available — that is the
// SAME source the browser will animate into the picker via env(), guaranteeing the
// picker and keyboard always have IDENTICAL height with zero rounding mismatch.
const stickerPickerTargetHeight = computed(() => {
  if (lockedComposerInsetHeight.value > 0) {
    return lockedComposerInsetHeight.value
  }

  // Use the largest env() value seen so far (= the keyboard's full open height) when supported.
  if (keyboardInsetEnvSupported.value && envKeyboardInsetMax.value > 0) {
    return envKeyboardInsetMax.value
  }

  const measuredHeight = getMeasuredKeyboardInset()
  if (measuredHeight > 0) {
    return measuredHeight
  }

  const viewportHeight = typeof window !== 'undefined'
    ? (window.visualViewport?.height ?? window.innerHeight)
    : DEFAULT_PICKER_HEIGHT

  return Math.min(
    Math.max(Math.round(viewportHeight * 0.42), MIN_PICKER_FALLBACK_HEIGHT),
    MAX_PICKER_FALLBACK_HEIGHT,
    DEFAULT_PICKER_HEIGHT,
  )
})

// Whether the picker should currently occupy the bottom inset slot at all.
// True when the picker is open OR we are mid-swap with the keyboard.
const pickerSlotActive = computed(() => {
  return isStickerPickerOpen.value
    || pendingPickerOpenAfterKeyboardClose.value
    || pendingKeyboardReturn.value
})

// Final CSS height for the picker.
// On modern Chrome (with interactive-widget=resizes-content), env(keyboard-inset-height)
// animates SMOOTHLY in lock-step with the keyboard. Subtracting it from the locked target
// gives a picker that always exactly fills (target - keyboard) — so the input row's Y
// position stays stable in both swap directions with zero JS frame lag.
const stickerPickerHeight = computed<number | string>(() => {
  if (!pickerSlotActive.value) return 0
  const target = stickerPickerTargetHeight.value
  if (target <= 0) return 0

  if (keyboardInsetEnvSupported.value) {
    return `max(0px, calc(${target}px - env(keyboard-inset-height, 0px)))`
  }
  // Fallback: use the JS-measured keyboard height. Less smooth but still correct at rest.
  return Math.max(0, target - keyboardHeight.value)
})

// The legacy spacer is no longer needed when env() is supported — the picker itself
// fills the inset slot smoothly. Only kept as a safety net for browsers without env().
const pickerTransitionSpacerHeight = computed(() => {
  if (keyboardInsetEnvSupported.value) return 0
  if (lockedComposerInsetHeight.value <= 0 || isStickerPickerOpen.value) return 0
  if (pendingPickerOpenAfterKeyboardClose.value || pendingKeyboardReturn.value) {
    return Math.max(
      lockedComposerInsetHeight.value - Math.min(keyboardHeight.value, lockedComposerInsetHeight.value),
      0,
    )
  }
  return 0
})

const summarizeMessage = (message: any | null) => {
  if (!message) return ''
  switch (message.message_type) {
    case 'text':
      return message.content
    case 'image':
      return '🖼️ تصویر'
    case 'video':
      return '📹 ویدیو'
    case 'location':
      return '📍 موقعیت'
    case 'voice':
      return '🎤 پیام صوتی'
    case 'document':
      return '📎 فایل'
    default:
      return '😊 استیکر'
  }
}

const replyBannerText = computed(() => summarizeMessage(props.replyingToMessage))
const editingBannerText = computed(() => summarizeMessage(props.editingMessage))

function clampSelectionToLength(value: string) {
  composerSelectionStart.value = Math.min(composerSelectionStart.value, value.length)
  composerSelectionEnd.value = Math.min(composerSelectionEnd.value, value.length)
}

function captureSelection() {
  const el = messageInputRef.value
  if (!el) {
    clampSelectionToLength(messageInput.value)
    return
  }

  composerSelectionStart.value = el.selectionStart ?? messageInput.value.length
  composerSelectionEnd.value = el.selectionEnd ?? composerSelectionStart.value
  clampSelectionToLength(messageInput.value)
}

function getComposerSelection() {
  if (document.activeElement === messageInputRef.value) {
    captureSelection()
  }

  return {
    start: composerSelectionStart.value,
    end: composerSelectionEnd.value,
  }
}

function blurInput() {
  const el = messageInputRef.value
  if (!el) return

  preserveComposerScrollPosition(() => {
    el.blur()
  })
}

function isScrollableElement(element: HTMLElement) {
  const style = window.getComputedStyle(element)
  const overflowY = style.overflowY
  const overflowX = style.overflowX
  const canScrollY = /(auto|scroll|overlay)/.test(overflowY) && element.scrollHeight > element.clientHeight + 1
  const canScrollX = /(auto|scroll|overlay)/.test(overflowX) && element.scrollWidth > element.clientWidth + 1
  return canScrollY || canScrollX
}

function captureComposerScrollSnapshot() {
  if (typeof window === 'undefined') return [] as ScrollSnapshotEntry[]

  const snapshot: ScrollSnapshotEntry[] = [
    { target: window, top: window.scrollY, left: window.scrollX },
  ]

  let current = messageInputRef.value?.parentElement ?? null
  while (current) {
    if (isScrollableElement(current)) {
      snapshot.push({
        target: current,
        top: current.scrollTop,
        left: current.scrollLeft,
      })
    }
    current = current.parentElement
  }

  return snapshot
}

function restoreComposerScrollSnapshot(snapshot: ScrollSnapshotEntry[] | null) {
  if (!snapshot || snapshot.length === 0 || typeof window === 'undefined') return

  for (const entry of snapshot) {
    if (entry.target === window) {
      window.scrollTo(entry.left, entry.top)
      continue
    }

    const elementTarget = entry.target as HTMLElement
    elementTarget.scrollTop = entry.top
    elementTarget.scrollLeft = entry.left
  }
}

function scheduleComposerScrollRestore(snapshot: ScrollSnapshotEntry[] | null, frames = 12) {
  if (!snapshot || snapshot.length === 0 || typeof window === 'undefined') return

  let remainingFrames = frames
  const restoreFrame = () => {
    restoreComposerScrollSnapshot(snapshot)
    remainingFrames -= 1
    if (remainingFrames > 0) {
      window.requestAnimationFrame(restoreFrame)
    }
  }

  window.requestAnimationFrame(restoreFrame)
}

function preserveComposerScrollPosition(action: () => void, frames = 12) {
  const snapshot = captureComposerScrollSnapshot()
  action()
  scheduleComposerScrollRestore(snapshot, frames)
}

function prepareTextareaFocus() {
  pendingTextareaFocusScrollSnapshot = captureComposerScrollSnapshot()
}

function clearPendingPickerTimer() {
  if (pendingPickerOpenTimer !== null) {
    window.clearTimeout(pendingPickerOpenTimer)
    pendingPickerOpenTimer = null
  }
}

function resolveChatDebugEnabled() {
  if (typeof window === 'undefined') return false

  try {
    const params = new URLSearchParams(window.location.search)
    const flag = params.get('chatDebug')

    if (flag === '1') {
      window.localStorage.setItem('chat_keyboard_debug', '1')
      return true
    }

    if (flag === '0') {
      window.localStorage.removeItem('chat_keyboard_debug')
      return false
    }

    return window.localStorage.getItem('chat_keyboard_debug') === '1'
  } catch {
    return false
  }
}

function captureDebugState(eventName: string, persistEvent = true) {
  if (!isChatDebugEnabled.value || typeof window === 'undefined') return

  const visualViewport = getVisualViewport()
  const nextState: ChatInputDebugState = {
    lastEvent: eventName,
    hasFocus: hasComposerFocus(),
    visualViewportHeight: Math.round(visualViewport?.height ?? 0),
    visualViewportWidth: Math.round(visualViewport?.width ?? 0),
    visualViewportOffsetTop: Math.round(visualViewport?.offsetTop ?? 0),
    innerHeight: Math.round(window.innerHeight),
    documentHeight: Math.round(document.documentElement?.clientHeight ?? 0),
    viewportBaseHeight: Math.round(viewportBaseHeight.value),
    keyboardHeight: Math.round(keyboardHeight.value),
    lastKnownKeyboardHeight: Math.round(lastKnownKeyboardHeight.value),
    lockedComposerInsetHeight: Math.round(lockedComposerInsetHeight.value),
    panelHeight: Math.round(stickerPickerTargetHeight.value),
    spacerHeight: Math.round(pickerTransitionSpacerHeight.value),
    isPickerOpen: isStickerPickerOpen.value,
    pendingPickerOpen: pendingPickerOpenAfterKeyboardClose.value,
    pendingKeyboardReturn: pendingKeyboardReturn.value,
  }

  debugState.value = nextState

  if (!persistEvent) return

  const timestamp = new Date().toLocaleTimeString('fa-IR', { hour12: false })
  const compactEntry = `${timestamp} ${eventName} vv=${nextState.visualViewportHeight} kb=${nextState.keyboardHeight} panel=${nextState.panelHeight} spacer=${nextState.spacerHeight} open=${nextState.isPickerOpen ? 1 : 0} focus=${nextState.hasFocus ? 1 : 0}`
  debugTrail.value = [compactEntry, ...debugTrail.value].slice(0, 10)
}

function syncDebugWindowHandle() {
  if (typeof window === 'undefined') return

  const debugWindow = window as ChatInputDebugWindow
  if (!isChatDebugEnabled.value) {
    delete debugWindow.__chatInputDebug
    return
  }

  debugWindow.__chatInputDebug = {
    getSnapshot: () => ({ ...debugState.value }),
    getTrail: () => [...debugTrail.value],
  }
}

function getVisualViewport() {
  if (typeof window === 'undefined') return null
  return (window.visualViewport ?? null) as VisualViewportWithEvents | null
}

function getViewportMetrics(): ViewportMetrics {
  if (typeof window === 'undefined') {
    return {
      fullHeightCandidate: 0,
      layoutViewportHeight: 0,
      width: 0,
    }
  }

  const visualViewport = getVisualViewport()
  const currentViewportHeight = visualViewport?.height ?? window.innerHeight
  const offsetTop = visualViewport?.offsetTop ?? 0

  return {
    fullHeightCandidate: currentViewportHeight + offsetTop,
    layoutViewportHeight: Math.max(window.innerHeight, document.documentElement?.clientHeight ?? 0),
    width: Math.round(visualViewport?.width ?? window.innerWidth),
  }
}

function getViewportBaselineHeight(metrics: ViewportMetrics) {
  return Math.max(viewportBaseHeight.value, metrics.layoutViewportHeight)
}

function syncViewportBaseHeight(metrics: ViewportMetrics, options?: { force?: boolean; preserveLarger?: boolean }) {
  const candidate = Math.max(metrics.fullHeightCandidate, metrics.layoutViewportHeight)
  const widthChanged = viewportBaseWidth.value > 0
    && Math.abs(metrics.width - viewportBaseWidth.value) >= 80

  if (options?.force || viewportBaseHeight.value <= 0 || widthChanged) {
    viewportBaseHeight.value = candidate
  } else if (options?.preserveLarger) {
    viewportBaseHeight.value = Math.max(viewportBaseHeight.value, candidate)
  } else {
    viewportBaseHeight.value = candidate
  }

  viewportBaseWidth.value = metrics.width
  return viewportBaseHeight.value
}

function hasComposerFocus() {
  return document.activeElement === messageInputRef.value
}

function shouldRefreshViewportBaseHeight() {
  return !isStickerPickerOpen.value
    && !pendingPickerOpenAfterKeyboardClose.value
    && !pendingKeyboardReturn.value
    && !hasComposerFocus()
}

function getMeasuredKeyboardInset() {
  if (typeof window === 'undefined') {
    return Math.max(keyboardHeight.value, lastKnownKeyboardHeight.value)
  }

  // Prefer env(keyboard-inset-height) probe — that's the authoritative value the browser
  // will animate into the picker, so locking it as the target guarantees pixel-perfect
  // matching between picker height and keyboard height.
  if (keyboardInsetEnvSupported.value && envKeyboardInset.value > 0) {
    return Math.max(envKeyboardInset.value, envKeyboardInsetMax.value)
  }

  const metrics = getViewportMetrics()
  const inferredHeight = Math.max(
    0,
    Math.round(getViewportBaselineHeight(metrics) - metrics.fullHeightCandidate),
  )

  return Math.max(
    keyboardHeight.value,
    inferredHeight,
    lastKnownKeyboardHeight.value,
    envKeyboardInsetMax.value,
  )
}

function lockComposerInsetHeight(preferredHeight = 0) {
  // When env() is supported, the env probe value is the authoritative source.
  // Override any caller-provided preferredHeight with it so target == future env exactly.
  if (keyboardInsetEnvSupported.value) {
    const envValue = Math.max(envKeyboardInset.value, envKeyboardInsetMax.value)
    if (envValue > 0) {
      lockedComposerInsetHeight.value = envValue
      return envValue
    }
  }

  const nextHeight = preferredHeight > 0 ? preferredHeight : getMeasuredKeyboardInset()
  if (nextHeight > 0) {
    lockedComposerInsetHeight.value = nextHeight
  }
  return nextHeight
}

function openStickerPickerAfterKeyboardClose() {
  clearPendingPickerTimer()
  pendingPickerOpenAfterKeyboardClose.value = false
  pendingKeyboardReturn.value = false
  setStickerPickerOpen(true)
  nextTick(() => {
    disablePickerTransition.value = false
    captureDebugState('picker-open-after-close')
  })
}

function finalizeKeyboardReturn() {
  // Keyboard is fully open and the picker has shrunk to 0 via env(). Now release the slot.
  pendingKeyboardReturn.value = false
  lockedComposerInsetHeight.value = 0
  disablePickerTransition.value = false
  if (isStickerPickerOpen.value) {
    setStickerPickerOpen(false)
  }
}

function updateKeyboardMetrics() {
  if (typeof window === 'undefined') return

  const metrics = getViewportMetrics()
  const predictedKeyboardHeight = Math.max(
    0,
    Math.round(getViewportBaselineHeight(metrics) - metrics.fullHeightCandidate),
  )
  const canRefreshViewportBase = shouldRefreshViewportBaseHeight()
    && predictedKeyboardHeight < KEYBOARD_OPEN_THRESHOLD

  if (viewportBaseHeight.value <= 0) {
    syncViewportBaseHeight(metrics, { force: true })
  } else if (canRefreshViewportBase) {
    syncViewportBaseHeight(metrics)
  } else {
    syncViewportBaseHeight(metrics, { preserveLarger: true })
  }

  const nextKeyboardHeight = Math.max(
    0,
    Math.round(getViewportBaselineHeight(metrics) - metrics.fullHeightCandidate),
  )
  keyboardHeight.value = nextKeyboardHeight

  if (nextKeyboardHeight >= KEYBOARD_OPEN_THRESHOLD) {
    lastKnownKeyboardHeight.value = Math.max(lastKnownKeyboardHeight.value, nextKeyboardHeight)
    if (pendingKeyboardReturn.value) {
      finalizeKeyboardReturn()
    } else {
      pendingKeyboardReturn.value = false
      lockedComposerInsetHeight.value = 0
    }
  } else if (
    nextKeyboardHeight <= KEYBOARD_CLOSE_THRESHOLD
    && !isStickerPickerOpen.value
    && !pendingPickerOpenAfterKeyboardClose.value
    && !pendingKeyboardReturn.value
    && !hasComposerFocus()
  ) {
    lockedComposerInsetHeight.value = 0
    lastKnownKeyboardHeight.value = 0
    disablePickerTransition.value = false
  }

  if (pendingPickerOpenAfterKeyboardClose.value && nextKeyboardHeight <= KEYBOARD_CLOSE_THRESHOLD) {
    openStickerPickerAfterKeyboardClose()
    return
  }

  captureDebugState('viewport', false)
}

const resizeTextarea = () => {
  const el = messageInputRef.value
  if (!el) return
  el.style.height = '1px'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

const handleInput = () => {
  resizeTextarea()
  captureSelection()
  emit('typing')
}

function focusInput(options?: { cursorToEnd?: boolean }) {
  const scrollSnapshot = captureComposerScrollSnapshot()
  nextTick(() => {
    const el = messageInputRef.value
    if (!el) return
    try {
      el.focus({ preventScroll: true })
    } catch {
      el.focus()
    }

    let selectionStart = composerSelectionStart.value
    let selectionEnd = composerSelectionEnd.value

    if (options?.cursorToEnd) {
      selectionStart = el.value.length
      selectionEnd = el.value.length
    }

    selectionStart = Math.min(selectionStart, el.value.length)
    selectionEnd = Math.min(selectionEnd, el.value.length)
    el.setSelectionRange(selectionStart, selectionEnd)
    composerSelectionStart.value = selectionStart
    composerSelectionEnd.value = selectionEnd
    scheduleComposerScrollRestore(scrollSnapshot)
  })
}

defineExpose({
  focusInput,
  adjustTextareaHeight: resizeTextarea
})

watch(() => props.modelValue, (value) => {
  nextTick(() => {
    clampSelectionToLength(value ?? '')
    resizeTextarea()
  })
})

watch(() => props.editingMessage, (message) => {
  if (message) {
    pendingPickerOpenAfterKeyboardClose.value = false
    pendingKeyboardReturn.value = false
    lockedComposerInsetHeight.value = 0
    clearPendingPickerTimer()
    emit('update:stickerPickerOpen', false)
    captureDebugState('editing-open')
  }
})

watch(() => props.isSelectionMode, (isSelectionEnabled) => {
  if (isSelectionEnabled) {
    pendingPickerOpenAfterKeyboardClose.value = false
    pendingKeyboardReturn.value = false
    lockedComposerInsetHeight.value = 0
    clearPendingPickerTimer()
    emit('update:stickerPickerOpen', false)
    captureDebugState('selection-open')
  }
})

watch(() => props.stickerPickerOpen, (isOpen) => {
  if (isOpen) {
    pendingKeyboardReturn.value = false
    captureDebugState('picker-open')
    return
  }
  pendingPickerOpenAfterKeyboardClose.value = false
  clearPendingPickerTimer()
  if (!pendingKeyboardReturn.value) {
    lockedComposerInsetHeight.value = 0
    disablePickerTransition.value = false
  }
  captureDebugState('picker-close')
})

function setStickerPickerOpen(nextValue: boolean) {
  emit('update:stickerPickerOpen', nextValue)
}

function toggleStickerPicker() {
  if (props.isDeleted) return

  captureDebugState('emoji-toggle')

  if (isStickerPickerOpen.value) {
    // Picker → Keyboard. Keep picker mounted; env(keyboard-inset-height) will smoothly
    // shrink it from `target` down to 0 as the keyboard slides up. Once the keyboard is
    // fully open, updateKeyboardMetrics() calls finalizeKeyboardReturn() which unmounts.
    pendingPickerOpenAfterKeyboardClose.value = false
    clearPendingPickerTimer()
    lockComposerInsetHeight(stickerPickerTargetHeight.value)
    pendingKeyboardReturn.value = true
    disablePickerTransition.value = true
    setStickerPickerOpen(false) // pickerSlotActive stays true via pendingKeyboardReturn
    focusInput()
    captureDebugState('keyboard-return-requested')
    return
  }

  captureSelection()
  const keyboardLooksOpen = document.activeElement === messageInputRef.value
    || keyboardHeight.value >= KEYBOARD_OPEN_THRESHOLD

  if (keyboardLooksOpen) {
    // Keyboard → Picker. Lock the slot at the current keyboard height, mount the picker
    // immediately (it renders at height 0 because env(keyboard-inset-height) == target),
    // then blur to start the keyboard close animation. As env() decreases the picker grows
    // smoothly to fill the slot — zero JS-driven jump.
    lockComposerInsetHeight()
    pendingPickerOpenAfterKeyboardClose.value = true
    pendingKeyboardReturn.value = false
    disablePickerTransition.value = true
    clearPendingPickerTimer()
    blurInput()
    // Safety net: if for some reason the resize event never fires (extension WebViews,
    // some embedded browsers), still finalize the open after the typical animation.
    pendingPickerOpenTimer = window.setTimeout(() => {
      updateKeyboardMetrics()
      if (pendingPickerOpenAfterKeyboardClose.value && keyboardHeight.value <= KEYBOARD_CLOSE_THRESHOLD) {
        openStickerPickerAfterKeyboardClose()
      }
    }, 320)
    captureDebugState('picker-open-requested')
    return
  }

  // Direct open with no keyboard — just mount and show.
  lockComposerInsetHeight(lastKnownKeyboardHeight.value)
  disablePickerTransition.value = false
  setStickerPickerOpen(true)
  captureDebugState('picker-open-direct')
}

function prepareStickerToggle() {
  if (props.isDeleted) return

  captureDebugState('emoji-press')

  if (isStickerPickerOpen.value) {
    lockComposerInsetHeight(stickerPickerTargetHeight.value)
    return
  }

  if (document.activeElement === messageInputRef.value || keyboardHeight.value >= KEYBOARD_OPEN_THRESHOLD) {
    captureSelection()
    lockComposerInsetHeight()
  }
}

function handleToggleAttachment() {
  pendingKeyboardReturn.value = false
  lockedComposerInsetHeight.value = 0
  disablePickerTransition.value = false
  setStickerPickerOpen(false)
  emit('toggle-attachment')
  captureDebugState('attachment-open')
}

function handleTextareaFocus() {
  const scrollSnapshot = pendingTextareaFocusScrollSnapshot
  pendingTextareaFocusScrollSnapshot = null
  captureSelection()
  pendingPickerOpenAfterKeyboardClose.value = false
  clearPendingPickerTimer()
  if (isStickerPickerOpen.value) {
    // Direct focus on textarea while picker is open: same as picker→keyboard swap.
    lockComposerInsetHeight(stickerPickerTargetHeight.value)
    pendingKeyboardReturn.value = true
    disablePickerTransition.value = true
    setStickerPickerOpen(false)
  }
  scheduleComposerScrollRestore(scrollSnapshot)
  captureDebugState('textarea-focus')
}

onMounted(() => {
  if (typeof window === 'undefined') return

  isChatDebugEnabled.value = resolveChatDebugEnabled()
  syncDebugWindowHandle()

  // Detect support for env(keyboard-inset-height). When supported (Chrome 108+ Android,
  // Edge, etc.), the browser drives picker height smoothly via CSS — no JS frame lag.
  try {
    keyboardInsetEnvSupported.value = typeof CSS !== 'undefined'
      && typeof CSS.supports === 'function'
      && CSS.supports('height: env(keyboard-inset-height)')
  } catch {
    keyboardInsetEnvSupported.value = false
  }

  syncViewportBaseHeight(getViewportMetrics(), { force: true })

  const visualViewport = getVisualViewport()

  updateKeyboardMetrics()
  captureDebugState('mounted')

  visualViewport?.addEventListener('resize', updateKeyboardMetrics)
  visualViewport?.addEventListener('scroll', updateKeyboardMetrics)
  window.addEventListener('resize', updateKeyboardMetrics)

  // Watch the env(keyboard-inset-height) probe element. Its height is the EXACT same
  // value the browser will subtract from the picker. By measuring it directly we
  // eliminate the rounding/source mismatch between visualViewport-derived keyboard
  // height and env() — making the picker pixel-perfect equal to the keyboard.
  const probe = keyboardInsetProbeRef.value
  if (probe && typeof ResizeObserver !== 'undefined') {
    keyboardInsetResizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      const h = Math.round(entry.contentRect.height)
      envKeyboardInset.value = h
      if (h > envKeyboardInsetMax.value) envKeyboardInsetMax.value = h
      // If the probe reports a non-zero env value, env() is genuinely supported on this engine.
      if (h > 0) keyboardInsetEnvSupported.value = true
      // Keep lastKnownKeyboardHeight in sync so other code paths see the same source.
      if (h >= KEYBOARD_OPEN_THRESHOLD) {
        lastKnownKeyboardHeight.value = Math.max(lastKnownKeyboardHeight.value, h)
      }
      captureDebugState('env-probe', false)
    })
    keyboardInsetResizeObserver.observe(probe)
  }
})

onBeforeUnmount(() => {
  if (typeof window === 'undefined') return

  clearPendingPickerTimer()
  syncDebugWindowHandle()

  const visualViewport = getVisualViewport()
  visualViewport?.removeEventListener('resize', updateKeyboardMetrics)
  visualViewport?.removeEventListener('scroll', updateKeyboardMetrics)
  window.removeEventListener('resize', updateKeyboardMetrics)

  if (keyboardInsetResizeObserver) {
    keyboardInsetResizeObserver.disconnect()
    keyboardInsetResizeObserver = null
  }
})

function applyComposerValue(nextValue: string, nextSelectionStart: number, nextSelectionEnd = nextSelectionStart) {
  const shouldRestoreSelection = document.activeElement === messageInputRef.value
  messageInput.value = nextValue
  composerSelectionStart.value = nextSelectionStart
  composerSelectionEnd.value = nextSelectionEnd
  emit('typing')

  nextTick(() => {
    resizeTextarea()
    const el = messageInputRef.value
    if (!el || !shouldRestoreSelection) return

    const selectionStart = Math.min(composerSelectionStart.value, el.value.length)
    const selectionEnd = Math.min(composerSelectionEnd.value, el.value.length)
    el.setSelectionRange(selectionStart, selectionEnd)
  })
}

function insertSticker(sticker: string) {
  const { start, end } = getComposerSelection()
  const nextValue = `${messageInput.value.slice(0, start)}${sticker}${messageInput.value.slice(end)}`

  if (countEmojiStickerOccurrences(nextValue) > MAX_STICKERS_PER_MESSAGE) {
    alert(`حداکثر ${MAX_STICKERS_PER_MESSAGE} استیکر در هر پیام مجاز است.`)
    return
  }

  applyComposerValue(nextValue, start + sticker.length)
}

function deleteComposerBackward() {
  const { start, end } = getComposerSelection()
  if (start === 0 && end === 0) return

  if (end > start) {
    const nextValue = `${messageInput.value.slice(0, start)}${messageInput.value.slice(end)}`
    applyComposerValue(nextValue, start)
    return
  }

  const prefix = messageInput.value.slice(0, start)
  const suffix = messageInput.value.slice(end)
  const graphemes = splitTextGraphemes(prefix)
  if (graphemes.length === 0) return

  graphemes.pop()
  const nextPrefix = graphemes.join('')
  applyComposerValue(`${nextPrefix}${suffix}`, nextPrefix.length)
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

  if (countEmojiStickerOccurrences(content) > MAX_STICKERS_PER_MESSAGE) {
    alert(`حداکثر ${MAX_STICKERS_PER_MESSAGE} استیکر در هر پیام مجاز است.`)
    return
  }
  
  emit('send-text', content)
  messageInput.value = ''
  composerSelectionStart.value = 0
  composerSelectionEnd.value = 0
  
  // reset height
  nextTick(() => {
    if (messageInputRef.value) messageInputRef.value.style.height = 'auto'
  })
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

.input-area.picker-open {
  padding-bottom: 0;
}

.picker-transition-spacer {
  margin: 0 -12px 0;
  background:
    radial-gradient(circle at top right, rgba(51, 144, 236, 0.12), transparent 34%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(244, 247, 251, 0.98));
  border-top: 1px solid rgba(15, 23, 42, 0.06);
}

/*
  Hidden probe element. Its height tracks env(keyboard-inset-height) exactly,
  including the smooth animation while the keyboard slides up/down. We measure
  this via ResizeObserver and use the value as the authoritative keyboard height.
  Position fixed off-screen so it cannot affect layout.
*/
.keyboard-inset-probe {
  position: fixed;
  left: -1px;
  bottom: 0;
  width: 1px;
  height: env(keyboard-inset-height, 0px);
  pointer-events: none;
  visibility: hidden;
  z-index: -1;
}

.chat-debug-panel {
  margin: 8px -12px 0;
  padding: 10px 12px 12px;
  background: rgba(15, 23, 42, 0.96);
  color: #e2e8f0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 11px;
  line-height: 1.45;
  direction: ltr;
  text-align: left;
}

.chat-debug-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px 10px;
}

.chat-debug-entry {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-debug-trail {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(148, 163, 184, 0.28);
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 96px;
  overflow: auto;
}

.chat-debug-trail-item {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgba(226, 232, 240, 0.86);
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
.input-container textarea::selection {
  background: rgba(51, 144, 236, 0.28);
  color: #000000;
}
.input-container textarea::-moz-selection {
  background: rgba(51, 144, 236, 0.28);
  color: #000000;
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
.emoji-btn.is-active svg { stroke: #3390ec; }
.attach-btn, .voice-btn { margin-right: 4px; }
.cancel-voice-btn { margin-left: 4px; }

.send-btn-inline {
  background: none; border: none; padding: 0; margin: 0; cursor: pointer; display: flex;
  align-items: center; justify-content: center; flex-shrink: 0; width: 32px; height: 32px; margin-right: 4px;
}
.send-btn-inline svg { width: 28px; height: 28px; }
.send-btn-inline.edit-mode svg { width: 24px; height: 24px; }
.send-btn-inline:disabled { opacity: 0.5; cursor: not-allowed; }

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
.edit-banner-icon { color: #3390ec; }
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
