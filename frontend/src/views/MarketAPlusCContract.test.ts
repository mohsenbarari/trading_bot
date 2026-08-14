import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { UI_ROUTE_PROTECTION, UI_V2_SCOPE, uiRouteContract } from '../router/uiRouteContract'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')

function readRepo(relPath: string) {
  return readFileSync(resolve(repoRoot, relPath), 'utf8')
}

function sha256(relPath: string) {
  return createHash('sha256').update(readRepo(relPath)).digest('hex')
}

describe('Market A+C source contracts', () => {
  const marketView = readRepo('frontend/src/views/MarketView.vue')
  const offersList = readRepo('frontend/src/components/OffersList.vue')
  const offerPreview = readRepo('frontend/src/components/OfferPreviewModal.vue')
  const suggestion = readRepo('frontend/src/components/TradeLotSuggestionAlert.vue')
  const appOfferCard = readRepo('frontend/src/components/ui/AppOfferCard.vue')
  const dashboard = readRepo('frontend/src/views/DashboardView.vue')
  const appVue = readRepo('frontend/src/App.vue')
  const mainCss = readRepo('frontend/src/assets/main.css')

  it('keeps /market FULL, protected-legacy, and v2 off', () => {
    expect(uiRouteContract.find((entry) => entry.path === '/market')).toMatchObject({
      protection: UI_ROUTE_PROTECTION.FULL,
      v2Scope: UI_V2_SCOPE.OFF,
      shellClass: 'protected-legacy',
    })
  })

  it('does not leak Market typography into body, app shell, or Stage 8B marker', () => {
    for (const source of [marketView, offersList, offerPreview, suggestion, appOfferCard]) {
      expect(source).not.toContain('app-route--persian-typography')
      expect(source).not.toMatch(/font-family:\s*['"]Vazirmatn/)
    }
    expect(appVue).toContain("usesApprovedPersianTypography.value ? 'app-route--persian-typography' : undefined")
    expect(appVue).not.toMatch(/protection\s*===\s*UI_ROUTE_PROTECTION\.FULL[\s\S]{0,80}persian-typography/)
    expect(mainCss).not.toContain('.market-page')
    expect(mainCss).not.toContain('.offer-decision-panel')
  })

  it('keeps trade two-tap, preview lock, and buy/sell authority in source', () => {
    expect(offersList).toContain('const pendingConfirm = ref<string | null>(null); // "offerId:amount"')
    expect(offersList).toContain('تایید {{ amount }} عدد؟')
    expect(offersList).toContain("apiFetch('/api/trades/'")
    expect(offersList).toContain("offer?.offer_type === 'buy' ? 'خرید' : 'فروش'")
    expect(offersList).toContain('viewer_effective_price')
    expect(offerPreview).toContain('confirmClickLocked')
    expect(offerPreview).toContain("if (props.submitting || confirmClickLocked.value) return")
    expect(suggestion).toContain("data-test=\"trade-suggestion-lot-button\"")
    expect(suggestion).toContain('تایید {{ amount.toLocaleString() }} عدد؟')
  })

  it('rejects direction B browse-model markers and overtime on Market', () => {
    expect(marketView).not.toContain('market-overtime-pref')
    expect(marketView).not.toContain('buy-column')
    expect(marketView).not.toContain('sell-column')
    expect(marketView).not.toContain('split-dashboard')
    expect(offersList).not.toContain('buy-column')
    expect(offersList).not.toContain('sell-column')
    expect(appOfferCard).toContain('decisionFocus: false')
  })

  it('keeps shared AppFilterChips and Home hero isolated from Market offer cards', () => {
    expect(sha256('frontend/src/components/ui/AppFilterChips.vue')).toBe(
      '66c9f96d8bab76b8ff6a2b055b77f2b4e4645512650fc8fbf12096e3881a9920',
    )
    expect(dashboard).not.toContain('OffersList')
    expect(dashboard).not.toContain('AppOfferCard')
    expect(dashboard).not.toContain('offer-decision-panel')
  })

  it('rejects bypasses that would leak Market styles or drop trade safety', () => {
    expect(mainCss).not.toContain('is-decision-focus')
    expect(mainCss).not.toContain('offer-decision-panel')
    expect(appVue).not.toContain('is-decision-focus')
    expect(offersList).toContain(':aria-label="tradeButtonAriaLabel(offer, amount)"')
    expect(offersList).toContain('data-test="offer-decision-panel"')
    expect(offerPreview).toContain('data-test="offer-preview-recap"')
    expect(suggestion).toContain("if (!props.show || event.key !== 'Escape') return")
    expect(suggestion).not.toMatch(/emit\('select-lot'\).*Escape/)
  })
})
