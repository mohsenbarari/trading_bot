import { nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AttachmentMenu from './AttachmentMenu.vue'

const attachmentMenuMocks = vi.hoisted(() => ({
  pushBackStateMock: vi.fn(),
  popBackStateMock: vi.fn(),
}))

vi.mock('../../composables/useBackButton', () => ({
  pushBackState: attachmentMenuMocks.pushBackStateMock,
  popBackState: attachmentMenuMocks.popBackStateMock,
}))

vi.mock('@vue-leaflet/vue-leaflet', () => ({
  LMap: { name: 'LMap', template: '<div class="leaflet-map-stub"><slot /></div>' },
  LTileLayer: { name: 'LTileLayer', template: '<div class="leaflet-tile-layer-stub"></div>' },
  LCircle: { name: 'LCircle', template: '<div class="leaflet-circle-stub"></div>' },
  LCircleMarker: { name: 'LCircleMarker', template: '<div class="leaflet-circle-marker-stub"></div>' },
}))

const mountedWrappers: Array<ReturnType<typeof mount>> = []

function mountAttachmentMenu() {
  const wrapper = mount(AttachmentMenu, {
    props: {
      modelValue: true,
      allowLocation: true,
    },
    global: {
      stubs: {
        teleport: true,
        transition: false,
      },
    },
  })

  mountedWrappers.push(wrapper)
  return wrapper
}

function makeGeoPosition(lat: number, lng: number, accuracy: number): GeolocationPosition {
  return {
    coords: {
      latitude: lat,
      longitude: lng,
      accuracy,
      altitude: null,
      altitudeAccuracy: null,
      heading: null,
      speed: null,
      toJSON: () => ({}),
    },
    timestamp: Date.now(),
    toJSON: () => ({}),
  }
}

describe('AttachmentMenu.vue', () => {
  const originalSecureContext = window.isSecureContext
  const originalGeolocation = navigator.geolocation
  const originalPermissions = navigator.permissions
  const originalMediaDevices = navigator.mediaDevices
  const originalRequestAnimationFrame = window.requestAnimationFrame
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL
  const originalMediaRecorder = globalThis.MediaRecorder

  function installInlineCameraMocks() {
    let currentZoom = 2
    const track = {
      stop: vi.fn(),
      getCapabilities: vi.fn(() => ({
        zoom: { min: 1, max: 4, step: 0.5 },
      })),
      getSettings: vi.fn(() => ({ zoom: currentZoom })),
      applyConstraints: vi.fn(async (constraints?: { advanced?: Array<{ zoom?: number }> }) => {
        currentZoom = Number(constraints?.advanced?.[0]?.zoom ?? currentZoom)
      }),
    }
    const stream = {
      getVideoTracks: vi.fn(() => [track]),
      getTracks: vi.fn(() => [track]),
    }
    const getUserMedia = vi.fn(async () => stream)

    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    })

    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)

    return {
      track,
      stream,
      getUserMedia,
      getZoom: () => currentZoom,
    }
  }

  beforeEach(() => {
    attachmentMenuMocks.pushBackStateMock.mockReset()
    attachmentMenuMocks.popBackStateMock.mockReset()
    vi.spyOn(console, 'info').mockImplementation(() => {})
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    localStorage.clear()

    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: false,
    })

    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition: vi.fn(),
        watchPosition: vi.fn(() => 1),
        clearWatch: vi.fn(),
      },
    })

    Object.defineProperty(navigator, 'permissions', {
      configurable: true,
      value: {
        query: vi.fn(async () => ({ state: 'granted' })),
      },
    })

    Object.defineProperty(window, 'requestAnimationFrame', {
      configurable: true,
      writable: true,
      value: (callback: FrameRequestCallback) => {
        callback(0)
        return 1
      },
    })

    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn((file: File) => `blob:${file.name}`),
    })

    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(),
    })
  })

  afterEach(async () => {
    while (mountedWrappers.length > 0) {
      mountedWrappers.pop()?.unmount()
    }
    await flushPromises()

    if (originalSecureContext !== undefined) {
      Object.defineProperty(window, 'isSecureContext', {
        configurable: true,
        value: originalSecureContext,
      })
    }

    if (originalGeolocation) {
      Object.defineProperty(navigator, 'geolocation', {
        configurable: true,
        value: originalGeolocation,
      })
    } else {
      delete (navigator as { geolocation?: Geolocation }).geolocation
    }

    if (originalPermissions) {
      Object.defineProperty(navigator, 'permissions', {
        configurable: true,
        value: originalPermissions,
      })
    } else {
      Reflect.deleteProperty(navigator, 'permissions')
    }

    if (originalMediaDevices) {
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: originalMediaDevices,
      })
    } else {
      Reflect.deleteProperty(navigator, 'mediaDevices')
    }

    if (originalRequestAnimationFrame) {
      Object.defineProperty(window, 'requestAnimationFrame', {
        configurable: true,
        writable: true,
        value: originalRequestAnimationFrame,
      })
    }

    if (originalCreateObjectURL) {
      Object.defineProperty(URL, 'createObjectURL', {
        configurable: true,
        writable: true,
        value: originalCreateObjectURL,
      })
    }

    if (originalRevokeObjectURL) {
      Object.defineProperty(URL, 'revokeObjectURL', {
        configurable: true,
        writable: true,
        value: originalRevokeObjectURL,
      })
    }

    if (originalMediaRecorder) {
      Object.defineProperty(globalThis, 'MediaRecorder', {
        configurable: true,
        writable: true,
        value: originalMediaRecorder,
      })
    } else {
      Reflect.deleteProperty(globalThis, 'MediaRecorder')
    }
  })

  it('falls back to native camera capture when inline camera is unavailable', async () => {
    const inputClickSpy = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    const wrapper = mountAttachmentMenu()

    await wrapper.findAll('.gallery-panel .action-card')[0]!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.camera-capture-overlay').exists()).toBe(true)
    expect(wrapper.find('.camera-status-overlay.native').exists()).toBe(true)

    await wrapper.find('.camera-shutter-btn').trigger('click')
    expect(inputClickSpy).toHaveBeenCalled()
  })

  it('keeps location send disabled until a manual pin is chosen and then emits the coordinates', async () => {
    const wrapper = mountAttachmentMenu()

    const locationTab = wrapper.findAll('.tab-btn').find((item) => item.text().includes('موقعیت'))
    expect(locationTab).toBeTruthy()

    await locationTab!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.send-location-btn').attributes('disabled')).toBeDefined()

    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      selectedLatLng: { lat: number; lng: number } | null
      hasManualLocationSelection: boolean
    }

    vm.activeTab = 'location'
    vm.selectedLatLng = { lat: 35.7219, lng: 51.3347 }
    vm.hasManualLocationSelection = true
    await nextTick()

    const sendButton = wrapper.find('.send-location-btn')
    expect(sendButton.attributes('disabled')).toBeUndefined()

    await sendButton.trigger('click')

    expect(wrapper.emitted('select-location')).toEqual([[35.7219, 51.3347]])
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
  })

  it('sends the captured camera queue as an album and closes the sheet first', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      showCameraCapture: boolean
      capturedCameraMedia: Array<{ id: string; file: File; type: 'photo' | 'video'; previewUrl: string }>
      sendCapturedMediaQueue: () => Promise<void>
    }

    const randomUuidSpy = vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('camera-album-id-0001-0001')
    const fileA = new File(['a'], 'a.jpg', { type: 'image/jpeg' })
    const fileB = new File(['b'], 'b.jpg', { type: 'image/jpeg' })
    vm.showCameraCapture = true
    vm.capturedCameraMedia = [
      { id: 'a', file: fileA, type: 'photo', previewUrl: 'blob:a' },
      { id: 'b', file: fileB, type: 'photo', previewUrl: 'blob:b' },
    ]
    await nextTick()

    await vm.sendCapturedMediaQueue()

    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
    expect(wrapper.emitted('select-media')).toEqual([
      [fileA, 'camera-album-id-0001-0001', 0, 2],
      [fileB, 'camera-album-id-0001-0001', 1, 2],
    ])
    randomUuidSpy.mockRestore()
  })

  it('prioritizes stage-back dismissal for editor, preview, and camera states before closing the sheet', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      editingFile: File | null
      multiPreviewFiles: File[] | null
      showCameraCapture: boolean
      handleStageBack: (fromBack?: boolean) => boolean
    }

    vm.editingFile = new File(['x'], 'edit.jpg', { type: 'image/jpeg' })
    await nextTick()
    expect(vm.handleStageBack()).toBe(true)
    expect(vm.editingFile).toBeNull()

    vm.multiPreviewFiles = [new File(['a'], 'one.jpg', { type: 'image/jpeg' }), new File(['b'], 'two.jpg', { type: 'image/jpeg' })]
    await nextTick()
    expect(vm.handleStageBack()).toBe(true)
    expect(vm.multiPreviewFiles).toBeNull()

    vm.showCameraCapture = true
    await nextTick()
    expect(vm.handleStageBack()).toBe(true)
    expect(vm.showCameraCapture).toBe(false)

    expect(vm.handleStageBack()).toBe(false)
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
  })

  it('moves multi-file gallery picks into the preview flow and emits the confirmed album selection', async () => {
    vi.useFakeTimers()
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      multiPreviewFiles: File[] | null
      onGalleryFile: (event: Event) => Promise<void>
      onMultiPreviewConfirm: (files: File[]) => void
    }
    const fileA = new File(['a'], 'one.jpg', { type: 'image/jpeg' })
    const fileB = new File(['b'], 'two.jpg', { type: 'image/jpeg' })
    const randomUuidSpy = vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('preview-album-id-0001-0001')

    const galleryPromise = vm.onGalleryFile({
      target: {
        files: [fileA, fileB],
        value: 'chosen',
      },
    } as unknown as Event)
    await vi.advanceTimersByTimeAsync(180)
    await galleryPromise
    await nextTick()

    expect(vm.multiPreviewFiles).toHaveLength(2)

    vm.onMultiPreviewConfirm([fileA, fileB])
    await nextTick()

    expect(wrapper.emitted('select-media')).toEqual([
      [fileA, 'preview-album-id-0001-0001', 0, 2],
      [fileB, 'preview-album-id-0001-0001', 1, 2],
    ])
    expect(vm.multiPreviewFiles).toBeNull()

    randomUuidSpy.mockRestore()
    vi.useRealTimers()
  })

  it('queues native camera files, restricts edit mode to photos, and replaces the edited capture', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      cameraEditingItemId: string | null
      capturedCameraMedia: Array<{ id: string; file: File; type: 'photo' | 'video'; previewUrl: string }>
      onNativeCameraFile: (event: Event) => void
      editCapturedMedia: (itemId: string) => void
      onCameraEditConfirm: (editedFile: File) => void
    }
    const photoFile = new File(['photo'], 'photo.jpg', { type: 'image/jpeg' })
    const videoFile = new File(['video'], 'clip.mp4', { type: 'video/mp4' })
    const input = {
      files: [photoFile, videoFile],
      value: 'picked',
    }

    vm.onNativeCameraFile({ target: input } as unknown as Event)
    await nextTick()

    expect(vm.capturedCameraMedia).toHaveLength(2)
    expect(vm.capturedCameraMedia.map((item) => item.type)).toEqual(['photo', 'video'])
    expect(input.value).toBe('')

    const photoId = vm.capturedCameraMedia[0]!.id
    const videoId = vm.capturedCameraMedia[1]!.id

    vm.editCapturedMedia(videoId)
    expect(vm.cameraEditingItemId).toBeNull()

    vm.editCapturedMedia(photoId)
    expect(vm.cameraEditingItemId).toBe(photoId)

    const editedPhoto = new File(['edited'], 'edited.jpg', { type: 'image/jpeg' })
    vm.onCameraEditConfirm(editedPhoto)
    await nextTick()

    expect(vm.cameraEditingItemId).toBeNull()
    expect(vm.capturedCameraMedia[0]).toMatchObject({
      file: editedPhoto,
      previewUrl: 'blob:edited.jpg',
    })
  })

  it('emits single edited media, clears cancelled edits, and forwards file-tab selections before closing', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      editingFile: File | null
      onEditorConfirm: (editedFile: File) => void
      onEditorCancel: () => void
      onFileSelected: (event: Event) => void
    }
    const editedFile = new File(['edited'], 'edited.jpg', { type: 'image/jpeg' })
    const docFile = new File(['doc'], 'sheet.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
    const txtFile = new File(['txt'], 'note.txt', { type: 'text/plain' })
    const input = {
      files: [docFile, txtFile],
      value: 'picked',
    }

    vm.editingFile = new File(['original'], 'original.jpg', { type: 'image/jpeg' })
    vm.onEditorConfirm(editedFile)
    await nextTick()

    expect(vm.editingFile).toBeNull()
    expect(wrapper.emitted('select-media')).toEqual([[editedFile, null, 0, 1]])

    vm.editingFile = new File(['other'], 'other.jpg', { type: 'image/jpeg' })
    vm.onEditorCancel()
    await nextTick()
    expect(vm.editingFile).toBeNull()

    vm.onFileSelected({ target: input } as unknown as Event)
    await nextTick()

    expect(wrapper.emitted('select-file')).toEqual([[docFile], [txtFile]])
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
    expect(input.value).toBe('')
  })

  it('resets the location tab when locations are disallowed and reports early geolocation errors', async () => {
    const insecureWrapper = mountAttachmentMenu()
    const insecureVm = insecureWrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      locationStatusMessage: string
      goToMyLocation: (silent?: boolean) => Promise<void>
    }

    insecureVm.activeTab = 'location'
    await nextTick()
    await insecureWrapper.setProps({ allowLocation: false })
    await nextTick()

    expect(insecureVm.activeTab).toBe('gallery')

    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: false,
    })
    await insecureVm.goToMyLocation()
    expect(insecureVm.locationStatusMessage).toContain('HTTPS')

    const noGeoWrapper = mountAttachmentMenu()
    const noGeoVm = noGeoWrapper.vm as unknown as {
      locationStatusMessage: string
      goToMyLocation: (silent?: boolean) => Promise<void>
    }

    delete (navigator as { geolocation?: Geolocation }).geolocation
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })

    await noGeoVm.goToMyLocation()
    expect(noGeoVm.locationStatusMessage).toContain('پشتیبانی نمی‌کند')
  })

  it('opens the camera overlay in native fallback mode and respects mode/facing guards while recording', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      showCameraCapture: boolean
      cameraMode: 'photo' | 'video'
      activeFacingMode: 'environment' | 'user'
      isUsingNativeCameraFallback: boolean
      isRecording: boolean
      openCameraCapture: () => void
      setCameraMode: (mode: 'photo' | 'video') => Promise<void>
      toggleFacingMode: () => Promise<void>
    }

    vm.openCameraCapture()
    await flushPromises()

    expect(vm.showCameraCapture).toBe(true)
    expect(vm.cameraMode).toBe('photo')
    expect(vm.activeFacingMode).toBe('environment')
    expect(vm.isUsingNativeCameraFallback).toBe(true)

    await vm.setCameraMode('video')
    expect(vm.cameraMode).toBe('video')

    await vm.toggleFacingMode()
    expect(vm.activeFacingMode).toBe('environment')

    vm.isRecording = true
    await vm.setCameraMode('photo')
    expect(vm.cameraMode).toBe('video')
  })

  it('routes a single standard gallery image into the editor flow after closing the sheet', async () => {
    vi.useFakeTimers()
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      editingFile: File | null
      singleEditorKey: number
      onGalleryFile: (event: Event) => Promise<void>
    }
    const imageFile = new File(['img'], 'photo.jpg', { type: 'image/jpeg' })

    const galleryPromise = vm.onGalleryFile({
      target: {
        files: [imageFile],
        value: 'picked',
      },
    } as unknown as Event)
    await vi.advanceTimersByTimeAsync(180)
    await galleryPromise
    await nextTick()

    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
    expect(vm.editingFile).toBe(imageFile)
    expect(vm.singleEditorKey).toBe(1)
    expect(wrapper.emitted('select-media')).toBeUndefined()

    vi.useRealTimers()
  })

  it('emits a single HEIC gallery file directly instead of opening the editor', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      editingFile: File | null
      onGalleryFile: (event: Event) => Promise<void>
    }
    const heicFile = new File(['heic'], 'photo.heic', { type: 'image/heic' })

    await vm.onGalleryFile({
      target: {
        files: [heicFile],
        value: 'picked',
      },
    } as unknown as Event)
    await nextTick()

    expect(vm.editingFile).toBeNull()
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
    expect(wrapper.emitted('select-media')).toEqual([[heicFile, null, 0, 1]])
  })

  it('removes and clears captured media while revoking preview urls', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      capturedCameraMedia: Array<{ id: string; file: File; type: 'photo' | 'video'; previewUrl: string }>
      onNativeCameraFile: (event: Event) => void
      removeCapturedMedia: (itemId: string) => void
      clearCapturedMediaQueue: () => void
    }
    const fileA = new File(['a'], 'one.jpg', { type: 'image/jpeg' })
    const fileB = new File(['b'], 'two.mp4', { type: 'video/mp4' })

    vm.onNativeCameraFile({
      target: {
        files: [fileA, fileB],
        value: 'picked',
      },
    } as unknown as Event)
    await nextTick()

    const [firstItem, secondItem] = vm.capturedCameraMedia
    expect(firstItem?.previewUrl).toBe('blob:one.jpg')
    expect(secondItem?.previewUrl).toBe('blob:two.mp4')

    vm.removeCapturedMedia(firstItem!.id)
    expect(vm.capturedCameraMedia).toHaveLength(1)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:one.jpg')

    vm.clearCapturedMediaQueue()
    expect(vm.capturedCameraMedia).toEqual([])
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:two.mp4')
  })

  it('updates manual map selections and validates sendLocation guard branches before successful auto-confirm sends', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      selectedLatLng: { lat: number; lng: number } | null
      detectedLocationAccuracyM: number | null
      hasManualLocationSelection: boolean
      hasConfirmedAutoLocation: boolean
      locationStatusMessage: string
      mapRef: { leafletObject: { getCenter: () => { lat: number; lng: number } } } | null
      onMapMoveEnd: () => void
      sendLocation: () => void
    }

    vm.activeTab = 'location'
    vm.mapRef = {
      leafletObject: {
        getCenter: () => ({ lat: 35.7001, lng: 51.4002 }),
      },
    }
    vm.onMapMoveEnd()
    await nextTick()

    expect(vm.selectedLatLng).toEqual({ lat: 35.7001, lng: 51.4002 })
    expect(vm.hasManualLocationSelection).toBe(true)

    vm.selectedLatLng = null
    vm.sendLocation()
    expect(vm.locationStatusMessage).toContain('ابتدا موقعیت خود را پیدا کنید')

    vm.selectedLatLng = { lat: 35.71, lng: 51.41 }
    vm.hasManualLocationSelection = false
    vm.hasConfirmedAutoLocation = false
    vm.detectedLocationAccuracyM = 450
    vm.sendLocation()
    expect(vm.locationStatusMessage).toContain('تا وقتی موقعیت خودکار دقیق نشود')

    vm.hasConfirmedAutoLocation = true
    vm.detectedLocationAccuracyM = 80
    vm.sendLocation()
    expect(wrapper.emitted('select-location')).toContainEqual([35.71, 51.41])
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
  })

  it('renders inline camera zoom controls and queued captured media state', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      showCameraCapture: boolean
      cameraCaptureMode: 'inline' | 'native'
      cameraZoomCapability: { min: number; max: number; step: number } | null
      cameraZoomValue: number
      capturedCameraMedia: Array<{ id: string; file: File; type: 'photo' | 'video'; previewUrl: string }>
    }

    vm.showCameraCapture = true
    vm.cameraCaptureMode = 'inline'
    vm.cameraZoomCapability = { min: 1, max: 3, step: 0.5 }
    vm.cameraZoomValue = 2
    vm.capturedCameraMedia = [
      { id: 'photo', file: new File(['photo'], 'photo.jpg', { type: 'image/jpeg' }), type: 'photo', previewUrl: 'blob:photo' },
      { id: 'video', file: new File(['video'], 'video.mp4', { type: 'video/mp4' }), type: 'video', previewUrl: 'blob:video' },
    ]
    await nextTick()

    expect(wrapper.find('.camera-capture-overlay').exists()).toBe(true)
    expect(wrapper.find('.camera-zoom-panel').exists()).toBe(true)
    expect(wrapper.get('.camera-zoom-label').text()).toBe('2.0x')
    expect(wrapper.findAll('.camera-captured-item')).toHaveLength(2)
    expect(wrapper.find('.camera-captured-video-badge').text()).toContain('ویدئو')
    expect(wrapper.get('.camera-send-count').text()).toBe('2')
    expect(wrapper.get('.camera-capture-queue-label').text()).toContain('2 مورد آماده ارسال')
  })

  it('renders the inline camera error overlay and retry affordance', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      showCameraCapture: boolean
      cameraCaptureMode: 'inline' | 'native'
      cameraError: string
    }

    vm.showCameraCapture = true
    vm.cameraCaptureMode = 'inline'
    vm.cameraError = 'خطای دوربین'
    await nextTick()

    expect(wrapper.find('.camera-status-overlay.error').exists()).toBe(true)
    expect(wrapper.find('.camera-status-overlay.error').text()).toContain('خطای دوربین')
    expect(wrapper.find('.camera-error-btn').text()).toContain('تلاش مجدد')
  })

  it('renders the precise-location chooser and then shows platform-specific guidance steps', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      locationStatusMessage: string
      locationStatusTone: 'info' | 'error'
      hidePreciseLocationGuideForever: boolean
      selectedPreciseLocationGuidePlatform: 'android' | 'ios' | null
    }

    vm.activeTab = 'location'
    vm.locationStatusMessage = 'GPS هنوز دقیق نشده است'
    vm.locationStatusTone = 'error'
    vm.hidePreciseLocationGuideForever = false
    vm.selectedPreciseLocationGuidePlatform = null
    await nextTick()

    expect(wrapper.find('.location-status.is-error').exists()).toBe(true)
    expect(wrapper.find('.location-status-action').text()).toContain('تلاش مجدد')
    expect(wrapper.find('.precise-location-guide').exists()).toBe(true)
    expect(wrapper.findAll('.precise-location-guide-choice')).toHaveLength(2)

    await wrapper.findAll('.precise-location-guide-choice')[0]!.trigger('click')
    await nextTick()

    expect(wrapper.findAll('.precise-location-guide-steps li').length).toBeGreaterThan(0)
    expect(wrapper.find('.precise-location-guide-note').text()).toContain('تلاش مجدد')
  })

  it('opens the inline camera preview, syncs zoom controls, and captures a photo into the queue', async () => {
    const { track, stream, getUserMedia, getZoom } = installInlineCameraMocks()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D))
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function toBlob(callback: BlobCallback) {
      callback(new Blob(['captured-photo'], { type: 'image/jpeg' }))
    })

    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      showCameraCapture: boolean
      cameraCaptureMode: 'inline' | 'native'
      cameraZoomCapability: { min: number; max: number; step: number } | null
      cameraZoomValue: number
      cameraStream: unknown
      capturedCameraMedia: Array<{ file: File; type: 'photo' | 'video'; previewUrl: string }>
      openCameraCapture: () => void
      handleCameraZoomInput: (event: Event) => void
      nudgeCameraZoom: (direction: -1 | 1) => void
      handlePrimaryCameraAction: () => void
    }

    vm.openCameraCapture()
    await flushPromises()

    const preview = wrapper.get('video').element as HTMLVideoElement
    Object.defineProperty(preview, 'videoWidth', { configurable: true, value: 1280 })
    Object.defineProperty(preview, 'videoHeight', { configurable: true, value: 720 })

    expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({ audio: false }))
    expect(vm.showCameraCapture).toBe(true)
    expect(vm.cameraCaptureMode).toBe('inline')
    expect(vm.cameraStream).toBeTruthy()
    expect((vm.cameraStream as { getVideoTracks: () => unknown[] }).getVideoTracks()).toEqual([track])
    expect(vm.cameraZoomCapability).toEqual({ min: 1, max: 4, step: 0.5 })
    expect(vm.cameraZoomValue).toBe(2)

    vm.handleCameraZoomInput({ target: { value: '3.1' } } as unknown as Event)
    await flushPromises()
    expect(track.applyConstraints).toHaveBeenCalledWith({ advanced: [{ zoom: 3 }] })
    expect(getZoom()).toBe(3)
    expect(vm.cameraZoomValue).toBe(3)

    vm.nudgeCameraZoom(1)
    await flushPromises()
    expect(getZoom()).toBe(3.5)
    expect(vm.cameraZoomValue).toBe(3.5)

    vm.handlePrimaryCameraAction()
    await flushPromises()

    expect(vm.capturedCameraMedia).toHaveLength(1)
    expect(vm.capturedCameraMedia[0]).toMatchObject({
      type: 'photo',
      previewUrl: expect.stringContaining('blob:camera_'),
    })
    expect(vm.capturedCameraMedia[0]!.file.name).toMatch(/^camera_.*\.jpg$/)
  })

  it('records an inline video clip and queues it after stopping the recorder', async () => {
    vi.useFakeTimers()
    installInlineCameraMocks()

    class FakeMediaRecorder {
      static isTypeSupported(type: string) {
        return type.includes('webm')
      }

      state: 'inactive' | 'recording' = 'inactive'
      mimeType: string
      ondataavailable: ((event: { data: Blob }) => void) | null = null
      onstop: (() => void) | null = null

      constructor(_stream: MediaStream, options?: { mimeType?: string }) {
        this.mimeType = options?.mimeType || 'video/webm'
      }

      start() {
        this.state = 'recording'
        this.ondataavailable?.({ data: new Blob(['video-chunk'], { type: this.mimeType }) })
      }

      stop() {
        this.state = 'inactive'
        this.onstop?.()
      }
    }

    Object.defineProperty(globalThis, 'MediaRecorder', {
      configurable: true,
      writable: true,
      value: FakeMediaRecorder,
    })

    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      cameraMode: 'photo' | 'video'
      isRecording: boolean
      recordingDeciseconds: number
      capturedCameraMedia: Array<{ file: File; type: 'photo' | 'video'; previewUrl: string }>
      openCameraCapture: () => void
      setCameraMode: (mode: 'photo' | 'video') => Promise<void>
      handlePrimaryCameraAction: () => void
    }

    vm.openCameraCapture()
    await flushPromises()
    await vm.setCameraMode('video')
    await flushPromises()

    vm.handlePrimaryCameraAction()
    await nextTick()
    expect(vm.isRecording).toBe(true)

    await vi.advanceTimersByTimeAsync(250)
    expect(vm.recordingDeciseconds).toBeGreaterThan(0)

    vm.handlePrimaryCameraAction()
    await flushPromises()

    expect(vm.isRecording).toBe(false)
    expect(vm.capturedCameraMedia).toHaveLength(1)
    expect(vm.capturedCameraMedia[0]).toMatchObject({
      type: 'video',
      previewUrl: expect.stringContaining('blob:camera_'),
    })
    expect(vm.capturedCameraMedia[0]!.file.name).toMatch(/^camera_.*\.webm$/)
    vi.useRealTimers()
  })

  it('confirms an accurate auto-detected location after a consistent GPS follow-up reading', async () => {
    vi.useFakeTimers()
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })

    const positions = [
      makeGeoPosition(35.7001, 51.4002, 30),
      makeGeoPosition(35.70012, 51.40022, 20),
    ]
    const fallbackPosition = makeGeoPosition(35.70015, 51.40025, 22)
    const getCurrentPosition = vi.fn((resolve: PositionCallback) => {
      resolve(positions.shift() ?? fallbackPosition)
    })

    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition,
        watchPosition: vi.fn(() => 1),
        clearWatch: vi.fn(),
      },
    })

    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      selectedLatLng: { lat: number; lng: number } | null
      detectedLocationAccuracyM: number | null
      hasConfirmedAutoLocation: boolean
      hasManualLocationSelection: boolean
      locationStatusMessage: string
      locationStatusTone: 'info' | 'error'
      isLocating: boolean
      canSendLocation: boolean
      goToMyLocation: (silent?: boolean) => Promise<void>
    }

    vm.activeTab = 'location'
  await nextTick()
  vi.clearAllTimers()
    await vm.goToMyLocation()
    await flushPromises()

    expect(getCurrentPosition.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(vm.hasConfirmedAutoLocation).toBe(true)
    expect(vm.hasManualLocationSelection).toBe(false)
    expect(vm.detectedLocationAccuracyM).toBe(20)
    expect(vm.selectedLatLng).toEqual({ lat: 35.70012, lng: 51.40022 })
    expect(vm.locationStatusTone).toBe('info')
    expect(vm.locationStatusMessage).toContain('موقعیت شما پیدا شد')
    expect(vm.canSendLocation).toBe(true)
    expect(vm.isLocating).toBe(false)
    vi.useRealTimers()
  })

  it('keeps auto location unsendable when confirmation readings are unstable and contradictory', async () => {
    vi.useFakeTimers()
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })

    const positions = [
      makeGeoPosition(35.7001, 51.4002, 25),
      makeGeoPosition(36.2001, 52.1002, 20),
      makeGeoPosition(35.70015, 51.40025, 22),
      makeGeoPosition(36.2501, 52.1502, 18),
    ]
    const getCurrentPosition = vi.fn((resolve: PositionCallback) => {
      resolve(positions.shift()!)
    })

    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition,
        watchPosition: vi.fn(() => 1),
        clearWatch: vi.fn(),
      },
    })

    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      selectedLatLng: { lat: number; lng: number } | null
      detectedLocationAccuracyM: number | null
      hasConfirmedAutoLocation: boolean
      hasManualLocationSelection: boolean
      locationStatusMessage: string
      locationStatusTone: 'info' | 'error'
      isLocating: boolean
      canSendLocation: boolean
      goToMyLocation: (silent?: boolean) => Promise<void>
    }

    vm.activeTab = 'location'
  await nextTick()
  vi.clearAllTimers()
    await vm.goToMyLocation()
    await flushPromises()

    expect(getCurrentPosition.mock.calls.length).toBeGreaterThanOrEqual(4)
    expect(vm.hasConfirmedAutoLocation).toBe(false)
    expect(vm.hasManualLocationSelection).toBe(false)
    expect(vm.selectedLatLng).toEqual({ lat: 35.70015, lng: 51.40025 })
    expect(vm.detectedLocationAccuracyM).toBe(22)
    expect(vm.locationStatusTone).toBe('error')
    expect(vm.locationStatusMessage).toContain('دو موقعیت متناقض')
    expect(vm.canSendLocation).toBe(false)
    expect(vm.isLocating).toBe(false)
    vi.useRealTimers()
  })

  it('keeps an accurate single GPS reading blocked when the second confirmation never becomes reliable', async () => {
    vi.useFakeTimers()
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })

    const readings = [
      makeGeoPosition(35.7001, 51.4002, 25),
      makeGeoPosition(35.70011, 51.40021, 260),
      makeGeoPosition(35.70012, 51.40022, 22),
      makeGeoPosition(35.70013, 51.40023, 280),
    ]
    const getCurrentPosition = vi.fn((resolve: PositionCallback) => {
      resolve(readings.shift() ?? makeGeoPosition(35.70014, 51.40024, 250))
    })

    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition,
        watchPosition: vi.fn(() => 41),
        clearWatch: vi.fn(),
      },
    })

    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      selectedLatLng: { lat: number; lng: number } | null
      detectedLocationAccuracyM: number | null
      hasConfirmedAutoLocation: boolean
      hasManualLocationSelection: boolean
      locationStatusMessage: string
      locationStatusTone: 'info' | 'error'
      isLocating: boolean
      canSendLocation: boolean
      goToMyLocation: (silent?: boolean) => Promise<void>
    }

    vm.activeTab = 'location'
    await nextTick()
    vi.clearAllTimers()
    await vm.goToMyLocation()
    await flushPromises()

    expect(getCurrentPosition.mock.calls.length).toBeGreaterThanOrEqual(4)
    expect(vm.hasConfirmedAutoLocation).toBe(false)
    expect(vm.hasManualLocationSelection).toBe(false)
    expect(vm.selectedLatLng).toEqual({ lat: 35.70012, lng: 51.40022 })
    expect(vm.detectedLocationAccuracyM).toBe(22)
    expect(vm.locationStatusTone).toBe('error')
    expect(vm.locationStatusMessage).toContain('تایید دوم')
    expect(vm.canSendLocation).toBe(false)
    expect(vm.isLocating).toBe(false)
    vi.useRealTimers()
  })

  it('switches to native fallback with a descriptive hint when inline camera startup fails', async () => {
    const getUserMedia = vi.fn(async () => {
      throw { name: 'NotAllowedError', message: 'permission denied' }
    })

    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia },
    })

    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      cameraCaptureMode: 'inline' | 'native'
      cameraFallbackReason: string
      cameraError: string
      openCameraCapture: () => void
    }

    vm.openCameraCapture()
    await flushPromises()

    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(vm.cameraCaptureMode).toBe('native')
    expect(vm.cameraFallbackReason).toBe('stream-start-failed')
    expect(vm.cameraError).toBe('')
    expect(wrapper.find('.camera-status-overlay.native').exists()).toBe(true)
    expect(wrapper.text()).toContain('پیش نمایش زنده دوربین در این مرورگر در دسترس نیست.')
  })

  it('alerts and avoids recording when MediaRecorder is unavailable or unsupported', async () => {
    installInlineCameraMocks()
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      capturedCameraMedia: Array<unknown>
      openCameraCapture: () => void
      setCameraMode: (mode: 'photo' | 'video') => Promise<void>
      handlePrimaryCameraAction: () => void
    }

    vm.openCameraCapture()
    await flushPromises()
    await vm.setCameraMode('video')
    await flushPromises()

    Reflect.deleteProperty(globalThis, 'MediaRecorder')
    vm.handlePrimaryCameraAction()
    await flushPromises()

    expect(window.alert).toHaveBeenCalledWith('مرورگر شما از فیلم‌برداری پشتیبانی نمی‌کند.')
    expect(vm.capturedCameraMedia).toHaveLength(0)

    Object.defineProperty(globalThis, 'MediaRecorder', {
      configurable: true,
      writable: true,
      value: class BrokenMediaRecorder {
        static isTypeSupported() {
          return true
        }

        constructor() {
          throw new Error('broken recorder')
        }
      },
    })

    vm.handlePrimaryCameraAction()
    await flushPromises()

    expect(window.alert).toHaveBeenCalledWith('مرورگر شما از فیلم‌برداری پشتیبانی نمی‌کند.')
    expect(vm.capturedCameraMedia).toHaveLength(0)
  })

  it('dismisses the sheet on a downward drag but ignores drag-to-dismiss when the map owns the gesture', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      sheetRef: HTMLElement | null
      onTouchStart: (event: TouchEvent) => void
      onTouchMove: (event: TouchEvent) => void
      onTouchEnd: () => void
    }

    vm.sheetRef = wrapper.find('.attachment-sheet').element as HTMLElement

    vm.onTouchStart({
      target: { closest: () => null },
      touches: [{ clientY: 24 }],
    } as unknown as TouchEvent)
    vm.onTouchMove({
      touches: [{ clientY: 180 }],
    } as unknown as TouchEvent)
    vm.onTouchEnd()

    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])

    const guardedWrapper = mountAttachmentMenu()
    const guardedVm = guardedWrapper.vm as unknown as {
      sheetRef: HTMLElement | null
      onTouchStart: (event: TouchEvent) => void
      onTouchMove: (event: TouchEvent) => void
      onTouchEnd: () => void
    }

    guardedVm.sheetRef = guardedWrapper.find('.attachment-sheet').element as HTMLElement
    guardedVm.onTouchStart({
      target: { closest: (selector: string) => (selector === '.map-wrapper' ? {} : null) },
      touches: [{ clientY: 40 }],
    } as unknown as TouchEvent)
    guardedVm.onTouchMove({
      touches: [{ clientY: 220 }],
    } as unknown as TouchEvent)
    guardedVm.onTouchEnd()

    expect(guardedWrapper.emitted('update:modelValue')).toBeUndefined()
    expect(guardedVm.sheetRef?.style.transform || '').toBe('')
  })

  it('persists precise-location guide dismissal and lets users pick the platform manually', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      locationStatusMessage: string
      locationStatusTone: 'info' | 'error'
      hidePreciseLocationGuideForever: boolean
      selectedPreciseLocationGuidePlatform: 'android' | 'ios' | null
    }

    vm.activeTab = 'location'
    vm.locationStatusMessage = 'GPS هنوز دقیق نشده است'
    vm.locationStatusTone = 'error'
    vm.hidePreciseLocationGuideForever = false
    vm.selectedPreciseLocationGuidePlatform = null
    await nextTick()

    await wrapper.findAll('.precise-location-guide-choice')[1]!.trigger('click')
    await nextTick()
    expect(wrapper.findAll('.precise-location-guide-steps li').length).toBeGreaterThan(0)

    const dismissInput = wrapper.get('.precise-location-guide-dismiss input')
    await dismissInput.setValue(true)
    await nextTick()

    expect(wrapper.find('.precise-location-guide').exists()).toBe(false)
    expect(localStorage.length).toBe(1)

    const remount = mountAttachmentMenu()
    const remountVm = remount.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      locationStatusMessage: string
      locationStatusTone: 'info' | 'error'
    }

    remountVm.activeTab = 'location'
    remountVm.locationStatusMessage = 'GPS هنوز دقیق نشده است'
    remountVm.locationStatusTone = 'error'
    await nextTick()

    expect(remount.find('.precise-location-guide').exists()).toBe(false)
  })

  it('derives fallback copy, guide state, zoom controls, and recording labels across alternate helper states', async () => {
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      cameraCaptureMode: 'inline' | 'native'
      cameraMode: 'photo' | 'video'
      cameraFallbackReason: 'none' | 'insecure-context' | 'media-devices-unavailable' | 'stream-start-failed'
      cameraFallbackDetails: string
      cameraZoomCapability: { min: number; max: number; step: number } | null
      cameraZoomValue: number
      recordingDeciseconds: number
      activeTab: 'gallery' | 'file' | 'location'
      hidePreciseLocationGuideForever: boolean
      selectedPreciseLocationGuidePlatform: 'android' | 'ios' | null
      isLocating: boolean
      hasManualLocationSelection: boolean
      hasConfirmedAutoLocation: boolean
      detectedLocationLatLng: [number, number] | null
      detectedLocationAccuracyM: number | null
      capturedCameraMedia: Array<{ id: string; file: File; type: 'photo' | 'video'; previewUrl: string }>
      cameraFallbackReasonText: string
      nativeCameraFallbackTitle: string
      nativeCameraFallbackHint: string
      preciseLocationGuideNeedsPlatformChoice: boolean
      preciseLocationGuideDetectedLabel: string
      preciseLocationGuideSteps: string[]
      shouldShowPreciseLocationGuide: boolean
      locationModeDebugText: string
      capturedMediaQueueLabel: string
      hasCameraZoomControl: boolean
      canZoomOut: boolean
      canZoomIn: boolean
      formattedRecordingTime: string
      cameraZoomDisplay: string
      selectedLatLngDebugText: string
      detectedLatLngDebugText: string
      detectedAccuracyDebugText: string
    }

    vm.cameraCaptureMode = 'native'
    vm.cameraMode = 'video'
    vm.cameraFallbackReason = 'media-devices-unavailable'
    vm.cameraFallbackDetails = ''
    vm.activeTab = 'location'
    vm.hidePreciseLocationGuideForever = false
    vm.selectedPreciseLocationGuidePlatform = null
    vm.recordingDeciseconds = 615
    vm.cameraZoomCapability = { min: 1, max: 3, step: 0.5 }
    vm.cameraZoomValue = 1
    vm.capturedCameraMedia = [{
      id: 'queued',
      file: new File(['queued'], 'queued.jpg', { type: 'image/jpeg' }),
      type: 'photo',
      previewUrl: 'blob:queued',
    }]
    await nextTick()

    expect(vm.cameraFallbackReasonText).toContain('getUserMedia')
    expect(vm.nativeCameraFallbackTitle).toContain('فیلم برداری')
    expect(vm.nativeCameraFallbackHint).toContain('ثانیه شمار سفارشی')
    expect(vm.preciseLocationGuideNeedsPlatformChoice).toBe(true)
    expect(vm.preciseLocationGuideDetectedLabel).toContain('فعال‌سازی')
    expect(vm.preciseLocationGuideSteps).toEqual([])
    expect(vm.shouldShowPreciseLocationGuide).toBe(true)
    expect(vm.capturedMediaQueueLabel).toBe('۱ مورد آماده ارسال')
    expect(vm.hasCameraZoomControl).toBe(false)
    expect(vm.canZoomOut).toBe(false)
    expect(vm.canZoomIn).toBe(true)
    expect(vm.formattedRecordingTime).toBe('1:01.5')
    expect(vm.cameraZoomDisplay).toBe('1.0x')
    expect(vm.selectedLatLngDebugText).toBe('—')
    expect(vm.detectedLatLngDebugText).toBe('—')
    expect(vm.detectedAccuracyDebugText).toBe('—')

    vm.selectedPreciseLocationGuidePlatform = 'android'
    await nextTick()
    expect(vm.preciseLocationGuideDetectedLabel).toBe('راهنمای اندروید')
    expect(vm.preciseLocationGuideSteps.length).toBeGreaterThan(0)

    vm.selectedPreciseLocationGuidePlatform = 'ios'
    await nextTick()
    expect(vm.preciseLocationGuideDetectedLabel).toBe('راهنمای آيفون')
    expect(vm.preciseLocationGuideSteps.length).toBeGreaterThan(0)

    vm.isLocating = true
    await nextTick()
    expect(vm.locationModeDebugText).toBe('در حال مکان‌یابی')

    vm.isLocating = false
    vm.hasManualLocationSelection = true
    await nextTick()
    expect(vm.locationModeDebugText).toBe('انتخاب دستی پین')

    vm.hasManualLocationSelection = false
    vm.hasConfirmedAutoLocation = true
    vm.detectedLocationLatLng = [35.7, 51.4]
    vm.detectedLocationAccuracyM = 19
    await nextTick()
    expect(vm.locationModeDebugText).toBe('تایید خودکار GPS')
    expect(vm.detectedLatLngDebugText).toContain('35.700000')
    expect(vm.detectedAccuracyDebugText).toBe('19m')

    vm.hasConfirmedAutoLocation = false
    await nextTick()
    expect(vm.locationModeDebugText).toBe('در انتظار تایید')

    vm.cameraCaptureMode = 'inline'
    vm.cameraZoomCapability = { min: 1, max: 3, step: 0.5 }
    vm.cameraZoomValue = 3
    await nextTick()
    expect(vm.hasCameraZoomControl).toBe(true)
    expect(vm.canZoomOut).toBe(true)
    expect(vm.canZoomIn).toBe(false)

    vm.cameraZoomValue = 10
    await nextTick()
    expect(vm.cameraZoomDisplay).toBe('10x')
  })

  it('covers camera fallback helpers and zoom reset/apply failure branches', async () => {
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })
    Reflect.deleteProperty(navigator, 'mediaDevices')

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      cameraZoomCapability: { min: number; max: number; step: number } | null
      cameraZoomValue: number
      cameraStream: { getVideoTracks: () => unknown[]; getTracks: () => unknown[] } | null
      getInlineCameraFallbackReason: () => string
      describeCameraStartError: (error: unknown) => string
      syncCameraZoomCapability: (track?: unknown) => Promise<void>
      applyCameraZoom: (value: number) => Promise<void>
    }

    expect(vm.getInlineCameraFallbackReason()).toBe('media-devices-unavailable')
    expect(vm.describeCameraStartError({ name: 'NotReadableError' })).toContain('دوربین در اختیار برنامه دیگری است')
    expect(vm.describeCameraStartError({ name: 'NotFoundError' })).toContain('هیچ دوربین در دسترس')
    expect(vm.describeCameraStartError({ name: 'OverconstrainedError' })).toContain('تنظیمات لازم')
    expect(vm.describeCameraStartError({ message: '  custom error  ' })).toBe('custom error')

    vm.cameraZoomCapability = { min: 1, max: 4, step: 0.5 }
    vm.cameraZoomValue = 2
    await vm.syncCameraZoomCapability(null)
    expect(vm.cameraZoomCapability).toBeNull()
    expect(vm.cameraZoomValue).toBe(1)

    const invalidTrack = {
      getCapabilities: () => ({ zoom: { min: 4, max: 1, step: 0.5 } }),
      getSettings: () => ({ zoom: 2 }),
    }
    await vm.syncCameraZoomCapability(invalidTrack)
    expect(vm.cameraZoomCapability).toBeNull()

    const validTrack = {
      getCapabilities: () => ({ zoom: { min: 1, max: 4, step: 0.5 } }),
      getSettings: () => ({ zoom: 2.2 }),
      applyConstraints: vi.fn(async () => {
        throw new Error('zoom failed')
      }),
      stop: vi.fn(),
    }

    vm.cameraStream = {
      getVideoTracks: () => [validTrack],
      getTracks: () => [validTrack],
    }

    await vm.syncCameraZoomCapability(validTrack)
    expect(vm.cameraZoomCapability).toEqual({ min: 1, max: 4, step: 0.5 })
    expect(vm.cameraZoomValue).toBe(2)

    await vm.applyCameraZoom(9)

    expect(validTrack.applyConstraints).toHaveBeenCalledWith({ advanced: [{ zoom: 4 }] })
    expect(vm.cameraZoomValue).toBe(2)
    expect(warnSpy).toHaveBeenCalledWith('Camera zoom apply failed:', expect.any(Error))

    warnSpy.mockRestore()
  })

  it('covers camera queue no-ops, capture failures, preview cleanup, and native capture routing', async () => {
    const photoClick = vi.fn()
    const videoClick = vi.fn()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const originalRandomUUID = globalThis.crypto.randomUUID
    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      configurable: true,
      value: undefined,
    })

    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      cameraPhotoInput: { click: () => void } | null
      cameraVideoInput: { click: () => void } | null
      cameraPreviewRef: HTMLVideoElement | null
      cameraStream: { getTracks: () => Array<{ stop: () => void }> } | null
      capturedCameraMedia: Array<{ id: string; file: File; type: 'photo' | 'video'; previewUrl: string }>
      mediaRecorder: { state: string; onstop: (() => void) | null; stop: () => void } | null
      createCapturedMediaId: () => string
      createCameraAlbumId: () => string
      revokeCapturedMediaPreview: (url?: string) => void
      removeCapturedMedia: (itemId: string) => void
      sendCapturedMediaQueue: () => Promise<void>
      capturePhoto: () => Promise<void>
      stopCameraTracks: () => void
      cleanupCamera: (discardRecording?: boolean, clearCapturedMedia?: boolean) => void
      supportsInlineCameraPreview: () => boolean
      openNativeCameraCapture: (mode?: 'photo' | 'video') => void
      attachCameraStream: (stream: MediaStream) => Promise<void>
      handleCameraZoomInput: (event: Event) => void
      nudgeCameraZoom: (direction: -1 | 1) => void
    }

    expect(vm.createCapturedMediaId()).toMatch(/^camera_capture_/)
    expect(vm.createCameraAlbumId()).toMatch(/^camera_album_/)

    vm.revokeCapturedMediaPreview('https://example.test/file.jpg')
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith('https://example.test/file.jpg')

    vm.removeCapturedMedia('missing')
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith(expect.stringContaining('missing'))

    await vm.sendCapturedMediaQueue()
    expect(wrapper.emitted('select-media')).toBeUndefined()

    vm.cameraPhotoInput = { click: photoClick }
    vm.cameraVideoInput = { click: videoClick }
    vm.openNativeCameraCapture('photo')
    vm.openNativeCameraCapture('video')
    expect(photoClick).toHaveBeenCalledTimes(1)
    expect(videoClick).toHaveBeenCalledTimes(1)

    expect(vm.supportsInlineCameraPreview()).toBe(false)

    await vm.capturePhoto()
    expect(window.alert).not.toHaveBeenCalledWith('خطا در ثبت تصویر')

    const pause = vi.fn(() => {
      throw new Error('pause failed')
    })
    const preview = document.createElement('video')
    Object.defineProperty(preview, 'pause', { configurable: true, value: pause })
    Object.defineProperty(preview, 'srcObject', { configurable: true, writable: true, value: null })
    const stop = vi.fn()
    vm.cameraPreviewRef = preview
    vm.cameraStream = { getTracks: () => [{ stop }] }
    vm.stopCameraTracks()
    expect(stop).toHaveBeenCalled()

    vm.cameraPreviewRef = null
    await vm.attachCameraStream({} as MediaStream)
    expect(warnSpy).not.toHaveBeenCalledWith('Camera preview play failed:', expect.anything())

    const failingPreview = document.createElement('video')
    Object.defineProperty(failingPreview, 'play', { configurable: true, value: vi.fn(async () => { throw new Error('play failed') }) })
    Object.defineProperty(failingPreview, 'srcObject', { configurable: true, writable: true, value: null })
    vm.cameraPreviewRef = failingPreview
    await vm.attachCameraStream({} as MediaStream)
    expect(warnSpy).toHaveBeenCalledWith('Camera preview play failed:', expect.any(Error))

    vm.handleCameraZoomInput({ target: null } as unknown as Event)
    vm.handleCameraZoomInput({ target: { value: 'not-a-number' } } as unknown as Event)
    vm.nudgeCameraZoom(1)

    vm.capturedCameraMedia = [{
      id: 'keep',
      file: new File(['keep'], 'keep.jpg', { type: 'image/jpeg' }),
      type: 'photo',
      previewUrl: 'blob:keep',
    }]
    vm.cleanupCamera(true, false)
    expect(vm.capturedCameraMedia).toHaveLength(1)

    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      configurable: true,
      value: originalRandomUUID,
    })
    warnSpy.mockRestore()
  })

  it('re-centers auto pins, resets location draft state, and auto-starts lookup when opening the location tab', async () => {
    vi.useFakeTimers()
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })

    const getCurrentPosition = vi.fn((resolve: PositionCallback) => {
      resolve(makeGeoPosition(35.7001, 51.4002, 30))
    })

    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition,
        watchPosition: vi.fn(() => 1),
        clearWatch: vi.fn(),
      },
    })

    const invalidateSize = vi.fn()
    const setView = vi.fn()
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      mapRef: { leafletObject: { invalidateSize: () => void; setView: (...args: unknown[]) => void; getZoom: () => number; getCenter: () => { lat: number; lng: number } } } | null
      selectedLatLng: { lat: number; lng: number } | null
      detectedLocationLatLng: [number, number] | null
      detectedLocationAccuracyM: number | null
      hasManualLocationSelection: boolean
      hasConfirmedAutoLocation: boolean
      isLocating: boolean
      locationStatusMessage: string
      mapCenter: [number, number]
      getAutoPinnedLatLng: () => [number, number] | null
      refreshLocationMapViewport: (recenterAutoPin?: boolean) => Promise<void>
      resetLocationDraft: () => void
      onMapMoveEnd: () => void
    }

    vm.mapRef = {
      leafletObject: {
        invalidateSize,
        setView,
        getZoom: () => 14,
        getCenter: () => ({ lat: 35.61, lng: 51.31 }),
      },
    }
    vm.selectedLatLng = { lat: 35.61, lng: 51.31 }
    vm.activeTab = 'location'
    await nextTick()
    await nextTick()

    expect(vm.activeTab).toBe('location')
    expect(vm.getAutoPinnedLatLng()).toEqual([35.61, 51.31])

    await vm.refreshLocationMapViewport(true)

    await vi.advanceTimersByTimeAsync(350)
    await flushPromises()

    expect(getCurrentPosition).toHaveBeenCalled()

    vm.hasManualLocationSelection = false
    vm.onMapMoveEnd()
    await nextTick()
    expect(vm.hasManualLocationSelection).toBe(false)

    setView.mockClear()
    vm.hasManualLocationSelection = true
  expect(vm.getAutoPinnedLatLng()).toBeNull()
    await vm.refreshLocationMapViewport(true)
    expect(setView).not.toHaveBeenCalled()

    vm.selectedLatLng = { lat: 30, lng: 50 }
    vm.detectedLocationLatLng = [31, 51]
    vm.detectedLocationAccuracyM = 45
    vm.hasManualLocationSelection = true
    vm.hasConfirmedAutoLocation = true
    vm.isLocating = true
    vm.locationStatusMessage = 'در حال یافتن موقعیت شما...'
    vm.resetLocationDraft()

    expect(vm.selectedLatLng).toBeNull()
    expect(vm.detectedLocationLatLng).toBeNull()
    expect(vm.detectedLocationAccuracyM).toBeNull()
    expect(vm.hasManualLocationSelection).toBe(false)
    expect(vm.hasConfirmedAutoLocation).toBe(false)
    expect(vm.isLocating).toBe(false)
    expect(vm.locationStatusMessage).toBe('')
    expect(vm.mapCenter).toEqual([35.6892, 51.389])

    vi.useRealTimers()
  })

  it('uses the timer fallback for direct non-image gallery sends and clears persisted guide dismissal state', async () => {
    vi.useFakeTimers()
    Object.defineProperty(window, 'requestAnimationFrame', {
      configurable: true,
      writable: true,
      value: undefined,
    })

    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      multiPreviewFiles: File[] | null
      cameraEditingItemId: string | null
      onGalleryFile: (event: Event) => Promise<void>
      onMultiPreviewConfirm: (files: File[]) => void
      cameraEditingItem: { id: string } | null
      handlePreciseLocationGuideDismissChange: (event: Event) => void
      formatAccuracyDebugText: (value: number | null) => string
      formatLatLngDebugText: (value: { lat: number; lng: number } | null) => string
      stringifyLocationDebugDetails: (details?: string | Record<string, unknown>) => string
      formatLocationAccuracy: (accuracyM: number) => string
      isWithinIranLocationBounds: (lat: number, lng: number) => boolean
      getBetterPosition: (currentBest: GeolocationPosition | null, candidate: GeolocationPosition) => GeolocationPosition
      getPositionConsistencyThresholdMeters: (primary: GeolocationPosition, confirmation: GeolocationPosition) => number
      getCurrentLocationMapCenter: () => { lat: number; lng: number }
      mapRef: { leafletObject: { getCenter: () => { lat: number; lng: number } } } | null
      mapCenter: [number, number]
    }

    const videoFile = new File(['video'], 'clip.webm', { type: 'video/webm' })
    const input = {
      files: [videoFile],
      value: 'picked',
    }

    const galleryPromise = vm.onGalleryFile({ target: input } as unknown as Event)
    await vi.runAllTimersAsync()
    await galleryPromise
    await nextTick()

    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
    expect(wrapper.emitted('select-media')).toContainEqual([videoFile, null, 0, 1])
    expect(input.value).toBe('')

    vm.multiPreviewFiles = [videoFile]
    vm.onMultiPreviewConfirm([videoFile])
    await nextTick()
    expect(vm.multiPreviewFiles).toBeNull()

    vm.cameraEditingItemId = 'missing'
    await nextTick()
    expect(vm.cameraEditingItem).toBeNull()

    localStorage.setItem('chat_precise_location_guide_hidden_v1', '1')
    vm.handlePreciseLocationGuideDismissChange({ target: { checked: false } } as unknown as Event)
    expect(localStorage.getItem('chat_precise_location_guide_hidden_v1')).toBeNull()

    expect(vm.formatAccuracyDebugText(null)).toBe('—')
    expect(vm.formatAccuracyDebugText(24.2)).toBe('24m')
    expect(vm.formatLatLngDebugText(null)).toBe('—')
    expect(vm.formatLatLngDebugText({ lat: 35.7, lng: 51.4 })).toContain('35.700000')
    expect(vm.stringifyLocationDebugDetails('plain')).toBe('plain')
    expect(vm.stringifyLocationDebugDetails({ accuracy: 12.34, manual: true, label: 'gps' })).toContain('manual=yes')
    expect(vm.formatLocationAccuracy(1500)).toBe('1.5 کیلومتر')
    expect(vm.isWithinIranLocationBounds(35.7, 51.4)).toBe(true)
    expect(vm.isWithinIranLocationBounds(42, 20)).toBe(false)

    const coarse = makeGeoPosition(35.7, 51.4, 80)
    const precise = makeGeoPosition(35.7001, 51.4001, 20)
    expect(vm.getBetterPosition(null, coarse)).toBe(coarse)
    expect(vm.getBetterPosition(coarse, precise)).toBe(precise)
    expect(vm.getBetterPosition(precise, coarse)).toBe(precise)
    expect(vm.getPositionConsistencyThresholdMeters(makeGeoPosition(35.7, 51.4, 10), makeGeoPosition(35.7001, 51.4001, 15))).toBe(75)
    expect(vm.getPositionConsistencyThresholdMeters(makeGeoPosition(35.7, 51.4, 400), makeGeoPosition(35.7001, 51.4001, 500))).toBe(300)

    vm.mapRef = {
      leafletObject: {
        getCenter: () => ({ lat: 36.1, lng: 52.2 }),
      },
    }
    expect(vm.getCurrentLocationMapCenter()).toEqual({ lat: 36.1, lng: 52.2 })

    vm.mapRef = null
    vm.mapCenter = [34.2, 50.2]
    expect(vm.getCurrentLocationMapCenter()).toEqual({ lat: 34.2, lng: 50.2 })

    vi.useRealTimers()
  })

  it('covers precise-location platform/storage helpers and detected-location preview branches', async () => {
    const originalUserAgent = navigator.userAgent
    const originalPlatform = navigator.platform
    const originalMaxTouchPoints = navigator.maxTouchPoints

    const wrapper = mountAttachmentMenu()
    const setView = vi.fn()
    const vm = wrapper.vm as unknown as {
      hasConfirmedAutoLocation: boolean
      hasManualLocationSelection: boolean
      selectedLatLng: { lat: number; lng: number } | null
      detectedLocationLatLng: [number, number] | null
      detectedLocationAccuracyM: number | null
      locationStatusMessage: string
      locationStatusTone: 'info' | 'error'
      mapCenter: [number, number]
      mapRef: { leafletObject: { setView: (...args: unknown[]) => void; getCenter: () => { lat: number; lng: number } } } | null
      detectPreciseLocationGuidePlatform: () => 'android' | 'ios' | null
      readPreciseLocationGuideDismissed: () => boolean
      writePreciseLocationGuideDismissed: (hidden: boolean) => void
      shouldPromoteDetectedLocation: (accuracy: number) => boolean
      shouldPreviewDetectedLocation: (lat: number, lng: number, accuracy: number) => boolean
      updateResolvedLocationStatus: (position: GeolocationPosition) => void
      applyDetectedLocation: (position: GeolocationPosition) => void
    }

    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 (Linux; Android 14)',
    })
    expect(vm.detectPreciseLocationGuidePlatform()).toBe('android')

    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    })
    Object.defineProperty(navigator, 'platform', {
      configurable: true,
      value: 'MacIntel',
    })
    Object.defineProperty(navigator, 'maxTouchPoints', {
      configurable: true,
      value: 2,
    })
    expect(vm.detectPreciseLocationGuidePlatform()).toBe('ios')

    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage-read-failed')
    })
    expect(vm.readPreciseLocationGuideDismissed()).toBe(false)
    getItemSpy.mockRestore()

    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage-write-failed')
    })
    expect(() => vm.writePreciseLocationGuideDismissed(true)).not.toThrow()
    setItemSpy.mockRestore()

    const removeItemSpy = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('storage-remove-failed')
    })
    expect(() => vm.writePreciseLocationGuideDismissed(false)).not.toThrow()
    removeItemSpy.mockRestore()

    vm.hasConfirmedAutoLocation = false
    expect(vm.shouldPromoteDetectedLocation(700)).toBe(false)
    vm.hasConfirmedAutoLocation = true
    expect(vm.shouldPromoteDetectedLocation(700)).toBe(true)
    vm.hasConfirmedAutoLocation = false

    vm.mapCenter = [35.6892, 51.389]
    vm.mapRef = {
      leafletObject: {
        setView,
        getCenter: () => ({ lat: 35.6892, lng: 51.389 }),
      },
    }

    expect(vm.shouldPreviewDetectedLocation(35.7, 51.4, 3000)).toBe(true)
    expect(vm.shouldPreviewDetectedLocation(10, 10, 6001)).toBe(false)
    expect(vm.shouldPreviewDetectedLocation(10, 10, 3000)).toBe(false)

    vm.updateResolvedLocationStatus(makeGeoPosition(35.7, 51.4, 40))
    expect(vm.locationStatusTone).toBe('info')
    expect(vm.locationStatusMessage).toContain('موقعیت شما پیدا شد')

    vm.updateResolvedLocationStatus(makeGeoPosition(35.7, 51.4, 240))
    expect(vm.locationStatusTone).toBe('error')
    expect(vm.locationStatusMessage).toContain('خودکار هنوز دقیق نیست')

    vm.hasManualLocationSelection = true
    vm.selectedLatLng = { lat: 33.1, lng: 50.2 }
    vm.applyDetectedLocation(makeGeoPosition(10, 10, 900))
    expect(vm.selectedLatLng).toEqual({ lat: 33.1, lng: 50.2 })
    expect(setView).not.toHaveBeenCalled()

    vm.hasManualLocationSelection = false
    vm.selectedLatLng = null
    vm.applyDetectedLocation(makeGeoPosition(35.7003, 51.4004, 55))
    expect(vm.selectedLatLng).toEqual({ lat: 35.7003, lng: 51.4004 })
    expect(vm.detectedLocationLatLng).toEqual([35.7003, 51.4004])
    expect(vm.detectedLocationAccuracyM).toBe(55)
    expect(setView).toHaveBeenCalled()

    Object.defineProperty(navigator, 'userAgent', {
      configurable: true,
      value: originalUserAgent,
    })
    Object.defineProperty(navigator, 'platform', {
      configurable: true,
      value: originalPlatform,
    })
    Object.defineProperty(navigator, 'maxTouchPoints', {
      configurable: true,
      value: originalMaxTouchPoints,
    })
  })

  it('covers confirmation/watch geolocation helpers and error-message branches', async () => {
    vi.useFakeTimers()
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })

    const clearWatch = vi.fn()
    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      activeLocationLookupId: number
      requestStableAutoConfirmation: (basePosition: GeolocationPosition, lookupId: number) => Promise<{ position: GeolocationPosition | null; unstable: boolean }>
      requestWatchPosition: (options: PositionOptions, waitMs?: number) => Promise<GeolocationPosition>
      requestBestWatchPosition: (
        options: PositionOptions,
        waitMs?: number,
        desiredAccuracyM?: number,
        onProgress?: (position: GeolocationPosition) => void,
      ) => Promise<GeolocationPosition>
      getGeolocationPermissionState: () => Promise<PermissionState | null>
      getLocationErrorMessage: (error: GeolocationPositionError | null) => string
    }

    vm.activeTab = 'location'
    vm.activeLocationLookupId = 9

    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition: vi.fn((resolve: PositionCallback) => {
          resolve(makeGeoPosition(35.7, 51.4, 280))
        }),
        watchPosition: vi.fn((success: PositionCallback) => {
          success(makeGeoPosition(35.71, 51.41, 120))
          return 21
        }),
        clearWatch,
      },
    })

    const lowAccuracyConfirmation = await vm.requestStableAutoConfirmation(makeGeoPosition(35.7, 51.4, 25), 9)
    expect(lowAccuracyConfirmation).toEqual({ position: null, unstable: false })

    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition: vi.fn((resolve: PositionCallback) => {
          resolve(makeGeoPosition(36.4, 52.5, 20))
        }),
        watchPosition: vi.fn((success: PositionCallback) => {
          success(makeGeoPosition(35.71, 51.41, 120))
          return 22
        }),
        clearWatch,
      },
    })

    const unstableConfirmation = await vm.requestStableAutoConfirmation(makeGeoPosition(35.7, 51.4, 25), 9)
    expect(unstableConfirmation).toEqual({ position: null, unstable: true })

    const watchPosition = vi.fn((success: PositionCallback, error: PositionErrorCallback) => {
      success(makeGeoPosition(35.71, 51.41, 180))
      error({ code: 2, message: 'unavailable', PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 } as GeolocationPositionError)
      return 23
    })
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition: vi.fn(),
        watchPosition,
        clearWatch,
      },
    })

    const directWatch = await vm.requestWatchPosition({ enableHighAccuracy: true }, 500)
    expect(directWatch.coords.accuracy).toBe(180)

    const progress: number[] = []
    watchPosition.mockImplementation((success: PositionCallback, error: PositionErrorCallback) => {
      success(makeGeoPosition(35.71, 51.41, 400))
      success(makeGeoPosition(35.7103, 51.4104, 130))
      error({ code: 2, message: 'still coarse', PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 } as GeolocationPositionError)
      return 24
    })

    const bestWatch = await vm.requestBestWatchPosition({ enableHighAccuracy: true }, 500, 60, (position) => {
      progress.push(position.coords.accuracy)
    })
    expect(progress).toEqual([400, 130])
    expect(bestWatch.coords.accuracy).toBe(130)

    Object.defineProperty(navigator, 'permissions', {
      configurable: true,
      value: {},
    })
    expect(await vm.getGeolocationPermissionState()).toBeNull()

    Object.defineProperty(navigator, 'permissions', {
      configurable: true,
      value: {
        query: vi.fn(async () => {
          throw new Error('permission-query-failed')
        }),
      },
    })
    expect(await vm.getGeolocationPermissionState()).toBeNull()

    expect(vm.getLocationErrorMessage(null)).toContain('امکان دریافت موقعیت')
    expect(vm.getLocationErrorMessage({ code: 1, message: 'denied', PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 } as GeolocationPositionError)).toContain('مسدود است')
    expect(vm.getLocationErrorMessage({ code: 3, message: 'timeout', PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 } as GeolocationPositionError)).toContain('زمان مناسب')
    expect(vm.getLocationErrorMessage({ code: 2, message: 'unavailable', PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 } as GeolocationPositionError)).toContain('مرورگر نتوانست')
    expect(vm.getLocationErrorMessage({ code: 99, message: 'other', PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 } as GeolocationPositionError)).toContain('امکان دریافت موقعیت')

    vi.useRealTimers()
  })

  it('covers the permission-denied branch in goToMyLocation', async () => {
    vi.useFakeTimers()
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })

    const deniedWrapper = mountAttachmentMenu()
    const deniedVm = deniedWrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      locationStatusMessage: string
      locationStatusTone: 'info' | 'error'
      isLocating: boolean
      goToMyLocation: (silent?: boolean) => Promise<void>
    }

    Object.defineProperty(navigator, 'permissions', {
      configurable: true,
      value: {
        query: vi.fn(async () => ({ state: 'denied' })),
      },
    })
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition: vi.fn(),
        watchPosition: vi.fn(() => 1),
        clearWatch: vi.fn(),
      },
    })

    deniedVm.activeTab = 'location'
    await nextTick()
    vi.clearAllTimers()
    vi.useRealTimers()
    await deniedVm.goToMyLocation()
    await flushPromises()

    expect(deniedVm.locationStatusTone).toBe('error')
    expect(deniedVm.locationStatusMessage).toContain('مسدود است')
    expect(deniedVm.isLocating).toBe(false)
  })

  it('falls back through precise and watch geolocation paths before blocking unconfirmed accurate fixes', async () => {
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })

    const geoTimeout = { code: 3, message: 'timeout', PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 } as GeolocationPositionError
    const geoUnavailable = { code: 2, message: 'unavailable', PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 } as GeolocationPositionError
    const getCurrentPosition = vi.fn((resolve: PositionCallback, reject: PositionErrorCallback) => {
      const callNumber = getCurrentPosition.mock.calls.length
      if (callNumber === 1) {
        reject(geoUnavailable)
        return
      }
      reject(geoTimeout)
    })
    const watchPosition = vi.fn((success: PositionCallback) => {
      const callNumber = watchPosition.mock.calls.length
      if (callNumber === 1) {
        success(makeGeoPosition(35.7, 51.4, 320))
        return 71
      }

      success(makeGeoPosition(35.7001, 51.4001, 180))
      success(makeGeoPosition(35.7002, 51.4002, 70))
      return 72
    })

    Object.defineProperty(navigator, 'permissions', {
      configurable: true,
      value: {
        query: vi.fn(async () => ({ state: 'prompt' })),
      },
    })
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition,
        watchPosition,
        clearWatch: vi.fn(),
      },
    })

    const wrapper = mountAttachmentMenu()
    const vm = wrapper.vm as unknown as {
      activeTab: 'gallery' | 'file' | 'location'
      selectedLatLng: { lat: number; lng: number } | null
      detectedLocationAccuracyM: number | null
      hasConfirmedAutoLocation: boolean
      locationStatusMessage: string
      locationStatusTone: 'info' | 'error'
      canSendLocation: boolean
      isLocating: boolean
      goToMyLocation: (silent?: boolean) => Promise<void>
    }

    vm.activeTab = 'location'
    await nextTick()
    await vm.goToMyLocation()
    await flushPromises()

    expect(getCurrentPosition).toHaveBeenCalledTimes(4)
    expect(watchPosition).toHaveBeenCalledTimes(2)
    expect(vm.selectedLatLng).toEqual({ lat: 35.7002, lng: 51.4002 })
    expect(vm.detectedLocationAccuracyM).toBe(70)
    expect(vm.hasConfirmedAutoLocation).toBe(false)
    expect(vm.locationStatusTone).toBe('error')
    expect(vm.locationStatusMessage).toContain('تایید دوم')
    expect(vm.canSendLocation).toBe(false)
    expect(vm.isLocating).toBe(false)
  })
})