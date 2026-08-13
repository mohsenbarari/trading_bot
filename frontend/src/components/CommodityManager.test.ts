import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const commodityManagerSource = readFileSync(
  resolve(process.cwd(), 'src/components/CommodityManager.vue'),
  'utf8',
)

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
  const wrapper = mount(CommodityManager, {
    attachTo: document.body,
    props: {
      apiBaseUrl: '',
      jwtToken: 'jwt-token',
    },
  })
  mountedWrappers.add(wrapper)
  return wrapper
}

const mountedWrappers = new Set<{ unmount: () => void }>()

function disposeWrapper(wrapper: { unmount: () => void }) {
  wrapper.unmount()
  mountedWrappers.delete(wrapper)
}

function bodyConfirmDialog() {
  const dialog = document.body.querySelector<HTMLElement>('.ui-confirm-dialog')
  if (!dialog) throw new Error('Expected the confirmation dialog to be teleported to document.body.')
  return dialog
}

function bodyConfirmButtons() {
  const buttons = bodyConfirmDialog().querySelectorAll<HTMLButtonElement>('button')
  if (buttons.length !== 2) throw new Error('Expected exactly cancel and confirm buttons in the confirmation dialog.')
  return { cancel: buttons[0]!, confirm: buttons[1]! }
}

async function confirmBodyDialog() {
  bodyConfirmButtons().confirm.click()
  await flushPromises()
}

async function cancelBodyDialog() {
  bodyConfirmButtons().cancel.click()
  await flushPromises()
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
        return responseOf(newCommodity, true, 201)
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
        return responseOf(newAlias, true, 201)
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

  afterEach(() => {
    for (const wrapper of mountedWrappers) wrapper.unmount()
    mountedWrappers.clear()
    document.body.replaceChildren()
    document.body.classList.remove('ui-overlay-open')
    delete document.body.dataset.uiOverlayLockCount
  })

  it('keeps Persian typography scoped to the commodity-manager root without changing text metrics', () => {
    const rootStyle = commodityManagerSource.match(/\.commodity-manager\s*\{([^}]*)\}/)?.[1]

    expect(commodityManagerSource).toMatch(/<div class="commodity-manager ds-page-content">/)
    expect(commodityManagerSource).toMatch(
      /<style scoped>[\s\S]*?\.commodity-manager\s*\{[\s\S]*?font-family:\s*Vazirmatn,\s*Tahoma,\s*Arial,\s*sans-serif;[\s\S]*?font-synthesis:\s*none;/,
    )
    expect(commodityManagerSource.match(/font-family:\s*Vazirmatn,\s*Tahoma,\s*Arial,\s*sans-serif;/g) ?? []).toHaveLength(1)
    expect(commodityManagerSource.match(/font-synthesis:\s*none;/g) ?? []).toHaveLength(1)
    expect(rootStyle).toBeDefined()
    expect(rootStyle).not.toMatch(/(?:font-size|font-weight|line-height)\s*:/)
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
    expect(wrapper.get('.commodity-back-control').classes()).toContain('ui-icon-button')

    await wrapper.find('.commodity-back-control').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('افزودن کالا')

    disposeWrapper(wrapper)
  }, 15000)

  it('renders each alias title once through its AppListItem without a duplicate detail text node', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[0]!.trigger('click')
    await flushPromises()

    const aliasRows = wrapper.findAll('.alias-row')
    expect(aliasRows).toHaveLength(2)
    expect(aliasRows.map((row) => row.get('.ui-list-item__copy > strong').text())).toEqual(['امامی', 'سکه جدید'])
    for (const row of aliasRows) {
      const aliasTitle = row.get('.ui-list-item__copy > strong').text()
      expect(row.findAll('.ui-list-item__copy > strong')).toHaveLength(1)
      expect(row.text().split(aliasTitle)).toHaveLength(2)
      expect(row.find('.alias-text').exists()).toBe(false)
    }

    disposeWrapper(wrapper)
  })

  it('adds a commodity with aliases and refreshes the list', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.find('.commodity-action.primary-soft').trigger('click')
    await flushPromises()

    const inputs = wrapper.findAll('input')
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

    disposeWrapper(wrapper)
  })

  it('keeps alias management available for canonical Imam but hides rename and delete actions', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[0]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('کالای پیش‌فرض امام فقط از مسیر نام‌های مستعار قابل مدیریت است')
    expect(wrapper.find('.commodity-action.secondary-soft').exists()).toBe(false)
    expect(wrapper.find('.commodity-action.danger-soft').exists()).toBe(false)
    expect(wrapper.find('.commodity-action.primary-soft').exists()).toBe(true)

    disposeWrapper(wrapper)
  })

  it('edits the commodity name and performs alias add, edit, and delete flows', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()

    const actionButtons = wrapper.findAll('.commodity-action')
    await actionButtons[1]!.trigger('click')
    await flushPromises()

    await wrapper.find('input').setValue('سکه بهار آزادی')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('سکه بهار آزادی')
    expect(wrapper.text()).not.toContain('>بهار<')

    await wrapper.findAll('.commodity-action')[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('input').setValue('بهار - طرح قدیم')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('بهار')
    expect(wrapper.text()).toContain('طرح قدیم')

    expect(wrapper.find('.commodity-icon-control.edit').classes()).toContain('ui-icon-button')
    expect(wrapper.find('.commodity-icon-control.delete').classes()).toContain('ui-icon-button')
    await wrapper.find('.commodity-icon-control.edit').trigger('click')
    await flushPromises()
    await wrapper.find('input').setValue('بهار آزادی')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('بهار آزادی')

    await wrapper.find('.commodity-icon-control.delete').trigger('click')
    await flushPromises()
    await confirmBodyDialog()

    const aliasTitles = wrapper.findAll('.alias-row .ui-list-item__copy > strong').map((node) => node.text())
    expect(aliasTitles).not.toContain('بهار آزادی')
    expect(aliasTitles).toContain('طرح قدیم')

    disposeWrapper(wrapper)
  })

  it('deletes a commodity from the confirmation flow and returns to the list', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.find('.commodity-action.danger-soft').trigger('click')
    await flushPromises()
    expect(bodyConfirmDialog().closest('.ui-dialog-backdrop')?.parentElement).toBe(document.body)
    await confirmBodyDialog()

    expect(wrapper.findAll('.list-item-btn').map((item) => item.text()).join(' ')).not.toContain('بهار')
    expect(wrapper.text()).toContain('کالا «بهار» با موفقیت حذف شد.')
    expect(wrapper.text()).toContain('امام')

    disposeWrapper(wrapper)
  })

  it('renders fetch and manage-alias failures with readable error details', async () => {
    commodityManagerMocks.apiFetchMock
      .mockResolvedValueOnce(responseOf({ detail: 'list failed' }, false, 500))
      .mockResolvedValueOnce(responseOf(commoditiesState))
    const wrapper = await mountCommodityManager()
    await flushPromises()

    expect(wrapper.text()).toContain('دریافت فهرست کالاها ممکن نشد. اطلاعات فعلی حفظ شده است.')
    await wrapper.get('.commodity-list-retry').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.list-item-btn')).toHaveLength(commoditiesState.length)
    disposeWrapper(wrapper)

    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/api/commodities/' && !options?.method) return responseOf(commoditiesState)
      if (path === '/api/commodities/1' && !options?.method) return responseOf({ detail: 'commodity missing' }, false, 404)
      return responseOf({}, true)
    })

    const manageWrapper = await mountCommodityManager()
    await flushPromises()
    await manageWrapper.find('.list-item-btn').trigger('click')
    await flushPromises()

    expect(manageWrapper.text()).toContain('دریافت اطلاعات کالا ممکن نشد. اطلاعات فعلی حفظ شده است.')
    expect(manageWrapper.text()).toContain('افزودن کالا')

    disposeWrapper(manageWrapper)
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

    await wrapper.find('.commodity-action.primary-soft').trigger('click')
    await flushPromises()
    await wrapper.findAll('input')[0]!.setValue('سکه امامی')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const createErrorAlert = wrapper.find('.commodity-feedback--error[role="alert"]')
    expect(createErrorAlert.exists()).toBe(true)
    expect(createErrorAlert.text()).toContain('ثبت اطلاعات انجام نشد')
    expect(createErrorAlert.text()).toContain('افزودن کالا انجام نشد. اطلاعات واردشده حفظ شده است.')
    expect(createErrorAlert.text()).not.toContain('duplicate')
    expect(wrapper.text()).toContain('افزودن کالا')

    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/' && method === 'GET') return responseOf(commoditiesState)
      if (path === '/api/commodities/2' && method === 'GET') return responseOf(commoditiesState[1])
      if (path === '/api/commodities/2' && method === 'PUT') return responseOf({ detail: { name: ['too short'] } }, false, 422)
      return responseOf({ detail: 'unexpected' }, false, 500)
    })

    await wrapper.find('.ui-button--secondary').trigger('click')
    await flushPromises()
    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.commodity-action')[1]!.trigger('click')
    await flushPromises()
    await wrapper.find('input').setValue('x')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const editErrorAlert = wrapper.find('.commodity-feedback--error[role="alert"]')
    expect(editErrorAlert.exists()).toBe(true)
    expect(editErrorAlert.text()).toContain('ویرایش نام کالا انجام نشد. اطلاعات واردشده حفظ شده است.')
    expect(editErrorAlert.text()).not.toContain('too short')
    expect(wrapper.text()).toContain('ویرایش نام کالا')

    disposeWrapper(wrapper)
  })

  it('validates alias input and reports partial alias add failures', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.find('.list-item-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.commodity-action')[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('input').setValue('   ')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('لطفاً حداقل یک نام مستعار وارد کنید.')

    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/1' && method === 'GET') return responseOf(commoditiesState[0])
      if (path === '/api/commodities/1/aliases' && method === 'POST') {
        const payload = JSON.parse(options!.body as string)
        if (payload.alias === 'خراب') return responseOf({ detail: 'تکراری' }, false, 409)
        return responseOf({ id: aliasId++, alias: payload.alias, commodity_id: 1 }, true, 201)
      }
      return responseOf(commoditiesState)
    })

    await wrapper.find('input').setValue('درست - خراب')
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
    const aliasErrorAlert = wrapper.find('.commodity-feedback--error[role="alert"]')
    expect(aliasErrorAlert.exists()).toBe(true)
    expect(aliasErrorAlert.text()).toContain('ثبت نام‌های «خراب» انجام نشد.')
    expect(aliasErrorAlert.text()).not.toContain('تکراری')
    expect(wrapper.text()).toContain('افزودن نام مستعار')

    disposeWrapper(wrapper)
  })

  it('rejects mismatched detail targets and alias creation receipts without changing the selected context', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    const rawWrongDetailName = 'wrong-detail-target-raw'
    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/' && method === 'GET') return responseOf(commoditiesState)
      if (path === '/api/commodities/2' && method === 'GET') {
        return responseOf({
          id: 1,
          name: rawWrongDetailName,
          aliases: [{ id: 11, alias: 'نام نادرست', commodity_id: 1 }],
        })
      }
      return responseOf({ detail: 'unexpected' }, false, 500)
    })

    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('دریافت اطلاعات کالا ممکن نشد. اطلاعات فعلی حفظ شده است.')
    expect(wrapper.text()).toContain('بهار')
    expect(wrapper.text()).not.toContain(rawWrongDetailName)

    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/' && method === 'GET') return responseOf(commoditiesState)
      if (path === '/api/commodities/2' && method === 'GET') return responseOf(commoditiesState[1])
      if (path === '/api/commodities/2/aliases' && method === 'POST') {
        const { alias } = JSON.parse(options!.body as string) as { alias: string }
        if (alias === 'وضعیت ۲۰۰') return responseOf({ id: 31, alias, commodity_id: 2 }, true, 200)
        return responseOf({ id: 32, alias: 'نام پاسخ متفاوت', commodity_id: 2 }, true, 201)
      }
      return responseOf({ detail: 'unexpected' }, false, 500)
    })

    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.commodity-action')[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('input').setValue('وضعیت ۲۰۰ - نام درخواست‌شده')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const alert = wrapper.find('.commodity-feedback--error[role="alert"]')
    expect(alert.text()).toContain('ثبت نام‌های «وضعیت ۲۰۰، نام درخواست‌شده» انجام نشد.')
    expect(wrapper.text()).toContain('افزودن نام مستعار')
    expect(wrapper.text()).not.toContain('نام پاسخ متفاوت')
    expect(wrapper.text()).not.toContain('unexpected')
    expect(commoditiesState[1]!.aliases).toEqual([{ id: 21, alias: 'بهار جدید', commodity_id: 2 }])

    disposeWrapper(wrapper)
  })

  it('keeps delete confirmation safe and selected data intact after failed receipts, then allows an alias retry', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()

    const rawCommodityDetail = 'commodity-delete-server-detail-403'
    const rawAliasDetail = 'alias-delete-unexpected-receipt'
    let aliasDeleteAttempts = 0
    commodityManagerMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/2' && method === 'GET') return responseOf(commoditiesState[1])
      if (path === '/api/commodities/2' && method === 'DELETE') return responseOf({ detail: rawCommodityDetail }, false, 403)
      if (path === '/api/commodities/aliases/21' && method === 'DELETE') {
        aliasDeleteAttempts += 1
        if (aliasDeleteAttempts === 1) return responseOf({ detail: rawAliasDetail }, true, 200)
        commoditiesState[1]!.aliases = []
        return responseOf(null, true, 204)
      }
      return responseOf(commoditiesState)
    })

    await wrapper.find('.commodity-action.danger-soft').trigger('click')
    await flushPromises()
    await confirmBodyDialog()

    expect(commodityManagerMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/2', expect.objectContaining({ method: 'DELETE' }))
    expect(wrapper.text()).toContain('بهار')
    expect(bodyConfirmDialog().textContent).toContain('حذف کالا تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.')
    expect(document.body.textContent).not.toContain(rawCommodityDetail)

    await cancelBodyDialog()
    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()

    await wrapper.find('.commodity-icon-control.delete').trigger('click')
    await flushPromises()
    await confirmBodyDialog()

    expect(commodityManagerMocks.apiFetchMock).toHaveBeenCalledWith('/api/commodities/aliases/21', expect.objectContaining({ method: 'DELETE' }))
    expect(wrapper.text()).toContain('بهار جدید')
    expect(bodyConfirmDialog().textContent).toContain('حذف نام مستعار تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.')
    expect(document.body.textContent).not.toContain(rawAliasDetail)

    await confirmBodyDialog()
    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()
    expect(wrapper.findAll('.alias-row .ui-list-item__copy > strong').map((node) => node.text())).not.toContain('بهار جدید')

    disposeWrapper(wrapper)
  })

  it('uses its body-teleported delete dialog so Escape and cancel never send a DELETE', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()

    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()

    await wrapper.find('.commodity-action.danger-soft').trigger('click')
    await flushPromises()
    expect(bodyConfirmDialog().closest('.ui-dialog-backdrop')?.parentElement).toBe(document.body)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()

    await wrapper.find('.commodity-icon-control.delete').trigger('click')
    await flushPromises()
    await cancelBodyDialog()
    expect(document.body.querySelector('.ui-confirm-dialog')).toBeNull()

    const deleteCalls = commodityManagerMocks.apiFetchMock.mock.calls.filter(([, options]) => (
      (options as RequestInit | undefined)?.method === 'DELETE'
    ))
    expect(deleteCalls).toHaveLength(0)
    expect(wrapper.text()).toContain('بهار')
    expect(wrapper.text()).toContain('بهار جدید')
  })

  it('guards duplicate creates and keeps the optimistic list plus success receipt when authoritative refresh fails', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()
    await wrapper.find('.commodity-action.primary-soft').trigger('click')
    await wrapper.findAll('input')[0]!.setValue('ربع')
    await wrapper.findAll('input')[1]!.setValue('ربع سکه')

    let resolveCreate: ((value: ReturnType<typeof responseOf>) => void) | undefined
    const createResponse = new Promise<ReturnType<typeof responseOf>>((resolve) => { resolveCreate = resolve })
    commodityManagerMocks.apiFetchMock.mockImplementation((path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/' && method === 'POST') return createResponse
      if (path === '/api/commodities/' && method === 'GET') return Promise.resolve(responseOf({ detail: 'refresh failed' }, false, 500))
      return Promise.resolve(responseOf({ detail: 'unexpected' }, false, 500))
    })

    await wrapper.find('form').trigger('submit.prevent')
    await wrapper.find('form').trigger('submit.prevent')
    expect(commodityManagerMocks.apiFetchMock.mock.calls.filter(([path, options]) => (
      path === '/api/commodities/' && (options as RequestInit | undefined)?.method === 'POST'
    ))).toHaveLength(1)

    resolveCreate!(responseOf({
      id: 8,
      name: 'ربع',
      aliases: [{ id: 81, alias: 'ربع سکه', commodity_id: 8 }],
    }, true, 201))
    await flushPromises()

    expect(wrapper.text()).toContain('کالا «ربع» با موفقیت افزوده شد.')
    expect(wrapper.text()).toContain('دریافت فهرست کالاها ممکن نشد. اطلاعات فعلی حفظ شده است.')
    const listText = wrapper.findAll('.list-item-btn').map((item) => item.text()).join(' ')
    expect(listText).toContain('امام')
    expect(listText).toContain('بهار')
    expect(listText).toContain('ربع')
  })

  it('guards duplicate updates and retains updated detail plus its receipt when detail refresh fails', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()
    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.find('.commodity-action.secondary-soft').trigger('click')
    await wrapper.find('input').setValue('بهار تازه')

    let resolveUpdate: ((value: ReturnType<typeof responseOf>) => void) | undefined
    const updateResponse = new Promise<ReturnType<typeof responseOf>>((resolve) => { resolveUpdate = resolve })
    commodityManagerMocks.apiFetchMock.mockImplementation((path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/2' && method === 'PUT') return updateResponse
      if (path === '/api/commodities/2' && method === 'GET') return Promise.resolve(responseOf({ detail: 'refresh failed' }, false, 500))
      return Promise.resolve(responseOf({ detail: 'unexpected' }, false, 500))
    })

    await wrapper.find('form').trigger('submit.prevent')
    await wrapper.find('form').trigger('submit.prevent')
    expect(commodityManagerMocks.apiFetchMock.mock.calls.filter(([path, options]) => (
      path === '/api/commodities/2' && (options as RequestInit | undefined)?.method === 'PUT'
    ))).toHaveLength(1)

    resolveUpdate!(responseOf({ ...commoditiesState[1], name: 'بهار تازه' }))
    await flushPromises()

    expect(wrapper.text()).toContain('نام کالا با موفقیت به «بهار تازه» تغییر یافت.')
    expect(wrapper.text()).toContain('دریافت اطلاعات کالا ممکن نشد. اطلاعات فعلی حفظ شده است.')
    expect(wrapper.text()).toContain('بهار تازه')
    expect(wrapper.text()).toContain('بهار جدید')
  })

  it('aborts and ignores an out-of-order detail refresh when the user returns to the commodity list', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()
    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.find('.commodity-action.secondary-soft').trigger('click')
    await wrapper.find('input').setValue('بهار تازه')

    let resolveDetailRefresh: ((value: ReturnType<typeof responseOf>) => void) | undefined
    let detailRefreshSignal: AbortSignal | null | undefined
    const detailRefresh = new Promise<ReturnType<typeof responseOf>>((resolve) => {
      resolveDetailRefresh = resolve
    })
    commodityManagerMocks.apiFetchMock.mockImplementation((path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/2' && method === 'PUT') {
        return Promise.resolve(responseOf({ ...commoditiesState[1], name: 'بهار تازه' }))
      }
      if (path === '/api/commodities/2' && method === 'GET') {
        detailRefreshSignal = options?.signal
        return detailRefresh
      }
      return Promise.resolve(responseOf({ detail: 'unexpected' }, false, 500))
    })

    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()
    expect(detailRefreshSignal).toBeTruthy()
    expect(wrapper.text()).toContain('بهار تازه')

    await wrapper.find('.commodity-back-control').trigger('click')
    await flushPromises()
    expect(detailRefreshSignal?.aborted).toBe(true)

    resolveDetailRefresh!(responseOf({
      id: 2,
      name: 'جزئیات قدیمی',
      aliases: [{ id: 21, alias: 'نام قدیمی', commodity_id: 2 }],
    }))
    await flushPromises()

    expect(wrapper.text()).toContain('افزودن کالا')
    expect(wrapper.text()).toContain('بهار تازه')
    expect(wrapper.text()).not.toContain('جزئیات قدیمی')
  })

  it('guards duplicate deletes and retains the updated list plus receipt when list refresh fails', async () => {
    const wrapper = await mountCommodityManager()
    await flushPromises()
    await wrapper.findAll('.list-item-btn')[1]!.trigger('click')
    await flushPromises()
    await wrapper.find('.commodity-action.danger-soft').trigger('click')

    let resolveDelete: ((value: ReturnType<typeof responseOf>) => void) | undefined
    const deleteResponse = new Promise<ReturnType<typeof responseOf>>((resolve) => { resolveDelete = resolve })
    commodityManagerMocks.apiFetchMock.mockImplementation((path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/commodities/2' && method === 'DELETE') return deleteResponse
      if (path === '/api/commodities/' && method === 'GET') return Promise.resolve(responseOf({ detail: 'refresh failed' }, false, 500))
      return Promise.resolve(responseOf({ detail: 'unexpected' }, false, 500))
    })

    await flushPromises()
    bodyConfirmButtons().confirm.click()
    bodyConfirmButtons().confirm.click()
    await flushPromises()
    expect(commodityManagerMocks.apiFetchMock.mock.calls.filter(([path, options]) => (
      path === '/api/commodities/2' && (options as RequestInit | undefined)?.method === 'DELETE'
    ))).toHaveLength(1)

    resolveDelete!(responseOf(null, true, 204))
    await flushPromises()

    expect(wrapper.text()).toContain('کالا «بهار» با موفقیت حذف شد.')
    expect(wrapper.text()).toContain('دریافت فهرست کالاها ممکن نشد. اطلاعات فعلی حفظ شده است.')
    const listText = wrapper.findAll('.list-item-btn').map((item) => item.text()).join(' ')
    expect(listText).toContain('امام')
    expect(listText).not.toContain('بهار')
  })
})
