import { expect, test, type Page } from '@playwright/test'
import {
  attachDiagnostics,
  createDiagnostics,
  expectCleanDiagnostics,
  installFailClosedApi,
} from './helpers/nativeAppV2Api'

type MessengerMode = 'empty' | 'direct'

const CURRENT_USER = {
  id: 7,
  account_name: 'native_ui_owner',
  full_name: 'کاربر بومی',
  role: 'عضو',
  account_status: 'active',
  is_accountant: false,
  is_customer: false,
  customer_tier: null,
  has_bot_access: true,
}

const DIRECT_USER = {
  id: 42,
  account_name: 'native_ui_peer',
  full_name: 'همکار نمونه با نام بلند فارسی',
  mobile_number: '09120000000',
  address: 'تهران، نشانی نمونه برای آزمون شکست متن در پروفایل',
  account_status: 'active',
  is_accountant: false,
  is_customer: false,
  last_seen_at: '2026-08-20T18:30:00.000Z',
}

function createJwt() {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const body = Buffer.from(JSON.stringify({
    sub: String(CURRENT_USER.id),
    exp: Math.floor(Date.now() / 1000) + 3600,
    session_id: 'native-app-messenger-visual',
  })).toString('base64url')
  return `${header}.${body}.native-ui`
}

async function primeMessengerSession(page: Page) {
  const token = createJwt()
  await page.addInitScript(({ accessToken, userSummary }) => {
    localStorage.setItem('auth_token', accessToken)
    localStorage.setItem('refresh_token', accessToken)
    localStorage.setItem('current_user_summary', JSON.stringify(userSummary))

    class QuietWebSocket extends EventTarget {
      static OPEN = 1
      static CLOSED = 3
      readyState = QuietWebSocket.OPEN
      url: string

      constructor(url: string | URL) {
        super()
        this.url = String(url)
        queueMicrotask(() => this.dispatchEvent(new Event('open')))
      }

      send() {}
      close() {
        this.readyState = QuietWebSocket.CLOSED
        this.dispatchEvent(new CloseEvent('close'))
      }
    }

    Object.defineProperty(window, 'WebSocket', {
      configurable: true,
      value: QuietWebSocket,
    })
  }, { accessToken: token, userSummary: CURRENT_USER })
}

async function installMessengerHarness(page: Page, mode: MessengerMode) {
  const diagnostics = createDiagnostics()
  await attachDiagnostics(page, diagnostics)
  await installFailClosedApi(page, diagnostics, {
    viewer: CURRENT_USER,
    extraKnown: (method, pathname) => {
      if (pathname === '/api/auth/me' && method === 'GET') return { status: 200, body: CURRENT_USER }
      if (pathname === `/api/users-public/${DIRECT_USER.id}` && method === 'GET') {
        return { status: 200, body: DIRECT_USER }
      }
      if ((pathname === '/api/chat/conversations' || pathname === '/api/chat/conversations/') && method === 'GET') {
        return {
          status: 200,
          body: mode === 'empty' ? [] : [{
            id: 81,
            other_user_id: DIRECT_USER.id,
            other_user_name: DIRECT_USER.full_name,
            other_user_is_deleted: false,
            last_message_content: 'پیام نمونه برای سنجش خوانایی و تراکم مناسب ردیف گفتگو',
            last_message_type: 'text',
            last_message_at: '2026-08-20T18:31:00.000Z',
            unread_count: 1,
            unread_mention_count: 0,
            other_user_last_seen_at: DIRECT_USER.last_seen_at,
            room_kind: 'direct',
            can_send: true,
            is_muted: false,
            is_pinned: true,
            pinned_at: '2026-08-20T18:31:00.000Z',
            pin_order: 1,
          }],
        }
      }
      if (pathname === `/api/chat/messages/${DIRECT_USER.id}` && method === 'GET') {
        return {
          status: 200,
          body: [{
            id: 901,
            sender_id: DIRECT_USER.id,
            receiver_id: CURRENT_USER.id,
            content: 'این یک پیام نمونهٔ فارسی برای بررسی خوانایی، شکست متن و فاصله‌گذاری حباب است.',
            message_type: 'text',
            is_read: false,
            is_deleted: false,
            reactions: [{ emoji: '👍', user_id: CURRENT_USER.id }],
            created_at: '2026-08-20T18:31:00.000Z',
          }],
        }
      }
      if (pathname === `/api/chat/read/${DIRECT_USER.id}` && method === 'POST') {
        return { status: 200, body: { ok: true } }
      }
      if (pathname === `/api/chat/direct/${DIRECT_USER.id}/pinned-message` && method === 'GET') {
        return { status: 200, body: { message: null } }
      }
      if (pathname === '/api/chat/poll' && method === 'GET') {
        return {
          status: 200,
          body: {
            conversations_with_unread: [],
            muted_conversation_ids: [],
            unread_chats_count: mode === 'direct' ? 1 : 0,
            total_unread_mentions: 0,
          },
        }
      }
      return null
    },
    extraAllowedMutation: (pathname, method) => (
      method === 'POST' && pathname === `/api/chat/read/${DIRECT_USER.id}`
    ),
  })
  return diagnostics
}

async function expectNativeMessengerIntegrity(page: Page, label: string) {
  const audit = await page.evaluate(() => {
    const isVisible = (element: HTMLElement) => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0
        && rect.width > 0 && rect.height > 0
    }
    const nativeName = (element: HTMLElement) => {
      const labelledBy = (element.getAttribute('aria-labelledby') || '')
        .split(/\s+/)
        .filter(Boolean)
        .map((id) => document.getElementById(id)?.textContent || '')
        .join(' ')
      const labels = 'labels' in element
        ? Array.from((element as HTMLInputElement).labels || []).map((item) => item.textContent || '').join(' ')
        : ''
      return [element.getAttribute('aria-label'), labelledBy, labels, element.getAttribute('title'), element.textContent]
        .join(' ')
        .trim()
    }
    const unnamed = Array.from(document.querySelectorAll<HTMLElement>('button, a[href], input, textarea, select, [role="button"]'))
      .filter(isVisible)
      .filter((element) => element.getAttribute('aria-hidden') !== 'true' && !nativeName(element))
      .map((element) => `${element.tagName}.${element.className}`)
    const nested = Array.from(document.querySelectorAll<HTMLElement>(
      'button button, button a[href], a[href] button, [role="button"] button, [role="button"] a[href]',
    )).filter(isVisible).map((element) => `${element.tagName}.${element.className}`)
    const targets = Array.from(document.querySelectorAll<HTMLElement>(
      '.chat-header button, .fab-new-chat, .input-area button, .search-bottom-bar button',
    )).filter(isVisible).map((element) => {
      const rect = element.getBoundingClientRect()
      return { className: element.className, width: rect.width, height: rect.height }
    })
    const root = document.querySelector<HTMLElement>('.messenger-page')
    return {
      docWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      unnamed,
      nested,
      undersized: targets.filter((item) => item.width < 48 || item.height < 48),
      fontFamily: root ? getComputedStyle(root).fontFamily : '',
      direction: document.documentElement.dir,
    }
  })

  expect(audit.docWidth, `${label}: document overflow`).toBeLessThanOrEqual(audit.viewportWidth + 1)
  expect(audit.unnamed, `${label}: unnamed controls`).toEqual([])
  expect(audit.nested, `${label}: nested controls`).toEqual([])
  expect(audit.undersized, `${label}: native chrome touch targets`).toEqual([])
  expect(audit.fontFamily).toContain('Vazirmatn')
  expect(audit.direction).toBe('rtl')
}

for (const viewport of [
  { width: 390, height: 844, label: 'mobile' },
  { width: 1440, height: 900, label: 'desktop' },
]) {
  for (const mode of ['empty', 'direct'] as const) {
    test(`${viewport.label}:${mode} keeps messenger native, named, and contained`, async ({ page, browserName }) => {
      const compatibilityDiagnostics: string[] = []
      page.on('console', (message) => {
        if (message.text() === 'Viewport argument key "interactive-widget" not recognized and ignored.') {
          compatibilityDiagnostics.push(message.text())
        }
      })
      await primeMessengerSession(page)
      const diagnostics = await installMessengerHarness(page, mode)
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.setViewportSize(viewport)
      const query = mode === 'direct'
        ? `?user_id=${DIRECT_USER.id}&user_name=${encodeURIComponent(DIRECT_USER.full_name)}`
        : ''
      await page.goto(`/chat${query}`, { waitUntil: 'domcontentloaded' })
      await expect(page.locator('.messenger-page')).toBeVisible({ timeout: 15_000 })
      if (mode === 'empty') {
        await expect(page.getByText('گفتگویی وجود ندارد')).toBeVisible({ timeout: 15_000 })
      } else {
        await expect(page.locator('.chat-header .header-name:visible')).toContainText(DIRECT_USER.full_name, { timeout: 15_000 })
        await expect(page.getByText('این یک پیام نمونهٔ فارسی', { exact: false })).toBeVisible({ timeout: 15_000 })
        const menuTrigger = page.getByRole('button', { name: 'گزینه‌های گفتگو', exact: true })
        await menuTrigger.focus()
        await menuTrigger.press('Enter')
        await expect(page.locator('#chat-header-menu:visible')).toBeVisible()
        await page.keyboard.press('Escape')
        await expect(menuTrigger).toBeFocused()
      }
      await expectNativeMessengerIntegrity(page, `${viewport.label}:${mode}`)
      expectCleanDiagnostics(diagnostics, `${viewport.label}:${mode}`)
      expect(compatibilityDiagnostics).toHaveLength(browserName === 'webkit' ? 1 : 0)
    })
  }
}

test('mobile:direct remains usable at 200% page zoom', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'CDP page-scale verification is Chromium-specific.')
  await primeMessengerSession(page)
  const diagnostics = await installMessengerHarness(page, 'direct')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/chat?user_id=${DIRECT_USER.id}&user_name=${encodeURIComponent(DIRECT_USER.full_name)}`, {
    waitUntil: 'domcontentloaded',
  })
  await expect(page.getByText('این یک پیام نمونهٔ فارسی', { exact: false })).toBeVisible({ timeout: 15_000 })

  const session = await page.context().newCDPSession(page)
  await session.send('Emulation.setPageScaleFactor', { pageScaleFactor: 2 })
  await page.waitForTimeout(120)

  const zoom = await page.evaluate(() => ({
    scale: window.visualViewport?.scale ?? 1,
    width: window.visualViewport?.width ?? window.innerWidth,
    height: window.visualViewport?.height ?? window.innerHeight,
  }))
  expect(zoom.scale).toBeCloseTo(2, 1)
  expect(zoom.width).toBeGreaterThanOrEqual(194)
  expect(zoom.height).toBeGreaterThanOrEqual(421)
  await expectNativeMessengerIntegrity(page, 'mobile:direct:zoom-200')
  expectCleanDiagnostics(diagnostics, 'mobile:direct:zoom-200')
})
