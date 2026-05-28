import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { decode } from 'blurhash'

vi.mock('blurhash', () => ({
  decode: vi.fn(),
}))

const decodeMock = vi.mocked(decode)

describe('BlurHashCanvas.vue', () => {
  const createImageDataMock = vi.fn((width: number, height: number) => ({
    data: new Uint8ClampedArray(width * height * 4),
  }))
  const putImageDataMock = vi.fn()

  beforeEach(() => {
    decodeMock.mockReset()
    createImageDataMock.mockClear()
    putImageDataMock.mockClear()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({
      createImageData: createImageDataMock,
      putImageData: putImageDataMock,
    }) as unknown as CanvasRenderingContext2D)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the decoded blurhash onto the canvas and rerenders when the hash changes', async () => {
    decodeMock.mockImplementation((_hash, width, height) => new Uint8ClampedArray(width * height * 4).fill(9))
    const BlurHashCanvas = (await import('./BlurHashCanvas.vue')).default
    const wrapper = mount(BlurHashCanvas, {
      props: {
        hash: 'LEHV6nWB2yk8pyo0adR*.7kCMdnj',
        width: 4,
        height: 3,
        punch: 2,
      },
    })

    const canvas = wrapper.get('canvas').element as HTMLCanvasElement
    expect(decodeMock).toHaveBeenCalledWith('LEHV6nWB2yk8pyo0adR*.7kCMdnj', 4, 3, 2)
    expect(canvas.width).toBe(4)
    expect(canvas.height).toBe(3)
    expect(createImageDataMock).toHaveBeenCalledWith(4, 3)
    expect(putImageDataMock).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ hash: 'LKO2?U%2Tw=w]~RBVZRi};RPxuwH' })
    expect(decodeMock).toHaveBeenCalledWith('LKO2?U%2Tw=w]~RBVZRi};RPxuwH', 4, 3, 2)
    expect(putImageDataMock).toHaveBeenCalledTimes(2)
  })

  it('rerenders when blurhash render dimensions or punch change', async () => {
    decodeMock.mockImplementation((_hash, width, height) => new Uint8ClampedArray(width * height * 4).fill(7))
    const BlurHashCanvas = (await import('./BlurHashCanvas.vue')).default
    const wrapper = mount(BlurHashCanvas, {
      props: {
        hash: 'LEHV6nWB2yk8pyo0adR*.7kCMdnj',
        width: 4,
        height: 3,
        punch: 1,
      },
    })

    expect(decodeMock).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ width: 6, height: 5, punch: 3 })

    expect(decodeMock).toHaveBeenCalledTimes(2)
    expect(decodeMock).toHaveBeenLastCalledWith('LEHV6nWB2yk8pyo0adR*.7kCMdnj', 6, 5, 3)
    expect(createImageDataMock).toHaveBeenLastCalledWith(6, 5)
  })

  it('skips rendering for empty hashes and swallows invalid blurhash errors', async () => {
    const BlurHashCanvas = (await import('./BlurHashCanvas.vue')).default
    const wrapper = mount(BlurHashCanvas, {
      props: {
        hash: '',
      },
    })

    expect(decodeMock).not.toHaveBeenCalled()

    decodeMock.mockImplementationOnce(() => {
      throw new Error('invalid blurhash')
    })

    await wrapper.setProps({ hash: 'broken-hash' })
    expect(decodeMock).toHaveBeenCalledWith('broken-hash', 32, 32, 1)
    expect(putImageDataMock).not.toHaveBeenCalled()
  })
})