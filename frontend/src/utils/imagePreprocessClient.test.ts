import { afterEach, describe, expect, it, vi } from 'vitest'

class MockWorker {
  static instances: MockWorker[] = []

  onmessage: ((event: MessageEvent<any>) => void) | null = null
  onerror: ((event: ErrorEvent) => void) | null = null
  postMessage = vi.fn<(payload: any) => void>()
  terminate = vi.fn<() => void>()

  constructor(_url: URL, _options: WorkerOptions) {
    MockWorker.instances.push(this)
  }

  emitMessage(data: any) {
    this.onmessage?.({ data } as MessageEvent<any>)
  }

  emitError(message: string) {
    this.onerror?.({ message } as ErrorEvent)
  }

  static reset() {
    MockWorker.instances = []
  }
}

class MockOffscreenCanvas {}

const workerDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'Worker')
const offscreenCanvasDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'OffscreenCanvas')
const createImageBitmapDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'createImageBitmap')
const hardwareConcurrencyDescriptor = Object.getOwnPropertyDescriptor(navigator, 'hardwareConcurrency')
const deviceMemoryDescriptor = Object.getOwnPropertyDescriptor(navigator, 'deviceMemory')

function restoreProperty(target: object, key: string, descriptor?: PropertyDescriptor) {
  if (descriptor) {
    Object.defineProperty(target, key, descriptor)
    return
  }
  delete (target as Record<string, unknown>)[key]
}

function installWorkerSupport(options?: { cpu?: number; memory?: number }) {
  const cpu = options?.cpu ?? 8
  const memory = options?.memory ?? 4

  MockWorker.reset()
  Object.defineProperty(globalThis, 'Worker', {
    configurable: true,
    value: MockWorker,
  })
  Object.defineProperty(globalThis, 'OffscreenCanvas', {
    configurable: true,
    value: MockOffscreenCanvas,
  })
  Object.defineProperty(globalThis, 'createImageBitmap', {
    configurable: true,
    value: vi.fn(),
  })
  Object.defineProperty(navigator, 'hardwareConcurrency', {
    configurable: true,
    value: cpu,
  })
  Object.defineProperty(navigator, 'deviceMemory', {
    configurable: true,
    value: memory,
  })
}

async function loadModule() {
  vi.resetModules()
  return import('./imagePreprocessClient')
}

function createImageFile(name = 'photo.jpg') {
  return new File(['image'], name, { type: 'image/jpeg' })
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  restoreProperty(globalThis, 'Worker', workerDescriptor)
  restoreProperty(globalThis, 'OffscreenCanvas', offscreenCanvasDescriptor)
  restoreProperty(globalThis, 'createImageBitmap', createImageBitmapDescriptor)
  restoreProperty(navigator, 'hardwareConcurrency', hardwareConcurrencyDescriptor)
  restoreProperty(navigator, 'deviceMemory', deviceMemoryDescriptor)
  MockWorker.reset()
})

describe('imagePreprocessClient', () => {
  it('detects worker support and recommends concurrency from device capabilities', async () => {
    installWorkerSupport({ cpu: 10, memory: 8 })
    const module = await loadModule()

    expect(module.canUseImagePreprocessWorker()).toBe(true)
    expect(module.getRecommendedImagePreprocessParallelism()).toBe(3)

    Object.defineProperty(navigator, 'hardwareConcurrency', {
      configurable: true,
      value: 2,
    })
    Object.defineProperty(navigator, 'deviceMemory', {
      configurable: true,
      value: 1,
    })

    expect(module.getRecommendedImagePreprocessParallelism()).toBe(1)

    Object.defineProperty(globalThis, 'Worker', {
      configurable: true,
      value: undefined,
    })
    expect(module.canUseImagePreprocessWorker()).toBe(false)
  })

  it('processes a queued job and resolves only the matching worker response', async () => {
    installWorkerSupport({ cpu: 1, memory: 1 })
    const module = await loadModule()

    const resultPromise = module.processImageInWorker(createImageFile())
    const worker = MockWorker.instances[0]!
    expect(worker).toBeDefined()
    expect(worker.postMessage).toHaveBeenCalledTimes(1)

    const payload = worker.postMessage.mock.calls[0]?.[0]
    worker.emitMessage({
      id: 'other-job',
      ok: true,
      blob: new Blob(['ignored'], { type: 'image/jpeg' }),
      width: 1,
      height: 1,
      thumbnailDataUrl: 'data:image/jpeg;base64,ignored',
    })
    await Promise.resolve()

    worker.emitMessage({
      id: payload.id,
      ok: true,
      blob: new Blob(['processed'], { type: 'image/jpeg' }),
      width: 320,
      height: 180,
      thumbnailDataUrl: 'data:image/jpeg;base64,done',
    })

    await expect(resultPromise).resolves.toMatchObject({
      width: 320,
      height: 180,
      thumbnailDataUrl: 'data:image/jpeg;base64,done',
    })
    expect(worker.terminate).not.toHaveBeenCalled()
  })

  it('aborts an active job, recycles the slot, and keeps later jobs working', async () => {
    installWorkerSupport({ cpu: 1, memory: 1 })
    const module = await loadModule()

    const controller = new AbortController()
    const firstPromise = module.processImageInWorker(createImageFile('first.jpg'), controller.signal)
    const firstWorker = MockWorker.instances[0]!

    controller.abort()

    await expect(firstPromise).rejects.toThrow('UploadCancelled')
    expect(firstWorker.terminate).toHaveBeenCalledTimes(1)
    expect(MockWorker.instances).toHaveLength(2)

    const secondPromise = module.processImageInWorker(createImageFile('second.jpg'))
    const secondWorker = MockWorker.instances[1]!
    const payload = secondWorker.postMessage.mock.calls[0]?.[0]
    secondWorker.emitMessage({
      id: payload.id,
      ok: true,
      blob: new Blob(['processed'], { type: 'image/jpeg' }),
      width: 640,
      height: 360,
      thumbnailDataUrl: 'data:image/jpeg;base64,second',
    })

    await expect(secondPromise).resolves.toMatchObject({ width: 640, height: 360 })
  })

  it('recycles timed-out workers and dispatches queued jobs onto the replacement slot', async () => {
    vi.useFakeTimers()
    installWorkerSupport({ cpu: 1, memory: 1 })
    const module = await loadModule()

    const firstPromise = module.processImageInWorker(createImageFile('first.jpg'))
    const secondPromise = module.processImageInWorker(createImageFile('second.jpg'))
    const firstWorker = MockWorker.instances[0]!
    const firstRejection = expect(firstPromise).rejects.toThrow('timed out')

    expect(firstWorker.postMessage).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(60_000)

    await firstRejection
    expect(firstWorker.terminate).toHaveBeenCalledTimes(1)
    expect(MockWorker.instances).toHaveLength(2)

    const replacementWorker = MockWorker.instances[1]!
    expect(replacementWorker.postMessage).toHaveBeenCalledTimes(1)

    const payload = replacementWorker.postMessage.mock.calls[0]?.[0]
    replacementWorker.emitMessage({
      id: payload.id,
      ok: true,
      blob: new Blob(['processed'], { type: 'image/jpeg' }),
      width: 800,
      height: 600,
      thumbnailDataUrl: 'data:image/jpeg;base64,recovered',
    })

    await expect(secondPromise).resolves.toMatchObject({ width: 800, height: 600 })
  })

  it('rejects unsupported environments and worker crashes', async () => {
    installWorkerSupport({ cpu: 1, memory: 1 })
    const module = await loadModule()

    Object.defineProperty(globalThis, 'Worker', {
      configurable: true,
      value: undefined,
    })
    await expect(module.processImageInWorker(createImageFile('unsupported.jpg'))).rejects.toThrow(
      'Web Worker is unavailable'
    )

    installWorkerSupport({ cpu: 1, memory: 1 })
    const crashModule = await loadModule()
    const crashPromise = crashModule.processImageInWorker(createImageFile('crash.jpg'))
    const crashWorker = MockWorker.instances[0]!

    crashWorker.emitError('worker exploded')

    await expect(crashPromise).rejects.toThrow('worker exploded')
    expect(crashWorker.terminate).toHaveBeenCalledTimes(1)
    expect(MockWorker.instances).toHaveLength(2)
  })
})