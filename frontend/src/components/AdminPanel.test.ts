import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const adminPanelMocks = vi.hoisted(() => ({
  isCachedMiddleManagerMock: vi.fn(),
}))

vi.mock('../utils/adminAccess', () => ({
  isCachedMiddleManager: adminPanelMocks.isCachedMiddleManagerMock,
}))

describe('AdminPanel.vue', () => {
  beforeEach(() => {
    vi.resetModules()
    adminPanelMocks.isCachedMiddleManagerMock.mockReset()
  })

  it('shows the reduced middle-manager actions and emits navigation', async () => {
    adminPanelMocks.isCachedMiddleManagerMock.mockReturnValue(true)
    const AdminPanel = (await import('./AdminPanel.vue')).default
    const wrapper = mount(AdminPanel)
    await flushPromises()

    const buttons = wrapper.findAll('button')
    expect(buttons).toHaveLength(2)
    expect(wrapper.text()).toContain('ارسال لینک دعوت')
    expect(wrapper.text()).toContain('مدیریت کاربران')
    expect(wrapper.text()).not.toContain('مدیریت کالاها')

    await buttons[1]!.trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['manage_users'])
  })

  it('shows the full admin action set for non-middle-manager admins', async () => {
    adminPanelMocks.isCachedMiddleManagerMock.mockReturnValue(false)
    const AdminPanel = (await import('./AdminPanel.vue')).default
    const wrapper = mount(AdminPanel)
    await flushPromises()

    expect(wrapper.findAll('button')).toHaveLength(4)
    expect(wrapper.text()).toContain('مدیریت کالاها')
    expect(wrapper.text()).toContain('تنظیمات سیستم')
  })
})