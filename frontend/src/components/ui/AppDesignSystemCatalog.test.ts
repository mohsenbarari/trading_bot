import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import postcss from 'postcss'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AppDesignSystemCatalog from './AppDesignSystemCatalog.vue'

const catalogSource = readFileSync(
  resolve(process.cwd(), 'src/components/ui/AppDesignSystemCatalog.vue'),
  'utf8',
)
const v2TokenSource = readFileSync(
  resolve(process.cwd(), 'src/styles/design-system-v2.tokens.css'),
  'utf8',
)

const expectedSemanticTokenNames = [
  '--ui-v2-color-surface-page',
  '--ui-v2-color-surface-card',
  '--ui-v2-color-surface-subtle',
  '--ui-v2-color-surface-brand-soft',
  '--ui-v2-color-text-primary',
  '--ui-v2-color-text-strong',
  '--ui-v2-color-text-secondary',
  '--ui-v2-color-text-placeholder',
  '--ui-v2-color-text-on-action',
  '--ui-v2-color-text-action',
  '--ui-v2-color-border-default',
  '--ui-v2-color-border-focus',
  '--ui-v2-color-action-primary',
  '--ui-v2-color-icon-brand',
  '--ui-v2-color-status-danger-bg',
  '--ui-v2-color-status-danger',
  '--ui-v2-color-status-warning-bg',
  '--ui-v2-color-status-warning',
  '--ui-v2-color-status-info-bg',
  '--ui-v2-color-status-info',
  '--ui-v2-color-status-success-bg',
  '--ui-v2-color-status-success',
  '--ui-v2-color-action-secondary',
  '--ui-v2-color-action-disabled',
  '--ui-v2-color-action-danger',
  '--ui-v2-color-text-disabled',
] as const

describe('AppDesignSystemCatalog', () => {
  it('stays private, explicitly scoped, and disconnected from every product route', () => {
    const wrapper = mount(AppDesignSystemCatalog)
    const root = wrapper.get('[data-test="ui-v2-catalog"]')

    expect(root.element.tagName).toBe('MAIN')
    expect(root.attributes('data-ui-system')).toBe('v2')
    expect(root.attributes('data-private-catalog')).toBe('true')
    expect(root.attributes('dir')).toBe('rtl')
    expect(wrapper.findAll('a').map((link) => link.attributes('href'))).toEqual([
      '#ui-v2-foundations',
      '#ui-v2-states',
      '#ui-v2-behavior',
      '#ui-v2-responsive',
    ])

    const publicIndex = readFileSync(resolve(process.cwd(), 'src/components/ui/index.ts'), 'utf8')
    const productRouter = readFileSync(resolve(process.cwd(), 'src/router/index.ts'), 'utf8')
    expect(publicIndex).not.toContain('AppDesignSystemCatalog')
    expect(productRouter).not.toContain('AppDesignSystemCatalog')
    expect(catalogSource).not.toMatch(/vue-router|RouterLink|useRoute|useRouter/)
  })

  it('uses only public primitives and contains no protected interior or protected wording', () => {
    const wrapper = mount(AppDesignSystemCatalog)
    const protectedComponents = [
      'AppOfferCard',
      'AppOfferCustomerContext',
      'AppOfferEmptyState',
      'AppOfferHistoryStamp',
      'AppOfferLoadingSkeletonList',
      'AppOfferPrice',
      'AppOfferQuantityBadge',
      'AppOfferSideBadge',
      'AppOfferTradeErrorToast',
      'AppSettlementBadge',
      'AppTradeActionButton',
    ]

    for (const componentName of protectedComponents) {
      expect(catalogSource).not.toContain(componentName)
    }
    expect(catalogSource).not.toMatch(/components\/chat|Messenger(?:View)?/i)
    expect(wrapper.text()).not.toMatch(/بازار|پیام[‌ ]?رسان/)
    expect(wrapper.findAll('a').every((link) => link.attributes('href')?.startsWith('#'))).toBe(
      true,
    )
  })

  it('executes the five canonical component states', () => {
    const wrapper = mount(AppDesignSystemCatalog)
    const states = wrapper.findAll('[data-catalog-state]')

    expect(states.map((state) => state.attributes('data-catalog-state'))).toEqual([
      'normal',
      'loading',
      'disabled',
      'error',
      'destructive',
    ])
    expect(
      wrapper.get('[data-catalog-state="normal"] .ui-button').attributes('disabled'),
    ).toBeUndefined()
    expect(wrapper.get('[data-catalog-state="loading"] .ui-button').classes()).toContain(
      'is-loading',
    )
    expect(
      wrapper.get('[data-catalog-state="loading"] .ui-button').attributes('disabled'),
    ).toBeDefined()
    expect(
      wrapper.get('[data-catalog-state="disabled"] .ui-button').attributes('disabled'),
    ).toBeDefined()
    expect(wrapper.get('[data-catalog-state="error"] [role="alert"]').attributes('role')).toBe(
      'alert',
    )
    expect(wrapper.get('[data-catalog-state="destructive"] .ui-button').classes()).toContain(
      'ui-button--danger',
    )
  })

  it('renders the full semantic, typography, and radius contracts from V2 tokens', () => {
    const wrapper = mount(AppDesignSystemCatalog)
    const semanticTokens = wrapper.findAll('[data-token]')
    const typographyRoles = wrapper.findAll('[data-type-role]')
    const radiusRoles = wrapper.findAll('[data-radius-role]')

    expect(semanticTokens).toHaveLength(26)
    expect(semanticTokens.map((token) => token.attributes('data-token'))).toEqual(
      expectedSemanticTokenNames,
    )
    for (const token of semanticTokens) {
      const tokenName = token.attributes('data-token')
      expect(tokenName).toMatch(/^--ui-v2-color-/)
      expect(token.get('.ui-v2-catalog__swatch').attributes('style')).toContain(`var(${tokenName})`)
      expect(v2TokenSource).toContain(`${tokenName}:`)
    }

    expect(typographyRoles).toHaveLength(10)
    for (const role of typographyRoles) {
      const style = role.get('.ui-v2-catalog__type-sample').attributes('style')
      expect(style).toContain('var(--ui-v2-type-')
      expect(style).not.toContain('--ds-')
    }

    expect(radiusRoles.map((role) => role.attributes('data-radius-role'))).toEqual([
      'compact',
      'control',
      'card',
      'container',
      'panel',
      'full',
    ])
    for (const role of radiusRoles) {
      expect(role.attributes('style')).toContain('var(--ui-v2-radius-')
    }
  })

  it('executes the approved icon scale and semantic list reference', () => {
    const wrapper = mount(AppDesignSystemCatalog)
    const iconProofs = wrapper.findAll('[data-icon-size]')

    expect(iconProofs.map((proof) => proof.attributes('data-icon-size'))).toEqual([
      'small',
      'control',
      'large',
    ])
    expect(iconProofs.map((proof) => proof.attributes('data-icon-token'))).toEqual([
      '--ui-v2-icon-size-small',
      '--ui-v2-icon-size-control',
      '--ui-v2-icon-size-large',
    ])
    expect(iconProofs.every((proof) => proof.get('svg').attributes('aria-hidden') === 'true')).toBe(
      true,
    )

    const list = wrapper.get('[data-test="list-contract"] ul')
    const rows = list.findAll(':scope > li')
    expect(rows).toHaveLength(2)
    expect(rows[0]?.get('.ui-list-item').element.tagName).toBe('BUTTON')
    expect(rows[0]?.get('.ui-list-item').attributes('role')).toBeUndefined()
    expect(rows[1]?.get('.ui-list-item').element.tagName).toBe('ARTICLE')
    expect(rows[1]?.get('.ui-list-item').attributes('role')).toBeUndefined()
  })

  it('proves focus, motion, reduced-motion disclosure, and an isolated portal dialog', async () => {
    const wrapper = mount(AppDesignSystemCatalog)

    expect(wrapper.get('[data-test="focus-proof"] .ui-button').element.tagName).toBe('BUTTON')
    expect(
      wrapper
        .findAll('[data-ui-v2-motion]')
        .map((item) => [
          item.attributes('data-ui-v2-motion'),
          item.attributes('data-motion-duration'),
        ]),
    ).toEqual([
      ['essential', '140'],
      ['decorative', '180'],
    ])
    expect(wrapper.get('[data-test="reduced-motion-disclosure"]').element.tagName).toBe('DETAILS')
    expect(wrapper.find('[data-test="catalog-portal-scope"]').exists()).toBe(false)

    await wrapper.get('[data-test="portal-proof-toggle"]').trigger('click')
    const portal = wrapper.get('[data-test="catalog-portal-scope"]')
    expect(portal.attributes('data-ui-system')).toBe('v2-portal')
    expect(portal.get('[role="dialog"]').attributes('aria-modal')).toBe('false')

    await portal.get('.ui-button--primary').trigger('click')
    expect(wrapper.find('[data-test="catalog-portal-scope"]').exists()).toBe(false)
  })

  it('declares all reference widths and explicit no-overflow landmarks', () => {
    const wrapper = mount(AppDesignSystemCatalog)
    const root = wrapper.get('[data-test="ui-v2-catalog"]')

    expect(root.attributes('data-responsive-contract')).toBe('360,375,390,414,430,1440')
    expect(
      wrapper
        .findAll('[data-responsive-width]')
        .map((item) => item.attributes('data-responsive-width')),
    ).toEqual(['360', '375', '390', '414', '430', '1440'])
    expect(
      wrapper
        .findAll('[data-overflow-contract]')
        .map((item) => item.attributes('data-overflow-contract')),
    ).toEqual(['header', 'content', 'responsive-proof'])
    expect(wrapper.get('header').element.tagName).toBe('HEADER')
    expect(wrapper.get('nav').attributes('aria-label')).toBe('فهرست نمونه‌ها')
    expect(wrapper.findAll('section').length).toBeGreaterThanOrEqual(4)
    expect(wrapper.get('.ui-v2-catalog__overflow-contract').text()).toContain('سرریز')
  })

  it('keeps every catalog style selector scoped and every custom-property reference namespaced', () => {
    const styleSource = catalogSource.match(/<style>([\s\S]*?)<\/style>/)?.[1]
    expect(styleSource).toBeDefined()

    const stylesheet = postcss.parse(styleSource ?? '')
    stylesheet.walkRules((rule) => {
      for (const selector of rule.selectors) {
        expect(selector).toMatch(/\[data-ui-system=["']v2/)
        expect(selector).not.toMatch(/:root\b|(^|[\s>+~,(])(?:html|body|\*)(?=$|[\s>+~.#[:),])/i)
      }
    })

    expect(styleSource).not.toContain('--ds-')
    stylesheet.walkDecls((declaration) => {
      if (declaration.prop.startsWith('--')) {
        expect(declaration.prop).toMatch(/^--ui-v2-/)
      }
      for (const match of declaration.value.matchAll(/var\(\s*(--[a-z0-9-]+)/g)) {
        expect(match[1]).toMatch(/^--ui-v2-/)
      }
    })
  })
})
