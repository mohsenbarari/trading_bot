type ImagePreprocessWorkerSuccessResponse = {
  id: string
  ok: true
  blob: Blob
  width: number
  height: number
  thumbnailDataUrl: string
}

type ImagePreprocessWorkerErrorResponse = {
  id: string
  ok: false
  error: string
}

type ImagePreprocessWorkerResponse = ImagePreprocessWorkerSuccessResponse | ImagePreprocessWorkerErrorResponse

export type ImagePreprocessResult = {
  blob: Blob
  width: number
  height: number
  thumbnailDataUrl: string
}

type PendingJob = {
  id: string
  file: File
  resolve: (result: ImagePreprocessResult) => void
  reject: (error: Error) => void
  signal?: AbortSignal
}

type WorkerSlot = {
  worker: Worker
  busy: boolean
  currentJob: PendingJob | null
  timeoutId: ReturnType<typeof setTimeout> | null
}

const queuedJobs: PendingJob[] = []
const activeAbortCleanups = new Map<string, () => void>()
const workerSlots: WorkerSlot[] = []

// Mobile browsers (notably Safari on iOS and Chrome on low-RAM Android) can
// silently kill a Web Worker when its OffscreenCanvas memory footprint grows
// during a large album preprocess. When this happens, neither `onmessage` nor
// `onerror` fires — the slot stays `busy=true` forever and the entire pool
// (plus the composable-level `activePreprocessCount` gate) locks up, leaving
// the user watching a "preparing..." spinner that never advances.
//
// To guarantee the pool keeps draining, we arm a hard per-job timeout. If a
// job doesn't post back within the window, we treat the worker as dead and
// recycle the slot.
const WORKER_JOB_TIMEOUT_MS = 60_000

export function canUseImagePreprocessWorker() {
  return (
    typeof Worker !== 'undefined' &&
    typeof OffscreenCanvas !== 'undefined' &&
    typeof createImageBitmap === 'function'
  )
}

export function getRecommendedImagePreprocessParallelism() {
  if (typeof navigator === 'undefined') return 1

  const cpuCount = navigator.hardwareConcurrency || 4
  const memory = typeof (navigator as Navigator & { deviceMemory?: number }).deviceMemory === 'number'
    ? (navigator as Navigator & { deviceMemory?: number }).deviceMemory || 0
    : 0

  if (memory > 0 && memory <= 2) return 1
  if (cpuCount >= 10 && (memory === 0 || memory >= 6)) return 3
  if (cpuCount >= 8 && (memory === 0 || memory >= 4)) return 3
  if (cpuCount >= 6) return 2
  if (cpuCount >= 4) return 2
  return 1
}

function supportsWorkerPreprocess() {
  return canUseImagePreprocessWorker()
}

function createWorkerInstance() {
  return new Worker(new URL('../workers/imagePreprocess.worker.ts', import.meta.url), { type: 'module' })
}

function cleanupActiveJob(jobId: string) {
  const cleanup = activeAbortCleanups.get(jobId)
  if (cleanup) {
    cleanup()
    activeAbortCleanups.delete(jobId)
  }
}

function clearSlotTimeout(slot: WorkerSlot) {
  if (slot.timeoutId !== null) {
    clearTimeout(slot.timeoutId)
    slot.timeoutId = null
  }
}

function armSlotTimeout(slot: WorkerSlot, job: PendingJob) {
  clearSlotTimeout(slot)
  slot.timeoutId = setTimeout(() => {
    // Worker is presumed dead (mobile OOM kill). Recycle the slot.
    const stuckJob = slot.currentJob
    try {
      slot.worker.terminate()
    } catch {
      /* ignore */
    }
    slot.worker = createWorkerInstance()
    attachWorkerHandlers(slot)
    slot.busy = false
    slot.currentJob = null
    slot.timeoutId = null
    if (stuckJob) {
      cleanupActiveJob(stuckJob.id)
      stuckJob.reject(new Error('Image preprocessing timed out (worker may have been killed).'))
    } else {
      // Defensive — shouldn't happen.
      void job
    }
    dispatchNext(slot)
  }, WORKER_JOB_TIMEOUT_MS)
}

function dispatchNext(slot: WorkerSlot) {
  while (queuedJobs.length > 0) {
    const nextJob = queuedJobs.shift()
    if (!nextJob) {
      break
    }

    if (nextJob.signal?.aborted) {
      nextJob.reject(new Error('UploadCancelled'))
      continue
    }

    slot.busy = true
    slot.currentJob = nextJob
    armSlotTimeout(slot, nextJob)

    const handleAbort = () => {
      clearSlotTimeout(slot)
      slot.worker.terminate()
      slot.worker = createWorkerInstance()
      attachWorkerHandlers(slot)
      slot.busy = false
      slot.currentJob = null
      cleanupActiveJob(nextJob.id)
      nextJob.reject(new Error('UploadCancelled'))
      dispatchNext(slot)
    }

    nextJob.signal?.addEventListener('abort', handleAbort, { once: true })
    activeAbortCleanups.set(nextJob.id, () => {
      nextJob.signal?.removeEventListener('abort', handleAbort)
    })

    slot.worker.postMessage({
      id: nextJob.id,
      file: nextJob.file,
      maxWidthOrHeight: 1920,
      quality: 0.85,
      thumbnailMaxWidthOrHeight: 64,
      thumbnailQuality: 0.58,
    })
    return
  }

  slot.busy = false
  slot.currentJob = null
}

function attachWorkerHandlers(slot: WorkerSlot) {
  slot.worker.onmessage = (event: MessageEvent<ImagePreprocessWorkerResponse>) => {
    const job = slot.currentJob
    if (!job) return

    const response = event.data
    if (!response || response.id !== job.id) {
      return
    }

    clearSlotTimeout(slot)
    cleanupActiveJob(job.id)

    if (!response.ok) {
      job.reject(new Error(response.error || 'Image preprocessing worker failed'))
    } else {
      job.resolve({
        blob: response.blob,
        width: response.width,
        height: response.height,
        thumbnailDataUrl: response.thumbnailDataUrl,
      })
    }

    slot.busy = false
    slot.currentJob = null
    dispatchNext(slot)
  }

  slot.worker.onerror = (event) => {
    clearSlotTimeout(slot)
    const job = slot.currentJob
    if (job) {
      cleanupActiveJob(job.id)
      job.reject(new Error(event.message || 'Image preprocessing worker crashed'))
    }

    slot.worker.terminate()
    slot.worker = createWorkerInstance()
    slot.busy = false
    slot.currentJob = null
    attachWorkerHandlers(slot)
    dispatchNext(slot)
  }
}

function ensureWorkerPool() {
  if (!supportsWorkerPreprocess()) {
    throw new Error('Web Worker is unavailable')
  }

  if (workerSlots.length > 0) {
    return workerSlots
  }

  const poolSize = getRecommendedImagePreprocessParallelism()
  for (let index = 0; index < poolSize; index += 1) {
    const slot: WorkerSlot = {
      worker: createWorkerInstance(),
      busy: false,
      currentJob: null,
      timeoutId: null,
    }
    attachWorkerHandlers(slot)
    workerSlots.push(slot)
  }

  return workerSlots
}

export function processImageInWorker(file: File, signal?: AbortSignal): Promise<ImagePreprocessResult> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error('UploadCancelled'))
      return
    }

    try {
      ensureWorkerPool()
    } catch (error) {
      reject(error instanceof Error ? error : new Error('Web Worker is unavailable'))
      return
    }

    const job: PendingJob = {
      id: `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`,
      file,
      resolve,
      reject,
      signal,
    }

    queuedJobs.push(job)

    const freeSlot = workerSlots.find(slot => !slot.busy)
    if (freeSlot) {
      dispatchNext(freeSlot)
    }
  })
}