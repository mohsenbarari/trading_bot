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
    expect(wrapper.emitted('start-chat')?.[0]?.[0]).toMatchObject({
      id: 8,
      account_name: 'owner-eight',
      resolved_from_accountant_id: 81,
      highlight_accountant_relation_display_name: 'حسابدار فروش',
    })
  })

  it('allows customer-mode direct starts from backend-filtered rows while hiding group creation', async () => {
    fetchMock.mockResolvedValue(
      makeResponse([
        { id: 20, account_name: 'owner20', full_name: 'مالک مشتری', mobile_number: '09120000020', avatar_file_id: null },
      ]),
    )

    const wrapper = buildWrapper({
      show: true,
      canStartDirectChat: true,
      canCreateGroup: false,
    })

    await flushPromises()

    expect(wrapper.find('.new-group-action').exists()).toBe(false)
    expect(wrapper.text()).toContain('مالک مشتری')

    await wrapper.get('.user-row').trigger('click')

    expect(wrapper.emitted('start-chat')?.[0]?.[0]).toMatchObject({
      id: 20,
      account_name: 'owner20',
      full_name: 'مالک مشتری',
    })
  })

  it('uses customer management names as the primary direct-chat discovery label', async () => {
    fetchMock.mockResolvedValue(
      makeResponse([
        {
          id: 91,
          account_name: 'customer91',
          full_name: null,
          mobile_number: '09125555555',
          avatar_file_id: null,
          chat_role_kind: 'customer',
          chat_role_label: 'مشتری',
          customer_management_name: 'مشتری بازار تهران',
          customer_tier: 'tier1',
        },
      ]),
    )

    const wrapper = buildWrapper({
      show: true,
      canStartDirectChat: true,
      canCreateGroup: false,
    })

    await flushPromises()

    expect(wrapper.get('.user-name').text()).toBe('مشتری بازار تهران')
    expect(wrapper.text()).toContain('مشتری')

    await wrapper.get('.user-row').trigger('click')

    expect(wrapper.emitted('start-chat')?.[0]?.[0]).toMatchObject({
      id: 91,
      account_name: 'customer91',
      chat_role_kind: 'customer',
      customer_management_name: 'مشتری بازار تهران',
    })
  })

  it('keeps shared-group accountants owner-resolved in messenger discovery', async () => {
    fetchMock.mockResolvedValue(
      makeResponse([
        {
          id: 20,
          account_name: 'owner20',
          full_name: 'مالک حسابدار گروه',
          mobile_number: '09124444444',
          avatar_file_id: null,
          resolved_from_accountant_id: 44,
          highlight_accountant_relation_display_name: 'حسابدار گروه',
        },
      ]),
    )

    const wrapper = buildWrapper({
      show: true,
      canStartDirectChat: true,
      canCreateGroup: false,
    })

    await flushPromises()

    expect(wrapper.find('.new-group-action').exists()).toBe(false)
    expect(wrapper.text()).toContain('مالک حسابدار گروه')
    expect(wrapper.text()).toContain('مالک')
    expect(wrapper.text()).toContain('از مسیر حسابدار: حسابدار گروه')

    await wrapper.get('.user-row').trigger('click')

    expect(wrapper.emitted('start-chat')?.[0]?.[0]).toMatchObject({
      id: 20,
      account_name: 'owner20',
      full_name: 'مالک حسابدار گروه',
      resolved_from_accountant_id: 44,
      highlight_accountant_relation_display_name: 'حسابدار گروه',
    })
  })

  it('prefers full names for display and emits a generic accountant context label when no relation name exists', async () => {
    fetchMock.mockResolvedValue(
      makeResponse([
        {
          id: 9,
          account_name: 'owner-nine',
          full_name: 'مالک نهم',
          mobile_number: '09124444444',
          avatar_file_id: null,
          resolved_from_accountant_id: 91,
          highlight_accountant_relation_display_name: null,
        },
      ]),
    )

    const wrapper = buildWrapper({
      show: true,
      canStartDirectChat: true,
      canCreateGroup: true,
    })

    await flushPromises()

    expect(wrapper.text()).toContain('مالک نهم')
    expect(wrapper.text()).toContain('از مسیر حسابدار')
    expect(wrapper.text()).not.toContain('از مسیر حسابدار:')

    await wrapper.get('.user-row').trigger('click')
    expect(wrapper.emitted('start-chat')?.[0]?.[0]).toMatchObject({
      id: 9,
      account_name: 'owner-nine',
      full_name: 'مالک نهم',
      resolved_from_accountant_id: 91,
      highlight_accountant_relation_display_name: null,
    })
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
    expect(String(url)).toContain('chat_targets=true')
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

  it('clears stale search text when reopened and refetches the default unfiltered list', async () => {
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
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('q=ali')

    await wrapper.setProps({ show: false })
    await flushPromises()
    fetchMock.mockClear()

    await wrapper.setProps({ show: true })
    await flushPromises()

    expect((wrapper.get('.new-chat-search-input').element as HTMLInputElement).value).toBe('')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/api/users-public/search?limit=50')
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('chat_targets=true')
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain('q=ali')
  })

  it('does not schedule a duplicate empty-query fetch after reopening from a stale search', async () => {
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

    await wrapper.setProps({ show: false })
    await flushPromises()
    fetchMock.mockClear()

    await wrapper.setProps({ show: true })
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/api/users-public/search?limit=50')
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('chat_targets=true')
  })

  it('cancels a pending debounced search when the modal closes', async () => {
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
    await wrapper.setProps({ show: false })
    await flushPromises()

    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()

    expect(fetchMock).not.toHaveBeenCalled()
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
