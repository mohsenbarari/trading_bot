import { afterEach, describe, expect, it, vi } from 'vitest'

class MockOffscreenCanvas {
  static instances: MockOffscreenCanvas[] = []

  width: number
  height: number
  drawImage = vi.fn<(...args: any[]) => void>()

  constructor(width: number, height: number) {
    this.width = width
    this.height = height
    MockOffscreenCanvas.instances.push(this)
  }

  getContext(type: string) {
    if (type !== '2d') return null
    return {
      drawImage: this.drawImage,
    }
  }

  async convertToBlob(options: { type: string }) {
    const content = `${this.width}x${this.height}`
    return {
      type: options.type,
      arrayBuffer: async () => Buffer.from(content, 'utf8'),
      text: async () => content,
    }
  }

  static reset() {
    MockOffscreenCanvas.instances = []
  }
}

const createImageBitmapDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'createImageBitmap')
const offscreenCanvasDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'OffscreenCanvas')
const btoaDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'btoa')

function restoreProperty(target: object, key: string, descriptor?: PropertyDescriptor) {
  if (descriptor) {
    Object.defineProperty(target, key, descriptor)
    return
  }
  delete (target as Record<string, unknown>)[key]
}

async function loadWorkerModule() {
  vi.resetModules()
  await import('./imagePreprocess.worker')
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.resetModules()
  MockOffscreenCanvas.reset()
  ;(globalThis as typeof globalThis & { onmessage?: unknown }).onmessage = null
  restoreProperty(globalThis, 'createImageBitmap', createImageBitmapDescriptor)
  restoreProperty(globalThis, 'OffscreenCanvas', offscreenCanvasDescriptor)
  restoreProperty(globalThis, 'btoa', btoaDescriptor)
})

describe('imagePreprocess.worker', () => {
  it('scales images, falls back to createImageBitmap without options, and posts a success response', async () => {
    const close = vi.fn()
    const createImageBitmapMock = vi.fn()
      .mockRejectedValueOnce(new Error('orientation unsupported'))
      .mockResolvedValue({ width: 4000, height: 2000, close })
    Object.defineProperty(globalThis, 'createImageBitmap', {
      configurable: true,
      value: createImageBitmapMock,
    })
    Object.defineProperty(globalThis, 'OffscreenCanvas', {
      configurable: true,
      value: MockOffscreenCanvas,
    })
    Object.defineProperty(globalThis, 'btoa', {
      configurable: true,
      value: (value: string) => Buffer.from(value, 'binary').toString('base64'),
    })
    const postMessageSpy = vi.spyOn(globalThis, 'postMessage').mockImplementation(() => undefined)

    await loadWorkerModule()
    const handler = (globalThis as typeof globalThis & {
      onmessage?: (event: MessageEvent<any>) => Promise<void>
    }).onmessage

    await handler?.({
      data: {
        id: 'job-1',
        file: new Blob(['raw'], { type: 'image/jpeg' }),
        maxWidthOrHeight: 1920,
        quality: 0.85,
        thumbnailMaxWidthOrHeight: 64,
        thumbnailQuality: 0.58,
      },
    } as MessageEvent<any>)

    expect(createImageBitmapMock).toHaveBeenCalledTimes(2)
    expect(close).toHaveBeenCalledTimes(1)
    expect(MockOffscreenCanvas.instances.map((canvas) => [canvas.width, canvas.height])).toEqual([
      [1920, 960],
      [64, 32],
    ])

    const response = postMessageSpy.mock.calls[0]![0] as {
      ok: boolean
      width: number
      height: number
      blob: {
        text: () => Promise<string>
      }
      thumbnailDataUrl: string
    }
    expect(response.ok).toBe(true)
    expect(response.width).toBe(1920)
    expect(response.height).toBe(960)
    await expect(response.blob.text()).resolves.toBe('1920x960')
    expect(response.thumbnailDataUrl.startsWith('data:image/jpeg;base64,')).toBe(true)
  })

  it('posts an error response when OffscreenCanvas support is unavailable', async () => {
    Object.defineProperty(globalThis, 'OffscreenCanvas', {
      configurable: true,
      value: undefined,
    })
    const postMessageSpy = vi.spyOn(globalThis, 'postMessage').mockImplementation(() => undefined)

    await loadWorkerModule()
    const handler = (globalThis as typeof globalThis & {
      onmessage?: (event: MessageEvent<any>) => Promise<void>
    }).onmessage

    await handler?.({
      data: {
        id: 'job-2',
        file: new Blob(['raw'], { type: 'image/jpeg' }),
        maxWidthOrHeight: 1920,
        quality: 0.85,
        thumbnailMaxWidthOrHeight: 64,
        thumbnailQuality: 0.58,
      },
    } as MessageEvent<any>)

    expect(postMessageSpy.mock.calls[0]![0]).toMatchObject({
      id: 'job-2',
      ok: false,
      error: 'OffscreenCanvas is unavailable in worker',
    })
  })

  it('posts an error response when a canvas context cannot be created', async () => {
    const createImageBitmapMock = vi.fn().mockResolvedValue({
      width: 800,
      height: 600,
      close: vi.fn(),
    })

    class NullContextCanvas extends MockOffscreenCanvas {
      override getContext() {
        return null
      }
    }

    Object.defineProperty(globalThis, 'createImageBitmap', {
      configurable: true,
      value: createImageBitmapMock,
    })
    Object.defineProperty(globalThis, 'OffscreenCanvas', {
      configurable: true,
      value: NullContextCanvas,
    })
    const postMessageSpy = vi.spyOn(globalThis, 'postMessage').mockImplementation(() => undefined)

    await loadWorkerModule()
    const handler = (globalThis as typeof globalThis & {
      onmessage?: (event: MessageEvent<any>) => Promise<void>
    }).onmessage

    await handler?.({
      data: {
        id: 'job-3',
        file: new Blob(['raw'], { type: 'image/jpeg' }),
        maxWidthOrHeight: 1920,
        quality: 0.85,
        thumbnailMaxWidthOrHeight: 64,
        thumbnailQuality: 0.58,
      },
    } as MessageEvent<any>)

    expect(postMessageSpy.mock.calls[0]![0]).toMatchObject({
      id: 'job-3',
      ok: false,
      error: 'No OffscreenCanvas 2D context',
    })
  })

  it('posts an error response when createImageBitmap support is unavailable', async () => {
    Object.defineProperty(globalThis, 'createImageBitmap', {
      configurable: true,
      value: undefined,
    })
    Object.defineProperty(globalThis, 'OffscreenCanvas', {
      configurable: true,
      value: MockOffscreenCanvas,
    })
    const postMessageSpy = vi.spyOn(globalThis, 'postMessage').mockImplementation(() => undefined)

    await loadWorkerModule()
    const handler = (globalThis as typeof globalThis & {
      onmessage?: (event: MessageEvent<any>) => Promise<void>
    }).onmessage

    await handler?.({
      data: {
        id: 'job-4',
        file: new Blob(['raw'], { type: 'image/jpeg' }),
        maxWidthOrHeight: 1920,
        quality: 0.85,
        thumbnailMaxWidthOrHeight: 64,
        thumbnailQuality: 0.58,
      },
    } as MessageEvent<any>)

    expect(postMessageSpy.mock.calls[0]![0]).toMatchObject({
      id: 'job-4',
      ok: false,
      error: 'createImageBitmap is unavailable in worker',
    })
  })
})