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
    expect(offersList).not.toContain('data-test="offer-decision-panel"')
    expect(offersList).not.toContain('مرور و تأیید معامله')
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
    expect(offersList).not.toContain('data-test="offer-decision-panel"')
    expect(offersList).toContain('min-height: 44px')
    expect(offersList).toContain('padding: 8px 9px 9px')
    expect(offerPreview).toContain('data-test="offer-preview-recap"')
    expect(suggestion).toContain("if (!props.show || event.key !== 'Escape') return")
    expect(suggestion).not.toMatch(/emit\('select-lot'\).*Escape/)
  })

  it('keeps the desktop rail media last so 60rem wins over the 480px token', () => {
    const styleStart = marketView.lastIndexOf('<style')
    const style = marketView.slice(styleStart)
    const mediaMatches = [...style.matchAll(/@media \(min-width: 1024px\) \{[\s\S]*?\n\}/g)]
    expect(mediaMatches.length).toBeGreaterThanOrEqual(1)
    const lastMedia = mediaMatches.at(-1)?.[0] || ''
    expect(lastMedia).toContain('--market-rail-max: 60rem')
    expect(lastMedia).not.toContain('--ds-page-max-width')
    const lastMediaIndex = style.lastIndexOf('@media (min-width: 1024px)')
    const afterLastMedia = style.slice(lastMediaIndex)
    expect(afterLastMedia).not.toMatch(/--ds-page-max-width:\s*480px/)
    expect(afterLastMedia).not.toMatch(/--market-rail-max:\s*var\(--ds-page-max-width\)/)
    expect(afterLastMedia.trim().endsWith('</style>')).toBe(true)
    for (const className of ['.market-title-row', '.header-controls', '.content-inner', '.action-bar-inner']) {
      expect(style).toMatch(new RegExp(`${className.replace('.', '\\.')}[^{]*\\{[^}]*max-width:\\s*var\\(--market-rail-max\\)`))
    }
    expect(marketView).toContain('--market-rail-max: var(--ds-page-max-width)')
  })

  it('separates offer side from responder action and keeps preview uninverted', () => {
    expect(offersList).toContain("function userActionLabel(offer: any): string {")
    expect(offersList).toContain("return offer?.offer_type === 'buy' ? 'فروش' : 'خرید'")
    expect(offersList).toContain('const side = offerSideLabel(offer)')
    expect(offersList).toContain('const action = userActionLabel(offer)')
    expect(offersList).toContain('تأیید نهایی اقدام شما: ${action}')
    expect(offersList).toContain('در برابر لفظ ${side}')
    expect(suggestion).toContain("return props.offerType === 'buy' ? 'فروش' : 'خرید'")
    expect(suggestion).toContain('نوع لفظ: {{ offerSideLabel() }}')
    expect(suggestion).toContain('اقدام شما: {{ userActionLabel() }}')
    expect(offerPreview).toContain('نوع لفظ شما: {{ tradeLabel }}')
    expect(offerPreview).not.toContain('userActionLabel')
    expect(offerPreview).not.toContain("tradeType === 'buy' ? 'فروش'")
  })

  it('binds a linear inset deadline meter and a reduced-motion-safe overtime sticker', () => {
    expect(offersList).toContain('data-test="offer-deadline-meter"')
    expect(offersList).toContain('role="progressbar"')
    expect(offersList).toContain('offer-deadline-meter__value')
    expect(offersList).toContain('transform: scaleX(var(--t-ratio, 1))')
    expect(offersList).toContain("'--t-color': timerColor(offer, remainingPct)")
    expect(offersList).toContain('return isOvertimePhase(offer) ? 100 - remainingPercent : remainingPercent')
    const meterValueRule = offersList.match(/\.offer-deadline-meter__value\s*\{([^}]*)\}/u)?.[1] || ''
    expect(meterValueRule).not.toContain('transition:')
    expect(offersList).not.toContain('offer-deadline-perimeter')
    expect(offersList).not.toContain('offer-trade-rail')
    expect(appOfferCard).not.toContain('offer-deadline-perimeter')
    expect(offersList).not.toContain('data-test="offer-deadline-label"')
    expect(offersList).toContain(':aria-label="deadlineMeterAriaLabel(offer)"')
    expect(offersList).toContain('data-test="offer-overtime-sticker"')
    expect(offersList).toContain('aria-label="وقت اضافه"')
    expect(offersList).toContain('overtime-hourglass-turn')
    expect(offersList).toContain('timerDeadlineTs(offer)')
    expect(offersList).toContain('مهلت اصلی')
    expect(offersList).toContain('وقت اضافه')
    expect(offersList).toContain('مهلت پایان یافته')
    expect(offersList).toContain('در حال نهایی‌سازی')
    expect(offersList).toContain('معامله در وقت اضافه')
    expect(offersList).toContain('offer_public_id: intent.offerPublicId')
    expect(offersList).toContain('const normalizedOfferId = Number(offerId)')
    expect(offersList).toContain('منقضی · بدون معامله')
    expect(offersList).toContain('بخشی معامله شد')
    expect(offersList).toContain('کامل معامله شد')
    expect(offersList).toContain("'offer-card-inner--history': isReadOnlyOffer(offer)")
    expect(offersList).toContain('.offer-card-inner--history .offer-main')
    expect(offersList).toContain('grid-template-columns: minmax(19rem, 0.92fr) minmax(0, 1.08fr)')
    expect(appOfferCard).toContain("'is-partially-traded': traded && partiallyTraded")
    expect(appOfferCard).toContain("'is-fully-traded': traded && !partiallyTraded")
    expect(offersList).not.toContain('⏳')
    expect(offersList).toContain('prefers-reduced-motion: reduce')
    expect(offersList).toContain('--market-focus-ring: var(--ds-primary-800)')
    expect(offersList).not.toContain('rgba(245, 158, 11, 0.34)')
    expect(offerPreview).toContain('outline: 2px solid var(--ds-primary-800)')
    expect(suggestion).toContain('outline: 2px solid var(--ds-primary-800)')
    expect(marketView).toContain('--market-focus-ring: var(--ds-primary-800)')
  })
})
