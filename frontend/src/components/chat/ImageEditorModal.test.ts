import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import ImageEditorModal from './ImageEditorModal.vue'

const originalCreateObjectURL = URL.createObjectURL
const originalRevokeObjectURL = URL.revokeObjectURL
const originalCanvasToBlob = HTMLCanvasElement.prototype.toBlob

const cropperDestroyMock = vi.fn()
const cropperSetAspectRatioMock = vi.fn()
const canvasToBlobMock = vi.fn((callback: (blob: Blob | null) => void) => {
  callback(new Blob(['edited-image'], { type: 'image/jpeg' }))
})
const cropperGetCroppedCanvasMock = vi.fn(() => document.createElement('canvas'))

const CropperCtorMock = vi.fn().mockImplementation(() => ({
  destroy: cropperDestroyMock,
  setAspectRatio: cropperSetAspectRatioMock,
  getCroppedCanvas: cropperGetCroppedCanvasMock,
}))

vi.mock('cropperjs', () => ({
  default: CropperCtorMock,
}))

describe('ImageEditorModal.vue', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    cropperDestroyMock.mockClear()
    cropperSetAspectRatioMock.mockClear()
    cropperGetCroppedCanvasMock.mockClear()
    CropperCtorMock.mockClear()
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => 'blob:editor-image'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => {}),
    })
    Object.defineProperty(HTMLCanvasElement.prototype, 'toBlob', {
      configurable: true,
      writable: true,
      value: canvasToBlobMock,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    if (originalCreateObjectURL) {
      Object.defineProperty(URL, 'createObjectURL', {
        configurable: true,
        writable: true,
        value: originalCreateObjectURL,
      })
    } else {
      delete (URL as Partial<typeof URL>).createObjectURL
    }

    if (originalRevokeObjectURL) {
      Object.defineProperty(URL, 'revokeObjectURL', {
        configurable: true,
        writable: true,
        value: originalRevokeObjectURL,
      })
    } else {
      delete (URL as Partial<typeof URL>).revokeObjectURL
    }

    if (originalCanvasToBlob) {
      Object.defineProperty(HTMLCanvasElement.prototype, 'toBlob', {
        configurable: true,
        writable: true,
        value: originalCanvasToBlob,
      })
    } else {
      delete (HTMLCanvasElement.prototype as Partial<HTMLCanvasElement>).toBlob
    }
  })

  it('emits cancel and can bypass editing with the original file', async () => {
    const file = new File(['raw-image'], 'sample.png', { type: 'image/png' })
    const wrapper = mount(ImageEditorModal, {
      props: { file },
    })

    await wrapper.find('.action-secondary').trigger('click')
    expect(wrapper.emitted('confirm')).toEqual([[file]])

    await wrapper.findAll('.top-btn')[0]!.trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('enables crop controls after image load and updates the active crop ratio', async () => {
    const file = new File(['raw-image'], 'sample.png', { type: 'image/png' })
    const wrapper = mount(ImageEditorModal, {
      props: { file },
    })

    await flushPromises()

    const confirmButton = wrapper.find('.action-primary')
    expect(confirmButton.attributes('disabled')).toBeDefined()

    await wrapper.find('img').trigger('load')
    await flushPromises()

    expect(CropperCtorMock).toHaveBeenCalledTimes(1)
    expect(confirmButton.attributes('disabled')).toBeUndefined()

    const squareRatioChip = wrapper.findAll('.ratio-chip').find((item) => item.text().includes('۱:۱'))
    expect(squareRatioChip).toBeTruthy()

    await squareRatioChip!.trigger('click')
    expect(squareRatioChip!.classes()).toContain('active')
  })
})