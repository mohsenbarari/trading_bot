import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setSessionApprovalBlocking } from './authenticatedOverlayPriority'
import { useOvertimeApprovalRuntime } from './useOvertimeApprovalRuntime'
import {
  M12_CANCEL_BUTTON,
  M15_CANCELLED,
  M21_REQUESTER_QUEUED,
  M35_OWNER_TITLE,
  M36_OWNER_APPROVE,
  formatOvertimeCountdown,
} from '../constants/offerOvertimeCopy'

const overtimeRuntimeMocks = vi.hoisted(() => {
  const handlers = new Map<string, Array<(payload?: unknown) => void>>()
  return {
    handlers,
    on: vi.fn((event: string, callback: (payload?: unknown) => void) => {
      const current = handlers.get(event) ?? []
      current.push(callback)
      handlers.set(event, current)
    }),
    off: vi.fn(),
    apiFetch: vi.fn(),
  }
})

vi.mock('./useWebSocket', () => ({
  useWebSocket: () => ({
    on: overtimeRuntimeMocks.on,
    off: overtimeRuntimeMocks.off,
  }),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: overtimeRuntimeMocks.apiFetch,
}))

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }
}

function mountRuntime() {
  let runtime!: ReturnType<typeof useOvertimeApprovalRuntime>
  const Harness = defineComponent({
    setup() {
      runtime = useOvertimeApprovalRuntime()
      return () => h('div')
    },
  })
  const wrapper = mount(Harness)
  return { wrapper, runtime }
}

describe('offer overtime copy helpers', () => {
  it('formats M22-style countdown with Persian digits', () => {
    expect(formatOvertimeCountdown(30)).toBe('۰۰:۳۰')
    expect(formatOvertimeCountdown(0)).toBe('۰۰:۰۰')
  })

  it('keeps approved owner prompt labels', () => {
    expect(M35_OWNER_TITLE).toBe('درخواست معامله در وقت اضافه')
    expect(M36_OWNER_APPROVE).toBe('تأیید معامله')
    expect(M12_CANCEL_BUTTON).toBe('لغو درخواست')
    expect(M15_CANCELLED).toBe('درخواست لغو شد.')
    expect(M21_REQUESTER_QUEUED).toBe('در حال ارسال درخواست...')
  })
})

describe('useOvertimeApprovalRuntime', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-05T12:00:00Z'))
    overtimeRuntimeMocks.handlers.clear()
    overtimeRuntimeMocks.on.mockClear()
    overtimeRuntimeMocks.off.mockClear()
    overtimeRuntimeMocks.apiFetch.mockReset()
    localStorage.clear()
    localStorage.setItem('auth_token', 'token-1')
    setSessionApprovalBlocking(false)
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })
  })

  afterEach(() => {
    setSessionApprovalBlocking(false)
    vi.useRealTimers()
  })

  it('loads owner current request after delay and seeds server countdown', async () => {
    const deadline = new Date(Date.now() + 25_000).toISOString()
    overtimeRuntimeMocks.apiFetch.mockImplementation(async (url: string) => {
      if (url.includes('/sessions/active')) {
        return jsonResponse([{ is_current: true, is_primary: true }])
      }
      if (url.includes('pending-owner')) {
        return jsonResponse({
          current: {
            request_public_id: 'req_ot_1',
            offer_public_id: 'ofr_1',
            is_occupying: true,
            is_actionable: true,
            decision_deadline_at: deadline,
            remaining_decision_seconds: 25,
            requested_quantity: 4,
            result_status: 'overtime_presented',
          },
          items: [],
        })
      }
      if (url.includes('pending-requester')) {
        return jsonResponse({ items: [] })
      }
      if (url.includes('/api/offers/public/')) {
        return jsonResponse({
          offer_public_id: 'ofr_1',
          offer_type: 'sell',
          settlement_type: 'cash',
          commodity_name: 'سکه',
          quantity: 10,
          remaining_quantity: 10,
          price: 1000,
          is_wholesale: true,
          notes: 'توضیح تست',
        })
      }
      return jsonResponse({})
    })

    const { wrapper, runtime } = mountRuntime()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(runtime.ownerRequest.value?.request_public_id).toBe('req_ot_1')
    expect(runtime.ownerVisible.value).toBe(true)
    expect(runtime.ownerCountdown.value).toBeGreaterThanOrEqual(24)
    expect(runtime.ownerCountdown.value).toBeLessThanOrEqual(25)
    expect(runtime.ownerCountdownLabel.value).toMatch(/^۰۰:(۲۴|۲۵)$/)
    expect(runtime.ownerOfferText.value).toContain('سکه')
    expect(runtime.ownerOfferText.value).toContain('توضیح تست')

    setSessionApprovalBlocking(true)
    await flushPromises()
    expect(runtime.ownerVisible.value).toBe(false)

    setSessionApprovalBlocking(false)
    await flushPromises()
    expect(runtime.ownerVisible.value).toBe(true)

    wrapper.unmount()
  })

  it('hides owner prompt on non-primary sessions', async () => {
    overtimeRuntimeMocks.apiFetch.mockImplementation(async (url: string) => {
      if (url.includes('/sessions/active')) {
        return jsonResponse([{ is_current: true, is_primary: false }])
      }
      if (url.includes('pending-owner')) {
        return jsonResponse({
          current: {
            request_public_id: 'req_ot_2',
            offer_public_id: 'ofr_2',
            is_occupying: true,
            is_actionable: true,
            remaining_decision_seconds: 20,
            result_status: 'overtime_presented',
          },
          items: [],
        })
      }
      if (url.includes('pending-requester')) {
        return jsonResponse({ items: [] })
      }
      return jsonResponse({})
    })

    const { wrapper, runtime } = mountRuntime()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(runtime.ownerVisible.value).toBe(false)
    wrapper.unmount()
  })

  it('shows and cancels a Web request whose authoritative offer home is foreign', async () => {
    overtimeRuntimeMocks.apiFetch.mockImplementation(async (url: string, options?: { method?: string }) => {
      if (url.includes('pending-owner')) {
        return jsonResponse({ current: null, items: [] })
      }
      if (url.includes('pending-requester') && (!options?.method || options.method === 'GET')) {
        return jsonResponse({
          items: [{
            request_public_id: 'req_ot_3',
            request_home_server: 'foreign',
            result_status: 'overtime_queued',
            is_actionable: false,
            is_occupying: false,
          }],
        })
      }
      if (url.includes('/cancel')) {
        return jsonResponse({ request_public_id: 'req_ot_3', result_status: 'overtime_cancelled' })
      }
      return jsonResponse({ current: null, items: [] })
    })

    const { wrapper, runtime } = mountRuntime()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(runtime.requesterVisible.value).toBe(true)
    expect(runtime.requesterMessage.value).toBe(M21_REQUESTER_QUEUED)

    await runtime.cancel()
    await flushPromises()
    expect(runtime.requesterTerminalNotice.value).toBe(M15_CANCELLED)
    wrapper.unmount()
  })
})
