import { expect, type Page } from '@playwright/test'
import type { RouteDescriptor } from './nativeAppV2Matrix'

type ContractProbe = {
  mainCount: number
  h1Count: number
  h1Text: string
  unnamed: string[]
  nested: string[]
  undersized: string[]
  overflow: string[]
  scrollerCount: number
  documentScrolls: boolean
  ctaMissing: boolean
  ctaAboveNav: boolean
  ctaHits: boolean
  reducedMotion: boolean
  longMotion: string[]
  accidental: string[]
}

export async function collectRouteContract(page: Page, route: RouteDescriptor): Promise<ContractProbe> {
  return page.evaluate((descriptor) => {
    const isLive = (element: Element) => {
      if (!(element instanceof HTMLElement)) return false
      if (element.closest('[hidden], [inert], [aria-hidden="true"]')) return false
      const style = window.getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number(style.opacity || '1') > 0
        && rect.width > 0
        && rect.height > 0
    }
    const isAccessibleHeading = (element: Element) => {
      if (!(element instanceof HTMLElement)) return false
      if (element.closest('[hidden], [inert], [aria-hidden="true"]')) return false
      const style = window.getComputedStyle(element)
      return style.display !== 'none' && style.visibility !== 'hidden'
    }
    const nameFor = (element: HTMLElement) => {
      const labelledBy = (element.getAttribute('aria-labelledby') || '')
        .split(/\s+/)
        .filter(Boolean)
        .map((id) => document.getElementById(id)?.textContent || '')
        .join(' ')
      const nativeLabels = 'labels' in element
        ? Array.from((element as HTMLInputElement).labels || []).map((item) => item.textContent || '').join(' ')
        : ''
      return [
        element.getAttribute('aria-label'),
        labelledBy,
        nativeLabels,
        element.getAttribute('title'),
        element.textContent,
      ].join(' ').trim()
    }
    const interactiveWrapper = (element: HTMLElement) => {
      const label = element.closest('label')
      if (label instanceof HTMLElement) return label
      const button = element.closest('button, [role="button"], .ui-checkbox, .ui-radio')
      return button instanceof HTMLElement ? button : null
    }
    const tooSmall = (element: HTMLElement) => {
      const rect = element.getBoundingClientRect()
      const input = element as HTMLInputElement
      if (input.type === 'checkbox' || input.type === 'radio') {
        const wrapper = interactiveWrapper(element)
        if (wrapper) {
          const wrapRect = wrapper.getBoundingClientRect()
          return wrapRect.width < 48 || wrapRect.height < 48
        }
      }
      return rect.width < 48 || rect.height < 48
    }
    const hitSelf = (element: HTMLElement) => {
      const rect = element.getBoundingClientRect()
      if (rect.width < 8 || rect.height < 8) return false
      const points = [
        [rect.left + rect.width / 2, rect.top + rect.height / 2],
        [rect.left + 2, rect.top + rect.height / 2],
        [rect.right - 2, rect.top + rect.height / 2],
        [rect.left + rect.width / 2, rect.top + 2],
        [rect.left + rect.width / 2, rect.bottom - 2],
      ] as const
      return points.every(([x, y]) => {
        if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) return false
        const hit = document.elementFromPoint(x, y)
        return Boolean(hit && (hit === element || element.contains(hit)))
      })
    }
    const overflowOf = (node: Element | null, label: string) => {
      if (!(node instanceof HTMLElement)) return []
      const style = window.getComputedStyle(node)
      if (style.overflowX === 'hidden') return []
      if (node.scrollWidth > node.clientWidth + 1) return [`${label}:${node.scrollWidth}>${node.clientWidth}`]
      return []
    }

    const interactives = Array.from(document.querySelectorAll<HTMLElement>(
      'a[href], button, input:not([type="hidden"]), textarea, select, [role="button"], [role="tab"], [role="menuitem"]',
    )).filter(isLive)
    const unnamed = interactives.filter((element) => !nameFor(element))
    const nested = interactives.filter((element) => interactives.some((other) => other !== element && other.contains(element)))
    const undersized = interactives.filter(tooSmall)

    const mains = Array.from(document.querySelectorAll('main, [role="main"]')).filter(isLive)
    const h1Nodes = Array.from(document.querySelectorAll('h1')).filter(
      descriptor.h1Mode === 'accessible' ? isAccessibleHeading : isLive,
    )
    const routeScroll = document.querySelector('.app-route-scroll') as HTMLElement | null
    const nav = document.querySelector('.bottom-nav-wrapper, .ui-v2-bottom-nav, .bottom-nav-bar') as HTMLElement | null
    const navRect = nav && isLive(nav) ? nav.getBoundingClientRect() : null
    if (routeScroll) routeScroll.scrollTop = routeScroll.scrollHeight
    window.scrollTo(0, document.documentElement.scrollHeight)

    const ctaCandidates = descriptor.ctaName
      ? interactives.filter((element) => nameFor(element).includes(descriptor.ctaName!))
      : []
    const lastCta = ctaCandidates.at(-1) || null
    const lastRect = lastCta?.getBoundingClientRect()
    const overflow = [
      ...overflowOf(document.documentElement, 'document'),
      ...overflowOf(document.body, 'body'),
      ...overflowOf(document.querySelector('#app'), '#app'),
      ...overflowOf(routeScroll, 'route-scroller'),
      ...Array.from(document.querySelectorAll('.ui-inset-group, .ui-section-card, [role="dialog"], [role="alertdialog"]'))
        .flatMap((node, index) => overflowOf(node, `surface-${index}`)),
    ]

    const designated = Array.from(document.querySelectorAll('.app-route-scroll'))
    const accidental = ['html', 'body', '#app'].flatMap((selector) => {
      const node = document.querySelector(selector)
      if (!(node instanceof HTMLElement) || node.classList.contains('app-route-scroll')) return []
      const style = window.getComputedStyle(node)
      const canScroll = /(auto|scroll)/.test(`${style.overflowY} ${style.overflow}`)
      return canScroll && node.scrollHeight > node.clientHeight + 1 ? [selector] : []
    })
    const internalMessenger = Array.from(document.querySelectorAll<HTMLElement>('.chat-messages, .conversation-list, .manager-body'))
      .filter((node) => node.scrollHeight > node.clientHeight + 1)

    const liveAnimated = Array.from(document.querySelectorAll<HTMLElement>('main, [role="main"], [role="dialog"], [role="alertdialog"]'))
      .filter(isLive)
    const longMotion = liveAnimated.flatMap((element) => {
      const style = window.getComputedStyle(element)
      const durations = `${style.transitionDuration},${style.animationDuration}`
        .split(',')
        .map((item) => Number.parseFloat(item) || 0)
      return durations.some((value) => value > 0.08)
        ? [`${element.tagName}:${durations.join('/')}`]
        : []
    })

    return {
      mainCount: mains.length,
      h1Count: h1Nodes.length,
      h1Text: (h1Nodes[0]?.textContent || '').trim(),
      unnamed: unnamed.slice(0, 8).map((element) => `${element.tagName}.${element.className}`),
      nested: nested.slice(0, 8).map((element) => `${element.tagName}.${element.className}`),
      undersized: undersized.slice(0, 8).map((element) => {
        const rect = element.getBoundingClientRect()
        const label = nameFor(element).replace(/\s+/g, ' ').slice(0, 40)
        return `${element.tagName}.${String(element.className).split(' ')[0]}:${rect.width.toFixed(2)}x${rect.height.toFixed(2)}:${label}`
      }),
      overflow,
      scrollerCount: designated.length,
      documentScrolls: accidental.length > 0,
      internalMessengerCount: internalMessenger.length,
      ctaMissing: Boolean(descriptor.ctaRequired && !lastCta),
      ctaAboveNav: !lastRect || !navRect ? true : lastRect.bottom <= navRect.top + 2,
      ctaHits: !lastCta ? !descriptor.ctaRequired : hitSelf(lastCta),
      reducedMotion: window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true,
      longMotion,
      accidental,
    }
  }, {
    ctaName: route.ctaName,
    ctaRequired: route.ctaRequired,
    h1Mode: route.h1Mode || 'visual',
  })
}

export async function expectRouteContract(page: Page, route: RouteDescriptor, label: string) {
  const contract = await collectRouteContract(page, route)
  expect(contract.mainCount, `${label}: exactly one live main`).toBe(1)
  expect(contract.h1Count, `${label}: exactly one live h1`).toBe(1)
  expect(contract.h1Text, `${label}: h1 text`).toContain(route.h1)
  expect(contract.unnamed, `${label}: unnamed controls`).toEqual([])
  expect(contract.nested, `${label}: nested controls`).toEqual([])
  expect(contract.undersized, `${label}: target < 48x48`).toEqual([])
  expect(contract.overflow, `${label}: horizontal overflow`).toEqual([])
  if (route.scroller.kind === 'standard' || route.scroller.kind === 'none-extra') {
    expect(contract.scrollerCount, `${label}: scroller count`).toBe(route.scroller.expected)
  } else {
    expect(contract.documentScrolls, `${label}: document scroll forbidden`).toBe(false)
    expect(contract.scrollerCount, `${label}: messenger internal scroller`).toBeGreaterThanOrEqual(route.scroller.expectedMin)
  }
  expect(contract.ctaMissing, `${label}: required CTA missing`).toBe(false)
  expect(contract.ctaAboveNav, `${label}: CTA above BottomNav`).toBe(true)
  expect(contract.ctaHits, `${label}: CTA hit-test`).toBe(true)
  expect(contract.reducedMotion, `${label}: reduced motion`).toBe(true)
  expect(contract.longMotion, `${label}: live motion after reduce`).toEqual([])
  await page.locator('body').press('Tab')
  const focusVisible = await page.evaluate(() => {
    const element = document.activeElement
    if (!(element instanceof HTMLElement) || element === document.body) return false
    const style = window.getComputedStyle(element)
    const outline = Number.parseFloat(style.outlineWidth) || 0
    return outline >= 2 || style.boxShadow !== 'none' || element.matches(':focus-visible')
  })
  expect(focusVisible, `${label}: focus-visible`).toBe(true)
}

export async function simulateSoftKeyboard(page: Page, inset = 336) {
  const before = await page.evaluate(() => ({
    inner: window.innerHeight,
    visual: window.visualViewport?.height ?? window.innerHeight,
    scroll: document.querySelector<HTMLElement>('.app-route-scroll')?.scrollTop || 0,
  }))
  const viewport = page.viewportSize()
  if (!viewport) throw new Error('viewport size is required for keyboard simulation')
  const reduced = Math.max(320, viewport.height - inset)
  await page.setViewportSize({ width: viewport.width, height: reduced })
  const after = await page.evaluate(() => ({
    inner: window.innerHeight,
    visual: window.visualViewport?.height ?? window.innerHeight,
    scroll: document.querySelector<HTMLElement>('.app-route-scroll')?.scrollTop || 0,
  }))
  return { before, after, restore: viewport }
}

export async function applyControlledSafeArea(page: Page) {
  await page.addStyleTag({
    content: `
      :root {
        --ds-safe-area-top: 47px !important;
        --ds-safe-area-bottom: 34px !important;
        --harness-safe-area-top: 47px;
        --harness-safe-area-bottom: 34px;
      }
    `,
  })
  return page.evaluate(() => {
    const root = getComputedStyle(document.documentElement)
    return {
      top: root.getPropertyValue('--ds-safe-area-top').trim(),
      bottom: root.getPropertyValue('--ds-safe-area-bottom').trim(),
    }
  })
}

export async function applyMeasurableZoom(page: Page, browserName: string) {
  if (browserName === 'chromium') {
    const session = await page.context().newCDPSession(page)
    await session.send('Emulation.setPageScaleFactor', { pageScaleFactor: 2 })
    await page.waitForTimeout(120)
    const zoom = await page.evaluate(() => ({
      scale: window.visualViewport?.scale ?? 1,
      width: window.visualViewport?.width ?? window.innerWidth,
      applied: String(window.visualViewport?.scale ?? 1),
      method: 'cdp-page-scale',
    }))
    return zoom
  }

  const zoom = await page.evaluate(() => {
    document.documentElement.style.setProperty('zoom', '2')
    const applied = getComputedStyle(document.documentElement).zoom
    const scale = window.visualViewport?.scale ?? 1
    return {
      scale,
      width: window.visualViewport?.width ?? window.innerWidth,
      applied,
      method: applied === '2' || applied === '2.0' ? 'css-zoom' : 'none',
    }
  })
  return zoom
}
