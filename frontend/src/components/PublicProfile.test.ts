import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import JalaliDatePicker from './JalaliDatePicker.vue'
import { currentUserSummary } from '../utils/currentUser'

const buildChatFileUrlMock = vi.fn(() => '')
const uploadAvatarImageMock = vi.fn()
const publicProfileRealtimeMocks = vi.hoisted(() => ({
  handlers: new Map<string, Array<(payload?: unknown) => void>>(),
  on: vi.fn((event: string, callback: (payload?: unknown) => void) => {
    const handlers = publicProfileRealtimeMocks.handlers.get(event) ?? []
    handlers.push(callback)
    publicProfileRealtimeMocks.handlers.set(event, handlers)
  }),
  off: vi.fn(),
}))
const publicProfileFileSource = readFileSync(
  resolve(process.cwd(), 'src/components/PublicProfile.vue'),
  'utf8',
)
const profileIdentityHeaderSource = readFileSync(
  resolve(process.cwd(), 'src/components/profile/ProfileIdentityHeader.vue'),
  'utf8',
)
const profileActionsSource = readFileSync(
  resolve(process.cwd(), 'src/components/profile/ProfileActions.vue'),
  'utf8',
)
const publicProfileSource = [
  publicProfileFileSource,
  profileIdentityHeaderSource,
  profileActionsSource,
].join('\n\n')

vi.mock('../utils/chatFiles', () => ({
  buildChatFileUrl: buildChatFileUrlMock,
  getAvatarInitial: (value: string) => value.slice(0, 1),
  uploadAvatarImage: uploadAvatarImageMock,
}))

vi.mock('../composables/useWebSocket', () => ({
  useWebSocket: () => ({ on: publicProfileRealtimeMocks.on, off: publicProfileRealtimeMocks.off }),
}))

function emitPublicProfileRealtime(event: string, payload?: unknown) {
  for (const handler of publicProfileRealtimeMocks.handlers.get(event) ?? []) handler(payload)
}

function makeResponse(payload: unknown, ok = true, status = ok ? 200 : 400): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

async function clickHistoryOverflowAction(wrapper: { find: (selector: string) => { exists: () => boolean; trigger: (event: string) => Promise<unknown> }; findAll: (selector: string) => Array<{ text: () => string; trigger: (event: string) => Promise<unknown> }> }, label: string) {
  const more = wrapper.find('[aria-label="اقدام‌های دیگر تاریخچه"]')
  expect(more.exists()).toBe(true)
  await more.trigger('click')
  const action = wrapper.findAll('button').find((node) => node.text().includes(label))
  expect(action).toBeTruthy()
  await action!.trigger('click')
}

function makeHistoryPage(items: unknown[], nextCursor: string | null = null, hasMore = false): Response {
  return makeResponse({
    items,
    next_cursor: nextCursor,
    has_more: hasMore,
    page_size: items.length,
  })
}

function makePublicPeerProfile(id = 30, accountName = 'plain30') {
  return {
    id,
    account_name: accountName,
    avatar_file_id: null,
    mobile_number: '09125555555',
    address: 'تهران',
    created_at_jalali: '۱۴۰۵/۰۱/۰۳',
    trades_count: 4,
    resolved_from_accountant_id: null,
    highlight_accountant_user_id: null,
    highlight_accountant_relation_display_name: null,
    accountant_relations: [],
  }
}

function makePublicBlockStatus(overrides: Record<string, unknown> = {}) {
  return {
    can_block: true,
    can_block_now: true,
    max_blocked: 3,
    current_blocked: 1,
    remaining: 2,
    reason_code: null,
    reason_message: null,
    ...overrides,
  }
}

async function getPublicBlockDialog() {
  await nextTick()
  await nextTick()
  const dialog = document.body.querySelector<HTMLElement>('.ui-confirm-dialog')
  expect(dialog).toBeTruthy()
  return dialog!
}

function getDialogButton(dialog: HTMLElement, label: string) {
  return Array.from(dialog.querySelectorAll<HTMLButtonElement>('button'))
    .find((button) => button.textContent?.includes(label))
}

function defaultFetchResponse(input: string): Promise<Response> {
  if (input.endsWith('/api/commodities/')) {
    return Promise.resolve(makeResponse([]))
  }

  if (input.endsWith('/api/blocks/status')) {
    return Promise.resolve(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
  }

  if (/\/api\/blocks\/check\/\d+$/.test(input)) {
    return Promise.resolve(makeResponse({ is_blocked_by_me: false }))
  }

  if (/\/api\/trades\/(?:my|with\/\d+)\/page\?/.test(input)) {
    return Promise.resolve(makeHistoryPage([]))
  }

  return Promise.reject(new Error(`Unhandled fetch call in PublicProfile.test.ts: ${input}`))
}

async function setHistoryDate(wrapper: ReturnType<typeof mount>, index: number, value: string) {
  const pickers = wrapper.findAllComponents(JalaliDatePicker)
  expect(pickers[index]?.exists()).toBe(true)
  pickers[index]!.vm.$emit('update:modelValue', value)
  pickers[index]!.vm.$emit('change', value)
  await flushPromises()
}

// Compile the large SFC before individual test timeouts begin. Each case still
// imports the cached module independently, preserving its existing isolation.
beforeAll(async () => {
  await import('./PublicProfile.vue')
})

describe('PublicProfile.vue', () => {
  beforeEach(() => {
    document.body.replaceChildren()
    buildChatFileUrlMock.mockClear()
    uploadAvatarImageMock.mockReset()
    publicProfileRealtimeMocks.handlers.clear()
    publicProfileRealtimeMocks.on.mockClear()
    publicProfileRealtimeMocks.off.mockClear()
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url
      return defaultFetchResponse(url)
    }))
    vi.stubGlobal('alert', vi.fn())
    currentUserSummary.value = null
    localStorage.clear()
    localStorage.setItem('auth_token', 'token')
  })

  it('keeps profile-header tracks shrinkable on narrow devices', () => {
    const headerRule = publicProfileSource.match(/\.profile-header-row\s*\{([\s\S]*?)\n\}/)?.[1]

    expect(headerRule).toContain(
      'grid-template-columns: minmax(4rem, 5.5rem) minmax(0, 1fr) minmax(3rem, 5.5rem);',
    )
    expect(headerRule).toContain('min-width: 0;')
    expect(publicProfileSource).toMatch(/\.profile-header-row\s*>\s*\*\s*\{\s*min-width:\s*0;/)
  })

  it('keeps public-profile typography locally aligned with the Figma Persian card scale', () => {
    expect(publicProfileSource).toMatch(/<div class="public-profile public-profile-typography">/)
    expect(publicProfileSource).toMatch(
      /\.public-profile-typography\s*\{[\s\S]*?font-family:\s*Vazirmatn,\s*Tahoma,\s*Arial,\s*sans-serif;[\s\S]*?font-synthesis:\s*none;/,
    )
    expect(publicProfileSource).toMatch(/<span class="project-user-mobile" dir="ltr">/)
    expect(publicProfileSource).toMatch(
      /\.project-user-mobile\s*\{[\s\S]*?direction:\s*ltr;[\s\S]*?text-align:\s*left;/,
    )
  })

  it('reserves a visible 48px back-navigation target', () => {
    const backRule = publicProfileSource.match(/\.profile-nav-back\s*\{([\s\S]*?)\n\}/)?.[1]

    expect(backRule).toContain('box-sizing: border-box;')
    expect(backRule).toContain('inline-size: 3rem;')
    expect(backRule).toContain('block-size: 3rem;')
    expect(backRule).toContain('min-inline-size: 3rem;')
    expect(backRule).toContain('min-block-size: 3rem;')
  })

  it('preserves ordinary profile-control motion while disabling every local control transition for reduced motion', () => {
    const backRule = publicProfileSource.match(/\.profile-nav-back\s*\{([\s\S]*?)\n\}/)?.[1]
    const addressEditRule = publicProfileSource.match(/\.address-edit-trigger\s*\{([\s\S]*?)\n\}/)?.[1]
    const actionCardRule = publicProfileSource.match(/\.profile-action-card\s*\{([\s\S]*?)\n\}/)?.[1]
    const miniTradeRule = Array.from(
      publicProfileSource.matchAll(/\.mini-trade-card\s*\{([\s\S]*?)\n\}/g),
    ).find(([, rule]) => rule.includes('transition: background 0.18s ease;'))
    const miniTradeReducedMotionRule = publicProfileSource.match(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{\s*\.mini-trade-card\s*\{\s*transition:\s*none;\s*\}\s*\}/,
    )?.[0]

    expect(backRule).toContain('justify-self: start;')
    expect(addressEditRule).toContain('transition: color 0.18s ease, background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;')
    expect(actionCardRule).toContain('transition: background 0.18s ease;')
    expect(miniTradeRule?.[1]).toContain('transition: background 0.18s ease;')
    expect(publicProfileSource).toMatch(/\.profile-nav-back\s*\{[\s\S]*?min-block-size:\s*3rem;/)
    expect(publicProfileSource).toMatch(/\.profile-action-card:active\s*\{\s*transform:\s*none;/)
    expect(publicProfileSource).toMatch(/\.mini-trade-card:active\s*\{\s*transform:\s*none;/)
    expect(publicProfileSource).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{\s*\.profile-nav-back,\s*\.address-edit-trigger\s*\{\s*transition:\s*none;\s*\}/,
    )
    expect(profileActionsSource).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{\s*\.profile-action-card\s*\{\s*transition:\s*none;\s*\}/,
    )
    expect(miniTradeReducedMotionRule).toBeTruthy()
    expect(publicProfileSource.indexOf(miniTradeReducedMotionRule!)).toBeGreaterThan(
      publicProfileSource.indexOf(miniTradeRule![0]),
    )
  })

  it('renders remaining public-profile chrome with shared icon and button primitives', () => {
    expect(publicProfileFileSource).not.toContain('HelpPopover')
    expect(publicProfileFileSource).toMatch(/<ProfileIdentityHeader/)
    expect(publicProfileSource).toMatch(/<AppBackButton[\s\S]*?class="profile-nav-back"/)
    expect(publicProfileSource).toMatch(/<AppButton class="retry-btn"/)
    expect(publicProfileSource).toMatch(/<AppIconButton\s+v-if="isOwnProfile"\s+class="address-edit-trigger"/)
    expect(publicProfileSource).toMatch(/data-test="profile-avatar-trigger"/)
    expect(publicProfileSource).toMatch(/<button\s+v-if="editable"[\s\S]*?class="profile-avatar profile-avatar-button profile-avatar-button--editable"/)
    expect(publicProfileSource).toMatch(/\.profile-link-btn\s*\{[\s\S]*?color:\s*var\(--ds-success-700\)/)
    expect(publicProfileSource).toMatch(/\.trade-counterparty \.profile-link-btn\s*\{[\s\S]*?color:\s*var\(--ds-success-700\)/)
    expect(publicProfileSource).toMatch(/background:\s*var\(--ds-primary-100\)/)
    expect(publicProfileSource).toMatch(/\.profile-avatar-button--editable\s*\{[\s\S]*?box-shadow:\s*none/)
    expect(publicProfileSource).toMatch(/\.settings-btn\s*\{[\s\S]*?var\(--ds-primary-50\)/)
    expect(publicProfileSource).toMatch(/\.block-btn\s*\{[\s\S]*?var\(--ds-danger-50\)/)
    expect(publicProfileSource).toMatch(/\.unblock-btn\s*\{[\s\S]*?var\(--ds-success-50\)/)
    expect(publicProfileSource).not.toMatch(/\.project-users-search-submit/)
    expect(publicProfileSource).toMatch(
      /<label class="sr-only" for="project-users-directory-search">جستجوی همکاران پروژه<\/label>/,
    )
    expect(publicProfileSource).toMatch(
      /<AppInput\s+id="project-users-directory-search"\s+v-model="projectUsersQuery"/,
    )
  })

  it('shows approved contact fields but strips unrelated owner-only fields for an ordinary peer', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 20,
      account_name: 'owner20',
      avatar_file_id: null,
      mobile_number: '09124444444',
      address: 'مشهد',
      last_seen_at: new Date(Date.now() - 60_000).toISOString(),
      created_at_jalali: '۱۴۰۵/۰۱/۰۲',
      trades_count: 12,
      resolved_from_accountant_id: 44,
      highlight_accountant_user_id: 44,
      highlight_accountant_relation_display_name: 'حسابدار فروش',
      accountant_relations: [
        {
          accountant_user_id: 44,
          accountant_account_name: 'acct44',
          relation_display_name: 'حسابدار فروش',
          duty_description: 'پیگیری معاملات',
        },
      ],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'acct44' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('/api/users-public/44', expect.objectContaining({
      headers: expect.objectContaining({
        authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.text()).toContain('owner20')
    expect(wrapper.text()).toContain('09124444444')
    expect(wrapper.text()).toContain('مشهد')
    expect(wrapper.text()).not.toContain('حسابدار فروش')
    expect(wrapper.text()).not.toContain('نمایش پروفایل مالک اصلی')
    expect(wrapper.text()).not.toContain('تاریخچه معاملات')
  })
  
  it('applies preset history ranges and renders partial filter summaries for one-sided dates', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-28T12:00:00Z'))

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 50,
      account_name: 'owner50',
      avatar_file_id: null,
      mobile_number: '09121112222',
      address: 'قم',
      created_at_jalali: '۱۴۰۵/۰۱/۱۰',
      trades_count: 3,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([{ id: 1, name: 'سکه', aliases: [] }]))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 50, account_name: 'owner50' },
        viewerUserId: 50,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const presetButton = wrapper.findAll('button').find((node) => node.text().includes('۳ ماه'))
    expect(presetButton).toBeTruthy()
    await presetButton!.trigger('click')
    await flushPromises()

    const presetFetchCalls = fetchMock.mock.calls.filter(([url]) => typeof url === 'string' && url.startsWith('/api/trades/my/page?'))
    expect(presetFetchCalls.length).toBeGreaterThanOrEqual(1)
    expect(presetFetchCalls.at(-1)?.[0]).toContain('from_date=2026-02-28')
    expect(presetFetchCalls.at(-1)?.[0]).toContain('to_date=2026-05-28')

    await setHistoryDate(wrapper, 0, '')
    await setHistoryDate(wrapper, 1, '2026-05-20')
    const commoditySelect = wrapper.find('.history-filter-field-wide select')
    expect(commoditySelect.exists()).toBe(true)
    expect(commoditySelect.classes()).toContain('ui-select')
    await commoditySelect.trigger('focus')
    await flushPromises()
    await commoditySelect.setValue('سکه')
    const applyButtonWithCommodity = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButtonWithCommodity).toBeTruthy()
    await applyButtonWithCommodity!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('تا')
    expect(wrapper.text()).toContain('کالا: سکه')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('falls back to generic api error text for malformed self-history exports', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۱/۰۳',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 30,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    fetchMock.mockResolvedValueOnce(new Response('server exploded', { status: 400, headers: { 'Content-Type': 'text/plain' } }))

    await clickHistoryOverflowAction(wrapper, 'خروجی PDF')
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در دریافت خروجی تاریخچه معاملات')
  })

  it('falls back to plain counterparty labels when history profile targets are missing', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 50,
      account_name: 'owner50',
      avatar_file_id: null,
      mobile_number: '09121112222',
      address: 'قم',
      created_at_jalali: '۱۴۰۵/۰۱/۱۰',
      trades_count: 3,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 11,
        trade_number: 10011,
        created_at: 'امروز',
        commodity_name: 'سکه',
        quantity: 1,
        price: 111000,
        trade_type: 'SELL',
        settlement_type: 'tomorrow',
        offer_user_id: null,
        offer_user_name: null,
        responder_user_id: null,
        responder_user_name: null,
        counterparty_name: 'خریدار بیرونی',
        counterparty_profile_user_id: 'bad-id',
        counterparty_profile_account_name: '',
        customer_context_visible: false,
      },
    ]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 50, account_name: 'owner50' },
        viewerUserId: 50,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const applyButtonWithFilters = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButtonWithFilters).toBeTruthy()
    await applyButtonWithFilters!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('خریدار بیرونی')
    expect(wrapper.find('.mini-trade-card .trade-settlement').text()).toBe('فردایی')
    expect(wrapper.find('.mini-trade-card .trade-counterparty .profile-link-btn').exists()).toBe(false)
  })
  it('shows formatted last-seen text in the profile hero when a timestamp exists', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-22T12:00:00Z'))
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      last_seen_at: '2026-05-22T11:55:00Z',
      created_at_jalali: '۱۴۰۵/۰۱/۰۳',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 30,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('آخرین بازدید 5 دقیقه پیش')
  })

  it('keeps the profile hero silent when no last-seen timestamp exists', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      last_seen_at: null,
      created_at_jalali: '۱۴۰۵/۰۱/۰۳',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.find('.profile-presence-status').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('آخرین بازدید')
    expect(wrapper.text()).not.toContain('آنلاین')
  })

  it('does not show the owner-resolution banner for direct public profiles', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۱/۰۳',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).not.toContain('نمایش پروفایل مالک اصلی')
  })

  it('shows an invalid-user error and lets a corrected request retry in place', async () => {
    const fetchMock = vi.mocked(fetch)
    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: null,
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: null,
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('اطلاعات کاربر نامعتبر است.')
    await wrapper.setProps({
      user: { id: 64, account_name: 'retry64' },
      jwtToken: 'token',
    })
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 64,
      account_name: 'retry64',
      avatar_file_id: null,
      mobile_number: '09120000064',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۵/۱۷',
      trades_count: 0,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))

    expect(wrapper.get('.retry-btn').classes()).toContain('ui-button')
    await wrapper.get('.retry-btn').trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => url === '/api/users-public/64')).toBe(true)
    expect(wrapper.find('.error-state').exists()).toBe(false)
    expect(wrapper.text()).toContain('retry64')
  })

  it('shows the visitor action and navigates to chat for direct public profiles', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۱/۰۳',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const messageButton = wrapper.findAll('button').find((button) => button.text().includes('ارسال پیام'))
    expect(messageButton).toBeTruthy()

    await messageButton!.trigger('click')

    expect(wrapper.emitted('navigate')?.[0]).toEqual(['chat', { userId: 30, userName: 'plain30' }])
  })

  it('shows no visitor actions when a customer viewer is denied an outside public profile', async () => {
    currentUserSummary.value = {
      id: 91,
      role: 'عادی',
      is_customer: true,
      customer_tier: 'tier2',
    }

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({ detail: 'User not found' }, false, 404))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 91,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('خطا در دریافت اطلاعات کاربر')
    expect(wrapper.findAll('button').some((button) => button.text().includes('ارسال پیام'))).toBe(false)
  })

  it('opens a block confirmation and lets cancel leave the state and API untouched', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicPeerProfile()))
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicBlockStatus()))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      attachTo: document.body,
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    const blockButton = wrapper.findAll('button').find((button) => button.text().includes('بلاک کاربر'))
    expect(blockButton).toBeTruthy()
    await blockButton!.trigger('click')

    const dialog = await getPublicBlockDialog()
    expect(dialog.textContent).toContain('بلاک کاربر؟')
    expect(dialog.textContent).not.toContain('plain30')
    expect(fetchMock).toHaveBeenCalledTimes(3)

    const cancelButton = getDialogButton(dialog, 'انصراف')
    expect(cancelButton).toBeTruthy()
    cancelButton!.click()
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()
    expect(wrapper.findAll('button').some((button) => button.text().includes('رفع بلاک'))).toBe(false)
    wrapper.unmount()
  })

  it('confirms block only after an explicit dialog action and renders a fixed success receipt', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicPeerProfile()))
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicBlockStatus()))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse({ success: true, message: 'متن پاسخ سرور نباید دیده شود' }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      attachTo: document.body,
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()
    const blockButton = wrapper.findAll('button').find((button) => button.text().includes('بلاک کاربر'))
    await blockButton!.trigger('click')
    const dialog = await getPublicBlockDialog()
    const confirmButton = getDialogButton(dialog, 'تأیید بلاک')
    expect(confirmButton).toBeTruthy()
    confirmButton!.click()
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/blocks/30', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ authorization: 'Bearer token' }),
    }))
    expect(wrapper.get('[data-test="public-block-feedback"]').text()).toBe('کاربر با موفقیت بلاک شد.')
    expect(wrapper.text()).not.toContain('متن پاسخ سرور نباید دیده شود')
    expect(wrapper.findAll('button').some((button) => button.text().includes('رفع بلاک'))).toBe(true)
    wrapper.unmount()
  })

  it('disables the block action with a capability-aware reason when new blocks are not allowed', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicPeerProfile()))
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicBlockStatus({
      can_block_now: false,
      max_blocked: 1,
      current_blocked: 1,
      remaining: 0,
      reason_code: 'limit_reached',
      reason_message: 'متن اختصاصی سرور نباید دیده شود',
    })))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    const blockButton = wrapper.findAll('button').find((button) => button.text().includes('بلاک کاربر'))
    expect(blockButton).toBeTruthy()
    expect(blockButton!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('ظرفیت بلاک شما تکمیل است')
    expect(wrapper.text()).not.toContain('متن اختصاصی سرور نباید دیده شود')
    await blockButton!.trigger('click')
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('keeps unblock available even when new blocks are globally disabled for the viewer', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicPeerProfile()))
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicBlockStatus({
      can_block: false,
      can_block_now: false,
      current_blocked: 1,
      remaining: 0,
      reason_code: 'capability_disabled',
      reason_message: 'متن اختصاصی سرور نباید دیده شود',
    })))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: true }))
    fetchMock.mockResolvedValueOnce(makeResponse({ success: true, message: 'رفع بلاک انجام شد.' }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    const unblockButton = wrapper.findAll('button').find((button) => button.text().includes('رفع بلاک'))
    expect(unblockButton).toBeTruthy()
    expect(unblockButton!.attributes('disabled')).toBeUndefined()

    await unblockButton!.trigger('click')
    const dialog = await getPublicBlockDialog()
    const confirmButton = getDialogButton(dialog, 'تأیید رفع بلاک')
    expect(confirmButton).toBeTruthy()
    confirmButton!.click()
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/blocks/30', expect.objectContaining({
      method: 'DELETE',
      headers: expect.objectContaining({
        authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.get('[data-test="public-block-feedback"]').text()).toBe('رفع بلاک کاربر انجام شد.')
    expect(wrapper.text()).not.toContain('متن اختصاصی سرور نباید دیده شود')
  })

  it.each([
    [400, { detail: 'جزئیات اعتبارسنجی حساس سرور' }, 'بلاک کاربر انجام نشد. وضعیت بلاک تغییر نکرد.'],
    [403, { detail: 'جزئیات دسترسی حساس سرور' }, 'دسترسی شما برای تغییر وضعیت بلاک این کاربر مجاز نیست. وضعیت بلاک تغییر نکرد.'],
    [404, { detail: 'جزئیات نبودن حساس سرور' }, 'این کاربر دیگر در دسترس نیست. وضعیت بلاک تغییر نکرد.'],
  ])('keeps the block state unchanged and hides raw server detail after a %s response', async (status, payload, safeMessage) => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicPeerProfile()))
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicBlockStatus()))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse(payload, false, status))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      attachTo: document.body,
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()
    const blockButton = wrapper.findAll('button').find((button) => button.text().includes('بلاک کاربر'))
    await blockButton!.trigger('click')
    const dialog = await getPublicBlockDialog()
    getDialogButton(dialog, 'تأیید بلاک')!.click()
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/blocks/30', expect.objectContaining({ method: 'POST' }))
    expect(wrapper.get('[data-test="public-block-feedback"]').text()).toBe(safeMessage)
    expect(dialog.textContent).toContain(safeMessage)
    expect(wrapper.text()).not.toContain((payload as { detail: string }).detail)
    expect(wrapper.findAll('button').some((button) => button.text().includes('رفع بلاک'))).toBe(false)
    wrapper.unmount()
  })

  it('rejects malformed successful block responses without flipping state or exposing payload text', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicPeerProfile()))
    fetchMock.mockResolvedValueOnce(makeResponse(makePublicBlockStatus()))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse({ success: false, message: 'جزئیات نامعتبر سرور' }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      attachTo: document.body,
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()
    const blockButton = wrapper.findAll('button').find((button) => button.text().includes('بلاک کاربر'))
    await blockButton!.trigger('click')
    const dialog = await getPublicBlockDialog()
    getDialogButton(dialog, 'تأیید بلاک')!.click()
    await flushPromises()

    expect(wrapper.get('[data-test="public-block-feedback"]').text()).toBe('پاسخ معتبر از سرور دریافت نشد. وضعیت بلاک تغییر نکرد.')
    expect(dialog.textContent).toContain('پاسخ معتبر از سرور دریافت نشد. وضعیت بلاک تغییر نکرد.')
    expect(wrapper.text()).not.toContain('جزئیات نامعتبر سرور')
    expect(wrapper.findAll('button').some((button) => button.text().includes('رفع بلاک'))).toBe(false)
    wrapper.unmount()
  })

  it('contains no native confirm or alert path for public block mutations', () => {
    expect(publicProfileSource).not.toMatch(/window\.(?:confirm|alert)\s*\(/)
  })

  it('loads the project users directory for self profiles and navigates through result rows', async () => {
    const fetchMock = vi.mocked(fetch)
    const firstPage = Array.from({ length: 25 }, (_, index) => {
      if (index === 0) {
        return {
          id: 44,
          account_name: 'owner44',
          mobile_number: '09127777777',
        }
      }
      if (index === 1) {
        return {
          id: 61,
          account_name: 'manager61',
          mobile_number: '0912****100',
        }
      }
      const userId = 60 + index
      return {
        id: userId,
        account_name: `manager${userId}`,
        mobile_number: `0912111${String(userId).padStart(4, '0')}`,
      }
    })
    const secondPage = Array.from({ length: 25 }, (_, index) => {
      const userId = 100 + index
      return {
        id: userId,
        account_name: `manager${userId}`,
        mobile_number: `0912222${String(userId).padStart(4, '0')}`,
      }
    })
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 44,
      account_name: 'owner44',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'اصفهان',
      created_at_jalali: '۱۴۰۵/۰۱/۰۵',
      trades_count: 18,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse(firstPage))
    fetchMock.mockResolvedValueOnce(makeResponse(secondPage))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 61,
        account_name: 'manager61',
        mobile_number: '09121110000',
      },
    ]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'owner44' },
        viewerUserId: 44,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.get('label[for="project-users-directory-search"]').text()).toBe('جستجوی همکاران پروژه')
    expect(wrapper.get('input#project-users-directory-search').exists()).toBe(true)

    await wrapper.get('.project-users-search').trigger('submit')
    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => (
      url === '/api/users-public/44/project-users?limit=25&offset=0'
    ))).toBe(true)
    expect(wrapper.text()).toContain('manager61')
    expect(wrapper.text()).toContain('0912****100')
    expect(wrapper.text()).toContain('••••••••')
    expect(wrapper.text()).not.toContain('09121110000')

    await wrapper.findAll('.ui-list-item').find((item) => item.text().includes('manager61'))!.trigger('click')
    expect(wrapper.emitted('navigate')?.at(-1)).toEqual([
      'public_profile',
      {
        id: 61,
      },
    ])

    await wrapper.get('.project-users-load-more').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/users-public/44/project-users?limit=25&offset=25', expect.objectContaining({
      headers: expect.objectContaining({
        authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.text()).toContain('manager124')

    await wrapper.get('.project-users-search-input').setValue('manager')
    await wrapper.get('.project-users-search').trigger('submit')
    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => (
      url === '/api/users-public/44/project-users?limit=25&offset=0&q=manager'
    ))).toBe(true)
    expect(wrapper.text()).toContain('manager61')
  })

  it('does not treat a resolved accountant target as the owner\'s self profile', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 20,
      account_name: 'owner20',
      avatar_file_id: null,
      mobile_number: '09124444444',
      address: 'مشهد',
      created_at_jalali: '۱۴۰۵/۰۱/۰۲',
      trades_count: 12,
      resolved_from_accountant_id: 44,
      highlight_accountant_user_id: 44,
      highlight_accountant_relation_display_name: 'حسابدار فروش',
      accountant_relations: [
        {
          accountant_user_id: 44,
          accountant_account_name: 'acct44',
          relation_display_name: 'حسابدار فروش',
          duty_description: 'پیگیری معاملات',
        },
      ],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 20,
        account_name: 'owner20',
        mobile_number: '09124444444',
      },
    ]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'acct44' },
        // The requested target is accountant 44, but the server resolves the
        // payload to owner 20. Self must be based on the requested target.
        viewerUserId: 20,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => (
      typeof url === 'string' && url.includes('/project-users?')
    ))).toBe(false)
    expect(wrapper.text()).toContain('owner20')
    expect(wrapper.text()).toContain('09124444444')
    expect(wrapper.text()).toContain('مشهد')
    expect(wrapper.text()).not.toContain('حسابدار فروش')
    expect(wrapper.text()).not.toContain('لیست همکاران')
    expect(wrapper.text()).not.toContain('تنظیمات کاربری')
    expect(wrapper.text()).not.toContain('تاریخچه معاملات من')
    expect(wrapper.find('.address-row').exists()).toBe(true)
    expect(wrapper.find('.address-edit-trigger').exists()).toBe(false)
  })

  it('shows project-users fetch errors and lets the owner retry with a new search', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 44,
      account_name: 'owner44',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'اصفهان',
      created_at_jalali: '۱۴۰۵/۰۱/۰۵',
      trades_count: 18,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ detail: 'دریافت کاربران پروژه ممکن نشد' }, false))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 61,
        account_name: 'manager61',
        mobile_number: '09121110000',
      },
    ]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'owner44' },
        viewerUserId: 44,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    await wrapper.get('.project-users-search').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('دریافت کاربران پروژه ممکن نشد')
    expect(wrapper.text()).not.toContain('manager61')

    await wrapper.get('.project-users-search-input').setValue('manager')
    await wrapper.get('.project-users-search').trigger('submit')
    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => (
      url === '/api/users-public/44/project-users?limit=25&offset=0&q=manager'
    ))).toBe(true)
    expect(wrapper.text()).not.toContain('دریافت کاربران پروژه ممکن نشد')
    expect(wrapper.text()).toContain('manager61')
  })

  it('keeps the previous project-user rows and search query when a new search fails', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url
      if (url === '/api/users-public/44') {
        return Promise.resolve(makeResponse({
          id: 44,
          account_name: 'owner44',
          avatar_file_id: null,
          mobile_number: '09127777777',
          address: 'اصفهان',
          created_at_jalali: '۱۴۰۵/۰۱/۰۵',
          trades_count: 18,
          accountant_relations: [],
        }))
      }
      if (url.includes('/api/users-public/44/project-users?') && url.includes('q=new-query')) {
        return Promise.resolve(makeResponse({ detail: 'جستجو موقتاً ممکن نیست' }, false, 400))
      }
      if (url.includes('/api/users-public/44/project-users?')) {
        return Promise.resolve(makeResponse([{
          id: 61,
          account_name: 'preserved61',
          mobile_number: '09121110000',
        }]))
      }
      return defaultFetchResponse(url)
    })

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'owner44' },
        viewerUserId: 44,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })
    await flushPromises()

    await wrapper.get('.project-users-search').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('preserved61')
    await wrapper.get('.project-users-search-input').setValue('new-query')
    await wrapper.get('.project-users-search').trigger('submit')
    await flushPromises()

    expect(wrapper.get('.project-users-search-input').element).toHaveProperty('value', 'new-query')
    expect(wrapper.text()).toContain('جستجو موقتاً ممکن نیست')
    expect(wrapper.text()).toContain('فهرست قبلی حفظ شده است')
    expect(wrapper.text()).toContain('preserved61')
  })

  it('hides the project users directory on customer self profiles', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 91,
      account_name: 'customer91',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'شیراز',
      created_at_jalali: '۱۴۰۵/۰۲/۰۲',
      trades_count: 5,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: 20,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
      customer_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 91, account_name: 'customer91' },
        viewerUserId: 91,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.get('.header-title h1 .customer-name-with-badge__name').text()).toBe('مشتری ویژه')
    expect(wrapper.get('.header-title h1 .customer-name-with-badge__badge').text()).toBe('مشتری')
    expect(wrapper.get('[data-test="profile-avatar-trigger"]').text()).toContain('م')
    expect(wrapper.text()).not.toContain('لیست همکاران')
    expect(wrapper.findAll('button').some((button) => button.text().includes('حسابداران'))).toBe(false)
    expect(wrapper.findAll('button').some((button) => button.text().includes('مشتریان'))).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('hides the public block action when the viewer is a customer', async () => {
    currentUserSummary.value = {
      id: 91,
      role: 'عادی',
      is_customer: true,
      customer_tier: 'tier1',
    }

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۱/۰۳',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 91,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.findAll('button').some((button) => button.text().includes('بلاک'))).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('hides the public block action when the viewer is an accountant', async () => {
    currentUserSummary.value = {
      id: 44,
      role: 'عادی',
      is_accountant: true,
      accountant_owner_user_id: 20,
      accountant_owner_account_name: 'owner20',
    }

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 30,
      account_name: 'plain30',
      avatar_file_id: null,
      mobile_number: '09125555555',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۱/۰۳',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 30, account_name: 'plain30' },
        viewerUserId: 44,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.findAll('button').some((button) => button.text().includes('بلاک'))).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not disclose customer membership while server-side block policy remains authoritative', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 91,
      account_name: 'customer91',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'شیراز',
      created_at_jalali: '۱۴۰۵/۰۲/۰۲',
      trades_count: 5,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: 20,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
      customer_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 91, account_name: 'customer91' },
        viewerUserId: 20,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.findAll('button').some((button) => button.text().includes('بلاک'))).toBe(true)
    expect(wrapper.text()).not.toContain('مشتری ویژه')
    expect(wrapper.text()).not.toContain('owner20')
    expect(wrapper.text()).toContain('09127777777')
    expect(wrapper.text()).toContain('شیراز')
  })

  it('opens a local admin user manager for admin viewers on other profiles', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 61,
      account_name: 'managed61',
      avatar_file_id: null,
      mobile_number: '09121110000',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۲/۰۱',
      trades_count: 1,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 61,
      account_name: 'managed61',
      mobile_number: '09121110000',
      role: 'عادی',
      account_status: 'active',
      is_customer: true,
      customer_owner_user_id: 20,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
      has_bot_access: false,
      trading_restricted_until: null,
      max_sessions: 1,
      max_accountants: 3,
      max_customers: 5,
      can_block_users: true,
      max_blocked_users: 10,
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 61, account_name: 'managed61' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          Teleport: true,
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          UserProfile: {
            props: ['user'],
            emits: ['navigate'],
            template: '<div class="user-profile-stub"><span>{{ user.account_name }}</span><span class="stub-customer-name">{{ user.customer_management_name }}</span><button @click="$emit(\'navigate\', \'manage_users\')">close user profile</button></div>',
          },
        },
      },
    })

    await flushPromises()

    const adminSettingsButton = wrapper.findAll('button').find((button) => button.text().includes('تنظیمات کاربر'))
    expect(adminSettingsButton).toBeTruthy()
    await adminSettingsButton!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/users/61', expect.objectContaining({
      headers: expect.objectContaining({
        authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.find('.user-profile-stub').exists()).toBe(true)
    expect(wrapper.get('.admin-user-modal').classes()).toContain('ui-responsive-dialog')
    expect(wrapper.get('.admin-user-modal-overlay').classes()).toContain('ui-responsive-dialog-backdrop')
    expect(wrapper.text()).toContain('managed61')
    expect(wrapper.text()).toContain('مشتری ویژه')
    expect(wrapper.emitted('navigate')).toBeUndefined()

    await wrapper.get('.user-profile-stub button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.user-profile-stub').exists()).toBe(false)
  })

  it('shows an inline admin error when loading user settings fails', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 61,
      account_name: 'managed61',
      avatar_file_id: null,
      mobile_number: '09121110000',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۲/۰۱',
      trades_count: 1,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse({ detail: 'بارگذاری تنظیمات کاربر ممکن نشد' }, false))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 61, account_name: 'managed61' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          Teleport: true,
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          UserProfile: true,
        },
      },
    })

    await flushPromises()

    const adminSettingsButton = wrapper.findAll('button').find((button) => button.text().includes('تنظیمات کاربر'))
    expect(adminSettingsButton).toBeTruthy()
    await adminSettingsButton!.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/users/61', expect.objectContaining({
      headers: expect.objectContaining({
        authorization: 'Bearer token',
      }),
    }))
    expect(wrapper.find('.user-profile-stub').exists()).toBe(false)
    expect(wrapper.text()).toContain('بارگذاری تنظیمات کاربر ممکن نشد')
  })

  it('renders the own-profile avatar trigger at the top-right without duplicate hero copy', async () => {
    const fetchMock = vi.mocked(fetch)
    buildChatFileUrlMock.mockImplementation((fileId?: string | null) => fileId ? `/files/${fileId}` : '')
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 31,
      account_name: 'owner31',
      avatar_file_id: 'avatar-1',
      mobile_number: '09126666666',
      address: 'تهران',
      last_seen_at: '2026-05-29T12:00:00Z',
      created_at_jalali: '۱۴۰۵/۰۱/۰۴',
      trades_count: 2,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 31, account_name: 'owner31' },
        viewerUserId: 31,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.find('[data-test="profile-avatar-trigger"]').exists()).toBe(true)
    expect(wrapper.find('.header-spacer [data-test="profile-avatar-trigger"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="profile-avatar-trigger"]').attributes('aria-label')).toBe('تغییر آواتار')
    expect(wrapper.text()).not.toContain('افزودن عکس')
    expect(wrapper.text()).not.toContain('تغییر عکس')
    expect(wrapper.find('.profile-hero').exists()).toBe(false)
    expect(wrapper.find('.profile-hero-copy').exists()).toBe(false)
    expect(wrapper.find('.profile-presence-status--own').exists()).toBe(true)
  })

  it('uses the standardized header avatar and menu layout on public profiles viewed by others', async () => {
    const fetchMock = vi.mocked(fetch)
    buildChatFileUrlMock.mockImplementation((fileId?: string | null) => fileId ? `/files/${fileId}` : '')
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 72,
      account_name: 'public72',
      avatar_file_id: 'avatar-72',
      mobile_number: '09125550000',
      address: 'تهران',
      last_seen_at: '2026-06-07T09:00:00Z',
      created_at_jalali: '۱۴۰۵/۰۳/۱۷',
      trades_count: 6,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 72, account_name: 'public72' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.find('[data-test="profile-avatar-readonly"]').exists()).toBe(true)
    expect(wrapper.find('.profile-presence-status--header').exists()).toBe(false)
    expect(wrapper.find('.profile-hero').exists()).toBe(false)
    expect(wrapper.find('.profile-menu-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('اقدام‌های عمومی')
    expect(wrapper.text()).toContain('ارسال پیام')
  })

  it('keeps customer context out of the public profile while preserving public actions', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 91,
      account_name: 'customer91',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'شیراز',
      created_at_jalali: '۱۴۰۵/۰۲/۰۲',
      trades_count: 5,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: 20,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
      customer_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 91, account_name: 'customer91' },
        viewerUserId: 20,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
          OwnerCustomerManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.find('.customer-context-banner').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('پروفایل مشتری')
    expect(wrapper.text()).not.toContain('سرگروه: owner20')
    expect(wrapper.text()).not.toContain('مالک: owner20')
    expect(wrapper.find('.profile-menu-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('ارسال پیام')
  })

  it('opens the avatar picker and uploads a new owner avatar', async () => {
    const fetchMock = vi.mocked(fetch)
    const inputClickSpy = vi.spyOn(HTMLInputElement.prototype, 'click')
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 45,
      account_name: 'owner45',
      avatar_file_id: null,
      mobile_number: '09128888888',
      address: 'تهران',
      created_at_jalali: '۱۴۰۵/۰۱/۰۶',
      trades_count: 7,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    uploadAvatarImageMock.mockResolvedValue({ file_id: 'avatar-99' })
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse({ avatar_file_id: 'avatar-99' }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 45, account_name: 'owner45' },
        viewerUserId: 45,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).not.toContain('افزودن عکس')
    await wrapper.get('[data-test="profile-avatar-trigger"]').trigger('click')
    expect(inputClickSpy).toHaveBeenCalled()

    const input = wrapper.get('.hidden-avatar-input')
    const file = new File(['avatar'], 'avatar.png', { type: 'image/png' })
    Object.defineProperty(input.element, 'files', {
      value: [file],
      configurable: true,
    })

    await input.trigger('change')
    await flushPromises()

    expect(uploadAvatarImageMock).toHaveBeenCalledWith(file, '')
    expect(fetchMock.mock.calls.some(([url, init]) => (
      url === '/api/auth/me/avatar'
      && (init as RequestInit | undefined)?.method === 'PUT'
      && (init as RequestInit | undefined)?.body === JSON.stringify({ avatar_file_id: 'avatar-99' })
    ))).toBe(true)

    inputClickSpy.mockRestore()
  })

  it('surfaces avatar upload errors from the owner avatar trigger flow', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 46,
      account_name: 'owner46',
      avatar_file_id: 'avatar-46',
      mobile_number: '09129999999',
      address: 'شیراز',
      created_at_jalali: '۱۴۰۵/۰۱/۰۷',
      trades_count: 9,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 46, account_name: 'owner46' },
        viewerUserId: 46,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    uploadAvatarImageMock.mockRejectedValueOnce(new Error('آپلود ناموفق بود'))
    const input = wrapper.get('.hidden-avatar-input')
    const file = new File(['avatar'], 'avatar.png', { type: 'image/png' })
    Object.defineProperty(input.element, 'files', {
      value: [file],
      configurable: true,
    })

    await input.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('آپلود ناموفق بود')
  })

  it('lets the owner edit their address from personal information', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 44,
      account_name: 'owner44',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'آدرس قبلی',
      created_at_jalali: '۱۴۰۵/۰۱/۰۵',
      trades_count: 18,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'owner44' },
        viewerUserId: 44,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.get('.address-edit-trigger').classes()).toContain('ui-icon-button')
    await wrapper.get('.address-edit-trigger').trigger('click')
    expect(wrapper.get('.address-edit-textarea').classes()).toContain('ui-textarea')
    await wrapper.get('.address-edit-textarea').setValue('بازار تهران، پلاک ۱۲')
    fetchMock.mockResolvedValueOnce(makeResponse({ address: 'بازار تهران، پلاک ۱۲' }))
    await wrapper.get('.address-edit-form').trigger('submit')
    await flushPromises()

    const addressCall = fetchMock.mock.calls.find(([url]) => url === '/api/auth/me/address')
    expect(addressCall?.[1]).toEqual(expect.objectContaining({
      method: 'PUT',
      body: JSON.stringify({ address: 'بازار تهران، پلاک ۱۲' }),
    }))
    expect(wrapper.text()).toContain('بازار تهران، پلاک ۱۲')
    expect(wrapper.find('.address-edit-form').exists()).toBe(false)
  })

  it('renders owner profile lists without help popovers and keeps accountant list visible as rows', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 44,
      account_name: 'owner44',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'اصفهان',
      created_at_jalali: '۱۴۰۵/۰۱/۰۵',
      trades_count: 18,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [
        {
          accountant_user_id: 66,
          accountant_account_name: 'acct66',
          relation_display_name: 'حسابدار اصلی',
          duty_description: null,
        },
      ],
      customer_relations: [
        {
          customer_user_id: 77,
          customer_account_name: 'cust77',
          management_name: 'مشتری تهران',
          customer_tier: 'tier1',
        },
      ],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'owner44' },
        viewerUserId: 44,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerCustomerManagerModal: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('لیست همکاران')
    expect(wrapper.text()).toContain('لیست حسابداران')
    expect(wrapper.text()).not.toContain('لیست مشتریان')
    expect(wrapper.find('[data-test="public-profile-customers-help"]').exists()).toBe(false)
    expect(wrapper.find('.profile-accordion').exists()).toBe(false)

    expect(wrapper.find('[data-test="public-profile-info-help"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="public-profile-project-users-help"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="public-profile-accountants-help"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="public-profile-history-help"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('برای حفظ حریم خصوصی')
    expect(wrapper.text()).not.toContain('با انتخاب نام هر همکار')
    expect(wrapper.text()).toContain('حسابدار اصلی')
    expect(wrapper.text()).toContain('@acct66')
  })

  it('exposes owner actions for settings and owner workspace navigation', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 44,
      account_name: 'owner44',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'اصفهان',
      created_at_jalali: '۱۴۰۵/۰۱/۰۵',
      trades_count: 18,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 44, account_name: 'owner44' },
        viewerUserId: 44,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
        },
      },
    })

    await flushPromises()

    const settingsButton = wrapper.findAll('button').find((button) => button.text().includes('تنظیمات کاربری'))
    expect(settingsButton).toBeTruthy()
    await settingsButton!.trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['settings'])

    const accountantButton = wrapper.findAll('button').find((button) => button.text().includes('حسابداران'))
    expect(accountantButton).toBeTruthy()

    const customerButton = wrapper.findAll('button').find((button) => button.text().includes('مشتریان'))
    expect(customerButton).toBeTruthy()
    await customerButton!.trigger('click')
    expect(wrapper.emitted('navigate')?.[1]).toEqual(['operations_customers'])
    expect(vi.mocked(alert)).not.toHaveBeenCalled()

    await accountantButton!.trigger('click')

    expect(wrapper.emitted('navigate')?.[2]).toEqual(['operations_accountants'])
  })

  it('does not render or request trade history for direct public profiles', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 50,
      account_name: 'owner50',
      avatar_file_id: null,
      mobile_number: '09121112222',
      address: 'قم',
      created_at_jalali: '۱۴۰۵/۰۱/۱۰',
      trades_count: 3,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({
      can_block: true,
      can_block_now: true,
      max_blocked: 10,
      current_blocked: 0,
      remaining: 10,
      reason_code: null,
      reason_message: null,
    }))
    fetchMock.mockResolvedValueOnce(makeResponse({ is_blocked_by_me: false }))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 1,
        trade_number: 10001,
        created_at: 'امروز',
        commodity_name: 'سکه',
        quantity: 2,
        price: 123000,
        trade_type: 'BUY',
        trade_path_kind: 'owner_customer_tier2',
        trade_path_summary: 'مالک ↔ مشتری سطح ۲',
        offer_user_name: 'مالک',
        responder_user_name: 'بیننده',
        responder_user_id: 99,
      },
      {
        id: 2,
        trade_number: 10002,
        created_at: 'دیروز',
        commodity_name: 'طلا',
        quantity: 1,
        price: 456000,
        trade_type: 'BUY',
        trade_path_kind: 'owner_customer_tier1',
        trade_path_summary: 'مالک ↔ مشتری سطح ۱',
        counterparty_user_id: 70,
        counterparty_name: 'حسابدار فروش',
        counterparty_profile_user_id: 70,
        counterparty_profile_account_name: 'owner-70',
        counterparty_highlight_accountant_user_id: 61,
        counterparty_highlight_accountant_relation_display_name: 'حسابدار فروش',
        customer_context_visible: true,
        customer_context_user_id: 61,
        customer_context_management_name: 'مشتری واسط',
        customer_context_tier: 'tier1',
        offer_user_id: 61,
        offer_user_name: 'حسابدار فروش',
        offer_user_profile_user_id: 70,
        offer_user_profile_account_name: 'owner-70',
        offer_user_highlight_accountant_user_id: 61,
        offer_user_highlight_accountant_relation_display_name: 'حسابدار فروش',
        responder_user_name: 'مالک',
        responder_user_id: 50,
      },
    ]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 50, account_name: 'owner50' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.findAll('button').some((node) => node.text().includes('اعمال فیلتر'))).toBe(false)
    expect(wrapper.text()).not.toContain('تاریخچه معاملات')
    expect(fetchMock.mock.calls.some(([url]) => typeof url === 'string' && url.includes('/api/trades/'))).toBe(false)
  })

  it('does not render target trade history for super-admin public viewers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 60,
      account_name: 'customer60',
      avatar_file_id: null,
      mobile_number: '09125556666',
      address: 'تبریز',
      created_at_jalali: '۱۴۰۵/۰۱/۱۲',
      trades_count: 4,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: 15,
      customer_owner_account_name: 'owner15',
      customer_management_name: 'مشتری راهبردی',
      customer_tier: 'tier2',
    }))
    fetchMock.mockResolvedValueOnce(makeResponse([
      {
        id: 3,
        trade_number: 10003,
        created_at: 'امروز',
        commodity_name: 'سکه',
        quantity: 3,
        price: 789000,
        trade_type: 'BUY',
        offer_user_id: 88,
        offer_user_name: 'فروشنده بیرونی',
        responder_user_id: 60,
        responder_user_name: 'مشتری راهبردی',
      },
    ]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 60, account_name: 'customer60' },
        viewerUserId: 900,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.findAll('button').some((node) => node.text().includes('اعمال فیلتر'))).toBe(false)
    expect(wrapper.text()).not.toContain('تاریخچه معاملات')
    expect(wrapper.text()).not.toContain('سرگروه owner15')
    expect(fetchMock.mock.calls.some(([url]) => typeof url === 'string' && url.includes('/api/trades/'))).toBe(false)
  })

  it('loads own trade history from the self endpoint and shows the empty state', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 51,
      account_name: 'owner51',
      avatar_file_id: null,
      mobile_number: '09123334444',
      address: 'کرج',
      created_at_jalali: '۱۴۰۵/۰۱/۱۱',
      trades_count: 0,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 51, account_name: 'owner51' },
        viewerUserId: 51,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const applyButtonWithCommodity = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButtonWithCommodity).toBeTruthy()
    await applyButtonWithCommodity!.trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.filter(([url]) => typeof url === 'string' && url.startsWith('/api/trades/my/page?'))).toHaveLength(1)
    expect(wrapper.text()).toContain('هنوز هیچ معامله‌ای انجام نداده‌اید.')
  })

  it('refreshes an already-loaded own trade history from a receipt-backed trade notification', async () => {
    const fetchMock = vi.mocked(fetch)
    let historyRequestCount = 0
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url
      if (url === '/api/users-public/51') {
        return Promise.resolve(makeResponse({
          id: 51,
          account_name: 'owner51',
          avatar_file_id: null,
          mobile_number: '09123334444',
          address: 'کرج',
          created_at_jalali: '۱۴۰۵/۰۱/۱۱',
          trades_count: 1,
          accountant_relations: [],
        }))
      }
      if (url === '/api/commodities/') return Promise.resolve(makeResponse([]))
      if (url.startsWith('/api/trades/my/page?')) {
        historyRequestCount += 1
        return Promise.resolve(makeHistoryPage(historyRequestCount === 1 ? [] : [{
          id: 91,
          trade_number: 777,
          created_at: 'امروز',
          commodity_name: 'سکه',
          quantity: 1,
          price: 50000000,
          trade_type: 'BUY',
          settlement_type: 'cash',
          offer_user_id: 51,
          offer_user_name: 'owner51',
          responder_user_id: 9,
          responder_user_name: 'peer9',
        }]))
      }
      return defaultFetchResponse(url)
    })

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 51, account_name: 'owner51' },
        viewerUserId: 51,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })
    await flushPromises()

    const applyButton = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    await applyButton!.trigger('click')
    await flushPromises()
    expect(historyRequestCount).toBe(1)

    vi.useFakeTimers()
    emitPublicProfileRealtime('message', { id: 501, category: 'trade', trade_number: 777 })
    await vi.advanceTimersByTimeAsync(90)
    await flushPromises()

    expect(historyRequestCount).toBe(2)
    expect(wrapper.text()).toContain('#777')

    wrapper.unmount()
    expect(publicProfileRealtimeMocks.off).toHaveBeenCalledWith('message', expect.any(Function))
    vi.useRealTimers()
  })

  it('hides customer tier and trade relationship details on customer own profiles', async () => {
    currentUserSummary.value = {
      id: 61,
      role: 'عادی',
      account_name: 'customer61',
      is_customer: true,
      customer_tier: 'tier2',
      customer_owner_user_id: 15,
      customer_owner_account_name: 'owner15',
    }

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url

      if (url === '/api/users-public/61') {
        return Promise.resolve(makeResponse({
          id: 61,
          account_name: 'customer61',
          avatar_file_id: null,
          mobile_number: '09123330061',
          address: 'تهران',
          created_at_jalali: '۱۴۰۵/۰۱/۱۱',
          trades_count: 1,
          resolved_from_accountant_id: null,
          highlight_accountant_user_id: null,
          highlight_accountant_relation_display_name: null,
          accountant_relations: [],
          customer_owner_user_id: 15,
          customer_owner_account_name: 'owner15',
          customer_management_name: 'مشتری سطح دو',
          customer_tier: 'tier2',
          customer_relations: [],
        }))
      }
      if (url.startsWith('/api/trades/my/page?')) {
        return Promise.resolve(makeResponse([
          {
            id: 10,
            trade_number: 10010,
            created_at: 'امروز',
            quantity: 3,
            commodity_name: 'سکه',
            price: 150000,
            trade_type: 'BUY',
            offer_user_id: 15,
            offer_user_name: 'owner15',
            responder_user_id: 61,
            responder_user_name: 'customer61',
            counterparty_name: 'owner15',
            trade_path_summary: 'مالک ↔ مشتری سطح ۲',
            customer_context_visible: true,
            customer_context_management_name: 'مشتری سطح دو',
            customer_context_tier: 'tier2',
          },
        ]))
      }
      return defaultFetchResponse(url)
    })

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 61, account_name: 'customer61' },
        viewerUserId: 61,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.find('.customer-context-banner').text()).toContain('سرگروه: owner15')
    expect(wrapper.find('.customer-context-banner').text()).not.toContain('سطح 2')
    expect(wrapper.text()).not.toContain('طرف دیگر معامله')
    expect(wrapper.find('[data-test="public-profile-history-help"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('بازه زمانی و کالا را از فهرست کالاهای ثبت‌شده محدود کنید')
    expect(wrapper.text()).not.toContain('طرف دیگر معامله را از میان همکاران پروژه انتخاب کنید')

    const applyButton = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButton).toBeTruthy()
    await applyButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('10010')
    expect(wrapper.text()).toContain('سکه')
    expect(wrapper.text()).not.toContain('طرف معامله:')
    expect(wrapper.text()).not.toContain('مسیر:')
    expect(wrapper.text()).not.toContain('رابطه:')
    expect(wrapper.text()).not.toContain('مالک ↔ مشتری سطح ۲')
    expect(wrapper.text()).not.toContain('سطح 2')
  })

  it('filters own trade history by a selected project coworker', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url

      if (url === '/api/users-public/51') {
        return Promise.resolve(makeResponse({
          id: 51,
          account_name: 'owner51',
          avatar_file_id: null,
          mobile_number: '09123334444',
          address: 'کرج',
          created_at_jalali: '۱۴۰۵/۰۱/۱۱',
          trades_count: 0,
          resolved_from_accountant_id: null,
          highlight_accountant_user_id: null,
          highlight_accountant_relation_display_name: null,
          accountant_relations: [],
        }))
      }
      if (url.startsWith('/api/trades/my/page?')) {
        return Promise.resolve(makeResponse([]))
      }
      if (url === '/api/commodities/') {
        return Promise.resolve(makeResponse([{ id: 1, name: 'سکه', aliases: [] }]))
      }
      if (url === '/api/users-public/51/project-users?limit=100') {
        return Promise.resolve(makeResponse([
          { id: 90, account_name: 'partner90', mobile_number: '09120000090' },
        ]))
      }
      if (url.startsWith('/api/trades/with/90/page?')) {
        return Promise.resolve(makeResponse([
          {
            id: 5,
            trade_number: 10005,
            created_at: '2026-05-20T09:00:00Z',
            quantity: 1,
            commodity_name: 'سکه',
            price: 101000,
            trade_type: 'SELL',
            offer_user_id: 51,
            offer_user_name: 'owner51',
            responder_user_id: 90,
            responder_user_name: 'partner90',
            counterparty_profile_user_id: 90,
            counterparty_profile_account_name: 'partner90',
            counterparty_highlight_accountant_user_id: 61,
            counterparty_highlight_accountant_relation_display_name: 'حسابدار فروش',
          },
        ]))
      }
      return defaultFetchResponse(url)
    })

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 51, account_name: 'owner51' },
        viewerUserId: 51,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const applyButtonAfterInvalidRange = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButtonAfterInvalidRange).toBeTruthy()
    await applyButtonAfterInvalidRange!.trigger('click')
    await flushPromises()

    const selects = wrapper.findAll('.history-filter-field-wide select')
    expect(selects).toHaveLength(2)
    expect(selects[1]!.classes()).toContain('ui-select')
    await selects[1]!.trigger('focus')
    await flushPromises()
    await selects[1]!.setValue('90')
    await applyButtonAfterInvalidRange!.trigger('click')
    await flushPromises()

    const filteredCall = fetchMock.mock.calls.find(([url]) => typeof url === 'string' && url.startsWith('/api/trades/with/90/page?'))
    expect(filteredCall).toBeTruthy()
    expect(wrapper.text()).toContain('طرف دیگر: partner90')
    expect(wrapper.text()).not.toContain('09120000090')
    expect(wrapper.text()).toContain('partner90')

    await wrapper.get('.mini-trade-card .trade-counterparty .profile-link-btn').trigger('click')
    expect(wrapper.emitted('navigate')?.at(-1)).toEqual([
      'public_profile',
      { id: 90 },
    ])
  })

  it('applies history filters and exports with the same query state', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 50,
      account_name: 'owner50',
      avatar_file_id: null,
      mobile_number: '09121112222',
      address: 'قم',
      created_at_jalali: '۱۴۰۵/۰۱/۱۰',
      trades_count: 3,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([{ id: 1, name: 'سکه', aliases: [{ alias: 'امامی' }] }]))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(new Response('export', {
      status: 200,
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': 'attachment; filename="trade_history_owner50.pdf"',
      },
    }))

    const createObjectURL = vi.fn(() => 'blob:history')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true })
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 50, account_name: 'owner50' },
        viewerUserId: 50,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const applyButtonBeforeFilter = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButtonBeforeFilter).toBeTruthy()
    await applyButtonBeforeFilter!.trigger('click')
    await flushPromises()

    await setHistoryDate(wrapper, 0, '2026-05-01')
    await setHistoryDate(wrapper, 1, '2026-05-31')
    const commoditySelect = wrapper.find('.history-filter-field-wide select')
    expect(commoditySelect.exists()).toBe(true)
    await commoditySelect.trigger('focus')
    await flushPromises()
    await commoditySelect.setValue('سکه')
    const historyFilterSelects = wrapper.findAll('.history-filter-field select')
    await historyFilterSelects[1]!.setValue('sell')
    await historyFilterSelects[2]!.setValue('tomorrow')

    const applyButtonAfterFilter = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButtonAfterFilter).toBeTruthy()
    await applyButtonAfterFilter!.trigger('click')
    await flushPromises()

    const filteredCall = fetchMock.mock.calls.find(([url]) => (
      typeof url === 'string'
      && url.includes('/api/trades/my/page?')
      && url.includes('from_date=2026-05-01')
    ))
    expect(filteredCall?.[0]).toContain('from_date=2026-05-01')
    expect(filteredCall?.[0]).toContain('to_date=2026-05-31')
    expect(filteredCall?.[0]).toContain('commodity_query=%D8%B3%DA%A9%D9%87')
    expect(filteredCall?.[0]).toContain('trade_type=sell')
    expect(filteredCall?.[0]).toContain('settlement_type=tomorrow')

    await clickHistoryOverflowAction(wrapper, 'خروجی PDF')
    await flushPromises()

    const exportCall = fetchMock.mock.calls.find(([url]) => typeof url === 'string' && url.includes('/api/trades/my/export?'))
    expect(exportCall?.[0]).toContain('format=pdf')
    expect(exportCall?.[0]).toContain('from_date=2026-05-01')
    expect(exportCall?.[0]).toContain('to_date=2026-05-31')
    expect(exportCall?.[0]).toContain('commodity_query=%D8%B3%DA%A9%D9%87')
    expect(exportCall?.[0]).toContain('trade_type=sell')
    expect(exportCall?.[0]).toContain('settlement_type=tomorrow')
    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(anchorClick).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledOnce()

    anchorClick.mockRestore()
  })

  it('loads trade history past 50 rows, deduplicates page boundaries, and retries the same cursor', async () => {
    const fetchMock = vi.mocked(fetch)
    let loadMoreAttempts = 0
    const tradeRow = (id: number) => ({
      id,
      trade_number: 20_000 + id,
      created_at: `2026-05-${String((id % 28) + 1).padStart(2, '0')}T09:00:00Z`,
      quantity: 1,
      commodity_name: 'سکه',
      price: 100_000 + id,
      trade_type: 'BUY',
      settlement_type: 'cash',
      offer_user_id: 80,
      offer_user_name: 'seller80',
      responder_user_id: 50,
      responder_user_name: 'owner50',
    })
    const firstPage = Array.from({ length: 50 }, (_, index) => tradeRow(100 - index))
    const secondPage = [tradeRow(51), ...Array.from({ length: 20 }, (_, index) => tradeRow(50 - index))]

    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url
      if (url === '/api/users-public/50') {
        return Promise.resolve(makeResponse({
          id: 50,
          account_name: 'owner50',
          avatar_file_id: null,
          mobile_number: '09121112222',
          address: 'قم',
          created_at_jalali: '۱۴۰۵/۰۱/۱۰',
          trades_count: 70,
          resolved_from_accountant_id: null,
          highlight_accountant_user_id: null,
          highlight_accountant_relation_display_name: null,
          accountant_relations: [],
        }))
      }
      if (url.startsWith('/api/trades/my/page?') && url.includes('cursor=cursor-50')) {
        loadMoreAttempts += 1
        if (loadMoreAttempts === 1) {
          return Promise.resolve(makeResponse({ detail: 'ارتباط موقتاً قطع شد.' }, false, 503))
        }
        return Promise.resolve(makeHistoryPage(secondPage))
      }
      if (url.startsWith('/api/trades/my/page?')) {
        return Promise.resolve(makeHistoryPage(firstPage, 'cursor-50', true))
      }
      return defaultFetchResponse(url)
    })

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 50, account_name: 'owner50' },
        viewerUserId: 50,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })
    await flushPromises()

    const historySelects = wrapper.findAll('.history-filter-field select')
    expect(historySelects).toHaveLength(4)
    await historySelects[1]!.setValue('buy')
    await historySelects[2]!.setValue('cash')
    const applyButton = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButton).toBeTruthy()
    await applyButton!.trigger('click')
    await flushPromises()

    const firstPageCall = fetchMock.mock.calls.find(([url]) => (
      typeof url === 'string'
      && url.startsWith('/api/trades/my/page?')
      && url.includes('trade_type=buy')
    ))
    expect(firstPageCall?.[0]).toContain('settlement_type=cash')
    expect(wrapper.findAll('.mini-trade-card')).toHaveLength(50)

    const loadMoreButton = wrapper.findAll('button').find((node) => node.text().includes('نمایش معاملات بیشتر'))
    expect(loadMoreButton).toBeTruthy()
    await loadMoreButton!.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.mini-trade-card')).toHaveLength(50)
    expect(wrapper.text()).toContain('خطا در دریافت ادامه تاریخچه معاملات')

    const retryButton = wrapper.findAll('button').find((node) => node.text().includes('تلاش دوباره'))
    expect(retryButton).toBeTruthy()
    await retryButton!.trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.mini-trade-card')).toHaveLength(70)
    const cursorCalls = fetchMock.mock.calls.filter(([url]) => (
      typeof url === 'string' && url.includes('cursor=cursor-50')
    ))
    expect(cursorCalls).toHaveLength(2)
  })

  it('keeps loaded history and selected filters visible when a refresh fails', async () => {
    const fetchMock = vi.mocked(fetch)
    const preservedTrade = {
      id: 501,
      trade_number: 30501,
      created_at: '2026-07-15T09:00:00Z',
      quantity: 2,
      commodity_name: 'سکه',
      price: 125000,
      trade_type: 'BUY',
      settlement_type: 'cash',
      offer_user_id: 80,
      offer_user_name: 'seller80',
      responder_user_id: 50,
      responder_user_name: 'owner50',
    }
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url
      if (url === '/api/users-public/50') {
        return Promise.resolve(makeResponse({
          id: 50,
          account_name: 'owner50',
          avatar_file_id: null,
          mobile_number: '09121112222',
          address: 'قم',
          created_at_jalali: '۱۴۰۵/۰۱/۱۰',
          trades_count: 1,
          accountant_relations: [],
        }))
      }
      if (url.startsWith('/api/trades/my/page?') && url.includes('trade_type=sell')) {
        return Promise.resolve(makeResponse({ detail: 'بازخوانی تاریخچه ناموفق بود' }, false, 400))
      }
      if (url.startsWith('/api/trades/my/page?')) {
        return Promise.resolve(makeHistoryPage([preservedTrade]))
      }
      return defaultFetchResponse(url)
    })

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 50, account_name: 'owner50' },
        viewerUserId: 50,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })
    await flushPromises()

    const tradeTypeSelect = wrapper.findAll('.history-filter-field select')[1]!
    const applyButton = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))!
    await tradeTypeSelect.setValue('buy')
    await applyButton.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('#30501')

    await tradeTypeSelect.setValue('sell')
    await applyButton.trigger('click')
    await flushPromises()

    expect(tradeTypeSelect.element).toHaveProperty('value', 'sell')
    expect(wrapper.text()).toContain('بازخوانی تاریخچه ناموفق بود')
    expect(wrapper.text()).toContain('#30501')
  })

  it('ignores an older history response after a newer filter request completes', async () => {
    const fetchMock = vi.mocked(fetch)
    let resolveBuy: ((response: Response) => void) | null = null
    let resolveSell: ((response: Response) => void) | null = null
    const historyRow = (id: number, tradeType: 'BUY' | 'SELL') => ({
      id,
      trade_number: id,
      created_at: '2026-07-15T09:00:00Z',
      quantity: 1,
      commodity_name: 'سکه',
      price: 100_000 + id,
      trade_type: tradeType,
      settlement_type: 'cash',
      offer_user_id: 80,
      offer_user_name: 'seller80',
      responder_user_id: 50,
      responder_user_name: 'owner50',
    })

    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url
      if (url === '/api/users-public/50') {
        return Promise.resolve(makeResponse({
          id: 50,
          account_name: 'owner50',
          avatar_file_id: null,
          mobile_number: '09121112222',
          address: 'قم',
          created_at_jalali: '۱۴۰۵/۰۱/۱۰',
          trades_count: 2,
          resolved_from_accountant_id: null,
          highlight_accountant_user_id: null,
          highlight_accountant_relation_display_name: null,
          accountant_relations: [],
        }))
      }
      if (url.startsWith('/api/trades/my/page?') && url.includes('trade_type=buy')) {
        return new Promise<Response>((resolve) => { resolveBuy = resolve })
      }
      if (url.startsWith('/api/trades/my/page?') && url.includes('trade_type=sell')) {
        return new Promise<Response>((resolve) => { resolveSell = resolve })
      }
      return defaultFetchResponse(url)
    })

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 50, account_name: 'owner50' },
        viewerUserId: 50,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })
    await flushPromises()

    const tradeTypeSelect = wrapper.findAll('.history-filter-field select')[1]!
    const applyButton = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))!
    await tradeTypeSelect.setValue('buy')
    await applyButton.trigger('click')
    await flushPromises()
    await tradeTypeSelect.setValue('sell')
    await applyButton.trigger('click')
    await flushPromises()

    if (!resolveSell || !resolveBuy) throw new Error('Expected both history requests')
    ;(resolveSell as (response: Response) => void)(makeHistoryPage([historyRow(222, 'SELL')]))
    await flushPromises()
    expect(wrapper.text()).toContain('#222')

    ;(resolveBuy as (response: Response) => void)(makeHistoryPage([historyRow(111, 'BUY')]))
    await flushPromises()
    expect(wrapper.text()).toContain('#222')
    expect(wrapper.text()).not.toContain('#111')
  })

  it('blocks invalid history date ranges before refetch or export', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 50,
      account_name: 'owner50',
      avatar_file_id: null,
      mobile_number: '09121112222',
      address: 'قم',
      created_at_jalali: '۱۴۰۵/۰۱/۱۰',
      trades_count: 3,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
    }))
    fetchMock.mockResolvedValueOnce(makeResponse([]))
    fetchMock.mockResolvedValueOnce(makeResponse([{ id: 1, name: 'سکه', aliases: [{ alias: 'امامی' }] }]))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 50, account_name: 'owner50' },
        viewerUserId: 50,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    const applyButtonBeforeInvalidRange = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButtonBeforeInvalidRange).toBeTruthy()
    await applyButtonBeforeInvalidRange!.trigger('click')
    await flushPromises()

    await setHistoryDate(wrapper, 0, '2026-06-01')
    await setHistoryDate(wrapper, 1, '2026-05-01')

    const applyButtonWithInvalidRange = wrapper.findAll('button').find((node) => node.text().includes('اعمال فیلتر'))
    expect(applyButtonWithInvalidRange).toBeTruthy()
    await applyButtonWithInvalidRange!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('بازه زمانی انتخاب‌شده معتبر نیست.')
    expect(fetchMock.mock.calls.filter(([url]) => typeof url === 'string' && url.startsWith('/api/trades/my/page?'))).toHaveLength(1)

    await clickHistoryOverflowAction(wrapper, 'خروجی PDF')
    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => typeof url === 'string' && url.includes('/api/trades/my/export?'))).toBe(false)
    expect(wrapper.text()).toContain('بازه زمانی انتخاب‌شده معتبر نیست.')
  })

  it('shows network fetch errors and still allows returning home from the back button', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockRejectedValueOnce(new Error('دریافت پروفایل ناموفق بود'))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 52, account_name: 'owner52' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('دریافت پروفایل ناموفق بود')
    expect(wrapper.get('.profile-nav-back').classes()).toContain('ui-back-button')
    await wrapper.get('.profile-nav-back').trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['home'])
  })

  it('does not render accountant route-context details for a public peer', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 20,
      account_name: 'owner20',
      avatar_file_id: null,
      mobile_number: '09124444444',
      address: 'مشهد',
      created_at_jalali: '۱۴۰۵/۰۱/۰۲',
      trades_count: 12,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [
        {
          accountant_user_id: 44,
          accountant_account_name: 'acct44',
          relation_display_name: 'حسابدار فروش',
          duty_description: 'پیگیری معاملات',
        },
        {
          accountant_user_id: 45,
          accountant_account_name: 'acct45',
          relation_display_name: 'حسابدار دوم',
          duty_description: null,
        },
      ],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 20, account_name: 'owner20' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
        highlightAccountantUserId: 44,
        highlightAccountantRelationDisplayName: 'حسابدار فروش',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).not.toContain('نمایش پروفایل مالک اصلی')
    expect(wrapper.text()).not.toContain('حسابداران این مالک')
    expect(wrapper.find('.public-accountant-card.profile-relation-card').exists()).toBe(false)
    expect(wrapper.find('.profile-accordion').exists()).toBe(false)
  })

  it('does not render customer context on a customer public profile', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 91,
      account_name: 'customer91',
      avatar_file_id: null,
      mobile_number: '09127777777',
      address: 'شیراز',
      created_at_jalali: '۱۴۰۵/۰۲/۰۲',
      trades_count: 5,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: 20,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
      customer_relations: [],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 91, account_name: 'customer91' },
        viewerUserId: 20,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.find('.customer-context-banner').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('پروفایل مشتری')
    expect(wrapper.text()).not.toContain('مشتری ویژه')
    expect(wrapper.text()).not.toContain('سرگروه: owner20')
    expect(wrapper.text()).not.toContain('سطح 2')
    expect(wrapper.text()).not.toContain('مالک: owner20')
    expect(wrapper.text()).not.toContain('نمای مشتری')
    expect(wrapper.text()).not.toContain('زیرمجموعه مالک')
  })

  it('does not show owner customer membership for super-admin public viewers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر ارشد' }))

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 20,
      account_name: 'owner20',
      avatar_file_id: null,
      mobile_number: '09124444444',
      address: 'مشهد',
      created_at_jalali: '۱۴۰۵/۰۱/۰۲',
      trades_count: 12,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: null,
      customer_owner_account_name: null,
      customer_management_name: null,
      customer_tier: null,
      customer_relations: [
        {
          customer_user_id: 91,
          customer_account_name: 'customer91',
          management_name: 'مشتری ویژه',
          customer_tier: 'tier1',
        },
      ],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 20, account_name: 'owner20' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).not.toContain('مشتریان این مالک')
    expect(wrapper.find('.public-customer-card.profile-relation-card').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('مشتری ویژه')
    expect(wrapper.text()).not.toContain('customer91')
    expect(wrapper.text()).not.toContain('سطح 1')
  })

  it('does not show owner customer list for middle-manager viewers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))

    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeResponse({
      id: 20,
      account_name: 'owner20',
      avatar_file_id: null,
      mobile_number: '09124444444',
      address: 'مشهد',
      created_at_jalali: '۱۴۰۵/۰۱/۰۲',
      trades_count: 12,
      resolved_from_accountant_id: null,
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
      accountant_relations: [],
      customer_owner_user_id: null,
      customer_owner_account_name: null,
      customer_management_name: null,
      customer_tier: null,
      customer_relations: [
        {
          customer_user_id: 91,
          customer_account_name: 'customer91',
          management_name: 'مشتری ویژه',
          customer_tier: 'tier1',
        },
      ],
    }))

    const PublicProfile = (await import('./PublicProfile.vue')).default
    const wrapper = mount(PublicProfile, {
      props: {
        user: { id: 20, account_name: 'owner20' },
        viewerUserId: 99,
        apiBaseUrl: '',
        jwtToken: 'token',
      },
      global: {
        stubs: {
          LoadingSkeleton: true,
          OwnerAccountantManagerModal: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).not.toContain('مشتریان این مالک')
    expect(wrapper.text()).not.toContain('مشتری ویژه')
  })
})
