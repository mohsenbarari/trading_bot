import { computed, ref, type Ref } from 'vue'

type SortDirection = 'none' | 'asc' | 'desc'
type FilterType = 'all' | 'buy' | 'sell'

type SortableOffer = {
  offer_type: 'buy' | 'sell' | string
  commodity_name: string
  price: number
  viewer_effective_price?: number | null
}

function getSortablePrice(offer: SortableOffer): number {
  return typeof offer.viewer_effective_price === 'number' ? offer.viewer_effective_price : offer.price
}

export function useTradingSort<T extends SortableOffer>(offers: Ref<T[]>) {
  const filterType = ref<FilterType>('all')
  const sortCommodity = ref('')
  const sortDirection = ref<SortDirection>('none')
  const showSortPanel = ref(false)

  const filteredOffers = computed(() => {
    let result = offers.value

    if (filterType.value !== 'all') {
      result = result.filter((offer) => offer.offer_type === filterType.value)
    }

    if (sortCommodity.value && sortDirection.value !== 'none') {
      const commodity = sortCommodity.value
      const direction = sortDirection.value

      result = [...result].sort((left, right) => {
        const leftMatches = left.commodity_name === commodity
        const rightMatches = right.commodity_name === commodity

        if (leftMatches && !rightMatches) return -1
        if (!leftMatches && rightMatches) return 1
        if (!leftMatches && !rightMatches) return 0

        return direction === 'asc'
          ? getSortablePrice(left) - getSortablePrice(right)
          : getSortablePrice(right) - getSortablePrice(left)
      })
    }

    return result
  })

  function toggleSort(commodity: string) {
    if (sortCommodity.value === commodity) {
      if (sortDirection.value === 'none') {
        sortDirection.value = 'asc'
      } else if (sortDirection.value === 'asc') {
        sortDirection.value = 'desc'
      } else {
        sortDirection.value = 'none'
        sortCommodity.value = ''
      }
      return
    }

    sortCommodity.value = commodity
    sortDirection.value = 'asc'
  }

  function clearSort() {
    sortCommodity.value = ''
    sortDirection.value = 'none'
    showSortPanel.value = false
  }

  return {
    clearSort,
    filterType,
    filteredOffers,
    showSortPanel,
    sortCommodity,
    sortDirection,
    toggleSort,
  }
}