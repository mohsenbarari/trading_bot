export const UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE = 'data-ui-system' as const
export const UI_DESIGN_SYSTEM_SCOPE_VALUE = 'v2' as const
export const UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE = 'v2-portal' as const

export const UI_DESIGN_SYSTEM_SCOPE_SELECTOR =
  `[${UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE}="${UI_DESIGN_SYSTEM_SCOPE_VALUE}"], ` +
  `[${UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE}="${UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE}"]`

export type UiDesignSystemScopeKind = 'root' | 'portal'
export type UiDesignSystemScopeValue =
  | typeof UI_DESIGN_SYSTEM_SCOPE_VALUE
  | typeof UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE

export type UiDesignSystemScopeAttributes = Readonly<{
  'data-ui-system': UiDesignSystemScopeValue
}>

const rootScopeAttributes = Object.freeze({
  'data-ui-system': UI_DESIGN_SYSTEM_SCOPE_VALUE,
}) satisfies UiDesignSystemScopeAttributes

const portalScopeAttributes = Object.freeze({
  'data-ui-system': UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE,
}) satisfies UiDesignSystemScopeAttributes

interface PortalScopeRecord {
  references: number
  previousValue: string | null
}

const portalScopeRecords = new WeakMap<HTMLElement, PortalScopeRecord>()

export function getUiDesignSystemScopeAttributes(
  kind: UiDesignSystemScopeKind = 'root',
): UiDesignSystemScopeAttributes {
  return kind === 'portal' ? portalScopeAttributes : rootScopeAttributes
}

export function isInsideUiDesignSystemScope(element: Element | null): boolean {
  return Boolean(element?.closest(UI_DESIGN_SYSTEM_SCOPE_SELECTOR))
}

/**
 * Opt an existing Teleport host into V2 without changing any overlay's
 * default behavior. The returned cleanup is idempotent and reference-counted
 * so independently mounted consumers can safely share the same host.
 */
export function attachUiDesignSystemPortalScope(element: HTMLElement): () => void {
  const currentValue = element.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)
  const existingRecord = portalScopeRecords.get(element)

  if (existingRecord) {
    if (currentValue === null) {
      element.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE)
    } else if (currentValue !== UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE) {
      throw new Error(
        `Cannot reattach UI Design System V2 portal scope: ${UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE} changed to "${currentValue}".`,
      )
    }

    existingRecord.references += 1
  } else {
    if (currentValue === UI_DESIGN_SYSTEM_SCOPE_VALUE) {
      return () => undefined
    }

    if (currentValue !== null && currentValue !== UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE) {
      throw new Error(
        `Cannot attach UI Design System V2 portal scope: ${UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE} is already "${currentValue}".`,
      )
    }

    portalScopeRecords.set(element, {
      references: 1,
      previousValue: currentValue,
    })
    element.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE)
  }

  let released = false

  return () => {
    if (released) return
    released = true

    const record = portalScopeRecords.get(element)
    if (!record) return

    record.references -= 1
    if (record.references > 0) return

    portalScopeRecords.delete(element)

    if (
      element.getAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE) !== UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE
    ) {
      return
    }

    if (record.previousValue === null) {
      element.removeAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE)
      return
    }

    element.setAttribute(UI_DESIGN_SYSTEM_SCOPE_ATTRIBUTE, record.previousValue)
  }
}
