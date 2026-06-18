<script setup lang="ts">

import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { Search, Loader2 } from 'lucide-vue-next';
import { apiFetch } from '../utils/auth';
import { createHttpErrorFromResponse, getUserFacingErrorMessage } from '../utils/httpErrorPolicy';
import TradeLotSuggestionAlert from './TradeLotSuggestionAlert.vue';

interface TradeLotSuggestionState {
  title: string;
  introText: string;
  offerId: number;
  offerType: 'buy' | 'sell' | '';
  offerTypeLabel: string;
  commodityName: string;
  price: number;
  remainingQuantity: number;
  lotSummary: string;
  availableLots: number[];
  expiresAtTs?: number | null;
  sourceSignature?: string | null;
}

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
const tradeIdempotencyKeys = new Map<string, string>();

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
  animationFrameId = requestAnimationFrame(tick)
})

onUnmounted(() => {
  if (animationFrameId) cancelAnimationFrame(animationFrameId)
  if (confirmTimeout) clearTimeout(confirmTimeout)
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
  return offer?.status === 'expired'
}

// --- Keep active offers live-filtered, but allow read-only expired history ---
const filteredOffers = computed(() => {
  const nowSec = now.value
  const source = Array.isArray(props.offers) ? props.offers : []
  const visible = source.filter(o => isExpiredOffer(o) || !o.expires_at_ts || o.expires_at_ts > nowSec)
  return props.limit ? visible.slice(0, props.limit) : visible
})

function timeAgo(dateString: string) {
    if (!dateString) return '';
    return dateString;
}

// --- Lot buttons logic (matching Telegram channel) ---
function getLotButtons(offer: any): number[] {
  const remaining = offer.remaining_quantity || offer.quantity;
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

function getTradeIdempotencyKey(offerId: number, quantity: number): string {
  const key = tradeKeyFor(offerId, quantity);
  const existing = tradeIdempotencyKeys.get(key);
  if (existing) return existing;
  const next = createMutationIdempotencyKey('trade');
  tradeIdempotencyKeys.set(key, next);
  return next;
}

function clearTradeIdempotencyKey(offerId: number, quantity: number) {
  tradeIdempotencyKeys.delete(tradeKeyFor(offerId, quantity));
}

function pruneTradeIdempotencyKeys() {
  const activeOfferIds = new Set((Array.isArray(props.offers) ? props.offers : []).map((offer: any) => Number(offer.id)));
  for (const key of tradeIdempotencyKeys.keys()) {
    const offerId = Number(key.split(':')[0]);
    if (!activeOfferIds.has(offerId)) {
      tradeIdempotencyKeys.delete(key);
    }
  }
}

function isRetryableMutationError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || '');
  const normalized = message.toLowerCase();
  return message === 'NetworkError'
    || normalized.includes('network')
    || normalized.includes('failed to fetch')
    || normalized.includes('load failed')
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
  const remaining = Number(offer.remaining_quantity ?? offer.quantity ?? 0);
  return [offer.status || '', remaining, availableLots.join(','), offer.expires_at_ts ?? ''].join('|');
}

function createTradeSuggestionState(data: any, fallbackOffer?: any): TradeLotSuggestionState {
  const sourceOffer = fallbackOffer || (Array.isArray(props.offers) ? props.offers.find((offer: any) => offer.id === (data.offer_id || 0)) : null);
  return {
    title: data.title || 'پیشنهاد معامله',
    introText: data.intro_text || data.detail || 'لات انتخابی شما دیگر در دسترس نیست.',
    offerId: data.offer_id || sourceOffer?.id || 0,
    offerType: data.offer_type || sourceOffer?.offer_type || '',
    offerTypeLabel: data.offer_type_label || ((data.offer_type || sourceOffer?.offer_type) === 'buy' ? 'خرید' : 'فروش'),
    commodityName: data.commodity_name || sourceOffer?.commodity_name || 'کالا',
    price: Number(data.price ?? getDisplayedOfferPrice(sourceOffer) ?? 0),
    remainingQuantity: Number(data.remaining_quantity || sourceOffer?.remaining_quantity || sourceOffer?.quantity || 0),
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
  const remaining = Number(sourceOffer.remaining_quantity ?? sourceOffer.quantity ?? 0);
  const availableLots = getLotButtons(sourceOffer);

  if (expired || sourceOffer.status !== 'active' || remaining <= 0 || availableLots.length === 0) {
    closeTradeSuggestion();
    return;
  }

  tradeSuggestion.value = {
    ...tradeSuggestion.value,
    offerType: sourceOffer.offer_type || tradeSuggestion.value.offerType,
    offerTypeLabel: sourceOffer.offer_type === 'buy' ? 'خرید' : 'فروش',
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
  pruneTradeIdempotencyKeys();
}, { deep: true });
watch(now, () => {
  if (tradeSuggestion.value?.expiresAtTs && tradeSuggestion.value.expiresAtTs <= now.value) {
    closeTradeSuggestion();
  }
});

async function executeTrade(offerId: number, quantity: number) {
  if (tradingOfferId.value !== null) return;
  tradingOfferId.value = offerId;
  tradingAmount.value = quantity;
  tradeError.value = '';
  const idempotencyKey = getTradeIdempotencyKey(offerId, quantity);
  
  try {
    const response = await apiFetch('/api/trades/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offer_id: offerId, quantity, idempotency_key: idempotencyKey }),
      retryNetwork: false,
    });
    
    let data: any = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (response.ok) {
      tradeSuggestion.value = null;
      clearTradeIdempotencyKey(offerId, quantity);
      emit('trade-completed');
    } else {
      if (data?.error_code === 'TRADE_LOT_UNAVAILABLE' && Array.isArray(data.available_lots) && data.available_lots.length > 0) {
        tradeSuggestion.value = createTradeSuggestionState(data);
        clearTradeIdempotencyKey(offerId, quantity);
        return;
      }
      tradeError.value = await readMarketMutationErrorMessage(response as Response, data, 'خطا در انجام معامله');
      clearTradeIdempotencyKey(offerId, quantity);
      setTimeout(() => tradeError.value = '', 5000);
    }
  } catch (e: any) {
    tradeError.value = isRetryableMutationError(e)
      ? 'ارتباط با سرور قطع شد. اگر معامله ثبت شده باشد، تکرار همین درخواست معامله دوم نمی‌سازد.'
      : getUserFacingErrorMessage(e, {
          surface: 'market',
          scope: 'action',
          operation: 'submit',
          userInitiated: true,
          fallbackMessage: 'خطا در ارتباط با سرور',
        });
    if (!isRetryableMutationError(e)) {
      clearTradeIdempotencyKey(offerId, quantity);
    }
    setTimeout(() => tradeError.value = '', 5000);
  } finally {
    tradingOfferId.value = null;
    tradingAmount.value = null;
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
        <div v-if="tradeError" class="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-gradient-to-r from-red-500 to-red-600 text-white px-5 py-2.5 rounded-2xl text-sm font-bold shadow-lg shadow-red-500/25">
            {{ tradeError }}
        </div>
    </transition>

    <div v-if="loading" class="offers-list">
       <div v-for="i in (limit || 5)" :key="i" class="skeleton-card"></div>
    </div>

    <div v-else-if="filteredOffers.length === 0" class="empty-state">
       <div class="empty-icon">
          <Search :size="28" />
       </div>
       <p>هیچ لفظ فعالی یافت نشد.</p>
    </div>

    <div v-else class="offers-list">
      <div 
        v-for="offer in filteredOffers" 
        :key="offer.id"
        class="offer-card-wrap"
        :class="{
          'timer-critical': !isExpiredOffer(offer) && isCritical(offer),
          'has-timer': !isExpiredOffer(offer) && hasTimer(offer),
          'is-expired': isExpiredOffer(offer),
        }"
        :style="cardTimerStyle(offer)"
      >
        <div class="offer-card-inner" :class="[offer.offer_type]">
          <span v-if="isExpiredOffer(offer)" class="expired-ribbon">منقضی</span>

          <!-- Header: role badge + time -->
          <div class="offer-header">
            <span class="role-badge" :class="offer.offer_type">
              {{ offer.offer_type === 'buy' ? 'خرید' : 'فروش' }}
            </span>
            <span class="offer-time">{{ timeAgo(offer.created_at) }}</span>
          </div>

          <!-- Body: commodity, remaining, price in one row -->
          <div class="offer-body">
            <div class="offer-main">
              <span class="commodity">{{ offer.commodity_name }}</span>
              <span class="quantity-badge">{{ offer.remaining_quantity }} عدد</span>
              <span class="price">{{ getDisplayedOfferPrice(offer) ? getDisplayedOfferPrice(offer).toLocaleString() : '---' }}</span>
            </div>
            <div v-if="offer.customer_badge_visible" class="customer-context-row">
              <span class="customer-context-badge">مشتری</span>
              <span v-if="offer.customer_management_name" class="customer-context-name">{{ offer.customer_management_name }}</span>
              <span v-if="offer.customer_tier" class="customer-context-tier">{{ getCustomerTierLabel(offer.customer_tier) }}</span>
            </div>
            <p v-if="offer.notes" class="offer-notes">
              توضیحات: {{ offer.notes }}
            </p>
          </div>

          <!-- Footer: lot buttons or own offer -->
          <div v-if="!isExpiredOffer(offer)" class="offer-footer">
            <div v-if="!isOwnOffer(offer) && (offer.remaining_quantity ?? offer.quantity) > 0" class="trade-buttons">
              <button
                v-for="amount in getLotButtons(offer)"
                :key="amount"
                @click="handleLotClick(offer.id, amount)"
                :disabled="tradingOfferId === offer.id"
                class="trade-btn"
                :class="[
                  isPending(offer.id, amount)
                    ? 'pending'
                    : offer.offer_type,
                  tradingOfferId === offer.id ? 'busy' : ''
                ]"
              >
                <Loader2 v-if="tradingOfferId === offer.id && tradingAmount === amount" class="inline animate-spin mr-1" :size="14" />
                <span v-if="isPending(offer.id, amount)">تایید {{ amount }} عدد؟</span>
                <span v-else>{{ amount }} عدد</span>
              </button>
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
      </div>
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

/* ── Loading skeleton ── */
.skeleton-card {
  height: 78px;
  background: rgba(255,255,255,0.5);
  border-radius: var(--ds-radius-md);
  border: 1px solid var(--ds-border-accent);
  animation: pulse-skeleton 1.5s ease-in-out infinite;
}
@keyframes pulse-skeleton {
  0%, 100% { opacity: 0.6; }
  50%      { opacity: 1; }
}

/* ── Empty state ── */
.empty-state {
  text-align: center;
  padding: 24px 16px;
  color: var(--ds-text-placeholder);
  font-size: 0.76rem;
}
.empty-icon {
  width: 42px;
  height: 42px;
  background: var(--ds-primary-50);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 8px;
  color: var(--ds-primary-500);
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

.offer-card-wrap.is-expired {
  border-color: var(--ds-border-subtle);
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

.offer-card-wrap.is-expired .offer-card-inner {
  background: var(--ds-bg-surface);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
  padding-top: 34px;
}

.offer-card-wrap.is-expired .price,
.offer-card-wrap.is-expired .commodity {
  color: var(--ds-text-secondary);
}

.expired-ribbon {
  position: absolute;
  top: 8px;
  left: 50%;
  z-index: 2;
  min-width: 74px;
  transform: translateX(-50%) rotate(-7deg);
  transform-origin: center;
  background: rgba(239, 68, 68, 0.06);
  color: #b91c1c;
  border: 2px solid rgba(185, 28, 28, 0.72);
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

.expired-ribbon::before,
.expired-ribbon::after {
  content: '';
  position: absolute;
  inset: 2px;
  border: 1px solid rgba(185, 28, 28, 0.32);
  border-radius: 3px;
  pointer-events: none;
}

.expired-ribbon::after {
  inset: -1px 5px;
  border-color: rgba(185, 28, 28, 0.14);
  transform: rotate(2deg);
}

/* Subtle outer shadow for depth */
.offer-card-inner.buy {
  box-shadow: 0 1px 4px 0 rgba(16, 185, 129, 0.18), 0 1px 2px 0 rgba(0,0,0,0.04);
}

.offer-card-inner.sell {
  box-shadow: 0 1px 4px 0 rgba(239, 68, 68, 0.18), 0 1px 2px 0 rgba(0,0,0,0.04);
}

/* ── Header ── */
.offer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
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
  color: #16a34a;
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
}

.customer-context-row {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 5px;
  flex-wrap: wrap;
}

.customer-context-badge,
.customer-context-tier {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 10.5px;
  font-weight: 800;
  line-height: 1;
}

.customer-context-badge {
  color: #92400e;
  background: rgba(251, 191, 36, 0.2);
  border: 1px solid rgba(245, 158, 11, 0.35);
}

.customer-context-name {
  font-size: 11.5px;
  font-weight: 700;
  color: var(--ds-text-primary);
}

.customer-context-tier {
  color: #1d4ed8;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.22);
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
