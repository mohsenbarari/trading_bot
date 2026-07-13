import { offerDraftPrefix, type SettlementType } from './settlementType'

export type OfferDraftPreviewLike = {
  trade_type: 'buy' | 'sell'
  settlement_type: SettlementType
  commodity_name: string
  quantity: number
  price: number
  is_wholesale: boolean
  lot_sizes: number[] | null
  notes: string | null
}

export function buildOfferDraftText(offer: OfferDraftPreviewLike): string {
  const tradeLabel = offerDraftPrefix(offer.trade_type, offer.settlement_type)
  const lotsPart = !offer.is_wholesale && offer.lot_sizes?.length ? ` ${offer.lot_sizes.join(' ')}` : ''
  const notes = offer.notes?.trim()
  const notesPart = notes ? `: ${notes}` : ''

  return `${tradeLabel} ${offer.commodity_name} ${offer.quantity} عدد ${offer.price}${lotsPart}${notesPart}`.trim()
}
