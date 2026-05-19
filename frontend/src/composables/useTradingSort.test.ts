import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import { useTradingSort } from './useTradingSort'

describe('useTradingSort', () => {
  it('filters by offer type and sorts the selected commodity through asc/desc/none cycle', () => {
    const offers = ref([
      { id: 1, offer_type: 'sell', commodity_name: 'Gold', price: 300 },
      { id: 2, offer_type: 'buy', commodity_name: 'Coin', price: 200 },
      { id: 3, offer_type: 'buy', commodity_name: 'Gold', price: 100, viewer_effective_price: 140 },
      { id: 4, offer_type: 'buy', commodity_name: 'Gold', price: 250, viewer_effective_price: 130 },
    ])

    const sort = useTradingSort(offers)
    expect(sort.filteredOffers.value.map((offer) => offer.id)).toEqual([1, 2, 3, 4])

    sort.filterType.value = 'buy'
    expect(sort.filteredOffers.value.map((offer) => offer.id)).toEqual([2, 3, 4])

    sort.toggleSort('Gold')
    expect(sort.sortCommodity.value).toBe('Gold')
    expect(sort.sortDirection.value).toBe('asc')
    expect(sort.filteredOffers.value.map((offer) => offer.id)).toEqual([4, 3, 2])

    sort.toggleSort('Gold')
    expect(sort.sortDirection.value).toBe('desc')
    expect(sort.filteredOffers.value.map((offer) => offer.id)).toEqual([3, 4, 2])

    sort.toggleSort('Gold')
    expect(sort.sortDirection.value).toBe('none')
    expect(sort.sortCommodity.value).toBe('')
    expect(sort.filteredOffers.value.map((offer) => offer.id)).toEqual([2, 3, 4])
  })

  it('clearSort resets commodity, direction, and sort panel state', () => {
    const offers = ref([{ offer_type: 'buy', commodity_name: 'Gold', price: 100 }])
    const sort = useTradingSort(offers)

    sort.showSortPanel.value = true
    sort.toggleSort('Gold')
    sort.clearSort()

    expect(sort.sortCommodity.value).toBe('')
    expect(sort.sortDirection.value).toBe('none')
    expect(sort.showSortPanel.value).toBe(false)
  })
})