import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const adminPanelSource = readFileSync(
  resolve(process.cwd(), 'src/components/AdminPanel.vue'),
  'utf8',
)

const adminPanelMocks = vi.hoisted(() => ({
  isCachedMiddleManagerMock: vi.fn(),
  isCachedSuperAdminMock: vi.fn(),
}))

vi.mock('../utils/adminAccess', () => ({
  isCachedMiddleManager: adminPanelMocks.isCachedMiddleManagerMock,
  isCachedSuperAdmin: adminPanelMocks.isCachedSuperAdminMock,
}))

describe('AdminPanel.vue', () => {
  beforeEach(() => {
    vi.resetModules()
    adminPanelMocks.isCachedMiddleManagerMock.mockReset()
    adminPanelMocks.isCachedSuperAdminMock.mockReset()
  })

  it('keeps Persian typography local to the admin-panel root and its action-card hierarchy', () => {
    expect(adminPanelSource).toMatch(
      /\.admin-panel-container\s*\{[\s\S]*?min-width:\s*0;[\s\S]*?font-family:\s*Vazirmatn,\s*Tahoma,\s*Arial,\s*sans-serif;[\s\S]*?font-synthesis:\s*none;/,
    )
    expect(adminPanelSource).toMatch(
      /<nav class="admin-panel-container" aria-label="ابزارهای مدیریت">\s*<AppInsetGroup>\s*<ul class="admin-action-list">\s*<li v-for="action in actions" :key="action.key" class="admin-action-list__item">\s*<AppListItem[\s\S]*?class="admin-panel-action hub-action"/,
    )
  })

  it('shows a flat, content-minimal action list for middle managers and emits the existing navigation key', async () => {
    adminPanelMocks.isCachedMiddleManagerMock.mockReturnValue(true)
    adminPanelMocks.isCachedSuperAdminMock.mockReturnValue(false)
    const AdminPanel = (await import('./AdminPanel.vue')).default
    const wrapper = mount(AdminPanel)
    await flushPromises()

    const buttons = wrapper.findAll('.admin-panel-action')
    expect(buttons).toHaveLength(2)
    expect(wrapper.get('.admin-panel-container').element.tagName).toBe('NAV')
    expect(wrapper.get('.admin-panel-container').attributes('aria-label')).toBe('ابزارهای مدیریت')
    expect(wrapper.findAll('.admin-action-list__item')).toHaveLength(2)
    expect(
      buttons.every(
        (button) => button.element.tagName === 'BUTTON' && button.attributes('type') === 'button',
      ),
    ).toBe(true)
    expect(wrapper.text()).toContain('ارسال لینک دعوت')
    expect(wrapper.text()).toContain('مدیریت کاربران')
    expect(wrapper.text()).not.toContain('مدیریت کالاها')
    expect(wrapper.find('.admin-intro').exists()).toBe(false)
    expect(wrapper.find('.admin-accordion').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('دسترسی مدیر میانی')
    expect(wrapper.text()).not.toContain('دسته')
    expect(wrapper.text()).not.toContain('ابزار')

    await buttons[1]!.trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['manage_users'])
  })

  it('keeps the full super-admin action keys and navigation payloads in one flat list', async () => {
    adminPanelMocks.isCachedMiddleManagerMock.mockReturnValue(false)
    adminPanelMocks.isCachedSuperAdminMock.mockReturnValue(true)
    const AdminPanel = (await import('./AdminPanel.vue')).default
    const wrapper = mount(AdminPanel)
    await flushPromises()

    const buttons = wrapper.findAll('.admin-panel-action')
    expect(buttons).toHaveLength(6)
    expect(wrapper.findAll('.admin-action-list__item')).toHaveLength(6)
    expect(wrapper.find('.admin-accordion').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('دسترسی کامل مدیریتی')
    expect(wrapper.text()).not.toContain('دسته')
    expect(wrapper.text()).not.toContain('ابزار')
    expect(wrapper.text()).toContain('مدیریت کالاها')
    expect(wrapper.text()).toContain('ساخت کانال')
    expect(wrapper.text()).toContain('پیام‌های مدیریت')
    expect(wrapper.text()).toContain('تنظیمات سیستم')

    for (const button of buttons) {
      await button.trigger('click')
    }
    expect(wrapper.emitted('navigate')).toEqual([
      ['create_invitation'],
      ['manage_users'],
      ['manage_commodities'],
      ['create_channel'],
      ['admin_messages'],
      ['settings'],
    ])
  })

  it('hides settings action for non-super-admin users', async () => {
    adminPanelMocks.isCachedMiddleManagerMock.mockReturnValue(false)
    adminPanelMocks.isCachedSuperAdminMock.mockReturnValue(false)
    const AdminPanel = (await import('./AdminPanel.vue')).default
    const wrapper = mount(AdminPanel)
    await flushPromises()

    expect(wrapper.findAll('.admin-panel-action')).toHaveLength(3)
    expect(wrapper.find('.admin-accordion').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('تنظیمات سیستم')
    expect(wrapper.text()).not.toContain('ساخت کانال')
    expect(wrapper.text()).not.toContain('پیام‌های مدیریت')
  })
})
