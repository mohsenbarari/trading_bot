import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const commodityManagerMocks = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: commodityManagerMocks.apiFetchMock,
}))

function responseOf(data: unknown, ok = true, status = ok ? 200 : 400) {
  return {
    ok,
    status,
    json: async () => data,
  }
}

async function mountCommodityManager() {
  const CommodityManager = (await import('./CommodityManager.vue')).default
  return mount(CommodityManager, {
    props: {
      apiBaseUrl: '',
      jwtToken: 'jwt-token',
    },
    global: {
      stubs: {
        LoadingSkeleton: { template: '<div class="loading-skeleton-stub"></div>' },
      },
    },
  })
}

describe('CommodityManager.vue', () => {
  let commodityId = 3
  let aliasId = 30
  let commoditiesState: Array<{ id: number; name: string; aliases: Array<{ id: number; alias: string; commodity_id: number }> }>

  beforeEach(() => {
    commodityId = 3
    aliasId = 30
    commoditiesState = [
      {
        id: 1,
        name: 'امام',
        aliases: [
          { id: 11, alias: 'امامی', commodity_id: 1 },
          { id: 12, alias: 'سکه جدید', commodity_id: 1 },
        ],
      },
      {
        id: 2,
        name: 'بهار',
        aliases: [
          { id: 21, alias: 'بهار جدید', commodity_id: 2 },
        ],
      },
    ]

    commodityManagerMocks.apiFetchMock.mockReset()
    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/' && method === 'GET') {
        return responseOf(commoditiesState.map((commodity) => ({ ...commodity, aliases: [...commodity.aliases] })))
      }

      if (path.startsWith('/api/commodities/') && method === 'GET' && !path.includes('/aliases')) {
        const id = Number(path.split('/').filter(Boolean).pop())
        const commodity = commoditiesState.find((entry) => entry.id === id)
        return commodity ? responseOf({ ...commodity, aliases: [...commodity.aliases] }) : responseOf({ detail: 'not found' }, false, 404)
      }

      if (path === '/api/commodities/' && method === 'POST') {
        const payload = JSON.parse(options?.body as string)
        const newCommodity = {
          id: commodityId++,
          name: payload.commodity_data.name,
          aliases: (payload.aliases || []).map((alias: string) => ({ id: aliasId++, alias, commodity_id: commodityId - 1 })),
        }
        commoditiesState.push(newCommodity)
        return responseOf(newCommodity)
      }

      if (path.startsWith('/api/commodities/') && method === 'PUT' && !path.includes('/aliases')) {
        const id = Number(path.split('/').filter(Boolean).pop())
        const payload = JSON.parse(options?.body as string)
        const commodity = commoditiesState.find((entry) => entry.id === id)
        if (!commodity) return responseOf({ detail: 'not found' }, false, 404)
        commodity.name = payload.name
        return responseOf({ ...commodity, aliases: [...commodity.aliases] })
      }

      if (path.startsWith('/api/commodities/') && path.endsWith('/aliases') && method === 'POST') {
        const id = Number(path.split('/').filter(Boolean).slice(-2)[0])
        const payload = JSON.parse(options?.body as string)
        const commodity = commoditiesState.find((entry) => entry.id === id)
        if (!commodity) return responseOf({ detail: 'not found' }, false, 404)
        const newAlias = { id: aliasId++, alias: payload.alias, commodity_id: id }
        commodity.aliases.push(newAlias)
        return responseOf(newAlias)
      }

      if (path.startsWith('/api/commodities/aliases/') && method === 'PUT') {
        const id = Number(path.split('/').filter(Boolean).pop())
        const payload = JSON.parse(options?.body as string)
        for (const commodity of commoditiesState) {
          const alias = commodity.aliases.find((entry) => entry.id === id)
          if (alias) {
            alias.alias = payload.alias
            return responseOf(alias)
          }
        }
        return responseOf({ detail: 'not found' }, false, 404)
      }

      if (path.startsWith('/api/commodities/aliases/') && method === 'DELETE') {
        const id = Number(path.split('/').filter(Boolean).pop())
        for (const commodity of commoditiesState) {
          const nextAliases = commodity.aliases.filter((entry) => entry.id !== id)
          if (nextAliases.length !== commodity.aliases.length) {
            commodity.aliases = nextAliases
            return responseOf(null, true, 204)
          }
        }
        return responseOf({ detail: 'not found' }, false, 404)
      }

      if (path.startsWith('/api/commodities/') && method === 'DELETE') {
        const id = Number(path.split('/').filter(Boolean).pop())
        commoditiesState = commoditiesState.filter((entry) => entry.id !== id)
        return responseOf(null, true, 204)
      }

      return responseOf({ detail: 'unhandled path' }, false, 500)
    })
  })

  it('loads the commodity list, opens alias management, and returns to the list', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    expect(wrapper.text()).toContain('امام')
    expect(wrapper.text()).toContain('بهار')

    await wrapper.find('.list-item-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('امامی')
    expect(wrapper.text()).toContain('سکه جدید')

    await wrapper.find('.back-icon-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('افزودن کالای جدید')

    wrapper.unmount()
  })

  it('adds a commodity with aliases and refreshes the list', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.find('.action-btn.primary-soft').trigger('click')
    await flushPromises()

    const inputs = wrapper.findAll('.ds-input')
    await inputs[0]!.setValue('طلای آب‌شده')
    await inputs[1]!.setValue('آبشده - طلای خام')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(commodityManagerMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        commodity_data: { name: 'طلای آب‌شده' },
        aliases: ['طلای آب‌شده', 'آبشده', 'طلای خام'],
      }),
    }))
    expect(wrapper.text()).toContain('طلای آب‌شده')

    wrapper.unmount()
  })

  it('keeps alias management available for canonical Imam but hides rename and delete actions', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[0]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('کالای پیش فرض امام فقط از مسیر نام های مستعار قابل مدیریت است')
    expect(wrapper.find('.action-btn.secondary-soft').exists()).toBe(false)
    expect(wrapper.find('.action-btn.danger-soft').exists()).toBe(false)
    expect(wrapper.find('.action-btn.primary-soft').exists()).toBe(true)

    wrapper.unmount()
  })

  it('edits the commodity name and performs alias add, edit, and delete flows', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()

    const actionButtons = wrapper.findAll('.action-btn')
    await actionButtons[1]!.trigger('click')
    await flushPromises()

    await wrapper.find('.ds-input').setValue('سکه بهار آزادی')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('سکه بهار آزادی')
    expect(wrapper.text()).not.toContain('>بهار<')

    await wrapper.findAll('.action-btn')[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('.ds-input').setValue('بهار - طرح قدیم')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('بهار')
    expect(wrapper.text()).toContain('طرح قدیم')

    await wrapper.find('.icon-btn.edit').trigger('click')
    await flushPromises()
    await wrapper.find('.ds-input').setValue('بهار آزادی')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('بهار آزادی')

    await wrapper.find('.icon-btn.delete').trigger('click')
    await flushPromises()
    await wrapper.find('.ds-btn.danger').trigger('click')
    await flushPromises()

    const aliasTexts = wrapper.findAll('.alias-text').map((node) => node.text())
    expect(aliasTexts).not.toContain('بهار آزادی')
    expect(aliasTexts).toContain('طرح قدیم')

    wrapper.unmount()
  })

  it('deletes a commodity from the confirmation flow and returns to the list', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.find('.action-btn.danger-soft').trigger('click')
    await flushPromises()
    await wrapper.find('.ds-btn.danger').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('بهار')
    expect(wrapper.text()).toContain('امام')

    wrapper.unmount()
  })

  it('renders fetch and manage-alias failures with readable error details', async () => {
    commodityManagerMocks.apiFetchMock.mockResolvedValueOnce(responseOf({ detail: 'list failed' }, false, 500))
    const wrapper = await mountCommodityManager()
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در بارگیری لیست کالاها')
    wrapper.unmount()

    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/commodities/' && !options?.method) return responseOf(commoditiesState)
      if (path === '/api/commodities/1' && !options?.method) return responseOf({ detail: 'commodity missing' }, false, 404)
      return responseOf({}, true)
    })

    const manageWrapper = await mountCommodityManager()
    await flushPromises()
    await manageWrapper.find('.list-item-btn').trigger('click')
    await flushPromises()

    expect(manageWrapper.text()).toContain('خطا در دریافت اطلاعات کالا')
    expect(manageWrapper.text()).toContain('افزودن کالای جدید')

    manageWrapper.unmount()
  })

  it('keeps add/edit forms open when APIs return structured validation errors', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/' && method === 'GET') return responseOf(commoditiesState)
      if (path === '/api/commodities/' && method === 'POST') return responseOf({ detail: { name: ['duplicate'] } }, false, 422)
      if (path === '/api/commodities/1' && method === 'GET') return responseOf(commoditiesState[0])
      if (path === '/api/commodities/2' && method === 'GET') return responseOf(commoditiesState[1])
      if (path === '/api/commodities/2' && method === 'PUT') return responseOf({ detail: { name: ['too short'] } }, false, 422)
      return responseOf({ detail: 'unexpected' }, false, 500)
    })

    await wrapper.find('.action-btn.primary-soft').trigger('click')
    await flushPromises()
    await wrapper.findAll('.ds-input')[0]!.setValue('سکه امامی')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('duplicate')
    expect(wrapper.text()).toContain('افزودن کالا')

    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/' && method === 'GET') return responseOf(commoditiesState)
      if (path === '/api/commodities/2' && method === 'GET') return responseOf(commoditiesState[1])
      if (path === '/api/commodities/2' && method === 'PUT') return responseOf({ detail: { name: ['too short'] } }, false, 422)
      return responseOf({ detail: 'unexpected' }, false, 500)
    })

    await wrapper.find('.ds-btn.secondary').trigger('click')
    await flushPromises()
    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.action-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.find('.ds-input').setValue('x')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('too short')
    expect(wrapper.text()).toContain('ویرایش نام کالا')

    wrapper.unmount()
  })

  it('validates alias input and reports partial alias add failures', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.find('.list-item-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.action-btn')[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('.ds-input').setValue('   ')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('لطفاً حداقل یک نام مستعار وارد کنید.')

    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/1' && method === 'GET') return responseOf(commoditiesState[0])
      if (path === '/api/commodities/1/aliases' && method === 'POST') {
        const payload = JSON.parse(options!.body as string)
        if (payload.alias === 'خراب') return responseOf({ detail: 'تکراری' }, false, 409)
        return responseOf({ id: aliasId++, alias: payload.alias, commodity_id: 1 })
      }
      return responseOf(commoditiesState)
    })

    await wrapper.find('.ds-input').setValue('درست - خراب')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(commodityManagerMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/1/aliases', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ alias: 'درست' }),
    }))
    expect(commodityManagerMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/1/aliases', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ alias: 'خراب' }),
    }))
    expect(wrapper.text()).toContain('خراب: تکراری')
    expect(wrapper.text()).toContain('افزودن نام مستعار جدید')

    wrapper.unmount()
  })

  it('returns to alias management when delete operations fail', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()

    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/2' && method === 'GET') return responseOf(commoditiesState[1])
      if (path === '/api/commodities/2' && method === 'DELETE') return responseOf({ detail: 'کالا وابسته است' }, false, 400)
      if (path === '/api/commodities/aliases/21' && method === 'DELETE') return responseOf({ detail: 'نام مستعار وابسته است' }, false, 400)
      return responseOf(commoditiesState)
    })

    await wrapper.find('.action-btn.danger-soft').trigger('click')
    await flushPromises()
    await wrapper.find('.ds-btn.danger').trigger('click')
    await flushPromises()

    expect(commodityManagerMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/2', { method: 'DELETE' })
    expect(wrapper.text()).toContain('بهار')

    await wrapper.find('.icon-btn.delete').trigger('click')
    await flushPromises()
    await wrapper.find('.ds-btn.danger').trigger('click')
    await flushPromises()

    expect(commodityManagerMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/aliases/21', { method: 'DELETE' })
    expect(wrapper.text()).toContain('بهار جدید')

    wrapper.unmount()
  })
})