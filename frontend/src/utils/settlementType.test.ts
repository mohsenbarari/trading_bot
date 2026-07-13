import { describe, expect, it } from 'vitest'

import { buildOfferDraftText } from './offerDraftText'
import {
  normalizeSettlementType,
  offerDraftPrefix,
  offerSettlementLabel,
  tradeSettlementLabel,
} from './settlementType'

describe('settlement type presentation', () => {
  it('uses explicit offer and trade labels for both settlement modes', () => {
    expect(offerSettlementLabel('cash')).toBe('نقد حاضر ☀️')
    expect(offerSettlementLabel('tomorrow')).toBe('فردا ➡️')
    expect(tradeSettlementLabel('cash')).toBe('نقد حاضر')
    expect(tradeSettlementLabel('tomorrow')).toBe('فردایی')
  })

  it('falls back to cash for missing or unknown rolling-client values', () => {
    expect(normalizeSettlementType(undefined)).toBe('cash')
    expect(normalizeSettlementType('unknown')).toBe('cash')
  })

  it('builds drafts with grammar accepted by the strict parser', () => {
    expect(offerDraftPrefix('buy', 'cash')).toBe('خرید نقد')
    expect(offerDraftPrefix('sell', 'tomorrow')).toBe('فروش نقد فردا')
    expect(buildOfferDraftText({
      trade_type: 'sell',
      settlement_type: 'tomorrow',
      commodity_name: 'ربع بهار',
      quantity: 40,
      price: 178000,
      is_wholesale: false,
      lot_sizes: [10, 30],
      notes: 'تحویل بازار',
    })).toBe('فروش نقد فردا ربع بهار 40 عدد 178000 10 30: تحویل بازار')
  })
})
