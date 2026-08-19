import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import WorkspaceDetailHeader from './WorkspaceDetailHeader.vue'
import WorkspaceFormActions from './WorkspaceFormActions.vue'

const formActionsSource = readFileSync(
  resolve(process.cwd(), 'src/components/workspace/WorkspaceFormActions.vue'),
  'utf8',
)
const customerSource = readFileSync(
  resolve(process.cwd(), 'src/views/CustomerWorkspaceView.vue'),
  'utf8',
)
const accountantSource = readFileSync(
  resolve(process.cwd(), 'src/views/AccountantWorkspaceView.vue'),
  'utf8',
)

describe('workspace presentation', () => {
  it('keeps form actions in document flow so a soft keyboard cannot cover cancel or submit', () => {
    expect(formActionsSource).toMatch(/position:\s*static/)
    expect(formActionsSource).not.toMatch(/position:\s*fixed/)
    expect(formActionsSource).toMatch(/safe-area-inset-bottom/)
    expect(customerSource).toMatch(/<WorkspaceFormActions/)
    expect(accountantSource).toMatch(/<WorkspaceFormActions/)
    expect(customerSource).toMatch(/<WorkspaceDetailHeader/)
    expect(accountantSource).toMatch(/<WorkspaceDetailHeader/)
  })

  it('renders a shared detail header and keyboard-safe actions', async () => {
    const header = mount(WorkspaceDetailHeader, {
      props: {
        title: 'همکار نمونه',
        description: 'رابطه فعال',
        headerClass: 'customer-detail-header',
      },
      slots: {
        default: '<span class="status-slot">فعال</span>',
      },
    })
    const actions = mount(WorkspaceFormActions, {
      props: { actionClass: 'customer-inline-actions' },
      slots: { default: '<button type="button">انصراف</button><button type="button">ثبت</button>' },
    })

    expect(header.classes()).toContain('customer-detail-header')
    expect(header.get('h2').text()).toBe('همکار نمونه')
    expect(header.get('.status-slot').text()).toBe('فعال')
    expect(actions.classes()).toContain('customer-inline-actions')
    expect(actions.get('[data-test="workspace-form-actions"]').text()).toContain('انصراف')
  })
})
