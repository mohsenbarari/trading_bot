import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import ImageEditorModal from './ImageEditorModal.vue'

const originalCreateObjectURL = URL.createObjectURL
const originalRevokeObjectURL = URL.revokeObjectURL
const originalCanvasToBlob = HTMLCanvasElement.prototype.toBlob
const originalCanvasGetContext = HTMLCanvasElement.prototype.getContext

const cropperDestroyMock = vi.fn()
const cropperSetAspectRatioMock = vi.fn()
const cropperRotateMock = vi.fn()
const cropperResetMock = vi.fn()
const canvasToBlobMock = vi.fn((callback: (blob: Blob | null) => void) => {
  callback(new Blob(['edited-image'], { type: 'image/jpeg' }))
})
const cropperGetCroppedCanvasMock = vi.fn(() => {
  const canvas = document.createElement('canvas')
  canvas.width = 400
  canvas.height = 300
  return canvas
})

const fabricState = vi.hoisted(() => ({
  instances: [] as any[],
  imageCalls: [] as Array<{ url: string; options: any }>,
}))

const CropperCtorMock = vi.fn().mockImplementation(() => ({
  destroy: cropperDestroyMock,
  setAspectRatio: cropperSetAspectRatioMock,
  rotate: cropperRotateMock,
  reset: cropperResetMock,
  getCroppedCanvas: cropperGetCroppedCanvasMock,
}))

vi.mock('cropperjs', () => ({
  default: CropperCtorMock,
}))

vi.mock('fabric', () => {
  class MockIText {
    type = 'i-text'
    text = 'متن'
    isEditing = false
    private handlers: Record<string, () => void> = {}

    constructor(_text: string, options: Record<string, unknown>) {
      Object.assign(this, options)
    }

    set(key: string | Record<string, unknown>, value?: unknown) {
      if (typeof key === 'string') {
        ;(this as Record<string, unknown>)[key] = value
        return
      }
      Object.assign(this, key)
    }

    on(event: string, handler: () => void) {
      this.handlers[event] = handler
    }

    off(event: string) {
      delete this.handlers[event]
    }

    enterEditing() {
      this.isEditing = true
    }

    exitEditing() {
      this.isEditing = false
      this.handlers['editing:exited']?.()
    }

    selectAll() {}

    isType(type: string) {
      return type === 'i-text'
    }
  }

  class MockCanvas {
    width: number
    height: number
    events: Record<string, (payload: any) => void>
    objects: any[]
    activeObject: any
    freeDrawingBrush: { color: string; width: number }
    isDrawingMode = false
    selection = false
    skipTargetFind = false
    backgroundImage: any = null
    loadedFromJson = false
    disposed = false
    renderRequests = 0

    constructor(_el: HTMLCanvasElement, options: Record<string, number>) {
      this.width = options.width ?? 1
      this.height = options.height ?? 1
      this.events = {}
      this.objects = []
      this.activeObject = null
      this.freeDrawingBrush = { color: '', width: 0 }
      fabricState.instances.push(this)
    }

    renderAll() {}

    requestRenderAll() {
      this.renderRequests += 1
    }

    setBackgroundImage(img: any, callback?: () => void) {
      this.backgroundImage = img
      callback?.()
    }

    on(event: string, handler: (payload: any) => void) {
      this.events[event] = handler
    }

    getPointer() {
      return { x: 111, y: 222 }
    }

    add(object: any) {
      this.objects.push(object)
    }

    setActiveObject(object: any) {
      this.activeObject = object
    }

    discardActiveObject() {
      this.activeObject = null
    }

    getActiveObject() {
      return this.activeObject
    }

    remove(object: any) {
      this.objects = this.objects.filter((candidate) => candidate !== object)
    }

    getObjects() {
      return this.objects
    }

    loadFromJSON(_json: unknown, callback: () => void) {
      this.loadedFromJson = true
      callback()
    }

    toJSON() {
      return { objects: this.objects.map((object) => ({ text: object.text ?? '' })) }
    }

    getWidth() {
      return this.width
    }

    getHeight() {
      return this.height
    }

    toDataURL() {
      return 'data:image/jpeg;base64,ZmFi'
    }

    dispose() {
      this.disposed = true
    }
  }

  return {
    fabric: {
      Canvas: MockCanvas,
      IText: MockIText,
      Image: {
        fromURL: (url: string, callback: (image: { scaleToWidth: (width: number) => void }) => void, options?: any) => {
          fabricState.imageCalls.push({ url, options })
          callback({ scaleToWidth: () => {} })
        },
      },
    },
  }
})

describe('ImageEditorModal.vue', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    cropperDestroyMock.mockClear()
    cropperSetAspectRatioMock.mockClear()
    cropperRotateMock.mockClear()
    cropperResetMock.mockClear()
    cropperGetCroppedCanvasMock.mockImplementation(() => {
      const canvas = document.createElement('canvas')
      canvas.width = 400
      canvas.height = 300
      return canvas
    })
    CropperCtorMock.mockImplementation(() => ({
      destroy: cropperDestroyMock,
      setAspectRatio: cropperSetAspectRatioMock,
      rotate: cropperRotateMock,
      reset: cropperResetMock,
      getCroppedCanvas: cropperGetCroppedCanvasMock,
    }))
    CropperCtorMock.mockClear()
    fabricState.instances.length = 0
    fabricState.imageCalls.length = 0
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

    if (originalCanvasGetContext) {
      Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
        configurable: true,
        writable: true,
        value: originalCanvasGetContext,
      })
    } else {
      delete (HTMLCanvasElement.prototype as Partial<HTMLCanvasElement>).getContext
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

  it('shows an error state and keeps confirm disabled when the source image fails to load', async () => {
    const file = new File(['broken-image'], 'broken.png', { type: 'image/png' })
    const wrapper = mount(ImageEditorModal, {
      props: { file },
    })

    await flushPromises()
    await wrapper.find('img').trigger('error')
    await flushPromises()

    expect(wrapper.find('.stage-error').text()).toContain('این فرمت تصویر پشتیبانی نمی‌شود')
    expect(wrapper.find('.action-primary').attributes('disabled')).toBeDefined()
  })

  it('rotates the crop source into a new blob url and falls back to cropper reset/rotate controls', async () => {
    const file = new File(['raw-image'], 'rotate.png', { type: 'image/png' })
    const drawImageMock = vi.fn()
    const createObjectUrlMock = vi.fn()
      .mockReturnValueOnce('blob:editor-image-1')
      .mockReturnValueOnce('blob:editor-image-2')
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: createObjectUrlMock,
    })
    const rotateCtxMock = {
      imageSmoothingEnabled: false,
      imageSmoothingQuality: 'low',
      translate: vi.fn(),
      rotate: vi.fn(),
      drawImage: drawImageMock,
    }
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
      configurable: true,
      writable: true,
      value: vi.fn(() => rotateCtxMock),
    })

    const wrapper = mount(ImageEditorModal, {
      props: { file },
    })

    await flushPromises()
    const img = wrapper.get('img')
    Object.defineProperty(img.element, 'naturalWidth', { configurable: true, value: 200 })
    Object.defineProperty(img.element, 'naturalHeight', { configurable: true, value: 100 })

    await img.trigger('load')
    await flushPromises()

    await wrapper.find('[aria-label="چرخش چپ"]').trigger('click')
    await flushPromises()

    expect(cropperDestroyMock).toHaveBeenCalled()
    expect(rotateCtxMock.translate).toHaveBeenCalled()
    expect(rotateCtxMock.rotate).toHaveBeenCalled()
    expect(drawImageMock).toHaveBeenCalled()
    expect(createObjectUrlMock).toHaveBeenCalledTimes(2)

    await wrapper.find('img').trigger('load')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:editor-image-1')

    await wrapper.findAll('.top-btn')[1]!.trigger('click')
    expect(cropperResetMock).toHaveBeenCalled()

    Object.defineProperty(img.element, 'naturalWidth', { configurable: true, value: 0 })
    Object.defineProperty(img.element, 'naturalHeight', { configurable: true, value: 0 })
    await wrapper.find('[aria-label="چرخش راست"]').trigger('click')
    expect(cropperRotateMock).toHaveBeenCalledWith(90)
  })

  it('enters annotate mode, creates draggable text, updates palette state, deletes the active object, and undoes history', async () => {
    const file = new File(['raw-image'], 'annotate.png', { type: 'image/png' })
    const wrapper = mount(ImageEditorModal, {
      props: { file },
    })

    await flushPromises()
    await wrapper.find('img').trigger('load')
    await flushPromises()

    const modeTabs = wrapper.findAll('.mode-tab')
    await modeTabs[1]!.trigger('click')
    await flushPromises()

    expect(fabricState.instances).toHaveLength(1)
    const canvas = fabricState.instances[0]!
    expect(cropperDestroyMock).toHaveBeenCalled()
    expect(wrapper.find('.palette').exists()).toBe(true)
    expect(canvas.isDrawingMode).toBe(true)
    expect(canvas.freeDrawingBrush.color).toBe('#ff3b30')
    expect(canvas.freeDrawingBrush.width).toBe(8)

    const colorDots = wrapper.findAll('.color-dot')
    await colorDots[1]!.trigger('click')
    await wrapper.findAll('.size-chip')[2]!.trigger('click')
    expect(canvas.freeDrawingBrush.color).toBe('#000000')
    expect(canvas.freeDrawingBrush.width).toBe(14)

    await modeTabs[2]!.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.mode-tab')[2]!.text()).toContain('برای افزودن متن ضربه بزنید')

    canvas.events['mouse:down']?.({ e: new MouseEvent('mousedown') })
    await flushPromises()
    const createdText = canvas.getObjects()[0]
    expect(createdText).toBeTruthy()
    expect(createdText.left).toBe(111)
    expect(createdText.top).toBe(222)
    expect(createdText.isEditing).toBe(true)

    createdText.exitEditing()
    await flushPromises()
    expect(createdText.editable).toBe(false)
    expect(wrapper.findAll('.mode-tab')[2]!.text()).toBe('متن')

    canvas.setActiveObject(createdText)
    await colorDots[2]!.trigger('click')
    expect(createdText.fill).toBe('#ff3b30')

    await wrapper.find('.size-chip.danger').trigger('click')
    expect(canvas.getObjects()).toHaveLength(0)

    await modeTabs[2]!.trigger('click')
    canvas.events['mouse:down']?.({ e: new MouseEvent('mousedown') })
    expect(canvas.getObjects()).toHaveLength(1)

    await wrapper.findAll('.top-btn')[1]!.trigger('click')
    expect(canvas.loadedFromJson).toBe(true)
  })

  it('exports crop-only edits as a marked jpeg file and can fall back to the original file when crop export fails', async () => {
    const file = new File(['raw-image'], 'crop-only.png', { type: 'image/png' })
    const wrapper = mount(ImageEditorModal, {
      props: { file },
    })

    await flushPromises()
    await wrapper.find('img').trigger('load')
    await flushPromises()

    await wrapper.find('.action-primary').trigger('click')
    await flushPromises()

    const successFile = wrapper.emitted('confirm')?.[0]?.[0] as File & Record<string, unknown>
    expect(successFile).toBeInstanceOf(File)
    expect(successFile.name).toBe('crop-only_edited.jpg')
    expect(successFile.type).toBe('image/jpeg')
    expect(successFile.__chatEditedImage).toBe(true)

    canvasToBlobMock.mockImplementationOnce((callback: (blob: Blob | null) => void) => {
      callback(null)
    })
    const fallbackWrapper = mount(ImageEditorModal, {
      props: { file },
    })
    await fallbackWrapper.find('img').trigger('load')
    await flushPromises()
    await fallbackWrapper.find('.action-primary').trigger('click')

    expect(fallbackWrapper.emitted('confirm')?.[0]).toEqual([file])
  })

  it('exports annotated edits through fabric and falls back to the original file when data-url conversion fails', async () => {
    const file = new File(['raw-image'], 'fabric.png', { type: 'image/png' })
    const fetchMock = vi.fn(async () => new Response(new Blob(['fabric-bytes'], { type: 'image/jpeg' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(ImageEditorModal, {
      props: { file },
    })
    await flushPromises()
    await wrapper.find('img').trigger('load')
    await flushPromises()

    await wrapper.findAll('.mode-tab')[1]!.trigger('click')
    await flushPromises()
    await wrapper.find('.action-primary').trigger('click')
    await flushPromises()

    const editedFile = wrapper.emitted('confirm')?.[0]?.[0] as File & Record<string, unknown>
    expect(fetchMock).toHaveBeenCalledWith('data:image/jpeg;base64,ZmFi')
    expect(editedFile.name).toBe('fabric_edited.jpg')
    expect(editedFile.type).toBe('image/jpeg')
    expect(editedFile.__chatEditedImage).toBe(true)

    fetchMock.mockRejectedValueOnce(new Error('fetch failed'))
    const fallbackWrapper = mount(ImageEditorModal, {
      props: { file },
    })
    await flushPromises()
    await fallbackWrapper.find('img').trigger('load')
    await flushPromises()
    await fallbackWrapper.findAll('.mode-tab')[1]!.trigger('click')
    await flushPromises()
    await fallbackWrapper.find('.action-primary').trigger('click')
    await flushPromises()

    expect(fallbackWrapper.emitted('confirm')?.[0]).toEqual([file])
  })
})