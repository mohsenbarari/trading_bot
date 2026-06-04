import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatForwardModal from './ChatForwardModal.vue'

const chatForwardModalMocks = vi.hoisted(() => ({
  apiFetchJson: vi.fn(),
}))

vi.mock('../../utils/auth', () => ({
  apiFetchJson: chatForwardModalMocks.apiFetchJson,
}))

function mountForwardModal() {
  return mount(ChatForwardModal, {
    props: {
      showForwardModal: true,
      sortedConversations: [],
      includeChannels: true,
      includeGroups: true,
    },
    global: {
      stubs: {
        teleport: true,
        transition: false,
      },
    },
  })
}

describe('ChatForwardModal.vue', () => {
  beforeEach(() => {
    chatForwardModalMocks.apiFetchJson.mockReset()
  })

  it('loads chat-target user identities so every direct-messageable user can be forwarded to', async () => {
    chatForwardModalMocks.apiFetchJson.mockResolvedValue([
      {
        id: 44,
        account_name: 'accountant44',
        full_name: 'حسابدار فروش',
        mobile_number: '09124444444',
      },
    ])

    const wrapper = mountForwardModal()
    await flushPromises()

    expect(chatForwardModalMocks.apiFetchJson).toHaveBeenCalledWith('/api/users-public/search?limit=5000&chat_targets=true')
    expect(wrapper.text()).toContain('حسابدار فروش')
    expect(wrapper.text()).toContain('09124444444')

    await wrapper.find('.forward-target-item').trigger('click')
    await wrapper.find('.forward-send-btn').trigger('click')

    expect(wrapper.emitted('forward-to')).toEqual([[
      [{
        kind: 'user',
        id: 44,
        title: 'حسابدار فروش',
        subtitle: '09124444444',
      }],
    ]])
  })
})
