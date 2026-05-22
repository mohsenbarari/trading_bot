import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
    vi.resetModules()
    localStorage.clear()
    localStorage.setItem('auth_token', 'jwt-token')
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  function buildWrapper(props: Record<string, unknown>) {
    return mount((globalThis as any).__chatNewConversationModalComponent, {
      props,
      global: {
        stubs: {
          LoadingSkeleton: { template: '<div class="loading-skeleton"></div>' },
          ChatUserListRow: {
            props: ['name', 'badges'],
            emits: ['click'],
            template: `
              <button class="user-row" @click="$emit('click')">
                <span class="user-name">{{ name }}</span>
                <span v-for="badge in badges" :key="badge.label" class="user-badge">{{ badge.label }}</span>
                <slot name="subtitle" />
              </button>
            `,
          },
        },
        directives: {
          ripple: {},
        },
      },
    })
  }

  beforeEach(async () => {
    ;(globalThis as any).__chatNewConversationModalComponent = (await import('./ChatNewConversationModal.vue')).default
  })

  it('hides group creation and suppresses chat starts when initiation is disabled', async () => {
    fetchMock.mockResolvedValue(
      makeResponse([
        { id: 5, account_name: 'ali-user', full_name: 'علی', mobile_number: '09120000000', avatar_file_id: null },
      ]),
    )

    const wrapper = buildWrapper({
      show: true,
      canStartDirectChat: false,
      canCreateGroup: false,
    })

    await flushPromises()

    expect(wrapper.find('.new-group-action').exists()).toBe(false)
    await wrapper.get('.user-row').trigger('click')
    expect(wrapper.emitted('start-chat')).toBeUndefined()
  })

  it('emits group creation and chat start when initiation is allowed', async () => {
    fetchMock.mockResolvedValue(
      makeResponse([
        {
          id: 8,
          account_name: 'owner-eight',
          mobile_number: '09123333333',
          avatar_file_id: null,
          resolved_from_accountant_id: 81,
          highlight_accountant_relation_display_name: 'حسابدار فروش',
        },
      ]),
    )

    const wrapper = buildWrapper({
      show: true,
      canStartDirectChat: true,
      canCreateGroup: true,
    })

    await flushPromises()

    expect(wrapper.text()).toContain('مالک')
    expect(wrapper.text()).toContain('از مسیر حسابدار: حسابدار فروش')

    await wrapper.get('.new-group-action').trigger('click')
    await wrapper.get('.user-row').trigger('click')

    expect(wrapper.emitted('create-group')).toHaveLength(1)
    expect(wrapper.emitted('start-chat')).toEqual([[8, 'owner-eight']])
  })

  it('debounces search queries and calls the public search endpoint with the typed filter', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(() => Promise.resolve(makeResponse([])))

    const wrapper = buildWrapper({
      show: true,
      canStartDirectChat: true,
      canCreateGroup: true,
    })

    await flushPromises()
    fetchMock.mockClear()

    await wrapper.get('.new-chat-search-input').setValue('ali')
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const firstCall = fetchMock.mock.calls[0]
    expect(firstCall).toBeDefined()
    const [url, options] = firstCall as [RequestInfo | URL, RequestInit | undefined]
    expect(String(url)).toContain('/api/users-public/search')
    expect(String(url)).toContain('q=ali')
    expect(String(url)).toContain('limit=50')
    expect(options).toEqual(expect.objectContaining({
      headers: expect.objectContaining({
        Authorization: 'Bearer jwt-token',
      }),
    }))
  })

  it('reloads results when the modal is opened later and emits close from the header button', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(makeResponse([])))

    const wrapper = buildWrapper({
      show: false,
      canStartDirectChat: true,
      canCreateGroup: true,
    })

    await flushPromises()
    expect(fetchMock).not.toHaveBeenCalled()

    await wrapper.setProps({ show: true })
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)

    await wrapper.get('.back-btn').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('logs failed user searches and keeps the empty state visible', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'bad request' }), {
        status: 400,
        headers: {
          'Content-Type': 'application/json',
        },
      }),
    )

    const wrapper = buildWrapper({
      show: true,
      canStartDirectChat: true,
      canCreateGroup: true,
    })

    await flushPromises()

    expect(errorSpy).toHaveBeenCalled()
    expect(wrapper.text()).toContain('کاربری یافت نشد')
    errorSpy.mockRestore()
  })
})