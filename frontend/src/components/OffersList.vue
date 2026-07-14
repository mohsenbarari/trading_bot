<script setup lang="ts">

import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { Loader2 } from 'lucide-vue-next';
import { apiFetch } from '../utils/auth';
import { createHttpErrorFromResponse, getUserFacingErrorMessage } from '../utils/httpErrorPolicy';
import { offerSettlementLabel, normalizeSettlementType, type SettlementType } from '../utils/settlementType';
import TradeLotSuggestionAlert from './TradeLotSuggestionAlert.vue';
import {
  AppOfferCard,
  AppOfferCustomerContext,
  AppOfferEmptyState,
  AppOfferHistoryStamp,
  AppOfferLoadingSkeletonList,
  AppOfferPrice,
  AppOfferQuantityBadge,
  AppSettlementBadge,
  AppOfferSideBadge,
  AppOfferTradeErrorToast,
  AppTradeActionButton,
} from './ui';

interface TradeLotSuggestionState {
  title: string;
  introText: string;
  offerId: number;
  offerType: 'buy' | 'sell' | '';
  offerTypeLabel: string;
  settlementType: SettlementType;
  settlementTypeLabel: string;
  commodityName: string;
  price: number;
  remainingQuantity: number;
  lotSummary: string;
  availableLots: number[];
  expiresAtTs?: number | null;
  sourceSignature?: string | null;
}

type TradeIntentStatus = 'in_flight' | 'uncertain';

interface TradeIntentState {
  version: 1;
  offerId: number;
  quantity: number;
  idempotencyKey: string;
  status: TradeIntentStatus;
  createdAt: number;
  updatedAt: number;
}

const TRADE_INTENT_STORAGE_PREFIX = 'market_trade_intents_v1';
const AMBIGUOUS_TRADE_MESSAGE = 'ارتباط با سرور قطع شد. اگر معامله ثبت شده باشد، تکرار همین درخواست معامله دوم نمی‌سازد.';
const CONFLICTING_TRADE_INTENT_MESSAGE = 'نتیجه درخواست قبلی این لفظ هنوز مشخص نیست. ابتدا همان درخواست را دوباره ارسال کنید.';

// Define Props
const props = defineProps<{
  offers: any[];
  loading: boolean;
  limit?: number;
  expiryMinutes?: number;
  currentUserId?: number;
  expiredLoading?: boolean;
  hasMoreExpired?: boolean;
  canLoadExpired?: boolean;
}>();

const emit = defineEmits<{
  (e: 'trade-completed'): void;
  (e: 'load-more-expired'): void;
}>();

// Trade execution state
const tradingOfferId = ref<number | null>(null);
const tradingAmount = ref<number | null>(null);
const tradeError = ref('');
const tradeSuggestion = ref<TradeLotSuggestionState | null>(null);
const cancelingOfferId = ref<number | null>(null);
const tradeIntents = new Map<string, TradeIntentState>();
let activeTradeIntentStorageKey: string | null = null;
let componentActive = true;
let tradeErrorTimeout: ReturnType<typeof setTimeout> | null = null;

// Confirmation state (double-tap like Telegram)
const pendingConfirm = ref<string | null>(null); // "offerId:amount"
let confirmTimeout: any = null;

// --- High-frequency tick for smooth animation ---
const now = ref(Date.now() / 1000)
let animationFrameId: number | null = null

function tick() {
  now.value = Date.now() / 1000
  animationFrameId = requestAnimationFrame(tick)
}

onMounted(() => {
  componentActive = true;
  animationFrameId = requestAnimationFrame(tick)
})

onUnmounted(() => {
  componentActive = false
  if (animationFrameId) cancelAnimationFrame(animationFrameId)
  if (confirmTimeout) clearTimeout(confirmTimeout)
  if (tradeErrorTimeout) clearTimeout(tradeErrorTimeout)
})

// --- Timer percent ---
function getTimerPercent(offer: any): number {
  if (!offer.expires_at_ts) return 100
  const remaining = offer.expires_at_ts - now.value
  if (remaining <= 0) return 0
  const total = (props.expiryMinutes || 2) * 60
  return Math.min(Math.max((remaining / total) * 100, 0), 100)
}

function cardTimerStyle(offer: any): Record<string, string> {
  if (isReadOnlyOffer(offer)) return {}
  if (!offer.expires_at_ts) return {}
  const remainingSec = offer.expires_at_ts - now.value
  if (remainingSec <= 0) return { '--t-pct': '0' }
  const total = (props.expiryMinutes || 2) * 60
  const pct = Math.min(Math.max((remainingSec / total) * 100, 0), 100)
  return {
    '--t-pct': String(pct)
  }
}

function isCritical(offer: any): boolean {
  return !!offer.expires_at_ts && getTimerPercent(offer) < 15
}

function hasTimer(offer: any): boolean {
  return !!offer.expires_at_ts
}

function isExpiredOffer(offer: any): boolean {
  if (isTradedHistoryOffer(offer)) return false
  return offer?.status === 'expired' || offer?.history_state === 'expired'
}

function isTradedHistoryOffer(offer: any): boolean {
  return offer?.history_state === 'traded'
}

function isReadOnlyOffer(offer: any): boolean {
  const status = String(offer?.status ?? '').toLowerCase()
  return offer?.is_read_only === true
    || typeof offer?.history_state === 'string'
    || (status !== '' && status !== 'active')
}

function getFiniteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const numericValue = Number(value)
  return Number.isFinite(numericValue) ? numericValue : null
}

function getOfferRemainingQuantity(offer: any): number {
  const remaining = offer?.remaining_quantity
  if (remaining !== null && remaining !== undefined) {
    return getFiniteNumber(remaining) ?? 0
  }
  return getFiniteNumber(offer?.quantity) ?? 0
}

function getHistoryStampLabel(offer: any): string {
  if (isTradedHistoryOffer(offer)) {
    const tradedQuantity = getFiniteNumber(offer?.traded_quantity)
    if (offer?.is_partially_traded === true && tradedQuantity !== null && tradedQuantity > 0) {
      return `معامله‌شده ${tradedQuantity.toLocaleString()} عدد`
    }
    return 'معامله‌شده'
  }
  if (isExpiredOffer(offer)) return 'منقضی'
  return ''
}

function getOfferQuantityLabel(offer: any): string {
  const remainingQuantity = getFiniteNumber(offer?.remaining_quantity)
  const totalQuantity = getFiniteNumber(offer?.quantity)
  if (isTradedHistoryOffer(offer) && totalQuantity !== null) {
    return `${totalQuantity.toLocaleString()} عدد`
  }
  if (remainingQuantity !== null) return `${remainingQuantity.toLocaleString()} عدد`
  if (totalQuantity !== null) return `${totalQuantity.toLocaleString()} عدد`
  return '---'
}

// Keep active offers live-filtered, while read-only history rows remain visible.
const filteredOffers = computed(() => {
  const nowSec = now.value
  const source = Array.isArray(props.offers) ? props.offers : []
  const visible = source.filter(o => isReadOnlyOffer(o) || !o.expires_at_ts || o.expires_at_ts > nowSec)
  return props.limit ? visible.slice(0, props.limit) : visible
})

function timeAgo(dateString: string) {
    if (!dateString) return '';
    return dateString;
}

// --- Lot buttons logic (matching Telegram channel) ---
function getLotButtons(offer: any): number[] {
  const remaining = getOfferRemainingQuantity(offer);
  if (remaining <= 0) return [];
  
  if (offer.is_wholesale || !offer.lot_sizes || offer.lot_sizes.length === 0) {
    // Wholesale: single button with remaining quantity
    return [remaining];
  }
  
  // Retail: only the offer owner's still-active lots are valid trade amounts.
  const allAmounts = [remaining, ...offer.lot_sizes].filter((l: number) => l > 0 && l <= remaining);
  // Deduplicate
  const seen = new Set<number>();
  const unique: number[] = [];
  for (const a of allAmounts) {
    if (!seen.has(a) && a > 0) {
      seen.add(a);
      unique.push(a);
    }
  }
  // Sort ascending: in RTL flex, first item is on the right, so ascending puts largest on the left
  return unique.sort((a, b) => a - b);
}

function formatLotSummary(amounts: number[]): string {
  return [...amounts].sort((a, b) => b - a).join(' + ');
}

function getDisplayedOfferPrice(offer: any): number {
  const numeric = Number(offer?.viewer_effective_price ?? offer?.price ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function getCustomerTierLabel(tier: string | null | undefined): string {
  if (tier === 'tier2') return 'سطح 2';
  if (tier === 'tier1') return 'سطح 1';
  return 'سطح نامشخص';
}

function isOwnOffer(offer: any): boolean {
  if (typeof offer?.is_own_offer === 'boolean') {
    return offer.is_own_offer;
  }
  return props.currentUserId ? offer.user_id === props.currentUserId : false;
}

function createMutationIdempotencyKey(prefix: string): string {
  const randomPart = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `${prefix}:${randomPart}`.slice(0, 64);
}

function tradeKeyFor(offerId: number, quantity: number): string {
  return `${offerId}:${quantity}`;
}

function currentTradeIntentStorageKey(): string | null {
  const userId = Number(props.currentUserId);
  if (!Number.isInteger(userId) || userId <= 0) return null;
  return `${TRADE_INTENT_STORAGE_PREFIX}:user:${userId}`;
}

function isStoredTradeIntent(value: unknown): value is TradeIntentState {
  if (!value || typeof value !== 'object') return false;
  const intent = value as Partial<TradeIntentState>;
  return intent.version === 1
    && Number.isInteger(intent.offerId)
    && Number(intent.offerId) > 0
    && Number.isInteger(intent.quantity)
    && Number(intent.quantity) > 0
    && typeof intent.idempotencyKey === 'string'
    && intent.idempotencyKey.startsWith('trade:')
    && intent.idempotencyKey.length <= 64
    && (intent.status === 'in_flight' || intent.status === 'uncertain')
    && Number.isFinite(intent.createdAt)
    && Number.isFinite(intent.updatedAt);
}

function persistTradeIntents() {
  if (!activeTradeIntentStorageKey || typeof window === 'undefined') return;
  try {
    if (tradeIntents.size === 0) {
      window.sessionStorage.removeItem(activeTradeIntentStorageKey);
      return;
    }
    window.sessionStorage.setItem(activeTradeIntentStorageKey, JSON.stringify([...tradeIntents.values()]));
  } catch {
    // The in-memory intent still protects retries while this component is mounted.
  }
}

function restoreTradeIntents() {
  tradeIntents.clear();
  activeTradeIntentStorageKey = currentTradeIntentStorageKey();
  if (!activeTradeIntentStorageKey || typeof window === 'undefined') return;

  try {
    const raw = window.sessionStorage.getItem(activeTradeIntentStorageKey);
    if (!raw) return;
    const stored = JSON.parse(raw);
    if (!Array.isArray(stored) || stored.some((item) => !isStoredTradeIntent(item))) {
      throw new Error('Invalid stored trade intent');
    }
    const restoredAt = Date.now();
    for (const item of stored) {
      const intent: TradeIntentState = {
        ...item,
        status: 'uncertain',
        updatedAt: restoredAt,
      };
      tradeIntents.set(tradeKeyFor(intent.offerId, intent.quantity), intent);
    }
    persistTradeIntents();
  } catch {
    tradeIntents.clear();
    try {
      window.sessionStorage.removeItem(activeTradeIntentStorageKey);
    } catch {
      // Ignore unavailable browser storage.
    }
  }
}

function getTradeIntent(offerId: number, quantity: number): TradeIntentState {
  const key = tradeKeyFor(offerId, quantity);
  const existing = tradeIntents.get(key);
  if (existing) return existing;

  const createdAt = Date.now();
  const next: TradeIntentState = {
    version: 1,
    offerId,
    quantity,
    idempotencyKey: createMutationIdempotencyKey('trade'),
    status: 'uncertain',
    createdAt,
    updatedAt: createdAt,
  };
  tradeIntents.set(key, next);
  persistTradeIntents();
  return next;
}

function setTradeIntentStatus(intent: TradeIntentState, status: TradeIntentStatus) {
  intent.status = status;
  intent.updatedAt = Date.now();
  tradeIntents.set(tradeKeyFor(intent.offerId, intent.quantity), intent);
  persistTradeIntents();
}

function clearTradeIntent(intent: TradeIntentState) {
  tradeIntents.delete(tradeKeyFor(intent.offerId, intent.quantity));
  persistTradeIntents();
}

function hasConflictingTradeIntent(offerId: number, quantity: number): boolean {
  return [...tradeIntents.values()].some((intent) => (
    intent.offerId === offerId && intent.quantity !== quantity
  ));
}

function isAmbiguousTradeResponse(response: Response): boolean {
  const status = Number(response?.status);
  return Number.isFinite(status) && (status >= 500 || status === 408 || status === 425 || status === 429);
}

function showTradeError(message: string) {
  if (!componentActive) return;
  tradeError.value = message;
  if (tradeErrorTimeout) clearTimeout(tradeErrorTimeout);
  tradeErrorTimeout = setTimeout(() => {
    if (componentActive) tradeError.value = '';
  }, 5000);
}

function isRetryableMutationError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || '');
  const errorName = error && typeof error === 'object' && 'name' in error
    ? String((error as { name?: unknown }).name || '')
    : '';
  const normalized = message.toLowerCase();
  return message === 'NetworkError'
    || errorName === 'AbortError'
    || normalized.includes('network')
    || normalized.includes('failed to fetch')
    || normalized.includes('load failed')
    || normalized.includes('timeout')
    || normalized.includes('سرور در دسترس نیست');
}

async function readMarketMutationErrorMessage(response: Response, payload: any, fallbackMessage: string): Promise<string> {
  const error = await createHttpErrorFromResponse(response, {
    surface: 'market',
    scope: 'action',
    operation: 'submit',
    userInitiated: true,
    fallbackMessage,
  }, payload);
  return getUserFacingErrorMessage(error, {
    surface: 'market',
    scope: 'action',
    operation: 'submit',
    userInitiated: true,
    fallbackMessage,
  });
}

// --- Trade execution with double-tap confirm ---
function handleLotClick(offerId: number, amount: number) {
  if (tradingOfferId.value !== null) return;
  const key = `${offerId}:${amount}`;
  
  if (pendingConfirm.value === key) {
    // Second tap — execute trade
    executeTrade(offerId, amount);
    pendingConfirm.value = null;
    if (confirmTimeout) clearTimeout(confirmTimeout);
  } else {
    // First tap — set pending
    pendingConfirm.value = key;
    if (confirmTimeout) clearTimeout(confirmTimeout);
    confirmTimeout = setTimeout(() => {
      pendingConfirm.value = null;
    }, 3000); // 3 seconds to confirm
  }
}

function isPending(offerId: number, amount: number): boolean {
  return pendingConfirm.value === `${offerId}:${amount}`;
}

function buildOfferSignature(offer: any | null): string | null {
  if (!offer) return null;
  const availableLots = getLotButtons(offer);
  const remaining = getOfferRemainingQuantity(offer);
  return [offer.status || '', remaining, availableLots.join(','), offer.expires_at_ts ?? ''].join('|');
}

function createTradeSuggestionState(data: any, fallbackOffer?: any): TradeLotSuggestionState {
  const sourceOffer = fallbackOffer || (Array.isArray(props.offers) ? props.offers.find((offer: any) => offer.id === (data.offer_id || 0)) : null);
  return {
    title: data.title || 'پیشنهاد معامله',
    introText: data.intro_text || data.detail || 'بخش انتخابی شما دیگر در دسترس نیست.',
    offerId: data.offer_id || sourceOffer?.id || 0,
    offerType: data.offer_type || sourceOffer?.offer_type || '',
    offerTypeLabel: data.offer_type_label || ((data.offer_type || sourceOffer?.offer_type) === 'buy' ? 'خرید' : 'فروش'),
    settlementType: normalizeSettlementType(data.settlement_type ?? sourceOffer?.settlement_type),
    settlementTypeLabel: data.settlement_type_label || offerSettlementLabel(data.settlement_type ?? sourceOffer?.settlement_type),
    commodityName: data.commodity_name || sourceOffer?.commodity_name || 'کالا',
    price: Number(data.price ?? getDisplayedOfferPrice(sourceOffer) ?? 0),
    remainingQuantity: data?.remaining_quantity !== null && data?.remaining_quantity !== undefined
      ? (getFiniteNumber(data.remaining_quantity) ?? 0)
      : getOfferRemainingQuantity(sourceOffer),
    lotSummary: data.lot_summary || (Array.isArray(data.available_lots) ? formatLotSummary(data.available_lots) : ''),
    availableLots: Array.isArray(data.available_lots) ? data.available_lots : [],
    expiresAtTs: sourceOffer?.expires_at_ts ?? null,
    sourceSignature: buildOfferSignature(sourceOffer),
  };
}

function syncTradeSuggestionFromOffers() {
  if (!tradeSuggestion.value) return;
  const sourceOffer = Array.isArray(props.offers)
    ? props.offers.find((offer: any) => offer.id === tradeSuggestion.value?.offerId)
    : null;
  const currentSourceSignature = buildOfferSignature(sourceOffer);

  if (currentSourceSignature === tradeSuggestion.value.sourceSignature) {
    return;
  }

  if (!sourceOffer) {
    closeTradeSuggestion();
    return;
  }

  const expired = !!sourceOffer.expires_at_ts && sourceOffer.expires_at_ts <= now.value;
  const remaining = getOfferRemainingQuantity(sourceOffer);
  const availableLots = getLotButtons(sourceOffer);

  if (expired || sourceOffer.status !== 'active' || remaining <= 0 || availableLots.length === 0) {
    closeTradeSuggestion();
    return;
  }

  tradeSuggestion.value = {
    ...tradeSuggestion.value,
    offerType: sourceOffer.offer_type || tradeSuggestion.value.offerType,
    offerTypeLabel: sourceOffer.offer_type === 'buy' ? 'خرید' : 'فروش',
    settlementType: normalizeSettlementType(sourceOffer.settlement_type),
    settlementTypeLabel: offerSettlementLabel(sourceOffer.settlement_type),
    commodityName: sourceOffer.commodity_name || tradeSuggestion.value.commodityName,
    price: getDisplayedOfferPrice(sourceOffer) || tradeSuggestion.value.price,
    remainingQuantity: remaining,
    lotSummary: formatLotSummary(availableLots),
    availableLots,
    expiresAtTs: sourceOffer.expires_at_ts ?? null,
    sourceSignature: currentSourceSignature,
  };
}

watch(() => props.offers, () => {
  syncTradeSuggestionFromOffers();
}, { deep: true });
watch(() => props.currentUserId, () => {
  restoreTradeIntents();
}, { immediate: true });
watch(now, () => {
  if (tradeSuggestion.value?.expiresAtTs && tradeSuggestion.value.expiresAtTs <= now.value) {
    closeTradeSuggestion();
  }
});

async function executeTrade(offerId: number, quantity: number) {
  if (tradingOfferId.value !== null) return;
  if (hasConflictingTradeIntent(offerId, quantity)) {
    showTradeError(CONFLICTING_TRADE_INTENT_MESSAGE);
    return;
  }

  const intent = getTradeIntent(offerId, quantity);
  const executionStorageKey = activeTradeIntentStorageKey;
  tradingOfferId.value = offerId;
  tradingAmount.value = quantity;
  tradeError.value = '';
  setTradeIntentStatus(intent, 'in_flight');
  
  try {
    const response = await apiFetch('/api/trades/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        offer_id: intent.offerId,
        quantity: intent.quantity,
        idempotency_key: intent.idempotencyKey,
      }),
      retryNetwork: false,
    });
    
    let data: any = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (!componentActive || activeTradeIntentStorageKey !== executionStorageKey) return;

    if (response.ok) {
      tradeSuggestion.value = null;
      clearTradeIntent(intent);
      if (componentActive) emit('trade-completed');
    } else {
      if (data?.error_code === 'TRADE_LOT_UNAVAILABLE' && Array.isArray(data.available_lots) && data.available_lots.length > 0) {
        tradeSuggestion.value = createTradeSuggestionState(data);
        clearTradeIntent(intent);
        return;
      }
      if (isAmbiguousTradeResponse(response as Response)) {
        setTradeIntentStatus(intent, 'uncertain');
        showTradeError(AMBIGUOUS_TRADE_MESSAGE);
        return;
      }
      clearTradeIntent(intent);
      showTradeError(await readMarketMutationErrorMessage(response as Response, data, 'خطا در انجام معامله'));
    }
  } catch (e: any) {
    if (!componentActive || activeTradeIntentStorageKey !== executionStorageKey) return;
    setTradeIntentStatus(intent, 'uncertain');
    showTradeError(isRetryableMutationError(e)
      ? AMBIGUOUS_TRADE_MESSAGE
      : getUserFacingErrorMessage(e, {
        surface: 'market',
        scope: 'action',
        operation: 'submit',
        userInitiated: true,
        fallbackMessage: AMBIGUOUS_TRADE_MESSAGE,
      }));
  } finally {
    if (componentActive) {
      tradingOfferId.value = null;
      tradingAmount.value = null;
    }
  }
}

function closeTradeSuggestion() {
  tradeSuggestion.value = null;
}

async function cancelOwnOffer(offerId: number) {
  if (cancelingOfferId.value !== null) return;
  cancelingOfferId.value = offerId;
  tradeError.value = '';
  try {
    const response = await apiFetch(`/api/offers/${offerId}`, { method: 'DELETE', retryNetwork: false });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      tradeError.value = await readMarketMutationErrorMessage(response as Response, data, 'خطا در منقضی کردن لفظ');
      setTimeout(() => tradeError.value = '', 5000);
    } else {
      emit('trade-completed');
    }
  } catch (e: any) {
    tradeError.value = isRetryableMutationError(e)
      ? 'ارتباط با سرور قطع شد. وضعیت لفظ را چند لحظه بعد بررسی کنید.'
      : getUserFacingErrorMessage(e, {
          surface: 'market',
          scope: 'action',
          operation: 'delete',
          userInitiated: true,
          fallbackMessage: 'خطا در ارتباط با سرور',
        });
    setTimeout(() => tradeError.value = '', 5000);
  } finally {
    cancelingOfferId.value = null;
  }
}
</script>

<template>
  <TradeLotSuggestionAlert
    :show="!!tradeSuggestion"
    :title="tradeSuggestion?.title || ''"
    :intro-text="tradeSuggestion?.introText || ''"
    :offer-type="tradeSuggestion?.offerType || ''"
    :offer-type-label="tradeSuggestion?.offerTypeLabel || ''"
    :settlement-type-label="tradeSuggestion?.settlementTypeLabel || ''"
    :commodity-name="tradeSuggestion?.commodityName || ''"
    :price="tradeSuggestion?.price || 0"
    :remaining-quantity="tradeSuggestion?.remainingQuantity || 0"
    :lot-summary="tradeSuggestion?.lotSummary || ''"
    :available-lots="tradeSuggestion?.availableLots || []"
    :busy="tradingOfferId === tradeSuggestion?.offerId"
    :busy-amount="tradingAmount"
    :auto-close-seconds="15"
    @close="closeTradeSuggestion"
    @select-lot="(amount) => tradeSuggestion && executeTrade(tradeSuggestion.offerId, amount)"
  />
    <!-- Trade Error Toast -->
    <transition name="fade">
      <AppOfferTradeErrorToast v-if="tradeError" :message="tradeError" />
    </transition>

    <AppOfferLoadingSkeletonList v-if="loading" :count="limit || 5" />

    <AppOfferEmptyState v-else-if="filteredOffers.length === 0" />

    <div v-else class="offers-list">
      <AppOfferCard
        v-for="offer in filteredOffers" 
        :key="offer.id"
        :timer-critical="!isReadOnlyOffer(offer) && isCritical(offer)"
        :has-timer="!isReadOnlyOffer(offer) && hasTimer(offer)"
        :history="isReadOnlyOffer(offer)"
        :expired="isExpiredOffer(offer)"
        :traded="isTradedHistoryOffer(offer)"
        :timer-style="cardTimerStyle(offer)"
      >
        <div class="offer-card-inner" :class="[offer.offer_type]">
          <AppOfferHistoryStamp
            v-if="getHistoryStampLabel(offer)"
            :label="getHistoryStampLabel(offer)"
            :traded="isTradedHistoryOffer(offer)"
          />

          <!-- Header: role badge + time -->
          <div class="offer-header">
            <div class="offer-classification">
              <AppOfferSideBadge :side="offer.offer_type" />
              <AppSettlementBadge
                class="offer-settlement"
                :settlement-type="offer.settlement_type"
              />
            </div>
            <span class="offer-time">{{ timeAgo(offer.created_at) }}</span>
          </div>

          <!-- Body: commodity, remaining, price in one row -->
          <div class="offer-body">
            <div class="offer-main">
              <span class="commodity">{{ offer.commodity_name }}</span>
              <AppOfferQuantityBadge>{{ getOfferQuantityLabel(offer) }}</AppOfferQuantityBadge>
              <AppOfferPrice :value="getDisplayedOfferPrice(offer)" />
            </div>
            <AppOfferCustomerContext
              v-if="offer.customer_badge_visible"
              :management-name="offer.customer_management_name"
              :tier-label="offer.customer_tier ? getCustomerTierLabel(offer.customer_tier) : null"
            />
            <p v-if="offer.notes" class="offer-notes">
              توضیحات: {{ offer.notes }}
            </p>
          </div>

          <!-- Footer: lot buttons or own offer -->
          <div v-if="!isReadOnlyOffer(offer)" class="offer-footer">
            <div v-if="!isOwnOffer(offer) && (offer.remaining_quantity ?? offer.quantity) > 0" class="trade-buttons">
              <AppTradeActionButton
                v-for="amount in getLotButtons(offer)"
                :key="amount"
                :side="offer.offer_type"
                :pending="isPending(offer.id, amount)"
                :busy="tradingOfferId === offer.id"
                :disabled="tradingOfferId === offer.id"
                @click="handleLotClick(offer.id, amount)"
              >
                <Loader2 v-if="tradingOfferId === offer.id && tradingAmount === amount" class="inline animate-spin mr-1" :size="14" />
                <span v-if="isPending(offer.id, amount)">تایید {{ amount }} عدد؟</span>
                <span v-else>{{ amount }} عدد</span>
              </AppTradeActionButton>
            </div>
            <div v-else-if="isOwnOffer(offer)" class="own-offer-actions">
              <button 
                @click="cancelOwnOffer(offer.id)" 
                :disabled="cancelingOfferId === offer.id"
                class="cancel-own-offer-btn"
              >
                <Loader2 v-if="cancelingOfferId === offer.id" class="inline animate-spin mr-1" :size="14" />
                منقضی کردن لفظ
              </button>
            </div>
          </div>

        </div><!-- /offer-card-inner -->
      </AppOfferCard>
      <div v-if="canLoadExpired && (hasMoreExpired || expiredLoading)" class="expired-load-more-row">
        <button
          type="button"
          class="expired-load-more-btn"
          :disabled="expiredLoading"
          @click="emit('load-more-expired')"
        >
          <Loader2 v-if="expiredLoading" class="inline animate-spin" :size="14" />
          <span>{{ expiredLoading ? 'در حال دریافت' : 'نمایش بیشتر' }}</span>
        </button>
      </div>
    </div>
</template>

<!-- Global @property (must NOT be scoped) -->
<style>
@property --t-pct {
  syntax: '<number>';
  inherits: true;
  initial-value: 100;
}
</style>

<style scoped>
/* ══════════════════════════════════════
   Offer Card — Mini-App-style layout
   ══════════════════════════════════════ */

/* ── Offers list ── */
.offers-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* ── Card wrapper (timer border ring) ── */
.offer-card-wrap {
  position: relative;
  border-radius: var(--ds-radius-md);
  border: 3px solid rgba(229, 231, 235, 0.45);
}

.offer-card-wrap.has-timer {
  border-color: transparent;
}

.offer-card-wrap.is-history {
  border-color: transparent;
}

.offer-card-wrap.has-timer::before {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: var(--ds-radius-md);
  padding: 3px;
  background: conic-gradient(
    from 0deg at 50% 50%,
    hsl(calc(var(--t-pct) * 1.6) 82% 45%)          calc(var(--t-pct) * 1% - 1%),
    hsl(calc(var(--t-pct) * 1.6) 82% 45% / 0.12)   calc(var(--t-pct) * 1%),
    rgba(229, 231, 235, 0.35)                        calc(var(--t-pct) * 1%)
  );
  -webkit-mask:
    linear-gradient(#fff 0 0) content-box,
    linear-gradient(#fff 0 0);
  mask:
    linear-gradient(#fff 0 0) content-box,
    linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
          mask-composite: exclude;
  pointer-events: none;
  z-index: 1;
}

.offer-card-wrap.timer-critical::before {
  animation: ring-pulse 1.2s ease-in-out infinite;
}

@keyframes ring-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.4; }
}

/* ── Inner card ── */
.offer-card-inner {
  position: relative;
  background: var(--ds-bg-card);
  border-radius: calc(var(--ds-radius-md) - 3px);
  padding: 10px 11px 9px;
  z-index: 0;
  overflow: hidden;
}

.offer-card-wrap.is-history .offer-card-inner {
  background: var(--ds-bg-surface);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
  padding-top: 34px;
}

.offer-card-wrap.is-expired .offer-card-inner {
  background:
    linear-gradient(90deg, rgba(71, 85, 105, 0.24), rgba(148, 163, 184, 0.15) 42%, rgba(241, 245, 249, 0.92) 100%),
    var(--ds-bg-surface);
  box-shadow: 0 1px 3px rgba(71, 85, 105, 0.05);
}

.offer-card-wrap.is-traded .offer-card-inner {
  background:
    linear-gradient(90deg, rgba(13, 148, 136, 0.28), rgba(20, 184, 166, 0.16) 42%, rgba(236, 253, 245, 0.94) 100%),
    var(--ds-bg-surface);
  box-shadow: 0 1px 3px rgba(15, 118, 110, 0.07);
}

.offer-card-wrap.is-expired .offer-card-inner::before,
.offer-card-wrap.is-traded .offer-card-inner::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 4px;
  pointer-events: none;
}

.offer-card-wrap.is-expired .offer-card-inner::before {
  background: rgba(100, 116, 139, 0.52);
}

.offer-card-wrap.is-traded .offer-card-inner::before {
  background: rgba(13, 148, 136, 0.62);
}

.offer-card-wrap.is-expired .offer-header,
.offer-card-wrap.is-expired .offer-body {
  opacity: 0.58;
}

.offer-card-wrap.is-traded .offer-header,
.offer-card-wrap.is-traded .offer-body {
  opacity: 0.92;
}

.offer-card-wrap.is-history .price,
.offer-card-wrap.is-history .commodity {
  color: var(--ds-text-secondary);
}

.history-ribbon {
  position: absolute;
  top: 8px;
  left: 50%;
  z-index: 2;
  min-width: 74px;
  transform: translateX(-50%) rotate(-7deg);
  transform-origin: center;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 900;
  line-height: 1.1;
  text-align: center;
  letter-spacing: 0;
  padding: 4px 10px 3px;
  box-shadow:
    inset 0 0 0 1px rgba(185, 28, 28, 0.22),
    0 1px 0 rgba(185, 28, 28, 0.08);
  opacity: 0.9;
  pointer-events: none;
}

.expired-ribbon {
  background: rgba(239, 68, 68, 0.06);
  color: var(--ds-danger-700);
  border: 2px solid rgba(185, 28, 28, 0.72);
}

.traded-ribbon {
  min-width: 92px;
  background: rgba(20, 184, 166, 0.08);
  color: #0f766e;
  border: 2px solid rgba(15, 118, 110, 0.72);
  box-shadow:
    inset 0 0 0 1px rgba(15, 118, 110, 0.22),
    0 1px 0 rgba(15, 118, 110, 0.08);
}

.history-ribbon::before,
.history-ribbon::after {
  content: '';
  position: absolute;
  inset: 2px;
  border-radius: 3px;
  pointer-events: none;
}

.expired-ribbon::before {
  border: 1px solid rgba(185, 28, 28, 0.32);
}

.traded-ribbon::before {
  border: 1px solid rgba(15, 118, 110, 0.32);
}

.history-ribbon::after {
  inset: -1px 5px;
  transform: rotate(2deg);
}

.expired-ribbon::after {
  border-color: rgba(185, 28, 28, 0.14);
}

.traded-ribbon::after {
  border-color: rgba(15, 118, 110, 0.14);
}

/* Subtle outer shadow for depth */
.offer-card-inner.buy {
  box-shadow: 0 1px 4px 0 var(--ds-trade-buy-shadow), 0 1px 2px 0 rgba(0,0,0,0.04);
}

.offer-card-inner.sell {
  box-shadow: 0 1px 4px 0 var(--ds-trade-sell-shadow), 0 1px 2px 0 rgba(0,0,0,0.04);
}

/* ── Header ── */
.offer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 6px;
}

.offer-classification {
  min-width: 0;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.role-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
}

.role-badge.buy {
  background: var(--ds-success-100);
  color: var(--ds-trade-buy-text);
}

.role-badge.sell {
  background: var(--ds-danger-100);
  color: var(--ds-danger-600);
}

.offer-time {
  font-size: 10px;
  color: var(--ds-text-placeholder);
}

/* ── Body ── */
.offer-body {
  margin-bottom: 7px;
}

.offer-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.commodity {
  font-weight: 700;
  font-size: 13px;
  color: var(--ds-text-primary);
}

.quantity-badge {
  background: var(--ds-bg-hover);
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--ds-text-secondary);
}

.price {
  font-weight: 800;
  font-size: 13px;
  color: var(--ds-primary-500);
}

.offer-notes {
  margin-top: 5px;
  font-size: 11.5px;
  line-height: 1.45;
  color: var(--ds-text-muted);
  background: var(--ds-bg-inset);
  padding: 4px 8px;
  border-radius: 6px;
}

/* ── Footer ── */
.offer-footer {
  display: flex;
  align-items: center;
}

.trade-buttons {
  display: flex;
  flex-wrap: nowrap;
  overflow-x: auto;
  scrollbar-width: none;
  gap: 5px;
  width: 100%;
}

.trade-buttons::-webkit-scrollbar {
  display: none;
}

.trade-btn {
  padding: 6px 10px;
  color: white;
  border: none;
  border-radius: var(--ds-radius-sm);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  flex: 1 1 auto;
  min-width: 50px;
  max-width: 120px;
  text-align: center;
  transition: all 0.2s ease;
  letter-spacing: 0.02em;
}

.trade-btn:active {
  transform: scale(0.96);
}

.trade-btn.buy {
  background: linear-gradient(135deg, var(--ds-success-500), var(--ds-success-600));
}

.trade-btn.sell {
  background: linear-gradient(135deg, var(--ds-danger-500), var(--ds-danger-600));
}

.trade-btn.pending {
  background: var(--ds-primary-500);
  animation: pulse-soft 1s ease-in-out infinite;
}

.trade-btn.busy {
  opacity: 0.6;
  cursor: wait;
}

.trade-btn:disabled {
  opacity: 0.6;
  cursor: wait;
}

/* ── Own offer ── */
.own-offer-actions {
  width: 100%;
  display: flex;
}

.cancel-own-offer-btn {
  width: 100%;
  padding: 6px 10px;
  background: var(--ds-danger-50);
  color: var(--ds-danger-600);
  border: 1px solid var(--ds-danger-200);
  border-radius: var(--ds-radius-sm);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.cancel-own-offer-btn:hover {
  background: var(--ds-danger-100);
}

.cancel-own-offer-btn:disabled {
  opacity: 0.6;
  cursor: wait;
}

.expired-load-more-row {
  display: flex;
  justify-content: center;
  padding: 2px 0 6px;
}

.expired-load-more-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 34px;
  padding: 7px 14px;
  border-radius: 999px;
  border: 1px solid var(--ds-border-subtle);
  background: var(--ds-bg-card);
  color: var(--ds-text-secondary);
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}

.expired-load-more-btn:disabled {
  opacity: 0.68;
  cursor: wait;
}

/* ── Soft pulse for confirm state ── */
@keyframes pulse-soft {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.85; transform: scale(0.98); }
}

/* ── Toasts ── */
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
