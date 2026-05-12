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

function mountAttachmentMenu() {
  return mount(AttachmentMenu, {
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
}

describe('AttachmentMenu.vue', () => {
  const originalSecureContext = window.isSecureContext
  const originalGeolocation = navigator.geolocation

  beforeEach(() => {
    attachmentMenuMocks.pushBackStateMock.mockReset()
    attachmentMenuMocks.popBackStateMock.mockReset()
    vi.spyOn(console, 'info').mockImplementation(() => {})

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
  })

  afterEach(() => {
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
})