<template>
  <div class="share-receive-root">
    <!-- Loading / error states (rare; modal takes over on success) -->
    <div v-if="loading" class="state-overlay">
      <div class="spinner"></div>
      <p>در حال آماده‌سازی...</p>
    </div>

    <div v-else-if="errorMsg" class="state-overlay error">
      <p>{{ errorMsg }}</p>
      <button class="primary-btn" @click="goHome">بازگشت به خانه</button>
    </div>

    <!-- Sending progress overlay -->
    <div v-else-if="sending" class="state-overlay">
      <div class="spinner"></div>
      <p>در حال ارسال... ({{ sentCount }}/{{ totalSendCount }})</p>
      <ul v-if="sendErrors.length" class="errors-list">
        <li v-for="(e, i) in sendErrors" :key="i">{{ e }}</li>
      </ul>
    </div>

    <!-- Final result (only shown briefly on errors before redirect) -->
    <div v-else-if="sendDone" class="state-overlay">
      <p v-if="sendErrors.length">
        ارسال با {{ sendErrors.length }} خطا انجام شد
      </p>
      <p v-else>ارسال انجام شد</p>
      <ul v-if="sendErrors.length" class="errors-list">
        <li v-for="(e, i) in sendErrors" :key="i">{{ e }}</li>
      </ul>
      <button class="primary-btn" @click="goHome">بازگشت</button>
    </div>

    <!-- Main UI: full-screen messenger-like target picker -->
    <ChatForwardModal
      v-else
      :showForwardModal="true"
      :sortedConversations="conversations"
      @close="handleClose"
      @forward-to="handleTargetsPicked"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiFetchJson } from '../utils/auth'
import { readSharedPayload, deleteSharedPayload, type SharedPayload, type SharedFileEntry } from '../utils/shareTargetStore'
import ChatForwardModal from '../components/chat/ChatForwardModal.vue'
import type { ChatForwardTarget, Conversation } from '../types/chat'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const errorMsg = ref('')
const payloadKey = ref('')
const files = ref<SharedFileEntry[]>([])
const sharedTitle = ref('')
const sharedText = ref('')
const sharedUrl = ref('')

const conversations = ref<Conversation[]>([])

const sending = ref(false)
const sendDone = ref(false)
const sentCount = ref(0)
const sendErrors = ref<string[]>([])

const mergedText = computed(() => {
  const parts: string[] = []
  if (sharedTitle.value) parts.push(sharedTitle.value)
  if (sharedText.value) parts.push(sharedText.value)
  if (sharedUrl.value) parts.push(sharedUrl.value)
  return parts.join('\n').trim()
})

const totalSendCount = ref(0)

async function loadConversations() {
  try {
    // Backend returns conversations already sorted by last_message_at desc,
    // matching the messenger conversation list ordering exactly.
    const data = await apiFetchJson('/api/chat/conversations') as Conversation[]
    conversations.value = Array.isArray(data) ? data : []
  } catch {
    conversations.value = []
  }
}

onMounted(async () => {
  loading.value = true
  try {
    if (route.query.share_error) {
      errorMsg.value = 'دریافت محتوای اشتراک‌گذاری شده با خطا مواجه شد.'
      return
    }
    const key = (route.query.share_key as string) || ''
    if (!key) {
      errorMsg.value = 'لینک اشتراک‌گذاری نامعتبر است.'
      return
    }
    payloadKey.value = key
    const payload: SharedPayload | null = await readSharedPayload(key)
    if (!payload) {
      errorMsg.value = 'محتوای اشتراک‌گذاری یافت نشد یا منقضی شده است.'
      return
    }
    sharedTitle.value = payload.title || ''
    sharedText.value = payload.text || ''
    sharedUrl.value = payload.url || ''
    files.value = payload.files || []
    if (files.value.length === 0 && !mergedText.value) {
      errorMsg.value = 'محتوایی برای اشتراک‌گذاری دریافت نشد.'
      return
    }
    await loadConversations()
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => { /* noop — blobs are released by GC */ })

function handleClose() {
  // User cancelled the picker → go back without sending.
  if (payloadKey.value) {
    void deleteSharedPayload(payloadKey.value)
  }
  if (window.history.length > 1) router.back()
  else router.replace('/')
}

function inferMessageType(mime: string): 'image' | 'video' | 'voice' | 'document' {
  if ((mime || '').startsWith('image/')) return 'image'
  if ((mime || '').startsWith('video/')) return 'video'
  if ((mime || '').startsWith('audio/')) return 'voice'
  return 'document'
}

async function uploadOne(file: SharedFileEntry): Promise<{ file_id: string, file_name: string, mime_type: string, size: number } | null> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
  const token = localStorage.getItem('auth_token') || ''
  const fd = new FormData()
  fd.append('file', file.blob, file.name)
  try {
    const resp = await fetch(`${baseUrl}/api/chat/upload-media`, {
      method: 'POST',
      headers: token ? { 'Authorization': `Bearer ${token}` } : {},
      body: fd,
    })
    if (!resp.ok) return null
    return await resp.json()
  } catch {
    return null
  }
}

async function sendMessageRaw(receiverId: number, type: string, content: string) {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
  const token = localStorage.getItem('auth_token') || ''
  const resp = await fetch(`${baseUrl}/api/chat/send`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ receiver_id: receiverId, content, message_type: type }),
  })
  if (!resp.ok) throw new Error(`send failed (${resp.status})`)
  return resp.json()
}

async function handleTargetsPicked(targets: ChatForwardTarget[]) {
  if (!Array.isArray(targets) || targets.length === 0) return

  const perTarget = files.value.length + (mergedText.value ? 1 : 0)
  totalSendCount.value = perTarget * targets.length

  sending.value = true
  sentCount.value = 0
  sendErrors.value = []
  sendDone.value = false

  // Upload files once, reuse file_ids across targets.
  const uploadedFiles: Array<{ src: SharedFileEntry, meta: { file_id: string, file_name: string, mime_type: string, size: number } }> = []
  for (const f of files.value) {
    const meta = await uploadOne(f)
    if (meta) {
      uploadedFiles.push({ src: f, meta })
    } else {
      sendErrors.value.push(`آپلود فایل ناموفق: ${f.name}`)
    }
  }

  for (const target of targets) {
    for (const { src, meta } of uploadedFiles) {
      try {
        const type = inferMessageType(src.type)
        const content = JSON.stringify({
          file_id: meta.file_id,
          file_name: meta.file_name || src.name,
          mime_type: meta.mime_type || src.type,
          size: meta.size || src.size,
        })
        await sendMessageRaw(target.id, type, content)
      } catch {
        sendErrors.value.push(`ارسال «${src.name}» به ${target.title} ناموفق`)
      } finally {
        sentCount.value++
      }
    }
    if (mergedText.value) {
      try {
        await sendMessageRaw(target.id, 'text', mergedText.value)
      } catch {
        sendErrors.value.push(`ارسال متن به ${target.title} ناموفق`)
      } finally {
        sentCount.value++
      }
    }
  }

  if (payloadKey.value) {
    void deleteSharedPayload(payloadKey.value)
  }

  sending.value = false
  sendDone.value = true

  // If no errors, redirect to last target's chat (single) or messenger home (multi).
  if (sendErrors.value.length === 0) {
    const first = targets[0]
    if (targets.length === 1 && first) {
      router.replace({ path: '/chat', query: { user_id: String(first.id) } })
    } else {
      router.replace({ path: '/chat' })
    }
  }
  // On errors, the result panel stays visible until user taps "بازگشت".
}

function goHome() { router.replace('/') }
</script>

<style scoped>
.share-receive-root {
  min-height: 100vh;
  background: var(--app-bg, #f5f7fb);
}
.state-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 24px;
  text-align: center;
  background: var(--app-bg, #f5f7fb);
  color: var(--app-fg, #1f2937);
  direction: rtl;
  z-index: 10;
}
.state-overlay.error { color: #b91c1c; }
.spinner {
  width: 36px; height: 36px; border-radius: 50%;
  border: 3px solid rgba(51,144,236,0.2);
  border-top-color: #3390ec;
  animation: spin 0.9s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.errors-list {
  list-style: disc;
  padding-inline-start: 20px;
  font-size: 13px;
  color: #92400e;
  max-width: 320px;
}
.primary-btn {
  background: #3390ec; color: #fff; border: none; border-radius: 12px;
  padding: 12px 22px; font-size: 14px; font-weight: 600; cursor: pointer;
}
</style>
