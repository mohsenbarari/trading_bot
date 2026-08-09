import { describe, expect, it } from 'vitest'
import {
  attachUiDesignSystemPortalScope,
  getUiDesignSystemScopeAttributes,
  isInsideUiDesignSystemScope,
  UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE,
  UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE,
  UI_DESIGN_SYSTEM_SCOPE_SELECTOR,
  UI_DESIGN_SYSTEM_SCOPE_VALUE,
} from './uiDesignSystemScope'

describe('uiDesignSystemScope', () => {
  it('exposes stable, immutable scope attributes and selectors', () => {
    const root = getUiDesignSystemScopeAttributes()
    const portal = getUiDesignSystemScopeAttributes('portal')

    expect(root).toEqual({ 'data-ui-system': UI_DESIGN_SYSTEM_SCOPE_VALUE })
    expect(portal).toEqual({ 'data-ui-system': UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE })
    expect(Object.isFrozen(root)).toBe(true)
    expect(Object.isFrozen(portal)).toBe(true)
    expect(UI_DESIGN_SYSTEM_SCOPE_SELECTOR).toContain('[data-ui-system="v2"]')
    expect(UI_DESIGN_SYSTEM_SCOPE_SELECTOR).toContain('[data-ui-system="v2-portal"]')
  })

  it('recognizes root and portal descendants without treating legacy DOM as V2', () => {
    const root = document.createElement('section')
    const child = document.createElement('button')
    root.appendChild(child)

    expect(isInsideUiDesignSystemScope(child)).toBe(false)

    root.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, UI_DESIGN_SYSTEM_SCOPE_VALUE)
    expect(isInsideUiDesignSystemScope(root)).toBe(true)
    expect(isInsideUiDesignSystemScope(child)).toBe(true)

    root.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE)
    expect(isInsideUiDesignSystemScope(child)).toBe(true)
    expect(isInsideUiDesignSystemScope(null)).toBe(false)
  })

  it('attaches and releases a shared portal scope with reference counting', () => {
    const host = document.createElement('div')
    const releaseFirst = attachUiDesignSystemPortalScope(host)
    const releaseSecond = attachUiDesignSystemPortalScope(host)

    expect(host.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe('v2-portal')

    releaseFirst()
    releaseFirst()
    expect(host.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe('v2-portal')

    releaseSecond()
    expect(host.hasAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe(false)
  })

  it('reasserts a removed portal attribute before increasing the shared reference count', () => {
    const host = document.createElement('div')
    const releaseFirst = attachUiDesignSystemPortalScope(host)
    host.removeAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)

    const releaseSecond = attachUiDesignSystemPortalScope(host)

    expect(host.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe('v2-portal')
    releaseFirst()
    expect(host.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe('v2-portal')
    releaseSecond()
    expect(host.hasAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe(false)
  })

  it('fails closed when an active shared portal scope is externally replaced', () => {
    const host = document.createElement('div')
    const release = attachUiDesignSystemPortalScope(host)
    host.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, UI_DESIGN_SYSTEM_SCOPE_VALUE)

    expect(() => attachUiDesignSystemPortalScope(host)).toThrow('data-ui-system changed to "v2"')

    release()
    expect(host.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe('v2')
  })

  it('preserves explicit scopes and never overwrites a conflicting system', () => {
    const rootHost = document.createElement('div')
    rootHost.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, UI_DESIGN_SYSTEM_SCOPE_VALUE)
    const releaseRoot = attachUiDesignSystemPortalScope(rootHost)
    releaseRoot()
    expect(rootHost.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe('v2')

    const portalHost = document.createElement('div')
    portalHost.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE)
    const releasePortal = attachUiDesignSystemPortalScope(portalHost)
    releasePortal()
    expect(portalHost.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe('v2-portal')

    const conflictingHost = document.createElement('div')
    conflictingHost.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, 'legacy')
    expect(() => attachUiDesignSystemPortalScope(conflictingHost)).toThrow(
      'data-ui-system is already "legacy"',
    )
    expect(conflictingHost.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe('legacy')
  })

  it('does not clobber an external attribute update during cleanup', () => {
    const host = document.createElement('div')
    const release = attachUiDesignSystemPortalScope(host)
    host.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, 'external-update')

    release()

    expect(host.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)).toBe('external-update')
  })
})
