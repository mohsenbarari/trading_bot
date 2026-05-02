<template>
  <div class="share-receive-view">
    <header class="share-header">
      <button class="back-btn" @click="goBack" aria-label="بازگشت">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="19" y1="12" x2="5" y2="12"></line>
          <polyline points="12 19 5 12 12 5"></polyline>
        </svg>
      </button>
      <h2>اشتراک‌گذاری در گفتگو</h2>
    </header>

    <div v-if="loading" class="state-msg">در حال بارگذاری...</div>

    <div v-else-if="errorMsg" class="state-msg error">
      <p>{{ errorMsg }}</p>
      <button class="primary-btn" @click="goHome">بازگشت به خانه</button>
    </div>

    <template v-else>
      <section class="preview" v-if="files.length || mergedText">
        <h3 class="section-title">محتوای دریافتی</h3>

        <div v-if="files.length" class="files-grid">
          <div v-for="(f, i) in files" :key="i" class="file-card">
            <img v-if="f.previewUrl && isImage(f.type)" :src="f.previewUrl" />
            <div v-else class="file-icon">
              <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14 2 14 8 20 8"></polyline>
              </svg>
            </div>
            <div class="file-meta">
              <span class="fname">{{ f.name }}</span>
              <span class="fsize">{{ formatSize(f.size) }}</span>
            </div>
          </div>
        </div>

        <div v-if="mergedText" class="text-preview">{{ mergedText }}</div>
      </section>

      <section class="targets">
        <button class="primary-btn full" @click="openTargetPicker">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
            <circle cx="9" cy="7" r="4"></circle>
            <line x1="19" y1="8" x2="19" y2="14"></line>
            <line x1="22" y1="11" x2="16" y2="11"></line>
          </svg>
          <span>{{ selectedTargets.length ? `${selectedTargets.length} مقصد انتخاب شده — تغییر` : 'انتخاب مقصد' }}</span>
        </button>

        <ul v-if="selectedTargets.length" class="selected-targets-list">
          <li v-for="t in selectedTargets" :key="t.id">
            <span class="t-avatar">{{ avatarInitial(t.title) }}</span>
            <span class="t-name">{{ t.title }}</span>
            <span class="t-mobile">{{ t.subtitle || '' }}</span>
          </li>
        </ul>
      </section>

      <section class="actions">
        <button
          class="primary-btn full big"
          :disabled="!canSend || sending"
          @click="handleSend"
        >
          <span v-if="sending">در حال ارسال... ({{ sentCount }}/{{ totalSendCount }})</span>
          <span v-else>ارسال</span>
        </button>
        <button class="ghost-btn" @click="goBack" :disabled="sending">انصراف</button>
      </section>

      <div v-if="sendErrors.length" class="state-msg warn">
        <p>برخی موارد ارسال نشد:</p>
        <ul>
          <li v-for="(e, i) in sendErrors" :key="i">{{ e }}</li>
        </ul>
      </div>

      <ChatForwardModal
        :showForwardModal="showPicker"
        :sortedConversations="conversations"
        @close="showPicker = false"
        @forward-to="handleTargetsPicked"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiFetchJson } from '../utils/auth'
import { readSharedPayload, deleteSharedPayload, type SharedPayload, type SharedFileEntry } from '../utils/shareTargetStore'
import ChatForwardModal from '../components/chat/ChatForwardModal.vue'
import type { ChatForwardTarget, Conversation } from '../types/chat'

type LocalFile = SharedFileEntry & { previewUrl?: string }

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const errorMsg = ref('')
const payloadKey = ref('')
const files = ref<LocalFile[]>([])
const sharedTitle = ref('')
const sharedText = ref('')
const sharedUrl = ref('')

const conversations = ref<Conversation[]>([])
const showPicker = ref(false)
const selectedTargets = ref<ChatForwardTarget[]>([])

const sending = ref(false)
const sentCount = ref(0)
const sendErrors = ref<string[]>([])

const mergedText = computed(() => {
  const parts: string[] = []
  if (sharedTitle.value) parts.push(sharedTitle.value)
  if (sharedText.value) parts.push(sharedText.value)
  if (sharedUrl.value) parts.push(sharedUrl.value)
  return parts.join('\n').trim()
})

const totalSendCount = computed(() => {
  const perTarget = files.value.length + (mergedText.value ? 1 : 0)
  return perTarget * selectedTargets.value.length
})

const canSend = computed(() => {
  if (selectedTargets.value.length === 0) return false
  return files.value.length > 0 || mergedText.value.length > 0
})

function isImage(mime: string) { return (mime || '').startsWith('image/') }

function formatSize(bytes: number) {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function avatarInitial(name: string) { return name ? name.charAt(0).toUpperCase() : '?' }

async function loadConversations() {
  try {
    const data = await apiFetchJson('/api/chat/conversations') as Conversation[]
    conversations.value = Array.isArray(data) ? data : []
  } catch {
    conversations.value = []
  }
}

function buildPreview(file: SharedFileEntry): LocalFile {
  const out: LocalFile = { ...file }
  if (isImage(file.type)) {
    try { out.previewUrl = URL.createObjectURL(file.blob) } catch { /* noop */ }
  }
  return out
}

function revokePreviews() {
  for (const f of files.value) {
    if (f.previewUrl) {
      try { URL.revokeObjectURL(f.previewUrl) } catch { /* noop */ }
    }
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
    files.value = (payload.files || []).map(buildPreview)
    await loadConversations()
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => { revokePreviews() })

function openTargetPicker() { showPicker.value = true }

function handleTargetsPicked(targets: ChatForwardTarget[]) {
  selectedTargets.value = Array.isArray(targets) ? targets : []
  showPicker.value = false
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

async function handleSend() {
  if (!canSend.value || sending.value) return
  sending.value = true
  sentCount.value = 0
  sendErrors.value = []

  // Upload files once, reuse file_ids across targets to avoid duplicate uploads.
  const uploadedFiles: Array<{ src: SharedFileEntry, meta: { file_id: string, file_name: string, mime_type: string, size: number } }> = []
  for (const f of files.value) {
    const meta = await uploadOne(f)
    if (meta) {
      uploadedFiles.push({ src: f, meta })
    } else {
      sendErrors.value.push(`آپلود فایل ناموفق: ${f.name}`)
    }
  }

  for (const target of selectedTargets.value) {
    // Send each file as a chat message.
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
      } catch (err: any) {
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

  // Cleanup the IDB entry — share is consumed.
  if (payloadKey.value) {
    void deleteSharedPayload(payloadKey.value)
  }

  sending.value = false

  // Navigate user to the last target's chat or to messenger home.
  const last = selectedTargets.value[selectedTargets.value.length - 1]
  if (last && sendErrors.value.length === 0) {
    router.replace({ path: '/chat', query: { user_id: String(last.id) } })
  }
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.replace('/')
}
function goHome() { router.replace('/') }
</script>

<style scoped>
.share-receive-view {
  min-height: 100vh;
  padding: 16px;
  padding-bottom: calc(env(safe-area-inset-bottom, 0) + 24px);
  direction: rtl;
  background: var(--app-bg, #f5f7fb);
  color: var(--app-fg, #1f2937);
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.share-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.share-header h2 { font-size: 17px; margin: 0; }
.back-btn {
  background: transparent; border: none; cursor: pointer; color: inherit;
  padding: 6px; border-radius: 50%;
}
.section-title { font-size: 14px; opacity: 0.75; margin: 0 0 8px; }
.preview { background: rgba(0,0,0,0.04); border-radius: 14px; padding: 12px; }
.files-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
}
.file-card {
  background: #fff; border-radius: 12px; padding: 8px; display: flex; flex-direction: column;
  gap: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); overflow: hidden;
}
.file-card img {
  width: 100%; aspect-ratio: 1 / 1; object-fit: cover; border-radius: 8px; background: #eee;
}
.file-icon {
  width: 100%; aspect-ratio: 1 / 1; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  background: #eef2ff; color: #3b5bdb;
}
.file-meta { display: flex; flex-direction: column; gap: 2px; }
.fname { font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fsize { font-size: 11px; opacity: 0.65; }
.text-preview {
  margin-top: 10px; padding: 10px 12px; background: #fff; border-radius: 10px;
  font-size: 13px; white-space: pre-wrap; word-break: break-word;
}
.targets { display: flex; flex-direction: column; gap: 10px; }
.selected-targets-list {
  list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px;
}
.selected-targets-list li {
  display: flex; align-items: center; gap: 10px;
  background: #fff; border-radius: 10px; padding: 8px 12px;
  font-size: 13px;
}
.t-avatar {
  width: 32px; height: 32px; border-radius: 50%; background: #3390ec; color: #fff;
  display: inline-flex; align-items: center; justify-content: center; font-weight: 700;
}
.t-name { flex: 1; font-weight: 600; }
.t-mobile { font-size: 12px; opacity: 0.6; direction: ltr; }
.actions { display: flex; gap: 10px; align-items: center; margin-top: 6px; }
.primary-btn {
  background: #3390ec; color: #fff; border: none; border-radius: 12px;
  padding: 10px 18px; font-size: 14px; font-weight: 600; cursor: pointer;
  display: inline-flex; align-items: center; gap: 8px; justify-content: center;
}
.primary-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.primary-btn.full { width: 100%; }
.primary-btn.big { padding: 14px 18px; font-size: 15px; }
.ghost-btn {
  background: transparent; border: 1px solid rgba(0,0,0,0.15); color: inherit;
  border-radius: 12px; padding: 12px 18px; font-size: 14px; cursor: pointer;
}
.ghost-btn:disabled { opacity: 0.5; }
.state-msg { padding: 14px; border-radius: 12px; background: #fff; }
.state-msg.error { color: #b91c1c; }
.state-msg.warn { color: #92400e; background: #fffbeb; }
.state-msg ul { margin: 8px 0 0; padding-inline-start: 18px; font-size: 13px; }
</style>
