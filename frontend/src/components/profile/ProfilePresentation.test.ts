import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { MessageCircle } from 'lucide-vue-next'
import ProfileActions from './ProfileActions.vue'
import ProfileIdentityHeader from './ProfileIdentityHeader.vue'
import ProfilePageShell from './ProfilePageShell.vue'
import ProfilePresence from './ProfilePresence.vue'
import ProfileSummary from './ProfileSummary.vue'

describe('profile presentation primitives', () => {
  it('renders self, public, and admin identity headers with the same chrome', async () => {
    const selfHeader = mount(ProfileIdentityHeader, {
      props: {
        displayName: 'کاربر نمونه با نام خیلی طولانی برای شکست خط',
        avatarInitial: 'ک',
        editable: true,
        showPresence: true,
        presenceStatus: 'آنلاین',
        online: true,
      },
    })
    const publicHeader = mount(ProfileIdentityHeader, {
      props: {
        displayName: 'مهمان',
        avatarInitial: 'م',
        editable: false,
      },
    })
    const adminHeader = mount(ProfileIdentityHeader, {
      props: {
        displayName: 'حساب نمونه',
        avatarInitial: 'ح',
        backLabel: 'بازگشت به لیست کاربران',
      },
    })

    expect(selfHeader.get('[data-test="profile-avatar-trigger"]').attributes('aria-label')).toBe('افزودن آواتار')
    expect(selfHeader.get('[data-test="profile-presence"]').text()).toBe('آنلاین')
    expect(publicHeader.get('[data-test="profile-avatar-readonly"]').text()).toContain('م')
    expect(publicHeader.find('[data-test="profile-avatar-trigger"]').exists()).toBe(false)
    expect(adminHeader.get('.header-title').text()).toBe('حساب نمونه')
    expect(selfHeader.find('h1.profile-identity-title').exists()).toBe(true)
    expect(adminHeader.get('.profile-nav-back').attributes('aria-label')).toBe('بازگشت به لیست کاربران')

    const loadingHeader = mount(ProfileIdentityHeader, {
      props: {
        displayName: '',
        loading: true,
      },
    })
    expect(loadingHeader.get('h1.profile-identity-title').find('div').exists()).toBe(false)
    expect(loadingHeader.find('h1.profile-identity-title span.skeleton-box').exists()).toBe(true)

    const nestedAdminTitle = mount(ProfileIdentityHeader, {
      props: {
        displayName: 'حساب تو در تو',
        titleTag: 'p',
      },
    })
    expect(nestedAdminTitle.find('h1').exists()).toBe(false)
    expect(nestedAdminTitle.get('p.profile-identity-title').text()).toBe('حساب تو در تو')

    await selfHeader.get('[data-test="profile-avatar-trigger"]').trigger('click')
    expect(selfHeader.emitted('pick-avatar')).toHaveLength(1)
    await adminHeader.get('.profile-nav-back').trigger('click')
    expect(adminHeader.emitted('back')).toHaveLength(1)
  })

  it('keeps presence, summary, and actions permission-agnostic', async () => {
    const presence = mount(ProfilePresence, {
      props: { status: 'آخرین بازدید دیروز', online: false, own: true },
    })
    const summary = mount(ProfileSummary, {
      props: {
        stats: [{ key: 'member-since', label: 'عضویت', value: '۱۴۰۴/۰۱/۰۱' }],
        customerContext: {
          managementName: 'مشتری نمونه',
          ownerAccountName: 'مالک نمونه',
          showTier: false,
        },
      },
    })
    const actions = mount(ProfileActions, {
      props: {
        title: 'اقدام‌های عمومی',
        actions: [
          {
            key: 'message',
            label: 'ارسال پیام',
            tone: 'info',
            icon: MessageCircle,
          },
        ],
      },
    })

    expect(presence.classes()).toContain('profile-presence-status--own')
    expect(summary.get('[aria-label="خلاصه وضعیت پروفایل"]').text()).toContain('عضویت')
    expect(summary.text()).toContain('پروفایل مشتری')
    expect(summary.text()).not.toContain('سطح')
    await actions.get('.profile-action-card').trigger('click')
    expect(actions.emitted('select')?.[0]?.[0]).toMatchObject({ key: 'message' })
  })

  it('keeps a stable loading and retry root', async () => {
    const loading = mount(ProfilePageShell, {
      props: { status: 'loading', loadingLabel: 'در حال دریافت پروفایل' },
    })
    expect(loading.get('[data-test="profile-page-shell"]').attributes('data-status')).toBe('loading')
    expect(loading.text()).toContain('در حال دریافت پروفایل')

    const error = mount(ProfilePageShell, {
      props: {
        status: 'error',
        errorMessage: 'دریافت پروفایل ممکن نشد.',
      },
    })
    await error.get('.retry-btn').trigger('click')
    expect(error.emitted('retry')).toHaveLength(1)
  })
})
