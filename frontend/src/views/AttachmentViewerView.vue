<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getCachedAttachmentBlob,
  getLiveAttachmentUrl,
  putCachedAttachmentBlob,
  restoreCachedAttachmentUrl,
  setLiveAttachmentUrl,
} from '../utils/chatAttachmentCache'

const route = useRoute()
const router = useRouter()

const fileUrl = ref('')
const attachmentBlob = ref<Blob | null>(null)
const textContent = ref('')
const isLoading = ref(false)
const errorMessage = ref('')
const actionMessage = ref('')

const fileId = computed(() => typeof route.query.file_id === 'string' ? route.query.file_id : '')
const mimeType = computed(() => typeof route.query.mime_type === 'string' ? route.query.mime_type : 'application/octet-stream')
const fileName = computed(() => typeof route.query.file_name === 'string' ? route.query.file_name : 'file')

const fileExtension = computed(() => {
  const name = fileName.value.trim().toLowerCase()
  const parts = name.split('.')
  return parts.length > 1 ? parts.pop() || '' : ''
})

function inferEffectiveMimeType(rawMimeType: string, extension: string) {
  const normalizedMime = (rawMimeType || '').trim().toLowerCase()
  const normalizedExt = (extension || '').trim().toLowerCase()

  if (normalizedMime && normalizedMime !== 'application/octet-stream') {
    return normalizedMime
  }

  switch (normalizedExt) {
    case 'pdf':
      return 'application/pdf'
    case 'txt':
    case 'log':
    case 'md':
      return 'text/plain'
    case 'json':
      return 'application/json'
    case 'xml':
      return 'application/xml'
    case 'csv':
      return 'text/csv'
    case 'jpg':
    case 'jpeg':
      return 'image/jpeg'
    case 'png':
      return 'image/png'
    case 'gif':
      return 'image/gif'
    case 'webp':
      return 'image/webp'
    case 'mp4':
      return 'video/mp4'
    case 'webm':
      return 'video/webm'
    case 'mp3':
      return 'audio/mpeg'
    case 'wav':
      return 'audio/wav'
    case 'ogg':
      return 'audio/ogg'
    case 'm4a':
      return 'audio/mp4'
    case 'xls':
      return 'application/vnd.ms-excel'
    case 'xlsx':
      return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    default:
      return normalizedMime || 'application/octet-stream'
  }
}

const effectiveMimeType = computed(() => inferEffectiveMimeType(mimeType.value, fileExtension.value))

const isImage = computed(() => effectiveMimeType.value.startsWith('image/'))
const isVideo = computed(() => effectiveMimeType.value.startsWith('video/'))
const isAudio = computed(() => effectiveMimeType.value.startsWith('audio/'))
const isPdf = computed(() => effectiveMimeType.value === 'application/pdf')
const isText = computed(() => {
  return effectiveMimeType.value.startsWith('text/')
    || effectiveMimeType.value.includes('json')
    || effectiveMimeType.value.includes('xml')
    || effectiveMimeType.value.includes('javascript')
  })
const canPreviewInline = computed(() => {
  return isImage.value || isVideo.value || isAudio.value || isPdf.value || isText.value
})
const supportsSystemShare = computed(() => {
  return typeof navigator !== 'undefined' && typeof navigator.share === 'function'
})

function goBack() {
  if (window.history.length > 1) {
    router.back()
    return
  }

  router.push('/chat')
}

function triggerDownload(url: string, fileNameValue: string) {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileNameValue
  anchor.rel = 'noopener'
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
}

async function resolveAttachmentBlobForActions() {
  if (attachmentBlob.value) return attachmentBlob.value

  if (fileId.value) {
    const cachedBlob = await getCachedAttachmentBlob(fileId.value)
    if (cachedBlob) {
      attachmentBlob.value = cachedBlob
      return cachedBlob
    }
  }

  if (fileUrl.value) {
    try {
      const blob = await fetch(fileUrl.value).then(res => res.blob())
      attachmentBlob.value = blob
      if (fileId.value) {
        void putCachedAttachmentBlob(fileId.value, blob)
      }
      return blob
    } catch {
      /* ignore */
    }
  }

  if (!fileId.value) return null

  try {
    const token = localStorage.getItem('auth_token') || ''
    const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
    const response = await fetch(`${baseUrl}/api/chat/files/${encodeURIComponent(fileId.value)}?token=${encodeURIComponent(token)}`)
    if (!response.ok) return null
    const blob = await response.blob()
    attachmentBlob.value = blob
    void putCachedAttachmentBlob(fileId.value, blob)
    return blob
  } catch {
    return null
  }
}

function canShareFile(file: File) {
  if (typeof navigator === 'undefined' || typeof navigator.share !== 'function') {
    return false
  }

  const nav = navigator as Navigator & { canShare?: (data?: ShareData) => boolean }
  if (typeof nav.canShare === 'function') {
    try {
      return nav.canShare({ files: [file] })
    } catch {
      return false
    }
  }

  return true
}

async function shareToDeviceApps() {
  actionMessage.value = ''

  const blob = await resolveAttachmentBlobForActions()
  if (!blob) {
    actionMessage.value = 'فایل برای ارسال به برنامه‌های دستگاه در دسترس نیست.'
    return
  }

  const shareFile = new File([blob], fileName.value || 'file', {
    type: effectiveMimeType.value || blob.type || 'application/octet-stream',
    lastModified: Date.now(),
  })

  if (!canShareFile(shareFile)) {
    actionMessage.value = 'مرورگر یا دستگاه شما باز کردن فایل با برنامه‌های دستگاه را از طریق Web Share API پشتیبانی نمی‌کند.'
    return
  }

  try {
    await navigator.share({
      title: fileName.value,
      text: fileName.value,
      files: [shareFile],
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      return
    }

    console.error('Attachment share failed:', error)
    actionMessage.value = 'ارسال فایل به برنامه‌های دستگاه ناموفق بود.'
  }
}

function openInBrowser() {
  if (!fileUrl.value) return
  const anchor = document.createElement('a')
  anchor.href = fileUrl.value
  anchor.target = '_blank'
  anchor.rel = 'noopener noreferrer'
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
}

async function resolveFileUrl() {
  if (!fileId.value) {
    errorMessage.value = 'شناسه فایل نامعتبر است.'
    return
  }

  isLoading.value = true
  errorMessage.value = ''
  actionMessage.value = ''
  textContent.value = ''
  attachmentBlob.value = null

  try {
    let resolvedBlob = await getCachedAttachmentBlob(fileId.value)
    let nextUrl = getLiveAttachmentUrl(fileId.value)

    if (!nextUrl && resolvedBlob) {
      nextUrl = URL.createObjectURL(resolvedBlob)
      setLiveAttachmentUrl(fileId.value, nextUrl)
    }

    if (!nextUrl) {
      nextUrl = await restoreCachedAttachmentUrl(fileId.value)
    }

    if (!nextUrl) {
      const token = localStorage.getItem('auth_token') || ''
      const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
      const response = await fetch(`${baseUrl}/api/chat/files/${encodeURIComponent(fileId.value)}?token=${encodeURIComponent(token)}`)
      if (!response.ok) {
        throw new Error(`File fetch failed (${response.status})`)
      }

      resolvedBlob = await response.blob()
      nextUrl = URL.createObjectURL(resolvedBlob)
      setLiveAttachmentUrl(fileId.value, nextUrl)
      void putCachedAttachmentBlob(fileId.value, resolvedBlob)
    }

    fileUrl.value = nextUrl
    attachmentBlob.value = resolvedBlob || null

    if (isText.value) {
      const textBlob = resolvedBlob || await getCachedAttachmentBlob(fileId.value) || await fetch(nextUrl).then(res => res.blob())
      if (textBlob) {
        attachmentBlob.value = textBlob
        textContent.value = await textBlob.text()
      }
    }
  } catch (error) {
    console.error('Attachment viewer failed:', error)
    errorMessage.value = 'باز کردن فایل ناموفق بود.'
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  void resolveFileUrl()
})

watch([fileId, mimeType], () => {
  void resolveFileUrl()
})
</script>

<template>
  <div class="attachment-viewer-page">
    <header class="top-nav">
      <button class="back-btn" @click="goBack" aria-label="بازگشت">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="19" y1="12" x2="5" y2="12"></line>
          <polyline points="12 19 5 12 12 5"></polyline>
        </svg>
      </button>
      <div class="title-wrap">
        <h1 class="title">{{ fileName }}</h1>
        <p class="subtitle">{{ effectiveMimeType }}</p>
      </div>
      <button class="download-btn" :disabled="!fileUrl" @click="fileUrl && triggerDownload(fileUrl, fileName)">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="7 10 12 15 17 10"></polyline>
          <line x1="12" y1="15" x2="12" y2="3"></line>
        </svg>
      </button>
    </header>

    <main class="viewer-shell">
      <div v-if="isLoading" class="state-card muted">در حال آماده‌سازی فایل...</div>
      <div v-else-if="errorMessage" class="state-card error">{{ errorMessage }}</div>

      <template v-else>
        <img v-if="isImage && fileUrl" :src="fileUrl" :alt="fileName" class="media-image" />

        <video v-else-if="isVideo && fileUrl" :src="fileUrl" class="media-video" controls playsinline />

        <audio v-else-if="isAudio && fileUrl" :src="fileUrl" class="media-audio" controls />

        <object v-else-if="isPdf && fileUrl" :data="fileUrl" type="application/pdf" class="pdf-frame">
          <div class="state-card unsupported inline-fallback">
            <h2>پیش‌نمایش PDF در این مرورگر در دسترس نیست</h2>
            <p>می‌توانید فایل را با برنامه‌های دستگاه باز کنید، در مرورگر باز کنید یا دانلود کنید.</p>
            <div class="unsupported-actions">
              <button v-if="supportsSystemShare" class="primary-btn" :disabled="!fileUrl" @click="shareToDeviceApps">باز کردن با برنامه‌های دستگاه</button>
              <button class="secondary-btn" :disabled="!fileUrl" @click="openInBrowser">باز کردن در مرورگر</button>
              <button class="primary-btn" :disabled="!fileUrl" @click="fileUrl && triggerDownload(fileUrl, fileName)">دانلود</button>
            </div>
            <p v-if="actionMessage" class="action-message">{{ actionMessage }}</p>
          </div>
        </object>

        <pre v-else-if="isText" class="text-preview">{{ textContent }}</pre>

        <div v-else class="state-card unsupported">
          <div class="unsupported-icon">
            <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
            </svg>
          </div>
          <h2>پیش‌نمایش در مرورگر موجود نیست</h2>
          <p>فایل داخل حافظه‌ی محلی اپ موجود است. اگر دستگاه و مرورگر پشتیبانی کنند می‌توانید آن را به برنامه‌های مناسب دستگاه بسپارید، وگرنه باز کردن در مرورگر یا دانلود در دسترس است.</p>
          <div class="unsupported-actions">
            <button v-if="supportsSystemShare" class="primary-btn" :disabled="!fileUrl" @click="shareToDeviceApps">باز کردن با برنامه‌های دستگاه</button>
            <button class="secondary-btn" :disabled="!fileUrl" @click="openInBrowser">باز کردن در مرورگر</button>
            <button class="secondary-btn" :disabled="!fileUrl" @click="fileUrl && triggerDownload(fileUrl, fileName)">دانلود</button>
          </div>
          <p v-if="actionMessage" class="action-message">{{ actionMessage }}</p>
        </div>
      </template>
    </main>
  </div>
</template>

<style scoped>
.attachment-viewer-page {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(circle at top, rgba(251, 191, 36, 0.18), transparent 30%),
    linear-gradient(180deg, #fffdf7 0%, #f8fafc 100%);
}

.top-nav {
  position: sticky;
  top: 0;
  z-index: 20;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 0.75rem;
  padding: 0.9rem 1rem;
  background: rgba(255, 255, 255, 0.82);
  backdrop-filter: blur(14px);
  border-bottom: 1px solid rgba(226, 232, 240, 0.9);
}

.back-btn,
.download-btn {
  width: 2.4rem;
  height: 2.4rem;
  border: none;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #ffffff;
  color: #0f172a;
  box-shadow: 0 10px 25px rgba(15, 23, 42, 0.08);
}

.download-btn:disabled {
  opacity: 0.45;
}

.title-wrap {
  min-width: 0;
}

.title {
  margin: 0;
  font-size: 0.98rem;
  font-weight: 800;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.subtitle {
  margin: 0.15rem 0 0;
  font-size: 0.72rem;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.viewer-shell {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
}

.state-card {
  width: min(100%, 680px);
  padding: 1.4rem;
  border-radius: 1.25rem;
  text-align: center;
  background: rgba(255, 255, 255, 0.84);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.08);
  border: 1px solid rgba(226, 232, 240, 0.9);
}

.state-card.muted {
  color: #475569;
}

.state-card.error {
  color: #b91c1c;
  background: rgba(254, 242, 242, 0.92);
}

.state-card.unsupported h2 {
  margin: 0.75rem 0 0.5rem;
  font-size: 1rem;
  font-weight: 800;
  color: #0f172a;
}

.state-card.unsupported p {
  margin: 0;
  color: #475569;
  line-height: 1.7;
}

.action-message {
  margin-top: 0.9rem !important;
  font-size: 0.82rem;
  color: #92400e !important;
}

.unsupported-icon {
  width: 4rem;
  height: 4rem;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 1.25rem;
  background: linear-gradient(135deg, #f8fafc, #e2e8f0);
  color: #334155;
}

.unsupported-actions {
  display: flex;
  gap: 0.75rem;
  justify-content: center;
  margin-top: 1rem;
  flex-wrap: wrap;
}

.primary-btn,
.secondary-btn {
  min-width: 9rem;
  border-radius: 999px;
  padding: 0.8rem 1rem;
  font-weight: 700;
  border: none;
}

.primary-btn {
  background: #0f172a;
  color: white;
}

.secondary-btn {
  background: white;
  color: #0f172a;
  border: 1px solid #cbd5e1;
}

.media-image,
.media-video,
.pdf-frame,
.text-preview {
  width: min(100%, 980px);
  max-width: 100%;
  border-radius: 1.25rem;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.08);
  border: 1px solid rgba(226, 232, 240, 0.9);
}

.media-image {
  max-height: calc(100dvh - 7rem);
  object-fit: contain;
}

.media-video {
  max-height: calc(100dvh - 7rem);
}

.media-audio {
  width: min(100%, 720px);
}

.pdf-frame {
  height: calc(100dvh - 7rem);
}

.text-preview {
  min-height: calc(100dvh - 7rem);
  padding: 1rem;
  overflow: auto;
  white-space: pre-wrap;
  direction: ltr;
  text-align: left;
  font-size: 0.86rem;
  line-height: 1.7;
  color: #0f172a;
}
</style>