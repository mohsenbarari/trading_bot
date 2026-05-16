import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@vue-leaflet/vue-leaflet', () => ({
  LMap: {
    name: 'LMap',
    props: ['zoom', 'center', 'useGlobalLeaflet'],
    template: '<div class="l-map-stub"><slot /></div>',
  },
  LTileLayer: {
    name: 'LTileLayer',
    props: ['url', 'attribution'],
    template: '<div class="l-tile-layer-stub"></div>',
  },
  LMarker: {
    name: 'LMarker',
    props: ['latLng'],
    template: '<div class="l-marker-stub"><slot /></div>',
  },
  LIcon: {
    name: 'LIcon',
    props: ['iconSize', 'iconAnchor'],
    template: '<div class="l-icon-stub"><slot /></div>',
  },
}))

describe('LocationViewerModal.vue', () => {
  it('does not render when modelValue is false', async () => {
    const LocationViewerModal = (await import('./LocationViewerModal.vue')).default
    const wrapper = mount(LocationViewerModal, {
      props: {
        modelValue: false,
        location: { lat: 35.7, lng: 51.4 },
      },
    })

    expect(wrapper.find('.location-viewer-overlay').exists()).toBe(false)
  })

  it('emits close and opens the external map URL for the provided coordinates', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const LocationViewerModal = (await import('./LocationViewerModal.vue')).default
    const wrapper = mount(LocationViewerModal, {
      props: {
        modelValue: true,
        location: { lat: 35.6892, lng: 51.389 },
      },
      global: {
        stubs: {
          transition: false,
        },
      },
    })

    expect(wrapper.find('.location-viewer-overlay').exists()).toBe(true)
    expect(wrapper.text()).toContain('موقعیت مکانی')

    await wrapper.get('.back-btn').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([false])

    await wrapper.get('.external-btn').trigger('click')
    expect(openSpy).toHaveBeenCalledWith('https://www.google.com/maps?q=35.6892,51.389', '_blank')
  })
})