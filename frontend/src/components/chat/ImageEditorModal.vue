<script setup lang="ts">
// ImageEditorModal — Phase C
// Two-step image editor: Crop (Cropper.js) → Annotate (Fabric.js).
//
// Flow:
//   - Default mode = 'crop'. User crops/rotates with Cropper.js exactly like
//     Phase A.
//   - User taps "نقاشی" or "متن" → cropper is finalized into a static base
//     canvas, fabric is initialized on top, and from that point crop is
//     locked (going back would drop annotations).
//   - In annotate mode the user can:
//       * Free-draw with brush (color + size palette)
//       * Add editable text (IText) — tap the canvas
//       * Undo last action
//   - On confirm:
//       * If fabric was activated → export fabric canvas as JPEG
//       * Otherwise → export cropper canvas as JPEG
//
// Why this design:
//   - Cropper.js does crop/rotate well; rebuilding that in fabric is
//     unnecessary work.
//   - Fabric.js handles draw + IText natively with object-based history.
//   - Both libs are lazy-imported (cropper ~40KB, fabric ~150KB) so the
//     Messenger bundle isn't bloated for users who never edit.
//
// Integration contract is unchanged from Phase A:
//   - Props: file: File
//   - Emits: 'cancel' | 'confirm' (file: File)
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import 'cropperjs/dist/cropper.css'

interface Props {
  file: File
}
const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'confirm', file: File): void
}>()

// --- Crop state ---
const imgRef = ref<HTMLImageElement | null>(null)
const sourceUrl = ref<string>(URL.createObjectURL(props.file))
const aspectRatio = ref<number | undefined>(undefined)

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
let CropperCtor: any = null
const imgLoaded = ref(false)
const loadError = ref<string | null>(null)

// --- Annotate state ---
type Mode = 'crop' | 'draw' | 'text'
const mode = ref<Mode>('crop')
const annotateStarted = ref(false)
const fabricCanvasRef = ref<HTMLCanvasElement | null>(null)
const fabricStageRef = ref<HTMLDivElement | null>(null)
let fabricCanvas: any = null
let fabricLib: any = null

// Text mode is one-shot per tab activation: each click on the «متن» tab
// allows creating exactly one new text. After it's placed, further taps on
// empty space just deselect (the user must re-tap the tab to add another).
// This matches the user's request and prevents stray duplicate texts when
// dragging existing ones.
const pendingTextCreation = ref(false)

const PALETTE = ['#ffffff', '#000000', '#ff3b30', '#ff9500', '#ffcc00', '#34c759', '#0a84ff']
const brushColor = ref<string>('#ff3b30')
const BRUSH_SIZES = [4, 8, 14]
const brushSize = ref<number>(BRUSH_SIZES[1] ?? 8)
const textColor = ref<string>('#ffffff')

const undoStack = ref<string[]>([])
const canUndo = computed(() => undoStack.value.length > 0)

const isProcessing = ref(false)

onMounted(async () => {
  // Pre-load the Cropper class so it's ready when the <img> finishes loading.
  try {
    const CropperModule = await import('cropperjs')
    CropperCtor = (CropperModule as any).default ?? CropperModule
  } catch (e) {
    loadError.value = 'بارگذاری ویرایشگر ناموفق بود'
    return
  }
  // If the image already loaded before the chunk arrived, init now.
  if (imgLoaded.value) initCropper()
})

function initCropper() {
  if (cropperInstance || !CropperCtor || !imgRef.value) return
  try {
    cropperInstance = new CropperCtor(imgRef.value, {
      viewMode: 1,
      autoCropArea: 1,
      dragMode: 'move',
      background: false,
      movable: true,
      zoomable: true,
      scalable: false,
      rotatable: true,
      restore: false,
      // EXIF rotation is already handled server-side (Pillow exif_transpose)
      // and client-side (createImageBitmap imageOrientation) before the file
      // ever reaches this editor. Cropper's own checkOrientation does an
      // extra XHR on the blob URL that can fail silently on some mobile
      // browsers and leaves the editor non-functional. Disabling it.
      checkOrientation: false,
      checkCrossOrigin: false,
      aspectRatio: aspectRatio.value,
    })
  } catch {
    loadError.value = 'باز کردن تصویر در ویرایشگر ناموفق بود'
  }
}

function onImgLoad() {
  imgLoaded.value = true
  loadError.value = null
  initCropper()
}

function onImgError() {
  imgLoaded.value = false
  loadError.value = 'این فرمت تصویر پشتیبانی نمی‌شود'
}

onBeforeUnmount(() => {
  try { cropperInstance?.destroy?.() } catch { /* ignore */ }
  try { fabricCanvas?.dispose?.() } catch { /* ignore */ }
  if (sourceUrl.value.startsWith('blob:')) URL.revokeObjectURL(sourceUrl.value)
})

function applyAspect(value: number | undefined) {
  aspectRatio.value = value
  try { cropperInstance?.setAspectRatio?.(value ?? NaN) } catch { /* ignore */ }
}
function rotate(delta: number) {
  try { cropperInstance?.rotate?.(delta) } catch { /* ignore */ }
}
function resetCrop() {
  try { cropperInstance?.reset?.() } catch { /* ignore */ }
}

async function switchMode(target: Mode) {
  if (target === 'crop' && annotateStarted.value) return

  if (target === 'draw' || target === 'text') {
    if (!annotateStarted.value) {
      const ok = await beginAnnotate()
      if (!ok) return
    }
    // Each activation of the «متن» tab arms a single text creation.
    // Re-tapping the tab while in text mode arms it again so the user
    // can place additional texts deliberately.
    if (target === 'text') {
      pendingTextCreation.value = true
      // Make sure no IText is in editing state so the next tap creates
      // a new one instead of typing into an existing one.
      try {
        fabricCanvas?.getObjects?.().forEach((o: any) => {
          if (o?.isEditing) o.exitEditing?.()
        })
        fabricCanvas?.discardActiveObject?.()
        fabricCanvas?.requestRenderAll?.()
      } catch { /* ignore */ }
    } else {
      pendingTextCreation.value = false
    }
    mode.value = target
    applyToolForMode()
    return
  }

  pendingTextCreation.value = false
  mode.value = target
}

async function beginAnnotate(): Promise<boolean> {
  let baseCanvas: HTMLCanvasElement | null = null
  try {
    // Match the crop-only path's quality ceiling so users don't lose
    // resolution just because they decided to draw or add text.
    baseCanvas = cropperInstance?.getCroppedCanvas?.({
      maxWidth: 4096,
      maxHeight: 4096,
      imageSmoothingEnabled: true,
      imageSmoothingQuality: 'high',
    }) ?? null
  } catch { /* ignore */ }

  if (!baseCanvas) return false

  try { cropperInstance?.destroy?.() } catch { /* ignore */ }
  cropperInstance = null

  annotateStarted.value = true
  await nextTick()

  if (!fabricCanvasRef.value || !fabricStageRef.value) {
    annotateStarted.value = false
    return false
  }

  const mod = await import('fabric')
  fabricLib = (mod as any).fabric ?? (mod as any).default ?? mod

  const stageRect = fabricStageRef.value.getBoundingClientRect()
  const baseW = baseCanvas.width
  const baseH = baseCanvas.height
  const scale = Math.min(stageRect.width / baseW, stageRect.height / baseH, 1)
  const dispW = Math.max(1, Math.round(baseW * scale))
  const dispH = Math.max(1, Math.round(baseH * scale))

  fabricCanvas = new fabricLib.Canvas(fabricCanvasRef.value, {
    width: dispW,
    height: dispH,
    backgroundColor: '#000',
    selection: false,
    preserveObjectStacking: true,
  })

  ;(fabricCanvas as any).__baseW = baseW
  ;(fabricCanvas as any).__baseH = baseH

  const dataUrl = baseCanvas.toDataURL('image/jpeg', 0.95)
  await new Promise<void>((resolve) => {
    fabricLib.Image.fromURL(
      dataUrl,
      (img: any) => {
        img.scaleToWidth(dispW)
        fabricCanvas.setBackgroundImage(img, fabricCanvas.renderAll.bind(fabricCanvas), {
          originX: 'left',
          originY: 'top',
        })
        resolve()
      },
      { crossOrigin: 'anonymous' },
    )
  })

  bindFabricEvents()
  applyToolForMode()
  return true
}

function pushUndoSnapshot() {
  if (!fabricCanvas) return
  try {
    undoStack.value.push(JSON.stringify(fabricCanvas.toJSON()))
    if (undoStack.value.length > 30) undoStack.value.shift()
  } catch { /* ignore */ }
}

function applyToolForMode() {
  if (!fabricCanvas || !fabricLib) return

  if (mode.value === 'draw') {
    fabricCanvas.isDrawingMode = true
    fabricCanvas.selection = false
    const brush = fabricCanvas.freeDrawingBrush
    if (brush) {
      brush.color = brushColor.value
      brush.width = brushSize.value
    }
  } else if (mode.value === 'text') {
    fabricCanvas.isDrawingMode = false
    // Allow selecting/moving existing IText objects with the finger.
    fabricCanvas.selection = false
    fabricCanvas.skipTargetFind = false
  } else {
    fabricCanvas.isDrawingMode = false
  }
}

function updateBrush() {
  if (!fabricCanvas) return
  const brush = fabricCanvas.freeDrawingBrush
  if (brush) {
    brush.color = brushColor.value
    brush.width = brushSize.value
  }
}

function pickBrushColor(c: string) {
  brushColor.value = c
  updateBrush()
}

function pickBrushSize(s: number) {
  brushSize.value = s
  updateBrush()
}

function onCanvasMouseDown(opts: any) {
  if (!fabricCanvas || !fabricLib || mode.value !== 'text') return

  // Tap on an existing IText: select it so the user can drag to move
  // (default fabric behavior). Editing the text is reserved for
  // double-tap (mouse:dblclick) so it doesn't clash with drag.
  if (opts.target) {
    return
  }

  // Empty-space tap. Only create a new text if the «متن» tab armed it
  // (one-shot per tab activation).
  if (!pendingTextCreation.value) {
    // Just deselect on empty taps when not armed.
    try {
      fabricCanvas.discardActiveObject()
      fabricCanvas.requestRenderAll()
    } catch { /* ignore */ }
    return
  }

  const pointer = fabricCanvas.getPointer(opts.e)
  pushUndoSnapshot()
  const text = new fabricLib.IText('متن', {
    left: pointer.x,
    top: pointer.y,
    originX: 'center',
    originY: 'center',
    fontSize: 32,
    fill: textColor.value,
    fontFamily: 'Vazirmatn, Tahoma, sans-serif',
    fontWeight: 'bold',
    stroke: 'rgba(0,0,0,0.45)',
    strokeWidth: 1,
    padding: 6,
    cornerColor: '#3390ec',
    borderColor: '#3390ec',
    editable: true,
    // Default movement/scaling is on; explicit for clarity.
    lockMovementX: false,
    lockMovementY: false,
    hasControls: true,
    hasBorders: true,
  })
  fabricCanvas.add(text)
  fabricCanvas.setActiveObject(text)
  text.enterEditing()
  text.selectAll()
  fabricCanvas.requestRenderAll()
  // Consume the one-shot. User must re-tap the «متن» tab to add another.
  pendingTextCreation.value = false
}

function onCanvasDoubleClick(opts: any) {
  // Double-tap on an existing IText enters edit mode in any annotate mode.
  if (!fabricCanvas || !fabricLib) return
  const target = opts?.target
  if (!target) return
  if (target.type === 'i-text' || target.isType?.('i-text')) {
    try {
      fabricCanvas.setActiveObject(target)
      target.enterEditing?.()
      fabricCanvas.requestRenderAll()
    } catch { /* ignore */ }
  }
}

function pickTextColor(c: string) {
  textColor.value = c
  const active = fabricCanvas?.getActiveObject?.()
  if (active && active.type === 'i-text') {
    pushUndoSnapshot()
    active.set('fill', c)
    fabricCanvas.requestRenderAll()
  }
}

function deleteActive() {
  if (!fabricCanvas) return
  const active = fabricCanvas.getActiveObject()
  if (!active) return
  pushUndoSnapshot()
  fabricCanvas.remove(active)
  fabricCanvas.discardActiveObject()
  fabricCanvas.requestRenderAll()
}

function undo() {
  if (!fabricCanvas || undoStack.value.length === 0) return
  const snapshot = undoStack.value.pop()
  if (!snapshot) return
  fabricCanvas.loadFromJSON(JSON.parse(snapshot), () => {
    fabricCanvas.requestRenderAll()
  })
}

function bindFabricEvents() {
  if (!fabricCanvas) return
  fabricCanvas.on('mouse:down', onCanvasMouseDown)
  fabricCanvas.on('mouse:dblclick', onCanvasDoubleClick)
  fabricCanvas.on('before:path:created', pushUndoSnapshot)
  // Snapshot when an IText is moved/scaled/rotated so undo covers it.
  fabricCanvas.on('object:modified', pushUndoSnapshot)
}

async function confirm() {
  if (isProcessing.value) return
  isProcessing.value = true
  try {
    let outCanvas: HTMLCanvasElement | null = null

    if (fabricCanvas) {
      try {
        fabricCanvas.discardActiveObject()
        fabricCanvas.getObjects().forEach((o: any) => {
          if (o?.exitEditing) try { o.exitEditing() } catch { /* ignore */ }
        })
        fabricCanvas.requestRenderAll()
      } catch { /* ignore */ }

      const baseW = (fabricCanvas as any).__baseW ?? fabricCanvas.getWidth()
      const dispW = fabricCanvas.getWidth()
      const multiplier = dispW > 0 ? baseW / dispW : 1

      // Export fabric canvas directly as a JPEG blob. Going through
      // dataUrl → <img> → canvas → toBlob('jpeg') would re-encode the
      // image a second time and lose quality for no reason.
      const dataUrl: string = fabricCanvas.toDataURL({
        format: 'jpeg',
        quality: 0.92,
        multiplier,
      })

      const fabricBlob = await dataUrlToBlob(dataUrl)
      if (!fabricBlob) {
        emit('confirm', props.file)
        return
      }

      const originalName = props.file.name || 'image.jpg'
      const stem = originalName.replace(/\.[^.]+$/, '')
      const edited = new File([fabricBlob], `${stem}_edited.jpg`, {
        type: 'image/jpeg',
        lastModified: Date.now(),
      })
      emit('confirm', edited)
      return
    } else {
      outCanvas = cropperInstance?.getCroppedCanvas?.({
        maxWidth: 4096,
        maxHeight: 4096,
        imageSmoothingEnabled: true,
        imageSmoothingQuality: 'high',
      }) ?? null
    }

    if (!outCanvas) {
      emit('confirm', props.file)
      return
    }

    const blob: Blob | null = await new Promise((resolve) => {
      try {
        outCanvas!.toBlob((b) => resolve(b), 'image/jpeg', 0.92)
      } catch {
        resolve(null)
      }
    })

    if (!blob) {
      emit('confirm', props.file)
      return
    }

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

async function dataUrlToBlob(dataUrl: string): Promise<Blob | null> {
  // Avoid going through <img>+<canvas> re-encode. fetch() handles data:
  // URLs natively in all modern browsers including iOS Safari.
  try {
    const res = await fetch(dataUrl)
    return await res.blob()
  } catch {
    return null
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
      <button
        class="top-btn"
        :disabled="annotateStarted ? !canUndo : false"
        @click="annotateStarted ? undo() : resetCrop()"
        :aria-label="annotateStarted ? 'بازگرد' : 'بازنشانی'"
        :title="annotateStarted ? 'بازگرد' : 'بازنشانی'"
      >
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
      </button>
    </div>

    <!-- Stage -->
    <div class="stage" ref="fabricStageRef">
      <img
        v-show="!annotateStarted"
        ref="imgRef"
        :src="sourceUrl"
        alt=""
        style="max-width: 100%; max-height: 100%; display: block;"
        @load="onImgLoad"
        @error="onImgError"
      />
      <div v-if="loadError && !annotateStarted" class="stage-error">
        {{ loadError }}
      </div>
      <canvas
        v-show="annotateStarted"
        ref="fabricCanvasRef"
      ></canvas>
    </div>

    <!-- Mode-specific tool rows -->
    <div v-if="mode === 'crop' && !annotateStarted" class="ratios">
      <button
        v-for="opt in ratioOptions"
        :key="opt.label"
        class="ratio-chip"
        :class="{ active: aspectRatio === opt.value }"
        @click="applyAspect(opt.value)"
      >{{ opt.label }}</button>
    </div>

    <div v-if="(mode === 'draw' || mode === 'text') && annotateStarted" class="palette">
      <button
        v-for="c in PALETTE"
        :key="c"
        class="color-dot"
        :class="{ active: (mode === 'draw' ? brushColor : textColor) === c }"
        :style="{ background: c }"
        aria-label="رنگ"
        @click="mode === 'draw' ? pickBrushColor(c) : pickTextColor(c)"
      ></button>

      <div v-if="mode === 'draw'" class="size-group">
        <button
          v-for="s in BRUSH_SIZES"
          :key="s"
          class="size-chip"
          :class="{ active: brushSize === s }"
          @click="pickBrushSize(s)"
        >
          <span class="size-dot" :style="{ width: s + 'px', height: s + 'px', background: brushColor }"></span>
        </button>
      </div>

      <button
        v-if="mode === 'text'"
        class="size-chip danger"
        @click="deleteActive"
        title="حذف انتخاب"
      >×</button>
    </div>

    <!-- Bottom toolbar: rotate (crop only) -->
    <div v-if="mode === 'crop' && !annotateStarted" class="toolbar">
      <button class="tool-btn" @click="rotate(-90)" aria-label="چرخش چپ">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
        <span>چرخش</span>
      </button>
      <button class="tool-btn" @click="rotate(90)" aria-label="چرخش راست">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transform: scaleX(-1);"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
        <span>چرخش</span>
      </button>
    </div>

    <!-- Mode tabs -->
    <div class="mode-tabs">
      <button
        class="mode-tab"
        :class="{ active: mode === 'crop' && !annotateStarted, disabled: annotateStarted }"
        :disabled="annotateStarted"
        @click="switchMode('crop')"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/></svg>
        کراپ
      </button>
      <button
        class="mode-tab"
        :class="{ active: mode === 'draw' }"
        @click="switchMode('draw')"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/></svg>
        نقاشی
      </button>
      <button
        class="mode-tab"
        :class="{ active: mode === 'text', armed: mode === 'text' && pendingTextCreation }"
        @click="switchMode('text')"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg>
        {{ mode === 'text' && pendingTextCreation ? 'برای افزودن متن ضربه بزنید' : 'متن' }}
      </button>
    </div>

    <!-- Action row -->
    <div class="actions">
      <button class="action-secondary" @click="sendUnedited">ارسال بدون ویرایش</button>
      <button
        class="action-primary"
        @click="confirm"
        :disabled="isProcessing || (!annotateStarted && (!imgLoaded || !!loadError))"
      >
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

.top-title { font-size: 15px; font-weight: 600; }

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
.top-btn:disabled { opacity: 0.35; cursor: default; }
.top-btn:active:not(:disabled) { background: rgba(255, 255, 255, 0.08); }

.stage {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  padding: 8px;
  position: relative;
  /* Keep all touch interactions inside cropper/fabric — no browser pan/zoom. */
  touch-action: none;
}

.stage :deep(.cropper-container) {
  direction: ltr;
  /*
    Cropper.js needs the touch event stream to itself; if any ancestor lets
    the browser pan/zoom, dragging the handles becomes flaky on mobile and
    needs several attempts before it registers.
  */
  touch-action: none;
  -webkit-user-select: none;
  user-select: none;
}

/*
  The cropper handles default to ~5px wide which is far too small for a
  finger. Enlarge their hit area without visually growing the dot itself
  by extending an invisible padded overlay via ::before.
*/
.stage :deep(.cropper-point),
.stage :deep(.cropper-line) {
  touch-action: none;
}
.stage :deep(.cropper-point) {
  width: 8px;
  height: 8px;
  background-color: #3390ec;
  opacity: 1;
}
.stage :deep(.cropper-point::before) {
  content: '';
  position: absolute;
  inset: -14px;
  /* Transparent but interactive: gives ~36px finger target. */
  background: transparent;
}
.stage :deep(.cropper-line) {
  /* Edge lines also need a generous touch zone. */
  background-color: #3390ec;
  opacity: 0.5;
}
.stage :deep(.cropper-line.line-n),
.stage :deep(.cropper-line.line-s) {
  height: 3px;
}
.stage :deep(.cropper-line.line-e),
.stage :deep(.cropper-line.line-w) {
  width: 3px;
}
.stage :deep(.cropper-line::before) {
  content: '';
  position: absolute;
  inset: -12px;
  background: transparent;
}

.stage-error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.85);
  font-size: 14px;
  text-align: center;
  padding: 24px;
  pointer-events: none;
}

.ratios,
.palette {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  overflow-x: auto;
  flex-shrink: 0;
  scrollbar-width: none;
}
.ratios::-webkit-scrollbar,
.palette::-webkit-scrollbar { display: none; }

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
.ratio-chip.active { background: #3390ec; border-color: #3390ec; }

.color-dot {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.25);
  cursor: pointer;
  padding: 0;
}
.color-dot.active {
  border-color: #fff;
  transform: scale(1.1);
  box-shadow: 0 0 0 2px rgba(51, 144, 236, 0.7);
}

.size-group {
  display: flex;
  gap: 6px;
  margin-right: auto;
  padding-right: 8px;
  border-right: 1px solid rgba(255, 255, 255, 0.12);
}

.size-chip {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #fff;
  font-size: 18px;
  padding: 0;
}
.size-chip.active { border-color: #fff; }
.size-chip.danger { background: rgba(255, 59, 48, 0.18); }

.size-dot {
  display: block;
  border-radius: 50%;
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

.mode-tabs {
  display: flex;
  justify-content: center;
  gap: 4px;
  padding: 4px 12px;
  flex-shrink: 0;
}
.mode-tab {
  background: transparent;
  border: none;
  color: rgba(255, 255, 255, 0.65);
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 13px;
}
.mode-tab.active {
  background: rgba(51, 144, 236, 0.18);
  color: #fff;
}
.mode-tab.armed {
  background: #3390ec;
  color: #fff;
  animation: tab-pulse 1.4s ease-in-out infinite;
}
@keyframes tab-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(51, 144, 236, 0.55); }
  50% { box-shadow: 0 0 0 6px rgba(51, 144, 236, 0); }
}
.mode-tab.disabled,
.mode-tab:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.actions {
  display: flex;
  gap: 8px;
  padding: 8px 12px calc(env(safe-area-inset-bottom, 0) + 12px);
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

.action-secondary { background: rgba(255, 255, 255, 0.12); color: #fff; }
.action-secondary:active { background: rgba(255, 255, 255, 0.18); }

.action-primary { background: #3390ec; color: #fff; }
.action-primary:disabled { opacity: 0.6; cursor: default; }
.action-primary:active:not(:disabled) { background: #2a7fd4; }
</style>
