<script setup lang="ts">
// ImageEditorModal — Phase A MVP
// Crop-only image editor based on Cropper.js. Invoked from AttachmentMenu
// when the user picks a single image from the gallery. Emits either a new
// File (cropped result) or the original File if the user taps "Send without
// edit".
//
// Why single-image only in Phase A:
//   - Multi-image (album) edit UI needs a preview grid with per-item pencil
//     button (Telegram-style), which is a bigger UX change. Phase A just
//     proves the pipeline end-to-end without touching album dispatch.
//
// Integration contract:
//   - Props:   file: File           (must be image/*)
//   - Emits:   'cancel'            user dismissed the sheet entirely
//              'confirm', file     user confirmed (either cropped or original)
import { onBeforeUnmount, onMounted, ref } from 'vue'
// Cropper.js v1.6 ships its own CSS. We import it lazily via dynamic import
// inside onMounted so the ~40KB payload is only fetched when the editor is
// actually opened. Without this the main Messenger bundle would grow by the
// cropper weight even for users who never edit images.
import 'cropperjs/dist/cropper.css'

interface Props {
  file: File
}
const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'confirm', file: File): void
}>()

const imgRef = ref<HTMLImageElement | null>(null)
const sourceUrl = ref<string>(URL.createObjectURL(props.file))
const isProcessing = ref(false)
const aspectRatio = ref<number | undefined>(undefined) // undefined = free

// Preset aspect ratios mirror Telegram's options
const ratioOptions: { label: string; value: number | undefined }[] = [
  { label: 'آزاد', value: undefined },
  { label: '۱:۱', value: 1 },
  { label: '۴:۵', value: 4 / 5 },
  { label: '۱۶:۹', value: 16 / 9 },
  { label: '۹:۱۶', value: 9 / 16 },
  { label: '۳:۴', value: 3 / 4 },
  { label: '۴:۳', value: 4 / 3 },
]

let cropperInstance: any = null

onMounted(async () => {
  // Lazy-load Cropper so its ~40KB only lands in the chunk when the editor
  // actually opens. This preserves the main Messenger bundle size budget.
  const CropperModule = await import('cropperjs')
  const Cropper = CropperModule.default
  if (!imgRef.value) return

  cropperInstance = new Cropper(imgRef.value, {
    viewMode: 1,
    autoCropArea: 1,
    dragMode: 'move',
    background: false,
    // Mobile-friendly defaults
    movable: true,
    zoomable: true,
    scalable: false,
    rotatable: true,
    // Deliberate: no free-form canvas movement outside the image bounds.
    restore: false,
    // Limit memory/perf on mobile by capping canvas to displayed size.
    // Cropper internally creates an offscreen canvas matching the source
    // image; no way to force-scale here without quality loss. We rely on
    // useChatMedia's downstream compression.
    aspectRatio: aspectRatio.value,
    ready() {
      // Nothing yet — placeholder for a future "entry animation" hook.
    },
  })
})

onBeforeUnmount(() => {
  try {
    cropperInstance?.destroy?.()
  } catch {
    /* ignore */
  }
  if (sourceUrl.value.startsWith('blob:')) {
    URL.revokeObjectURL(sourceUrl.value)
  }
})

function applyAspect(value: number | undefined) {
  aspectRatio.value = value
  try {
    cropperInstance?.setAspectRatio?.(value ?? NaN)
  } catch {
    /* ignore */
  }
}

function rotate(delta: number) {
  try {
    cropperInstance?.rotate?.(delta)
  } catch {
    /* ignore */
  }
}

function reset() {
  try {
    cropperInstance?.reset?.()
  } catch {
    /* ignore */
  }
}

async function confirm() {
  if (isProcessing.value) return
  isProcessing.value = true
  try {
    const canvas: HTMLCanvasElement | null =
      cropperInstance?.getCroppedCanvas?.({
        // Bound the exported canvas so mobile devices don't blow up memory
        // when the user crops a 12MP photo. useChatMedia will further scale
        // the output to 1920px max during its EXIF-safe compression pass.
        maxWidth: 4096,
        maxHeight: 4096,
        imageSmoothingEnabled: true,
        imageSmoothingQuality: 'high',
      }) ?? null

    if (!canvas) {
      // Cropper failed to produce a canvas — fall back to original.
      emit('confirm', props.file)
      return
    }

    const blob: Blob | null = await new Promise((resolve) => {
      try {
        canvas.toBlob(
          (b) => resolve(b),
          // Re-encode as JPEG to keep size reasonable. PNG can 10x the
          // payload for photos. useChatMedia will still pass through its
          // own compression later.
          'image/jpeg',
          0.92,
        )
      } catch {
        resolve(null)
      }
    })

    if (!blob) {
      emit('confirm', props.file)
      return
    }

    // Preserve the original filename stem so the file feels the same in the
    // conversation, but force .jpg extension since we re-encoded.
    const originalName = props.file.name || 'image.jpg'
    const stem = originalName.replace(/\.[^.]+$/, '')
    const edited = new File([blob], `${stem}_edited.jpg`, {
      type: 'image/jpeg',
      lastModified: Date.now(),
    })
    emit('confirm', edited)
  } finally {
    isProcessing.value = false
  }
}

function sendUnedited() {
  emit('confirm', props.file)
}

function cancel() {
  emit('cancel')
}
</script>

<template>
  <div class="image-editor-overlay" role="dialog" aria-modal="true">
    <!-- Top bar -->
    <div class="top-bar">
      <button class="top-btn" @click="cancel" aria-label="انصراف">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M6 18L18 6"/></svg>
      </button>
      <div class="top-title">ویرایش تصویر</div>
      <button class="top-btn" @click="reset" aria-label="بازنشانی" title="بازنشانی">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
      </button>
    </div>

    <!-- Cropper canvas area -->
    <div class="stage">
      <img ref="imgRef" :src="sourceUrl" alt="editing" style="max-width: 100%;" />
    </div>

    <!-- Aspect ratio chips -->
    <div class="ratios">
      <button
        v-for="opt in ratioOptions"
        :key="opt.label"
        class="ratio-chip"
        :class="{ active: aspectRatio === opt.value }"
        @click="applyAspect(opt.value)"
      >{{ opt.label }}</button>
    </div>

    <!-- Bottom toolbar -->
    <div class="toolbar">
      <button class="tool-btn" @click="rotate(-90)" aria-label="چرخش چپ">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
        <span>چرخش</span>
      </button>
      <button class="tool-btn" @click="rotate(90)" aria-label="چرخش راست">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transform: scaleX(-1);"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
        <span>چرخش</span>
      </button>
    </div>

    <!-- Action row -->
    <div class="actions">
      <button class="action-secondary" @click="sendUnedited">ارسال بدون ویرایش</button>
      <button class="action-primary" @click="confirm" :disabled="isProcessing">
        {{ isProcessing ? 'در حال پردازش...' : 'تایید و ارسال' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.image-editor-overlay {
  position: fixed;
  inset: 0;
  background: #000;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  color: #fff;
  direction: rtl;
}

.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: env(safe-area-inset-top, 0) 12px 0 12px;
  height: calc(env(safe-area-inset-top, 0) + 52px);
  flex-shrink: 0;
}

.top-title {
  font-size: 15px;
  font-weight: 600;
}

.top-btn {
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
.top-btn:active { background: rgba(255, 255, 255, 0.08); }

.stage {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  padding: 8px;
}

/* Cropper internals expect a block-level container */
.stage :deep(.cropper-container) {
  direction: ltr; /* Cropper internals are LTR; override parent RTL */
}

.ratios {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  overflow-x: auto;
  flex-shrink: 0;
  scrollbar-width: none;
}
.ratios::-webkit-scrollbar { display: none; }

.ratio-chip {
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  border: 1px solid transparent;
  border-radius: 18px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}
.ratio-chip.active {
  background: #3390ec;
  border-color: #3390ec;
}

.toolbar {
  display: flex;
  justify-content: center;
  gap: 18px;
  padding: 6px 12px 8px;
  flex-shrink: 0;
}

.tool-btn {
  background: transparent;
  border: none;
  color: #fff;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  font-size: 11px;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 8px;
}
.tool-btn:active { background: rgba(255, 255, 255, 0.08); }

.actions {
  display: flex;
  gap: 8px;
  padding: 10px 12px calc(env(safe-area-inset-bottom, 0) + 12px);
  flex-shrink: 0;
  background: rgba(0, 0, 0, 0.4);
}

.action-secondary,
.action-primary {
  flex: 1;
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border: none;
}

.action-secondary {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}
.action-secondary:active { background: rgba(255, 255, 255, 0.18); }

.action-primary {
  background: #3390ec;
  color: #fff;
}
.action-primary:disabled {
  opacity: 0.6;
  cursor: default;
}
.action-primary:active:not(:disabled) { background: #2a7fd4; }
</style>
