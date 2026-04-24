<script setup lang="ts">
// GalleryPreviewModal — Phase B
// Telegram-style preview sheet for multi-image gallery picks.
// Shows thumbnails for each selected file, lets the user:
//   - Tap an image thumbnail (or the pencil) to open the crop editor
//   - Tap × to remove an item
//   - Tap send to dispatch the remaining items as an album
//
// Videos are previewed only (no editor in Phase B). HEIC files pass through
// as-is (normalize happens later in useChatMedia).
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from 'vue'

const ImageEditorModal = defineAsyncComponent(() => import('./ImageEditorModal.vue'))

interface PreviewItem {
  id: string
  file: File
  previewUrl: string
  isVideo: boolean
  isEditable: boolean
}

const props = defineProps<{
  files: File[]
}>()

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'confirm', files: File[]): void
}>()

const items = ref<PreviewItem[]>([])
const editingItemId = ref<string | null>(null)

function toItem(file: File): PreviewItem {
  const id = (globalThis.crypto?.randomUUID?.() ?? `p_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`)
  const isVideo = file.type.startsWith('video/')
  const isHeic =
    file.type === 'image/heic' ||
    file.type === 'image/heif' ||
    /\.(heic|heif)$/i.test(file.name)
  // HEIC can't be rendered by Cropper directly in this phase; treat as
  // non-editable so we avoid a broken editor state.
  const isEditable = !isVideo && !isHeic && file.type.startsWith('image/')
  return {
    id,
    file,
    previewUrl: URL.createObjectURL(file),
    isVideo,
    isEditable,
  }
}

function hydrateFromProps() {
  // Revoke old URLs before replacing
  items.value.forEach((it) => URL.revokeObjectURL(it.previewUrl))
  items.value = props.files.map(toItem)
}

hydrateFromProps()
watch(() => props.files, hydrateFromProps)

onBeforeUnmount(() => {
  items.value.forEach((it) => URL.revokeObjectURL(it.previewUrl))
})

const editingItem = computed<PreviewItem | null>(() => {
  if (!editingItemId.value) return null
  return items.value.find((it) => it.id === editingItemId.value) ?? null
})

function editItem(item: PreviewItem) {
  if (!item.isEditable) return
  editingItemId.value = item.id
}

function removeItem(id: string) {
  const idx = items.value.findIndex((it) => it.id === id)
  if (idx < 0) return
  const [removed] = items.value.splice(idx, 1)
  if (removed) URL.revokeObjectURL(removed.previewUrl)
  if (items.value.length === 0) {
    // Nothing left → close as cancel so caller doesn't receive an empty album.
    emit('cancel')
  }
}

function onEditorConfirm(editedFile: File) {
  const id = editingItemId.value
  editingItemId.value = null
  if (!id) return
  const idx = items.value.findIndex((it) => it.id === id)
  if (idx < 0) return
  const old = items.value[idx]
  if (!old) return
  URL.revokeObjectURL(old.previewUrl)
  items.value[idx] = {
    ...old,
    file: editedFile,
    previewUrl: URL.createObjectURL(editedFile),
  }
}

function onEditorCancel() {
  editingItemId.value = null
}

function sendAll() {
  if (items.value.length === 0) {
    emit('cancel')
    return
  }
  emit('confirm', items.value.map((it) => it.file))
}

function cancelAll() {
  emit('cancel')
}
</script>

<template>
  <teleport to="body">
    <div class="gallery-preview-overlay" role="dialog" aria-modal="true">
      <div class="gp-top-bar">
        <button class="gp-top-btn" @click="cancelAll" aria-label="انصراف">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M6 18L18 6"/></svg>
        </button>
        <div class="gp-title">{{ items.length }} مورد</div>
        <div style="width: 40px;"></div>
      </div>

      <div class="gp-stage">
        <div class="gp-grid">
          <div
            v-for="item in items"
            :key="item.id"
            class="gp-cell"
            :class="{ editable: item.isEditable }"
            @click="editItem(item)"
          >
            <img
              v-if="!item.isVideo"
              :src="item.previewUrl"
              alt="preview"
              class="gp-media"
              draggable="false"
            />
            <video
              v-else
              :src="item.previewUrl"
              class="gp-media"
              muted
              loop
              playsinline
              autoplay
            ></video>

            <span v-if="item.isVideo" class="gp-video-badge">ویدئو</span>
            <span v-else-if="item.isEditable" class="gp-edit-badge" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            </span>

            <button
              class="gp-remove"
              type="button"
              aria-label="حذف"
              @click.stop="removeItem(item.id)"
            >×</button>
          </div>
        </div>
      </div>

      <div class="gp-actions">
        <button class="gp-send" @click="sendAll" :disabled="items.length === 0">
          ارسال {{ items.length }} مورد
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 6px;">
            <line x1="22" y1="2" x2="11" y2="13"></line>
            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
          </svg>
        </button>
      </div>

      <!-- Per-item editor -->
      <ImageEditorModal
        v-if="editingItem"
        :file="editingItem.file"
        @confirm="onEditorConfirm"
        @cancel="onEditorCancel"
      />
    </div>
  </teleport>
</template>

<style scoped>
.gallery-preview-overlay {
  position: fixed;
  inset: 0;
  background: #000;
  z-index: 9998;
  display: flex;
  flex-direction: column;
  color: #fff;
  direction: rtl;
}

.gp-top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: env(safe-area-inset-top, 0) 12px 0 12px;
  height: calc(env(safe-area-inset-top, 0) + 52px);
  flex-shrink: 0;
}

.gp-title {
  font-size: 15px;
  font-weight: 600;
}

.gp-top-btn {
  background: transparent;
  border: none;
  color: #fff;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  cursor: pointer;
}
.gp-top-btn:active { background: rgba(255, 255, 255, 0.08); }

.gp-stage {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 10px;
}

.gp-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}

@media (min-width: 480px) {
  .gp-grid { grid-template-columns: repeat(4, 1fr); }
}

.gp-cell {
  position: relative;
  aspect-ratio: 1;
  border-radius: 10px;
  overflow: hidden;
  background: #111;
  cursor: default;
}
.gp-cell.editable { cursor: pointer; }

.gp-media {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  user-select: none;
}

.gp-remove {
  position: absolute;
  top: 4px;
  left: 4px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
  font-size: 18px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
}
.gp-remove:active { background: rgba(0, 0, 0, 0.85); }

.gp-edit-badge {
  position: absolute;
  bottom: 4px;
  right: 4px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  pointer-events: none;
}

.gp-video-badge {
  position: absolute;
  bottom: 4px;
  right: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  padding: 2px 6px;
  border-radius: 8px;
  font-size: 10px;
}

.gp-actions {
  padding: 10px 12px calc(env(safe-area-inset-bottom, 0) + 12px);
  flex-shrink: 0;
  background: rgba(0, 0, 0, 0.4);
}

.gp-send {
  width: 100%;
  padding: 13px 16px;
  border-radius: 12px;
  background: #3390ec;
  color: #fff;
  border: none;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.gp-send:disabled { opacity: 0.5; cursor: default; }
.gp-send:active:not(:disabled) { background: #2a7fd4; }
</style>
