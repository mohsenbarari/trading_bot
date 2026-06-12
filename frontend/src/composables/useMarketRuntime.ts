import { computed, readonly, ref } from 'vue'

import { apiFetch } from '../utils/auth'
import { useWebSocket } from './useWebSocket'

export type MarketRuntimeState = {
  is_open: boolean
  active_web_notice_visible: boolean
  offers_since_last_open: number
  last_transition_at: string | null
  next_transition_at: string | null
}

const DEFAULT_MARKET_RUNTIME_STATE: MarketRuntimeState = {
  is_open: true,
  active_web_notice_visible: false,
  offers_since_last_open: 0,
  last_transition_at: null,
  next_transition_at: null,
}

const marketRuntimeState = ref<MarketRuntimeState>({ ...DEFAULT_MARKET_RUNTIME_STATE })
const marketRuntimeLoaded = ref(false)
const marketRuntimeLoading = ref(false)

let fetchPromise: Promise<void> | null = null
let subscriberCount = 0
let listenersRegistered = false

export const marketRuntime = readonly(marketRuntimeState)
export const isMarketRuntimeClosed = computed(() => !marketRuntimeState.value.is_open)
export const isMarketRuntimeLoading = readonly(marketRuntimeLoading)

export function applyMarketRuntimePatch(patch: Partial<MarketRuntimeState> | undefined) {
  if (!patch) return

  const nextState: MarketRuntimeState = { ...marketRuntimeState.value }

  if (typeof patch.is_open === 'boolean') {
    nextState.is_open = patch.is_open
  }
  if (typeof patch.active_web_notice_visible === 'boolean') {
    nextState.active_web_notice_visible = patch.active_web_notice_visible
  }
  if (typeof patch.offers_since_last_open === 'number') {
    nextState.offers_since_last_open = patch.offers_since_last_open
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'last_transition_at')) {
    nextState.last_transition_at = patch.last_transition_at ?? null
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'next_transition_at')) {
    nextState.next_transition_at = patch.next_transition_at ?? null
  }

  marketRuntimeState.value = nextState
}

export async function fetchMarketRuntimeState(options: { force?: boolean } = {}) {
  if (fetchPromise && !options.force) {
    return fetchPromise
  }
  if (marketRuntimeLoaded.value && !options.force) {
    return
  }

  const promise = (async () => {
    marketRuntimeLoading.value = true
    try {
      const response = await apiFetch('/api/trading-settings/market-state')
      if (!response.ok) return

      const data = await response.json().catch(() => null)
      if (!data || typeof data.is_open !== 'boolean') return

      applyMarketRuntimePatch(data as Partial<MarketRuntimeState>)
      marketRuntimeLoaded.value = true
    } catch (error) {
      console.error('Failed to load market runtime state', error)
    } finally {
      marketRuntimeLoading.value = false
    }
  })()

  fetchPromise = promise
  try {
    await promise
  } finally {
    if (fetchPromise === promise) {
      fetchPromise = null
    }
  }
}

function handleMarketOpened(data: Partial<MarketRuntimeState> | undefined) {
  applyMarketRuntimePatch({
    is_open: true,
    active_web_notice_visible: data?.active_web_notice_visible ?? true,
    offers_since_last_open: data?.offers_since_last_open ?? 0,
    last_transition_at: data?.last_transition_at ?? marketRuntimeState.value.last_transition_at,
    next_transition_at: data?.next_transition_at ?? marketRuntimeState.value.next_transition_at,
  })
  marketRuntimeLoaded.value = true
}

function handleMarketClosed(data: Partial<MarketRuntimeState> | undefined) {
  applyMarketRuntimePatch({
    is_open: false,
    active_web_notice_visible: data?.active_web_notice_visible ?? true,
    offers_since_last_open: data?.offers_since_last_open ?? marketRuntimeState.value.offers_since_last_open,
    last_transition_at: data?.last_transition_at ?? marketRuntimeState.value.last_transition_at,
    next_transition_at: data?.next_transition_at ?? marketRuntimeState.value.next_transition_at,
  })
  marketRuntimeLoaded.value = true
}

function handleMarketNoticeHidden(data: Partial<MarketRuntimeState> | undefined) {
  applyMarketRuntimePatch({
    active_web_notice_visible: false,
    offers_since_last_open: data?.offers_since_last_open ?? marketRuntimeState.value.offers_since_last_open,
    last_transition_at: data?.last_transition_at ?? marketRuntimeState.value.last_transition_at,
    next_transition_at: data?.next_transition_at ?? marketRuntimeState.value.next_transition_at,
  })
}

function handleWsReconnect() {
  void fetchMarketRuntimeState({ force: true })
}

function registerMarketRuntimeListeners() {
  if (listenersRegistered) return
  const { on } = useWebSocket()
  on('market:opened', handleMarketOpened)
  on('market:closed', handleMarketClosed)
  on('market:notice_hidden', handleMarketNoticeHidden)
  on('ws:reconnect', handleWsReconnect)
  listenersRegistered = true
}

function unregisterMarketRuntimeListeners() {
  if (!listenersRegistered) return
  const { off } = useWebSocket()
  off('market:opened', handleMarketOpened)
  off('market:closed', handleMarketClosed)
  off('market:notice_hidden', handleMarketNoticeHidden)
  off('ws:reconnect', handleWsReconnect)
  listenersRegistered = false
}

export function startMarketRuntimeUpdates(options: { fetchImmediately?: boolean } = {}) {
  subscriberCount += 1
  registerMarketRuntimeListeners()

  if (options.fetchImmediately !== false) {
    void fetchMarketRuntimeState()
  }
}

export function stopMarketRuntimeUpdates() {
  subscriberCount = Math.max(0, subscriberCount - 1)
  if (subscriberCount === 0) {
    unregisterMarketRuntimeListeners()
  }
}

export function resetMarketRuntimeForTests() {
  marketRuntimeState.value = { ...DEFAULT_MARKET_RUNTIME_STATE }
  marketRuntimeLoaded.value = false
  marketRuntimeLoading.value = false
  fetchPromise = null
  subscriberCount = 0
  listenersRegistered = false
}
