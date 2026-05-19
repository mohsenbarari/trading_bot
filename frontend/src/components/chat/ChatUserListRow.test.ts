import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ChatUserListRow from './ChatUserListRow.vue'

vi.mock('../../utils/chatFiles', () => ({
  buildChatFileUrl: vi.fn((fileId: string | null, apiBaseUrl = '') => fileId ? `${apiBaseUrl}/api/chat/files/${fileId}` : ''),
  getAvatarInitial: vi.fn((name: string) => name[0] ?? '?'),
}))

describe('ChatUserListRow.vue', () => {
  it('prevents disabled button clicks and emits clicks once re-enabled', async () => {
    const wrapper = mount(ChatUserListRow, {
      props: {
        tag: 'div',
        name: 'علی',
        disabled: true,
      },
    })

    await wrapper.get('.chat-user-row').trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()

    await wrapper.setProps({ tag: 'button', disabled: false })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('renders avatar, badges, subtitle, trailing, and action slots for interactive rows', () => {
    const wrapper = mount(ChatUserListRow, {
      props: {
        tag: 'button',
        name: 'کاربر گروه',
        subtitle: 'مدیر اتاق',
        subtitleDir: 'rtl',
        avatarFileId: 'avatar-1',
        apiBaseUrl: '/api',
        interactive: true,
        selected: true,
        badges: [{ label: 'admin', tone: 'admin' }],
      },
      slots: {
        trailing: '<span class="tail">tail</span>',
        actions: '<button class="action">remove</button>',
      },
    })

    expect(wrapper.classes()).toContain('is-interactive')
    expect(wrapper.classes()).toContain('is-selected')
    expect(wrapper.classes()).toContain('has-actions')
    expect(wrapper.get('img').attributes('src')).toBe('/api/api/chat/files/avatar-1')
    expect(wrapper.text()).toContain('admin')
    expect(wrapper.get('.chat-user-row__subtitle').attributes('dir')).toBe('rtl')
    expect(wrapper.find('.tail').exists()).toBe(true)
    expect(wrapper.find('.action').exists()).toBe(true)
  })
})