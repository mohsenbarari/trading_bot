export type SettlementType = 'cash' | 'tomorrow'

export function normalizeSettlementType(value: unknown): SettlementType {
  return value === 'tomorrow' ? 'tomorrow' : 'cash'
}

export function offerSettlementLabel(value: unknown): string {
  return normalizeSettlementType(value) === 'tomorrow' ? 'فردا 📆' : 'نقد حاضر ☀️'
}

export function tradeSettlementLabel(value: unknown): string {
  return normalizeSettlementType(value) === 'tomorrow' ? 'فردایی' : 'نقد حاضر'
}

export function offerDraftPrefix(tradeType: 'buy' | 'sell', settlementType: unknown): string {
  const tradeLabel = tradeType === 'buy' ? 'خرید' : 'فروش'
  return normalizeSettlementType(settlementType) === 'tomorrow'
    ? `${tradeLabel} نقد فردا`
    : `${tradeLabel} نقد`
}
