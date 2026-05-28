import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'

import GalleryPreviewModal from './GalleryPreviewModal.vue'

const ImageEditorModalStub = defineComponent({
  name: 'ImageEditorModalStub',
  props: {
    file: {
      type: Object,
      required: true,
    },
  },
  emits: ['confirm', 'cancel'],
  setup(props, { emit }) {
    return () =>
      h('div', { 'data-test': 'image-editor-stub' }, [
        h('span', { class: 'editor-file-name' }, (props.file as File).name),
        h(
          'button',
          {
            class: 'editor-confirm',
            onClick: () => emit('confirm', new File(['edited-image'], 'edited.jpg', { type: 'image/jpeg' })),
          },
          'confirm',
        ),
        h(
          'button',
          {
            class: 'editor-cancel',
            onClick: () => emit('cancel'),
          },
          'cancel',
        ),
      ])
  },
})

vi.mock('./ImageEditorModal.vue', () => ({
  __esModule: true,
  __isTeleport: false,
  default: ImageEditorModalStub,
}))

function makeFile(name: string, type: string, content = name) {
  return new File([content], name, { type })
}

function getBodyElements(selector: string) {
  return Array.from(document.body.querySelectorAll(selector)) as HTMLElement[]
}

async function clickBody(selector: string, index = 0) {
  const element = getBodyElements(selector)[index]
  expect(element).toBeTruthy()
  element!.click()
  await flushPromises()
}

describe('GalleryPreviewModal.vue', () => {
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL

  beforeEach(() => {
    let counter = 0
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => `blob:preview-${counter++}`),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(),
    })
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: originalCreateObjectURL,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: originalRevokeObjectURL,
    })
  })

  it('renders mixed previews and emits all current files on confirm', async () => {
    const image = makeFile('photo.jpg', 'image/jpeg')
    const video = makeFile('clip.mp4', 'video/mp4')
    const heic = makeFile('raw.heic', 'image/heic')

    const wrapper = mount(GalleryPreviewModal, {
      props: { files: [image, video, heic] },
      attachTo: document.body,
      global: {
        stubs: {
          teleport: false,
        },
      },
    })

    expect(document.body.querySelectorAll('.gp-cell')).toHaveLength(3)
    expect(document.body.querySelectorAll('.gp-edit-badge')).toHaveLength(1)
    expect(document.body.querySelectorAll('.gp-video-badge')).toHaveLength(1)
    expect(document.body.textContent).toContain('3 مورد')

    await clickBody('.gp-send')

    const emitted = wrapper.emitted('confirm')
    expect(emitted).toHaveLength(1)
    expect((emitted?.[0]?.[0] as File[]).map((file) => file.name)).toEqual(['photo.jpg', 'clip.mp4', 'raw.heic'])
  })

  it('opens the editor only for editable images and uses the edited file after confirm', async () => {
    const image = makeFile('photo.jpg', 'image/jpeg')
    const video = makeFile('clip.mp4', 'video/mp4')

    const wrapper = mount(GalleryPreviewModal, {
      props: { files: [image, video] },
      attachTo: document.body,
      global: {
        stubs: {
          teleport: false,
        },
      },
    })

    await clickBody('.gp-cell', 0)

    expect(document.body.querySelector('[data-test="image-editor-stub"]')).not.toBeNull()
    expect(document.body.textContent).toContain('photo.jpg')

    await clickBody('.editor-confirm')

    expect(document.body.querySelector('[data-test="image-editor-stub"]')).toBeNull()

    await clickBody('.gp-cell', 1)

    expect(document.body.querySelector('[data-test="image-editor-stub"]')).toBeNull()

    await clickBody('.gp-send')

    const emitted = wrapper.emitted('confirm')
    expect(emitted).toHaveLength(1)
    expect((emitted?.[0]?.[0] as File[]).map((file) => file.name)).toEqual(['edited.jpg', 'clip.mp4'])
  })

  it('closes the editor without changing the file when cancel is triggered', async () => {
    const image = makeFile('photo.jpg', 'image/jpeg')

    const wrapper = mount(GalleryPreviewModal, {
      props: { files: [image] },
      attachTo: document.body,
      global: {
        stubs: {
          teleport: false,
        },
      },
    })

    await clickBody('.gp-cell')
    await clickBody('.editor-cancel')

    expect(document.body.querySelector('[data-test="image-editor-stub"]')).toBeNull()

    await clickBody('.gp-send')

    const emitted = wrapper.emitted('confirm')
    expect(emitted).toHaveLength(1)
    expect((emitted?.[0]?.[0] as File[])[0]?.name).toBe('photo.jpg')
  })

  it('revokes removed preview urls and emits cancel when the last item is removed', async () => {
    const image = makeFile('photo.jpg', 'image/jpeg')
    const video = makeFile('clip.mp4', 'video/mp4')

    const wrapper = mount(GalleryPreviewModal, {
      props: { files: [image, video] },
      attachTo: document.body,
      global: {
        stubs: {
          teleport: false,
        },
      },
    })

    await clickBody('.gp-remove', 0)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview-0')
    expect(wrapper.emitted('cancel')).toBeUndefined()

    await clickBody('.gp-remove', 0)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview-1')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('rehydrates from updated props and revokes previous preview urls before replacing them', async () => {
    const image = makeFile('photo.jpg', 'image/jpeg')
    const video = makeFile('clip.mp4', 'video/mp4')

    const wrapper = mount(GalleryPreviewModal, {
      props: { files: [image, video] },
      attachTo: document.body,
      global: {
        stubs: {
          teleport: false,
        },
      },
    })

    const replacement = makeFile('next.jpg', 'image/jpeg')
    await wrapper.setProps({ files: [replacement] })
    await flushPromises()

    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview-0')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview-1')
    expect(URL.createObjectURL).toHaveBeenCalledTimes(3)
    expect(document.body.querySelectorAll('.gp-cell')).toHaveLength(1)
    expect(document.body.textContent).toContain('1 مورد')
  })
})