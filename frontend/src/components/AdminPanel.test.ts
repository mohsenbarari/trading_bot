import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

  it('shows the reduced middle-manager actions and emits navigation', async () => {
    adminPanelMocks.isCachedMiddleManagerMock.mockReturnValue(true)
    adminPanelMocks.isCachedSuperAdminMock.mockReturnValue(false)
    const AdminPanel = (await import('./AdminPanel.vue')).default
    const wrapper = mount(AdminPanel)
    await flushPromises()

    const buttons = wrapper.findAll('.admin-action-btn')
    expect(buttons).toHaveLength(2)
    expect(wrapper.text()).toContain('ارسال لینک دعوت')
    expect(wrapper.text()).toContain('مدیریت کاربران')
    expect(wrapper.text()).not.toContain('مدیریت کالاها')

    await buttons[1]!.trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['manage_users'])
  })

  it('shows the full admin action set for super admin', async () => {
    adminPanelMocks.isCachedMiddleManagerMock.mockReturnValue(false)
    adminPanelMocks.isCachedSuperAdminMock.mockReturnValue(true)
    const AdminPanel = (await import('./AdminPanel.vue')).default
    const wrapper = mount(AdminPanel)
    await flushPromises()

    expect(wrapper.findAll('.admin-action-btn')).toHaveLength(5)
    expect(wrapper.findAll('.admin-accordion')).toHaveLength(3)
    expect(wrapper.text()).toContain('مدیریت کالاها')
    expect(wrapper.text()).toContain('پیام‌های مدیریت')
    expect(wrapper.text()).toContain('تنظیمات سیستم')
  })

  it('hides settings action for non-super-admin users', async () => {
    adminPanelMocks.isCachedMiddleManagerMock.mockReturnValue(false)
    adminPanelMocks.isCachedSuperAdminMock.mockReturnValue(false)
    const AdminPanel = (await import('./AdminPanel.vue')).default
    const wrapper = mount(AdminPanel)
    await flushPromises()

    expect(wrapper.findAll('.admin-action-btn')).toHaveLength(3)
    expect(wrapper.text()).not.toContain('تنظیمات سیستم')
  })
})
