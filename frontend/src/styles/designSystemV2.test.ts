/// <reference types="node" />

import { readFileSync } from 'node:fs'
import path from 'node:path'
import postcss from 'postcss'
import { describe, expect, it } from 'vitest'

const tokenCss = readFileSync(
  path.resolve(process.cwd(), 'src/styles/design-system-v2.tokens.css'),
  'utf8',
)
const componentCss = readFileSync(
  path.resolve(process.cwd(), 'src/styles/design-system-v2.components.css'),
  'utf8',
)

const expectedCanonicalVariables: Record<string, string> = {
  '--ui-v2-neutral-ink-950': '#0f233c',
  '--ui-v2-neutral-ink-900': '#12314a',
  '--ui-v2-neutral-ink-700': '#52697b',
  '--ui-v2-neutral-ink-500': '#94a3b8',
  '--ui-v2-neutral-border-300': '#8091a3',
  '--ui-v2-neutral-surface-100': '#f4f7fa',
  '--ui-v2-neutral-surface-50': '#f8fafc',
  '--ui-v2-neutral-white': '#ffffff',
  '--ui-v2-brand-action-600': '#2f6fed',
  '--ui-v2-brand-text-700': '#2353b5',
  '--ui-v2-brand-icon-650': '#315da8',
  '--ui-v2-brand-subtle-100': '#e8f0ff',
  '--ui-v2-danger-strong': '#b4232c',
  '--ui-v2-danger-subtle': '#fdecee',
  '--ui-v2-warning-strong': '#8a6110',
  '--ui-v2-warning-subtle': '#fff4d8',
  '--ui-v2-info-strong': '#176b8c',
  '--ui-v2-info-subtle': '#e8f5fa',
  '--ui-v2-success-strong': '#0f766e',
  '--ui-v2-success-subtle': '#eaf8f3',
  '--ui-v2-color-surface-page': 'var(--ui-v2-neutral-surface-100)',
  '--ui-v2-color-surface-card': 'var(--ui-v2-neutral-white)',
  '--ui-v2-color-surface-subtle': 'var(--ui-v2-neutral-surface-50)',
  '--ui-v2-color-surface-brand-soft': 'var(--ui-v2-brand-subtle-100)',
  '--ui-v2-color-text-primary': 'var(--ui-v2-neutral-ink-900)',
  '--ui-v2-color-text-strong': 'var(--ui-v2-neutral-ink-950)',
  '--ui-v2-color-text-secondary': 'var(--ui-v2-neutral-ink-700)',
  '--ui-v2-color-text-placeholder': 'var(--ui-v2-neutral-ink-700)',
  '--ui-v2-color-text-on-action': 'var(--ui-v2-neutral-white)',
  '--ui-v2-color-text-action': 'var(--ui-v2-brand-text-700)',
  '--ui-v2-color-border-default': 'var(--ui-v2-neutral-border-300)',
  '--ui-v2-color-border-focus': 'var(--ui-v2-brand-action-600)',
  '--ui-v2-color-action-primary': 'var(--ui-v2-brand-action-600)',
  '--ui-v2-color-icon-brand': 'var(--ui-v2-brand-icon-650)',
  '--ui-v2-color-status-danger-bg': 'var(--ui-v2-danger-subtle)',
  '--ui-v2-color-status-danger': 'var(--ui-v2-danger-strong)',
  '--ui-v2-color-status-warning-bg': 'var(--ui-v2-warning-subtle)',
  '--ui-v2-color-status-warning': 'var(--ui-v2-warning-strong)',
  '--ui-v2-color-status-info-bg': 'var(--ui-v2-info-subtle)',
  '--ui-v2-color-status-info': 'var(--ui-v2-info-strong)',
  '--ui-v2-color-status-success-bg': 'var(--ui-v2-success-subtle)',
  '--ui-v2-color-status-success': 'var(--ui-v2-success-strong)',
  '--ui-v2-color-action-secondary': 'var(--ui-v2-neutral-white)',
  '--ui-v2-color-action-disabled': 'var(--ui-v2-neutral-surface-50)',
  '--ui-v2-color-action-danger': 'var(--ui-v2-danger-strong)',
  '--ui-v2-color-text-disabled': 'var(--ui-v2-neutral-ink-500)',
  '--ui-v2-spacing-2': '2px',
  '--ui-v2-spacing-4': '4px',
  '--ui-v2-spacing-8': '8px',
  '--ui-v2-spacing-12': '12px',
  '--ui-v2-spacing-16': '16px',
  '--ui-v2-spacing-20': '20px',
  '--ui-v2-spacing-24': '24px',
  '--ui-v2-spacing-32': '32px',
  '--ui-v2-radius-8': '8px',
  '--ui-v2-radius-12': '12px',
  '--ui-v2-radius-14': '14px',
  '--ui-v2-radius-16': '16px',
  '--ui-v2-radius-20': '20px',
  '--ui-v2-radius-full': '9999px',
  '--ui-v2-size-target-min': '44px',
  '--ui-v2-size-cta-min': '48px',
  '--ui-v2-size-bottom-nav': '80px',
  '--ui-v2-stroke-standard': '1px',
  '--ui-v2-stroke-focus': '3px',
}

function canonicalVariableBlock() {
  const match = tokenCss.match(
    /\/\* canonical-figma-variables:start \*\/([\s\S]*?)\/\* canonical-figma-variables:end \*\//,
  )
  if (!match?.[1]) throw new Error('Canonical Figma variable block is missing.')
  return match[1]
}

function parseVariables(source: string) {
  return Object.fromEntries(
    [...source.matchAll(/(--ui-v2-[a-z0-9-]+)\s*:\s*([^;]+);/g)].map((match) => {
      const name = match[1]
      const value = match[2]
      if (!name || !value) throw new Error('Malformed canonical Figma variable declaration.')
      return [name, value.trim()]
    }),
  )
}

describe('Design System V2 CSS contract', () => {
  it('contains the exact 65 canonical Figma variables', () => {
    const variables = parseVariables(canonicalVariableBlock())

    expect(Object.keys(variables)).toHaveLength(65)
    expect(variables).toEqual(expectedCanonicalVariables)
  })

  it('keeps canonical aliases in the V2 namespace and defines official roles', () => {
    expect(tokenCss).not.toContain('--ds-')
    expect(tokenCss).toContain('--ui-v2-radius-control: var(--ui-v2-radius-12)')
    expect(tokenCss).toContain('--ui-v2-radius-card: var(--ui-v2-radius-14)')
    expect(tokenCss).toContain('--ui-v2-radius-panel: var(--ui-v2-radius-20)')
    expect(tokenCss).toContain('--ui-v2-radius-compact: var(--ui-v2-radius-8)')
    expect(tokenCss).toContain('--ui-v2-radius-container: var(--ui-v2-radius-16)')
    expect(tokenCss).toContain('--ui-v2-icon-size-small: var(--ui-v2-spacing-16)')
    expect(tokenCss).toContain('--ui-v2-icon-size-control: var(--ui-v2-spacing-20)')
    expect(tokenCss).toContain('--ui-v2-icon-size-large: var(--ui-v2-spacing-24)')
    expect(tokenCss).toContain('--ui-v2-layout-content-max: 1280px')
    expect(tokenCss).toContain('--ui-v2-layout-card-min: 220px')
    expect(tokenCss).toContain('--ui-v2-layout-type-label-min: 110px')
    expect(tokenCss).toContain('--ui-v2-layout-reading-max: 72ch')
    expect(tokenCss).toContain('--ui-v2-type-avatar-initial-line: normal')
  })

  it('has no root, document, body, or universal selectors', () => {
    const combinedCss = `${tokenCss}\n${componentCss}`
    const selectors: string[] = []
    postcss.parse(combinedCss).walkRules((rule) => {
      selectors.push(rule.selector)
    })

    expect(selectors.length).toBeGreaterThan(0)
    for (const selector of selectors) {
      expect(selector).not.toMatch(/(^|[\s>+~,(])(?::root|html|body|\*)(?=$|[\s>+~,.#:[)])/)
    }
  })

  it('only activates selectors below an explicit root or portal scope', () => {
    const selectors: string[] = []
    postcss.parse(`${tokenCss}\n${componentCss}`).walkRules((rule) => {
      selectors.push(rule.selector)
    })

    expect(selectors.length).toBeGreaterThan(0)
    for (const selector of selectors) {
      const normalizedSelector = selector.replaceAll("'", '"')
      expect(normalizedSelector).toContain('[data-ui-system="v2"]')
      expect(normalizedSelector).toContain('[data-ui-system="v2-portal"]')
    }
  })

  it('uses the approved motion durations and collapses them for reduced motion', () => {
    expect(tokenCss).toContain('--ui-v2-motion-micro: 140ms')
    expect(tokenCss).toContain('--ui-v2-motion-state: 180ms')
    expect(tokenCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*--ui-v2-motion-micro: 1ms;[\s\S]*--ui-v2-motion-state: 1ms;/,
    )
    expect(componentCss.replaceAll("'", '"')).toContain('[data-ui-v2-motion="decorative"]')
  })

  it('wins the legacy focus cascade with an explicit three-pixel V2 ring', () => {
    let focusRule: import('postcss').Rule | undefined
    postcss.parse(componentCss).walkRules((rule) => {
      if (!focusRule && rule.selector.includes(':focus-visible')) focusRule = rule
    })

    expect(focusRule?.type).toBe('rule')
    if (!focusRule) return

    const normalizedSelector = focusRule.selector.replaceAll("'", '"')
    expect(normalizedSelector).toContain('[data-ui-system="v2"][data-ui-system="v2"]')
    expect(normalizedSelector).toContain('[data-ui-system="v2-portal"][data-ui-system="v2-portal"]')

    const declarations = Object.fromEntries(
      focusRule.nodes.filter((node) => node.type === 'decl').map((node) => [node.prop, node.value]),
    )
    expect(declarations).toMatchObject({
      outline: 'var(--ui-v2-stroke-focus) solid var(--ui-v2-color-border-focus)',
      'outline-offset': 'var(--ui-v2-spacing-2)',
      'border-color': 'var(--ui-v2-color-border-focus)',
      'box-shadow': 'none',
    })
  })
})
