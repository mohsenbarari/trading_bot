import { afterEach, describe, expect, it, vi } from 'vitest'

const intersectionObserverDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'IntersectionObserver')

class MockIntersectionObserver {
  static instances: MockIntersectionObserver[] = []

  callback: IntersectionObserverCallback
  options?: IntersectionObserverInit
  observe = vi.fn<(target: Element) => void>()
  unobserve = vi.fn<(target: Element) => void>()
  disconnect = vi.fn<() => void>()
  takeRecords = vi.fn<() => IntersectionObserverEntry[]>(() => [])
  root: Element | Document | null = null
  rootMargin = '0px'
  thresholds = [0]

  constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
    this.callback = callback
    this.options = options
    this.root = null
    this.rootMargin = options?.rootMargin || '0px'
    this.thresholds = Array.isArray(options?.threshold) ? options.threshold : [options?.threshold ?? 0]
    MockIntersectionObserver.instances.push(this)
  }

  emit(entries: Partial<IntersectionObserverEntry>[]) {
    this.callback(entries as IntersectionObserverEntry[], this as unknown as IntersectionObserver)
  }

  static reset() {
    MockIntersectionObserver.instances = []
  }
}

function restoreIntersectionObserver() {
  if (intersectionObserverDescriptor) {
    Object.defineProperty(globalThis, 'IntersectionObserver', intersectionObserverDescriptor)
    return
  }
  Reflect.deleteProperty(globalThis, 'IntersectionObserver')
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.resetModules()
  MockIntersectionObserver.reset()
  restoreIntersectionObserver()
})

describe('sharedVisibilityObserver', () => {
  it('falls back to a timeout when IntersectionObserver is unavailable', async () => {
    vi.useFakeTimers()
    restoreIntersectionObserver()
    const visibility = await import('./sharedVisibilityObserver')
    const element = document.createElement('div')

    const cancelledCallback = vi.fn()
    const cancel = visibility.observeVisibility(element, cancelledCallback)
    cancel()
    await vi.runAllTimersAsync()
    expect(cancelledCallback).not.toHaveBeenCalled()

    const callback = vi.fn()
    visibility.observeVisibility(element, callback)
    await vi.runAllTimersAsync()
    expect(callback).toHaveBeenCalledTimes(1)
  })

  it('shares one observer instance, unobserves once visible, and swallows callback failures', async () => {
    Object.defineProperty(globalThis, 'IntersectionObserver', {
      configurable: true,
      value: MockIntersectionObserver,
    })
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const visibility = await import('./sharedVisibilityObserver')
    const elementA = document.createElement('div')
    const elementB = document.createElement('div')
    const elementC = document.createElement('div')

    const callbackA = vi.fn(() => {
      throw new Error('hydrate failed')
    })
    const callbackB = vi.fn()
    const callbackC = vi.fn()

    const stopC = visibility.observeVisibility(elementC, callbackC)
    visibility.observeVisibility(elementA, callbackA)
    visibility.observeVisibility(elementB, callbackB)

    expect(MockIntersectionObserver.instances).toHaveLength(1)
    const observer = MockIntersectionObserver.instances[0]!
    expect(observer.options).toMatchObject({
      rootMargin: '900px 0px 900px 0px',
      threshold: 0.01,
    })

    stopC()
    expect(observer.unobserve).toHaveBeenCalledWith(elementC)

    observer.emit([
      { target: elementA, isIntersecting: false, intersectionRatio: 0 },
      { target: elementA, isIntersecting: true, intersectionRatio: 1 },
      { target: elementB, isIntersecting: true, intersectionRatio: 1 },
    ])

    expect(callbackA).toHaveBeenCalledTimes(1)
    expect(callbackB).toHaveBeenCalledTimes(1)
    expect(observer.unobserve).toHaveBeenCalledWith(elementA)
    expect(observer.unobserve).toHaveBeenCalledWith(elementB)
    expect(consoleSpy).toHaveBeenCalled()

    observer.emit([{ target: elementA, isIntersecting: true, intersectionRatio: 1 }])
    expect(callbackA).toHaveBeenCalledTimes(1)
  })
})