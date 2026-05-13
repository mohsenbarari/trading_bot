import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const fetchMock = vi.fn()

vi.stubGlobal('fetch', fetchMock)

function makeResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

describe('ChatNewConversationModal.vue', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    localStorage.clear()
    localStorage.setItem('auth_token', 'jwt-token')
  })

  it('hides group creation and suppresses chat starts when initiation is disabled', async () => {
    fetchMock.mockResolvedValue(
      makeResponse([
        { id: 5, account_name: 'ali-user', full_name: 'علی', mobile_number: '09120000000', avatar_file_id: null },
      ]),
    )

    const ChatNewConversationModal = (await import('./ChatNewConversationModal.vue')).default
    const wrapper = mount(ChatNewConversationModal, {
      props: {
        show: true,
        canStartDirectChat: false,
        canCreateGroup: false,
      },
      global: {
        stubs: {
          LoadingSkeleton: { template: '<div class="loading-skeleton"></div>' },
          ChatUserListRow: {
            props: ['name'],
            emits: ['click'],
            template: '<button class="user-row" @click="$emit(\'click\')">{{ name }}</button>',
          },
        },
        directives: {
          ripple: {},
        },
      },
    })

    await flushPromises()

    expect(wrapper.find('.new-group-action').exists()).toBe(false)
    await wrapper.get('.user-row').trigger('click')
    expect(wrapper.emitted('start-chat')).toBeUndefined()
  })

  it('emits group creation and chat start when initiation is allowed', async () => {
    fetchMock.mockResolvedValue(
      makeResponse([
        { id: 8, account_name: 'mina-user', full_name: 'مینا', mobile_number: '09123333333', avatar_file_id: null },
      ]),
    )

    const ChatNewConversationModal = (await import('./ChatNewConversationModal.vue')).default
    const wrapper = mount(ChatNewConversationModal, {
      props: {
        show: true,
        canStartDirectChat: true,
        canCreateGroup: true,
      },
      global: {
        stubs: {
          LoadingSkeleton: { template: '<div class="loading-skeleton"></div>' },
          ChatUserListRow: {
            props: ['name'],
            emits: ['click'],
            template: '<button class="user-row" @click="$emit(\'click\')">{{ name }}</button>',
          },
        },
        directives: {
          ripple: {},
        },
      },
    })

    await flushPromises()

    await wrapper.get('.new-group-action').trigger('click')
    await wrapper.get('.user-row').trigger('click')

    expect(wrapper.emitted('create-group')).toHaveLength(1)
    expect(wrapper.emitted('start-chat')).toEqual([[8, 'مینا']])
  })
})