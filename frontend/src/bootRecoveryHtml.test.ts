import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'

const indexHtml = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')
const inlineBootScript = indexHtml.match(
  /<script>\s*(\(function \(\) \{[\s\S]*?\}\)\(\))\s*<\/script>/u,
)?.[1]

type TimeoutCallback = () => Promise<void> | void

function executeBootRecovery(href: string, recoveredAlready = false) {
  const replace = vi.fn()
  let timeoutCallback: TimeoutCallback | null = null
  const attributes = new Map<string, string>()
  const locationUrl = new URL(href)
  const windowObject = {
    location: {
      href,
      origin: locationUrl.origin,
      pathname: locationUrl.pathname,
      replace,
    },
    setTimeout: vi.fn((callback: TimeoutCallback) => {
      timeoutCallback = callback
      return 1
    }),
  }
  const sessionStorageObject = {
    getItem: vi.fn(() => (recoveredAlready ? '1' : null)),
    setItem: vi.fn(),
  }

  const execute = new Function(
    'window',
    'document',
    'navigator',
    'caches',
    'sessionStorage',
    'console',
    'URL',
    inlineBootScript ?? '',
  )
  execute(
    windowObject,
    {
      documentElement: {
        getAttribute: (name: string) => attributes.get(name) ?? null,
        setAttribute: (name: string, value: string) => attributes.set(name, value),
        removeAttribute: (name: string) => attributes.delete(name),
      },
    },
    {
      serviceWorker: {
        getRegistrations: vi.fn().mockResolvedValue([]),
      },
    },
    {
      keys: vi.fn().mockResolvedValue([]),
      delete: vi.fn(),
    },
    sessionStorageObject,
    { warn: vi.fn() },
    URL,
  )

  return {
    replace,
    sessionStorageObject,
    timeoutCallback: () => timeoutCallback,
    windowObject: windowObject as typeof windowObject & {
      __startAppRecovery?: () => void
    },
  }
}

describe('index.html boot recovery privacy boundary', () => {
  it('contains the expected executable recovery script', () => {
    expect(inlineBootScript).toBeTruthy()
    expect(indexHtml).not.toContain('window.location.reload()')
    expect(indexHtml).toContain('<meta name="referrer" content="no-referrer">')
    expect(indexHtml.indexOf('name="referrer"')).toBeLessThan(indexHtml.indexOf('<script'))
  })

  it('starts manual recovery with a path-only replacement', () => {
    const harness = executeBootRecovery(
      'https://example.test/register?registration_token=RAW-SECRET#token=RAW-SECRET',
    )

    harness.windowObject.__startAppRecovery?.()

    const destination = String(harness.replace.mock.calls[0]?.[0])
    expect(destination).toMatch(/^\/register\?app_recovery=\d+$/u)
    expect(destination).not.toContain('RAW-SECRET')
    expect(destination).not.toContain('registration_token')
    expect(destination).not.toContain('#')
  })

  it('finishes manual recovery without preserving the original query or fragment', async () => {
    const harness = executeBootRecovery(
      'https://example.test/register?app_recovery=1&registration-token=RAW-SECRET#token=RAW-SECRET',
    )

    await vi.waitFor(() => expect(harness.replace).toHaveBeenCalledTimes(1))

    const destination = String(harness.replace.mock.calls[0]?.[0])
    expect(destination).toMatch(/^\/register\?app_recovered=\d+$/u)
    expect(destination).not.toContain('RAW-SECRET')
    expect(destination).not.toContain('registration-token')
    expect(destination).not.toContain('#')
  })

  it('uses a path-only replacement for automatic cache recovery', async () => {
    const harness = executeBootRecovery(
      'https://example.test/register?registration_token=RAW-SECRET#token=RAW-SECRET',
    )
    const timeoutCallback = harness.timeoutCallback()

    expect(timeoutCallback).toBeTypeOf('function')
    await timeoutCallback?.()

    expect(harness.replace).toHaveBeenCalledWith('/register')
    expect(harness.sessionStorageObject.setItem).toHaveBeenCalledWith(
      'app_boot_recovery_attempted',
      '1',
    )
    expect(JSON.stringify(harness.replace.mock.calls)).not.toContain('RAW-SECRET')
  })
})
