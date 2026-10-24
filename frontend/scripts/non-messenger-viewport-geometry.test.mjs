import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ts from 'typescript'

const source = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../e2e/non-messenger-viewport.spec.ts'), 'utf8')
const syntax = ts.createSourceFile('viewport.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const audit = syntax.statements.find(node => (
  ts.isFunctionDeclaration(node) && node.name?.text === 'expectLastControlClearOfBottomChrome'
))
const metricDeclaration = audit?.body?.statements.find(node => ts.isVariableStatement(node))
const evaluateCall = metricDeclaration?.declarationList.declarations[0]?.initializer?.expression
if (!evaluateCall || !ts.isCallExpression(evaluateCall) || evaluateCall.expression.getText(syntax) !== 'page.evaluate') {
  throw new Error('viewport geometry regression must execute the actual page.evaluate callback')
}
const callback = ts.transpileModule(`(${evaluateCall.arguments[0].getText(syntax)})`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText
const layoutWait = syntax.statements.find(node => (
  ts.isFunctionDeclaration(node) && node.name?.text === 'waitForStableRouteLayout'
))
if (!layoutWait) throw new Error('viewport readiness regression must execute the actual layout wait')
const waitForLayout = window.eval(ts.transpileModule(`(${layoutWait.getText(syntax)})`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText)

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

function readinessPage({ mounted = Promise.resolve(), hidden = Promise.resolve() } = {}) {
  const mountedWait = vi.fn(() => mounted)
  const hiddenWait = vi.fn(() => hidden)
  const page = {
    locator: vi.fn(selector => {
      if (selector === 'html[data-app-mounted="1"]') return { waitFor: mountedWait }
      if (selector === '#boot-loader') return { waitFor: hiddenWait }
      throw new Error('unexpected readiness selector')
    }),
    evaluate: vi.fn(async () => {}),
  }
  return { page, mountedWait, hiddenWait }
}

// Execute the browser audit itself, not a copied approximation of its rules.
function geometry({ lastTop = 700, navControlTop = 830 } = {}) {
  document.body.innerHTML = '<main><button id="early">earlier control</button>'
    + '<button id="last">final product control</button></main>'
    + '<nav class="bottom-nav-bar" style="position:fixed"><a id="nav-item" href="/">home</a></nav>'
  vi.stubGlobal('innerHeight', 896)
  vi.stubGlobal('innerWidth', 414)
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
    const top = this.id === 'early' ? 100
      : this.id === 'last' ? lastTop
        : this.id === 'nav-item' ? navControlTop
          : this.matches('nav') ? 816 : 0
    const height = this.matches('nav') ? 80 : 48
    return { x: 0, y: top, top, bottom: top + height, left: 0, right: 414, width: 414, height, toJSON() {} }
  })
  return window.eval(callback)()
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.replaceChildren()
})

describe('actual non-messenger viewport geometry callback', () => {
  it('keeps a clear final route control and excludes the navigation itself', () => {
    const metrics = geometry()
    expect(metrics.lastControl.text).toBe('final product control')
    expect(metrics.bottomChromeTop).toBe(816)
    expect(metrics.gap).toBe(68)
    expect(metrics.gap).toBeGreaterThanOrEqual(12)
  })

  it('never discards product controls merely because the bottom chrome overlaps them', () => {
    const metrics = geometry({ lastTop: 800 })
    expect(metrics.lastControl.text).toBe('final product control')
    expect(metrics.gap).toBe(-32)
    expect(metrics.gap).toBeLessThan(12)
  })

  it('excludes actual navigation descendants even when they extend outside its rectangle', () => {
    const metrics = geometry({ navControlTop: 200 })
    expect(metrics.lastControl.text).toBe('final product control')
    expect(metrics.gap).toBe(68)
  })

  it('preserves the production viewport test minimum clearance assertion', () => {
    expect(source).toContain(').toBeGreaterThanOrEqual(12)')
  })
})

describe('actual non-messenger route readiness', () => {
  it('does not sample layout until both app mount and boot-overlay hiding finish', async () => {
    const mounted = deferred()
    const hidden = deferred()
    const { page, mountedWait, hiddenWait } = readinessPage({ mounted: mounted.promise, hidden: hidden.promise })
    const waiting = waitForLayout(page)
    expect(mountedWait).toHaveBeenCalledWith({ state: 'attached', timeout: 10_000 })
    expect(hiddenWait).not.toHaveBeenCalled()
    expect(page.evaluate).not.toHaveBeenCalled()

    mounted.resolve()
    await Promise.resolve()
    expect(hiddenWait).toHaveBeenCalledWith({ state: 'hidden', timeout: 10_000 })
    expect(page.evaluate).not.toHaveBeenCalled()

    hidden.resolve()
    await waiting
    expect(page.evaluate).toHaveBeenCalledTimes(1)
  })

  it('fails instead of auditing through a stuck visible boot overlay', async () => {
    const failure = new Error('boot overlay stayed visible')
    const { page, hiddenWait } = readinessPage()
    hiddenWait.mockRejectedValue(failure)
    await expect(waitForLayout(page)).rejects.toBe(failure)
    expect(page.evaluate).not.toHaveBeenCalled()
  })

  it('fails before layout sampling or loader handling when the app never mounts', async () => {
    const failure = new Error('app did not mount')
    const { page, mountedWait, hiddenWait } = readinessPage()
    mountedWait.mockRejectedValue(failure)
    await expect(waitForLayout(page)).rejects.toBe(failure)
    expect(hiddenWait).not.toHaveBeenCalled()
    expect(page.evaluate).not.toHaveBeenCalled()
  })
})
