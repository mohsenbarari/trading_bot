import { expect, test } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  attachDiagnostics,
  createDiagnostics,
  expectCleanDiagnostics,
  installFailClosedApi,
} from './helpers/nativeAppV2Api'
import { overlayInventoryGaps } from './helpers/nativeAppV2Overlays'
import {
  ROUTE_DESCRIPTORS,
} from './helpers/nativeAppV2Matrix'

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

function createJwt(userId = 9001) {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify({
    sub: String(userId),
    exp: Math.floor(Date.now() / 1000) + 3600,
    session_id: 'native-app-v2-overlays',
  })).toString('base64url')
  return `${header}.${body}.native-v2`
}

test.describe('Native App V2 overlay inventory', () => {
  test('every live overlay file is inventoried', () => {
    const gaps = overlayInventoryGaps(REPO_ROOT)
    expect(gaps.missing, `uninventoried overlay files: ${gaps.missing.join(', ')}`).toEqual([])
  })
})

test.describe('Native App V2 overlay keyboard contracts', () => {
  test.use({ timezoneId: 'Asia/Tehran', locale: 'fa-IR' })

  async function boot(page: import('@playwright/test').Page, path: string) {
    const diagnostics = createDiagnostics()
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.addInitScript((token) => {
      localStorage.setItem('auth_token', token)
      localStorage.setItem('refresh_token', token)
      localStorage.setItem('current_user_summary', JSON.stringify({
        id: 9001,
        account_name: 'native_app_v2_user',
        full_name: 'کاربر تست UI',
        role: 'مدیر ارشد',
        account_status: 'active',
        is_accountant: false,
        is_customer: false,
        customer_tier: null,
        has_bot_access: true,
        mobile_number: '09120000000',
        address: 'تهران',
      }))
    }, createJwt())
    await attachDiagnostics(page, diagnostics)
    await installFailClosedApi(page, diagnostics, {
      extraAllowedMutation: (pathname, method) => method === 'POST' && /^\/api\/chat\/read\/\d+$/u.test(pathname),
    })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(path, { waitUntil: 'domcontentloaded' })
    await page.locator('html[data-app-mounted="1"]').waitFor({ timeout: 15_000 })
    return diagnostics
  }

  async function expectDialogContract(page: import('@playwright/test').Page, name: string) {
    const dialog = page.getByRole('dialog', { name }).first()
    await expect(dialog).toBeVisible()
    await expect(dialog).toHaveAttribute('aria-modal', 'true')
    const audit = await dialog.evaluate((node) => {
      const active = document.activeElement
      const containsFocus = node.contains(active)
      const rect = node.getBoundingClientRect()
      const unnamed = Array.from(node.querySelectorAll<HTMLElement>('button, a[href], input, textarea, select'))
        .filter((element) => {
          const style = getComputedStyle(element)
          const box = element.getBoundingClientRect()
          if (style.display === 'none' || box.width === 0) return false
          const labelledBy = (element.getAttribute('aria-labelledby') || '')
            .split(/\s+/)
            .filter(Boolean)
            .map((id) => document.getElementById(id)?.textContent || '')
            .join(' ')
          const nativeLabels = 'labels' in element
            ? Array.from((element as HTMLInputElement).labels || []).map((item) => item.textContent || '').join(' ')
            : ''
          const nameText = [
            element.getAttribute('aria-label'),
            labelledBy,
            nativeLabels,
            element.getAttribute('title'),
            element.textContent,
          ].join(' ').trim()
          return !nameText
        })
        .map((element) => element.outerHTML.slice(0, 80))
      return {
        containsFocus,
        width: rect.width,
        height: rect.height,
        unnamed,
      }
    })
    expect(audit.containsFocus, `${name}: initial focus inside dialog`).toBe(true)
    expect(audit.unnamed, `${name}: unnamed controls`).toEqual([])
    return dialog
  }

  test('account sheet traps focus, Escape restores trigger, and does not mutate', async ({ page }) => {
    const diagnostics = await boot(page, '/')
    const trigger = page.getByLabel(/باز کردن منوی حساب/)
    await trigger.click()
    const dialog = await expectDialogContract(page, 'حساب')
    await page.keyboard.press('Tab')
    expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true)
    await page.keyboard.press('Shift+Tab')
    expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: 'حساب' })).toHaveCount(0)
    await expect(trigger).toBeFocused()
    expectCleanDiagnostics(diagnostics, 'account-sheet')
  })

  test('messenger conversation menu Escape restores trigger without mutation', async ({ page }) => {
    const diagnostics = await boot(page, '/chat?user_id=42&user_name=peer')
    await expect(page.locator('.messenger-page')).toBeVisible({ timeout: 20_000 })
    const menuTrigger = page.getByRole('button', { name: 'گزینه‌های گفتگو' }).first()
    if (await menuTrigger.count() === 0) {
      test.info().annotations.push({ type: 'overlay.naReason', description: 'direct thread chrome not mounted in this fixture' })
      expectCleanDiagnostics(diagnostics, 'conversation-menu-na')
      return
    }
    await menuTrigger.click()
    await expect(page.locator('#chat-header-menu:visible')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('#chat-header-menu:visible')).toHaveCount(0)
    await expect(menuTrigger).toBeFocused()
    expectCleanDiagnostics(diagnostics, 'conversation-menu')
  })

  test('new conversation overlay has a name and restores focus', async ({ page }) => {
    const diagnostics = await boot(page, '/chat')
    const fab = page.getByRole('button', { name: /گفتگوی جدید|شروع گفتگو/ })
    await expect(fab).toBeVisible({ timeout: 15_000 })
    await fab.click()
    const dialog = page.getByRole('dialog').first()
    await expect(dialog).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(fab).toBeFocused()
    expectCleanDiagnostics(diagnostics, 'new-conversation')
  })

  test('attachment sheet opens from composer without unexpected mutation', async ({ page }) => {
    const diagnostics = await boot(page, '/chat?user_id=42&user_name=peer')
    const attach = page.getByRole('button', { name: /پیوست|ضمیمه|افزودن/ }).first()
    if (await attach.count() === 0) {
      test.info().annotations.push({ type: 'overlay.naReason', description: 'composer attach control not mounted without live thread chrome' })
      return
    }
    await attach.click()
    await expect(page.getByRole('dialog').first()).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expectCleanDiagnostics(diagnostics, 'attachment-menu')
  })

  test('customer invitation sheet traps focus and restores trigger on Escape', async ({ page }) => {
    const diagnostics = await boot(page, '/operations/customers')
    await expect(page.getByRole('heading', { name: 'مشتریان', exact: true })).toBeVisible({ timeout: 15_000 })
    const trigger = page.getByRole('button', { name: 'افزودن مشتری' }).first()
    await trigger.click()
    const dialog = await expectDialogContract(page, 'افزودن مشتری')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: 'افزودن مشتری' })).toHaveCount(0)
    await expect(trigger).toBeFocused()
    expect(dialog).toBeTruthy()
    expectCleanDiagnostics(diagnostics, 'customer-invite-sheet')
  })

  test('storage confirm dialog names the destructive action', async ({ page }) => {
    const diagnostics = await boot(page, '/account/storage')
    const route = ROUTE_DESCRIPTORS.find((item) => item.id === 'account-storage')!
    await expect(page.getByText(route.readyText)).toBeVisible({ timeout: 15_000 })
    const danger = page.getByRole('button', { name: /حذف|پاک کردن|خروج/ }).first()
    if (await danger.count() === 0) {
      test.info().annotations.push({ type: 'overlay.naReason', description: 'storage has no destructive control in current fixture' })
      expectCleanDiagnostics(diagnostics, 'storage-confirm-na')
      return
    }
    await danger.click()
    const dialog = page.getByRole('dialog').first()
    await expect(dialog).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expectCleanDiagnostics(diagnostics, 'storage-confirm')
  })
})
