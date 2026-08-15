<script setup lang="ts">

import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { Hourglass, Loader2 } from 'lucide-vue-next';
import { apiFetch } from '../utils/auth';
import { createHttpErrorFromResponse, getUserFacingErrorMessage } from '../utils/httpErrorPolicy';
import { offerSettlementLabel, normalizeSettlementType, type SettlementType } from '../utils/settlementType';
import TradeLotSuggestionAlert from './TradeLotSuggestionAlert.vue';
import {
  AppOfferCard,
  AppOfferCustomerContext,
  AppOfferEmptyState,
  AppErrorState,
  AppOfferHistoryStamp,
  AppOfferLoadingSkeletonList,
  AppOfferPrice,
  AppOfferQuantityBadge,
  AppSettlementBadge,
  AppOfferSideBadge,
  AppOfferTradeErrorToast,
  AppTradeActionButton,
} from './ui';
import {
  isActiveLifecycleVisible,
  isFinalTailPhase,
  isOvertimePhase,
  timerDeadlineTs,
} from '../utils/offerLifecycle';

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
  offerPublicId?: string | null;
  quantity: number;
  idempotencyKey: string;
  status: TradeIntentStatus;
  createdAt: number;
  updatedAt: number;
}

const TRADE_INTENT_STORAGE_PREFIX = 'market_trade_intents_v1';
const TRADE_CONTENTION_BUSY_CODE = 'TRADE_CONTENTION_BUSY';
const AMBIGUOUS_TRADE_MESSAGE = 'ارتباط با سرور قطع شد. اگر معامله ثبت شده باشد، تکرار همین درخواست معامله دوم نمی‌سازد.';
const CONFLICTING_TRADE_INTENT_MESSAGE = 'نتیجه درخواست قبلی این لفظ هنوز مشخص نیست. ابتدا همان درخواست را دوباره ارسال کنید.';

// Define Props
const props = withDefaults(defineProps<{
  offers: any[];
  loading: boolean;
  limit?: number;
  expiryMinutes?: number;
  currentUserId?: number;
  currentUserReady?: boolean;
  expiredLoading?: boolean;
  hasMoreExpired?: boolean;
  canLoadExpired?: boolean;
  activeLoading?: boolean;
  hasMoreActive?: boolean;
  activeLoadError?: string;
}>(), {
  currentUserReady: true,
});

const emit = defineEmits<{
  (e: 'trade-completed'): void;
  (e: 'load-more-active'): void;
  (e: 'retry-active'): void;
  (e: 'load-more-expired'): void;
}>();

// Trade execution state
const tradingOfferId = ref<number | null>(null);
const tradingAmount = ref<number | null>(null);
const tradeError = ref('');
const tradeSuggestion = ref<TradeLotSuggestionState | null>(null);
const cancelingOfferId = ref<number | null>(null);
const tradeIdentityReady = computed(() => {
  const currentUserId = Number(props.currentUserId);
  return props.currentUserReady !== false && Number.isInteger(currentUserId) && currentUserId > 0;
});
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

function clearPendingConfirm() {
  pendingConfirm.value = null
  if (confirmTimeout) {
    clearTimeout(confirmTimeout)
    confirmTimeout = null
  }
}

function pendingOfferId(): number | null {
  if (!pendingConfirm.value) return null
  const offerId = Number(pendingConfirm.value.split(':')[0])
  return Number.isInteger(offerId) && offerId > 0 ? offerId : null
}

function isDecisionFocus(offer: any): boolean {
  return pendingOfferId() === Number(offer?.id)
}

function pendingAmountFor(offer: any): number | null {
  if (!isDecisionFocus(offer) || !pendingConfirm.value) return null
  const amount = Number(pendingConfirm.value.split(':')[1])
  return Number.isInteger(amount) && amount > 0 ? amount : null
}

function offerSideLabel(offer: any): string {
  return offer?.offer_type === 'buy' ? 'خرید' : 'فروش'
}

function userActionLabel(offer: any): string {
  return offer?.offer_type === 'buy' ? 'فروش' : 'خرید'
}

function remainingFieldLabel(offer: any): string {
  return isTradedHistoryOffer(offer) ? 'مقدار' : 'باقی‌مانده'
}

function tradeButtonAriaLabel(offer: any, amount: number): string {
  const action = userActionLabel(offer)
  const side = offerSideLabel(offer)
  const commodity = offer?.commodity_name || 'کالا'
  if (isPending(offer.id, amount)) {
    return `تأیید نهایی اقدام شما: ${action} ${amount} عدد ${commodity} در برابر لفظ ${side} به قیمت ${getDisplayedOfferPrice(offer).toLocaleString()} تومان`
  }
  return `انتخاب مقدار ${amount} عدد برای اقدام شما: ${action} در برابر لفظ ${side} ${commodity}`
}

function expectedTradeResult(offer: any): string {
  const amount = pendingAmountFor(offer)
  if (amount == null) return ''
  return `ثبت ${userActionLabel(offer)} ${amount.toLocaleString()} عدد در برابر این لفظ ${offerSideLabel(offer)}`
}

function handlePendingEscape(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !pendingConfirm.value) return
  event.preventDefault()
  clearPendingConfirm()
}

onMounted(() => {
  componentActive = true;
  animationFrameId = requestAnimationFrame(tick)
  if (typeof document !== 'undefined') {
    document.addEventListener('keydown', handlePendingEscape)
  }
})

onUnmounted(() => {
  componentActive = false
  if (typeof document !== 'undefined') {
    document.removeEventListener('keydown', handlePendingEscape)
  }
  if (animationFrameId) cancelAnimationFrame(animationFrameId)
  if (confirmTimeout) clearTimeout(confirmTimeout)
  if (tradeErrorTimeout) clearTimeout(tradeErrorTimeout)
})

// --- Timer percent (server phase + timer_total_seconds; no client phase invention) ---
function timerTotalSeconds(offer: any): number {
  const authoritative = Number(offer?.timer_total_seconds)
  if (Number.isFinite(authoritative) && authoritative > 0) return authoritative
  return (props.expiryMinutes || 2) * 60
}

function getTimerPercent(offer: any): number {
  if (isFinalTailPhase(offer)) return 0
  const deadline = timerDeadlineTs(offer)
  if (deadline == null) return 100
  const remaining = deadline - now.value
  if (remaining <= 0) return 0
  const total = timerTotalSeconds(offer)
  return Math.min(Math.max((remaining / total) * 100, 0), 100)
}

function getDeadlineMeterPercent(offer: any): number {
  const remainingPercent = getTimerPercent(offer)
  return isOvertimePhase(offer) ? 100 - remainingPercent : remainingPercent
}

function cardTimerStyle(offer: any): Record<string, string> {
  if (isInteractionLocked(offer)) return {}
  if (isFinalTailPhase(offer)) return {}
  const deadline = timerDeadlineTs(offer)
  if (deadline == null) return {}
  const remainingPct = getTimerPercent(offer)
  const pct = getDeadlineMeterPercent(offer)
  return {
    '--t-pct': String(pct),
    '--t-ratio': String(pct / 100),
    '--t-color': timerColor(offer, remainingPct),
  }
}

function timerColor(offer: any, percent: number): string {
  const pct = Math.min(Math.max(percent, 0), 100)
  // Normal time moves continuously green → amber → red. Overtime is a
  // shorter amber → red window so its urgency never reads as a fresh offer.
  const hue = isOvertimePhase(offer)
    ? 4 + pct * 0.38
    : pct >= 50
      ? 42 + ((pct - 50) / 50) * 100
      : 4 + (pct / 50) * 38
  return `hsl(${hue.toFixed(2)} 76% 37%)`
}

function isCritical(offer: any): boolean {
  if (isFinalTailPhase(offer) || isInteractionLocked(offer)) return false
  return timerDeadlineTs(offer) != null && getTimerPercent(offer) < 15
}

function deadlinePhase(offer: any): 'normal' | 'overtime' | 'critical' {
  if (isOvertimePhase(offer)) return 'overtime'
  if (isCritical(offer)) return 'critical'
  return 'normal'
}

function formatDeadlineClock(offer: any): string {
  const deadline = timerDeadlineTs(offer)
  if (deadline == null) return ''
  const remaining = Math.max(0, Math.ceil(deadline - now.value))
  const minutes = Math.floor(remaining / 60)
  const seconds = remaining % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function deadlineLabel(offer: any): string {
  const clock = formatDeadlineClock(offer)
  if (isOvertimePhase(offer)) return clock ? `${clock} باقی‌مانده` : 'در حال محاسبه مهلت'
  return clock ? `مهلت اصلی · ${clock}` : 'مهلت اصلی'
}

function deadlineMeterAriaLabel(offer: any): string {
  if (!isOvertimePhase(offer)) return deadlineLabel(offer)
  const progress = Math.round(getDeadlineMeterPercent(offer))
  const clock = formatDeadlineClock(offer)
  return `وقت اضافه سپری‌شده · ${progress} درصد${clock ? ` · ${clock} باقی‌مانده` : ''}`
}

function showOvertimeSticker(offer: any): boolean {
  return isOvertimePhase(offer) && !isReadOnlyOffer(offer)
}

function showFinalTailBadge(offer: any): boolean {
  return isFinalTailPhase(offer)
}

function showOvertimeTradeBadge(offer: any): boolean {
  return isTradedHistoryOffer(offer) && offer?.overtime_trade_committed === true
}

function hasTimer(offer: any): boolean {
  if (isInteractionLocked(offer) || isFinalTailPhase(offer)) return false
  return timerDeadlineTs(offer) != null
}

function isOvertimeTimer(offer: any): boolean {
  return isOvertimePhase(offer) && hasTimer(offer)
}

function isExpiredOffer(offer: any): boolean {
  if (isTradedHistoryOffer(offer)) return false
  return offer?.status === 'expired' || offer?.history_state === 'expired'
}

function isTradedHistoryOffer(offer: any): boolean {
  return offer?.history_state === 'traded'
}

function isPartiallyTradedHistoryOffer(offer: any): boolean {
  if (!isTradedHistoryOffer(offer)) return false
  if (offer?.is_partially_traded === true) return true
  const tradedQuantity = getFiniteNumber(offer?.traded_quantity)
  const totalQuantity = getFiniteNumber(offer?.quantity)
  return tradedQuantity !== null
    && totalQuantity !== null
    && tradedQuantity > 0
    && tradedQuantity < totalQuantity
}

function isReadOnlyOffer(offer: any): boolean {
  const status = String(offer?.status ?? '').toLowerCase()
  return offer?.is_read_only === true
    || typeof offer?.history_state === 'string'
    || (status !== '' && status !== 'active')
}

/** History rows, terminal rows, and final-tail (no new public interaction). */
function isInteractionLocked(offer: any): boolean {
  if (isReadOnlyOffer(offer)) return true
  if (isFinalTailPhase(offer)) return true
  if (offer?.accepts_new_public_interaction === false && String(offer?.status ?? '').toLowerCase() === 'active') {
    return true
  }
  return false
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
    const totalQuantity = getFiniteNumber(offer?.quantity)
    if (isPartiallyTradedHistoryOffer(offer)) {
      if (tradedQuantity !== null && totalQuantity !== null) {
        return `بخشی معامله شد · ${tradedQuantity.toLocaleString()} از ${totalQuantity.toLocaleString()}`
      }
      if (tradedQuantity !== null) return `بخشی معامله شد · ${tradedQuantity.toLocaleString()} عدد`
      return 'بخشی معامله شد'
    }
    if (totalQuantity !== null) {
      const completedQuantity = tradedQuantity !== null && tradedQuantity > 0 ? tradedQuantity : totalQuantity
      return `کامل معامله شد · ${completedQuantity.toLocaleString()} از ${totalQuantity.toLocaleString()}`
    }
    return 'کامل معامله شد'
  }
  if (isExpiredOffer(offer)) return 'منقضی · بدون معامله'
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
// Final-tail stays visible via server lifecycle even when expires_at_ts has elapsed.
const filteredOffers = computed(() => {
  const nowSec = now.value
  const source = Array.isArray(props.offers) ? props.offers : []
  const visible = source.filter(o => isReadOnlyOffer(o) || isActiveLifecycleVisible(o, nowSec))
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
    && (intent.offerPublicId === undefined
      || intent.offerPublicId === null
      || (typeof intent.offerPublicId === 'string'
        && intent.offerPublicId.trim().length > 0
        && intent.offerPublicId.trim().length <= 40))
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

function getTradeIntent(offerId: number, quantity: number, offerPublicId: string | null): TradeIntentState {
  const key = tradeKeyFor(offerId, quantity);
  const existing = tradeIntents.get(key);
  if (existing) {
    if (!existing.offerPublicId && offerPublicId) {
      existing.offerPublicId = offerPublicId;
      existing.updatedAt = Date.now();
      persistTradeIntents();
    }
    return existing;
  }

  const createdAt = Date.now();
  const next: TradeIntentState = {
    version: 1,
    offerId,
    offerPublicId,
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

function tradeResponseErrorCode(payload: any): string | null {
  const code = payload?.error_code ?? payload?.code
    ?? payload?.detail?.error_code ?? payload?.detail?.code;
  return typeof code === 'string' && code.trim() ? code.trim() : null;
}

function isAmbiguousTradeResponse(response: Response, payload: any): boolean {
  const status = Number(response?.status);
  return Number.isFinite(status) && (
    status >= 500
    || status === 408
    || status === 425
    || status === 429
    || (status === 409 && tradeResponseErrorCode(payload) === TRADE_CONTENTION_BUSY_CODE)
  );
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
  if (response.status === 422 && Array.isArray(payload?.detail)) {
    const invalidFields = new Set(payload.detail.flatMap((item: any) => (
      Array.isArray(item?.loc) ? item.loc.map((part: unknown) => String(part)) : []
    )))
    if (invalidFields.has('offer_id') || invalidFields.has('offer_public_id')) {
      return 'شناسه لفظ معتبر نیست. فهرست بازار را تازه‌سازی و دوباره تلاش کنید.'
    }
    if (invalidFields.has('quantity')) {
      return 'مقدار معامله معتبر نیست. یکی از مقدارهای نمایش‌داده‌شده را انتخاب کنید.'
    }
    if (invalidFields.has('idempotency_key')) {
      return 'درخواست معامله کامل نیست. دوباره تلاش کنید.'
    }
  }
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
function handleLotClick(offerId: unknown, amount: unknown) {
  if (tradingOfferId.value !== null) return;
  const normalizedOfferId = Number(offerId)
  const normalizedAmount = Number(amount)
  if (!Number.isInteger(normalizedOfferId) || normalizedOfferId <= 0) {
    showTradeError('شناسه لفظ معتبر نیست. فهرست بازار را تازه‌سازی و دوباره تلاش کنید.')
    return
  }
  if (!Number.isInteger(normalizedAmount) || normalizedAmount <= 0) {
    showTradeError('مقدار معامله معتبر نیست. یکی از مقدارهای نمایش‌داده‌شده را انتخاب کنید.')
    return
  }
  const key = `${normalizedOfferId}:${normalizedAmount}`;
  
  if (pendingConfirm.value === key) {
    // Second tap — execute trade
    clearPendingConfirm();
    executeTrade(normalizedOfferId, normalizedAmount);
  } else {
    // First tap — set pending
    pendingConfirm.value = key;
    if (confirmTimeout) clearTimeout(confirmTimeout);
    confirmTimeout = setTimeout(() => {
      clearPendingConfirm();
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
  if (!tradeIdentityReady.value) {
    showTradeError('اطلاعات حساب در حال بارگذاری است. لطفاً چند لحظه دیگر تلاش کنید.');
    return;
  }
  const normalizedOfferId = Number(offerId)
  const normalizedQuantity = Number(quantity)
  if (!Number.isInteger(normalizedOfferId) || normalizedOfferId <= 0) {
    showTradeError('شناسه لفظ معتبر نیست. فهرست بازار را تازه‌سازی و دوباره تلاش کنید.');
    return;
  }
  if (!Number.isInteger(normalizedQuantity) || normalizedQuantity <= 0) {
    showTradeError('مقدار معامله معتبر نیست. یکی از مقدارهای نمایش‌داده‌شده را انتخاب کنید.');
    return;
  }
  const sourceOffer = props.offers.find((offer: any) => Number(offer?.id) === normalizedOfferId)
  const candidatePublicId = typeof sourceOffer?.offer_public_id === 'string'
    ? sourceOffer.offer_public_id.trim()
    : ''
  const offerPublicId = candidatePublicId.length > 0 && candidatePublicId.length <= 40
    ? candidatePublicId
    : null
  if (hasConflictingTradeIntent(normalizedOfferId, normalizedQuantity)) {
    showTradeError(CONFLICTING_TRADE_INTENT_MESSAGE);
    return;
  }

  const intent = getTradeIntent(normalizedOfferId, normalizedQuantity, offerPublicId);
  const executionStorageKey = activeTradeIntentStorageKey;
  tradingOfferId.value = normalizedOfferId;
  tradingAmount.value = normalizedQuantity;
  tradeError.value = '';
  setTradeIntentStatus(intent, 'in_flight');
  
  try {
    const response = await apiFetch('/api/trades/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        offer_id: intent.offerId,
        ...(intent.offerPublicId ? { offer_public_id: intent.offerPublicId } : {}),
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
      if (isAmbiguousTradeResponse(response as Response, data)) {
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

    <AppErrorState
      v-else-if="activeLoadError && filteredOffers.length === 0"
      title="لفظ‌های بازار دریافت نشد"
      :message="activeLoadError"
    >
      <template #actions>
        <button type="button" class="market-page-retry-btn" @click="emit('retry-active')">
          تلاش دوباره
        </button>
      </template>
    </AppErrorState>

    <AppOfferEmptyState v-else-if="filteredOffers.length === 0" />

    <div v-else class="offers-list">
      <AppOfferCard
        v-for="offer in filteredOffers" 
        :key="offer.id"
        :timer-critical="!isInteractionLocked(offer) && isCritical(offer)"
        :has-timer="!isInteractionLocked(offer) && hasTimer(offer)"
        :timer-overtime="!isInteractionLocked(offer) && isOvertimeTimer(offer)"
        :history="isReadOnlyOffer(offer)"
        :expired="isExpiredOffer(offer)"
        :traded="isTradedHistoryOffer(offer)"
        :partially-traded="isPartiallyTradedHistoryOffer(offer)"
        :decision-focus="isDecisionFocus(offer)"
        :timer-style="cardTimerStyle(offer)"
      >
        <div
          class="offer-card-inner"
          :class="[offer.offer_type, { 'is-decision-focus': isDecisionFocus(offer) }]"
        >
          <!-- Header: role badge + lifecycle chips + time -->
          <div class="offer-header">
            <div class="offer-classification">
              <AppOfferSideBadge :side="offer.offer_type" />
              <AppSettlementBadge
                class="offer-settlement"
                :settlement-type="offer.settlement_type"
              />
              <AppOfferHistoryStamp
                v-if="getHistoryStampLabel(offer)"
                :label="getHistoryStampLabel(offer)"
                :traded="isTradedHistoryOffer(offer)"
              />
              <span
                v-if="showOvertimeSticker(offer)"
                class="offer-overtime-sticker"
                data-test="offer-overtime-sticker"
                role="img"
                aria-label="وقت اضافه"
                title="وقت اضافه"
              >
                <Hourglass class="offer-overtime-sticker__icon" :size="17" aria-hidden="true" />
              </span>
              <span
                v-if="showFinalTailBadge(offer)"
                class="offer-lifecycle-chip offer-lifecycle-chip--final-tail"
                data-test="offer-final-tail-badge"
              >
                مهلت پایان یافته
              </span>
              <span
                v-if="showOvertimeTradeBadge(offer)"
                class="offer-lifecycle-chip offer-lifecycle-chip--overtime-trade"
                data-test="offer-overtime-trade-badge"
              >
                معامله در وقت اضافه
              </span>
            </div>
            <div class="offer-meta-end">
              <span class="offer-time">{{ timeAgo(offer.created_at) }}</span>
            </div>
          </div>

          <div class="offer-body">
            <div class="offer-main">
              <span class="commodity">{{ offer.commodity_name }}</span>
              <div class="offer-metrics">
                <div
                  class="offer-remaining"
                  data-test="offer-remaining"
                  :aria-label="`${remainingFieldLabel(offer)} ${getOfferQuantityLabel(offer)}`"
                >
                  <span class="offer-remaining-label">{{ remainingFieldLabel(offer) }}</span>
                  <AppOfferQuantityBadge>{{ getOfferQuantityLabel(offer) }}</AppOfferQuantityBadge>
                </div>
                <div
                  class="offer-price-block"
                  :aria-label="`قیمت هر عدد ${getDisplayedOfferPrice(offer).toLocaleString()} تومان`"
                >
                  <span class="offer-price-label">قیمت هر عدد</span>
                  <span class="offer-price-value">
                    <AppOfferPrice :value="getDisplayedOfferPrice(offer)" />
                    <span class="offer-price-unit">تومان</span>
                  </span>
                </div>
              </div>
            </div>
            <AppOfferCustomerContext
              v-if="offer.customer_badge_visible"
              :management-name="offer.customer_management_name"
              :tier-label="offer.customer_tier ? getCustomerTierLabel(offer.customer_tier) : null"
            />
            <p v-if="offer.notes" class="offer-notes">
              توضیحات: {{ offer.notes }}
            </p>
            <p v-if="showFinalTailBadge(offer)" class="offer-final-tail-copy">
              در حال نهایی‌سازی
            </p>
          </div>

          <div
            v-if="isDecisionFocus(offer) && pendingAmountFor(offer) !== null"
            class="offer-decision-panel"
            data-test="offer-decision-panel"
            role="status"
            aria-live="polite"
          >
            <p class="offer-decision-title">مرور و تأیید معامله</p>
            <p data-test="offer-decision-side">نوع لفظ: {{ offerSideLabel(offer) }}</p>
            <p data-test="offer-decision-action">اقدام شما: {{ userActionLabel(offer) }} {{ pendingAmountFor(offer)?.toLocaleString() }} عدد</p>
            <p class="offer-decision-selected">
              مقدار انتخاب‌شده: {{ pendingAmountFor(offer)?.toLocaleString() }} عدد
            </p>
            <p>قیمت هر عدد: {{ getDisplayedOfferPrice(offer).toLocaleString() }} تومان</p>
            <p>باقی‌مانده: {{ getOfferRemainingQuantity(offer).toLocaleString() }} عدد</p>
            <p class="offer-decision-result">{{ expectedTradeResult(offer) }}</p>
            <p class="offer-decision-hint">برای تأیید، همان مقدار را دوباره انتخاب کنید</p>
            <button
              type="button"
              class="offer-decision-cancel"
              data-test="offer-decision-cancel"
              @click="clearPendingConfirm"
            >
              انصراف
            </button>
          </div>

          <!-- Footer: lot buttons or own offer (hidden in history and final-tail) -->
          <div v-if="!isInteractionLocked(offer)" class="offer-footer">
            <div v-if="!isOwnOffer(offer) && (offer.remaining_quantity ?? offer.quantity) > 0" class="trade-buttons">
              <AppTradeActionButton
                v-for="amount in getLotButtons(offer)"
                :key="amount"
                :side="offer.offer_type"
                :pending="isPending(offer.id, amount)"
                :busy="tradingOfferId === offer.id"
                :disabled="tradingOfferId === offer.id || !tradeIdentityReady"
                :aria-label="tradeButtonAriaLabel(offer, amount)"
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

          <div
            v-if="hasTimer(offer)"
            class="offer-deadline"
            :data-phase="deadlinePhase(offer)"
            :data-critical="isCritical(offer) ? 'true' : 'false'"
          >
            <p class="offer-deadline-label" data-test="offer-deadline-label">{{ deadlineLabel(offer) }}</p>
            <div
              class="offer-deadline-meter"
              data-test="offer-deadline-meter"
              :data-phase="deadlinePhase(offer)"
              :data-critical="isCritical(offer) ? 'true' : 'false'"
              role="progressbar"
              :aria-label="deadlineMeterAriaLabel(offer)"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuenow="Math.round(getDeadlineMeterPercent(offer))"
            >
              <span class="offer-deadline-meter__value" aria-hidden="true"></span>
            </div>
          </div>

        </div><!-- /offer-card-inner -->
      </AppOfferCard>
      <div v-if="activeLoadError" class="active-load-error" role="alert">
        <span>{{ activeLoadError }}</span>
        <button type="button" class="market-page-retry-btn" @click="emit('retry-active')">
          تلاش دوباره
        </button>
      </div>
      <div
        v-if="!activeLoadError && (hasMoreActive || activeLoading)"
        class="active-load-more-row"
      >
        <button
          type="button"
          class="active-load-more-btn"
          :disabled="activeLoading"
          @click="emit('load-more-active')"
        >
          <Loader2 v-if="activeLoading" class="inline animate-spin" :size="14" />
          <span>{{ activeLoading ? 'در حال دریافت' : 'نمایش لفظ‌های بیشتر' }}</span>
        </button>
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

<style scoped>
/* ══════════════════════════════════════
   Offer Card — Mini-App-style layout
   ══════════════════════════════════════ */

/* ── Offers list ── */
.offers-list {
  --market-focus-ring: var(--ds-primary-800);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* ── Card wrapper ── */
.offer-card-wrap {
  --market-focus-ring: var(--ds-primary-800);
  position: relative;
  border-radius: var(--ds-radius-md);
  border: 1px solid var(--ds-border-light);
  overflow: hidden;
}

.offer-card-wrap.has-timer {
  border-color: color-mix(in srgb, var(--t-color) 34%, var(--ds-border-light));
}

/* ── Inner card ── */
.offer-card-inner {
  position: relative;
  background: var(--ds-bg-card);
  border-radius: var(--ds-radius-md);
  padding: 10px 11px 12px;
  z-index: 0;
}

.offer-card-wrap.is-decision-focus {
  box-shadow: 0 0 0 2px var(--market-focus-ring);
}

.offer-card-wrap.is-history .offer-card-inner {
  box-shadow: none;
}

.offer-card-wrap.is-expired .offer-card-inner {
  background: linear-gradient(145deg, #f8fafc, #f1f5f9);
}

.offer-card-wrap.is-expired {
  border-color: #94a3b8;
  border-top-width: 3px;
}

.offer-card-wrap.is-fully-traded .offer-card-inner {
  background: linear-gradient(145deg, #f0fdfa, #ecfdf5);
}

.offer-card-wrap.is-fully-traded {
  border-color: #0f766e;
  border-top-width: 3px;
}

.offer-card-wrap.is-partially-traded .offer-card-inner {
  background: linear-gradient(145deg, #fffbeb, #fefce8);
}

.offer-card-wrap.is-partially-traded {
  border-color: #d97706;
  border-top-width: 3px;
}

.offer-card-wrap.is-history .role-badge {
  opacity: 0.78;
}

.offer-card-wrap.is-history .price,
.offer-card-wrap.is-history .commodity {
  color: var(--ds-text-primary);
}

.history-ribbon,
.offer-lifecycle-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  min-height: 22px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
  line-height: 1.2;
  border: 1px solid transparent;
}

.expired-ribbon {
  background: #475569;
  color: #fff;
  border-color: #475569;
}

.traded-ribbon {
  background: #0f766e;
  color: #fff;
  border-color: #0f766e;
}

.offer-card-wrap.is-partially-traded .traded-ribbon {
  background: #b45309;
  border-color: #b45309;
}

.offer-overtime-sticker {
  position: relative;
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  border: 1px solid color-mix(in srgb, var(--ds-warning-600) 72%, white);
  border-radius: 9px;
  color: var(--ds-warning-700);
  background:
    radial-gradient(circle at 36% 28%, rgba(255, 255, 255, 0.96), transparent 34%),
    linear-gradient(145deg, var(--ds-warning-50), color-mix(in srgb, var(--ds-warning-100) 80%, white));
  box-shadow:
    0 4px 12px color-mix(in srgb, var(--ds-warning-600) 16%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.82);
}

.offer-overtime-sticker__icon {
  transform-origin: 50% 50%;
  animation: overtime-hourglass-turn 3.6s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}

@keyframes overtime-hourglass-turn {
  0%, 32% {
    transform: translateY(0) rotate(0deg);
  }
  42% {
    transform: translateY(-1px) rotate(180deg);
  }
  48%, 82% {
    transform: translateY(0) rotate(180deg);
  }
  92% {
    transform: translateY(-1px) rotate(360deg);
  }
  100% {
    transform: translateY(0) rotate(360deg);
  }
}

.offer-lifecycle-chip--final-tail {
  background: #f8fafc;
  color: #475569;
  border-color: #cbd5e1;
}

.offer-lifecycle-chip--overtime-trade {
  background: var(--ds-warning-50);
  color: var(--ds-warning-700);
  border-color: var(--ds-warning-600);
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

.offer-meta-end {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
  flex: 0 0 auto;
  min-height: 14px;
}

.offer-time {
  font-size: 10px;
  color: var(--ds-text-placeholder);
  line-height: 14px;
  white-space: nowrap;
}

.offer-final-tail-copy {
  margin: 0.45rem 0 0;
  font-size: 12px;
  font-weight: 700;
  color: #475569;
}

.offer-deadline {
  display: grid;
  gap: 0.38rem;
  margin-top: 0.58rem;
}

.offer-deadline-label {
  margin: 0;
  font-size: 11px;
  font-weight: 700;
  color: var(--ds-text-secondary);
  text-align: end;
}

.offer-deadline[data-phase="overtime"] .offer-deadline-label {
  color: var(--ds-warning-700);
}

.offer-deadline[data-critical="true"] .offer-deadline-label,
.offer-deadline[data-phase="critical"] .offer-deadline-label {
  color: var(--ds-danger-700);
}

.offer-deadline-meter {
  position: relative;
  height: 5px;
  overflow: hidden;
  border-radius: 999px;
  background: color-mix(in srgb, var(--t-color) 14%, var(--ds-bg-hover));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--t-color) 18%, transparent);
}

.offer-deadline-meter__value {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--t-color) 78%, white),
    var(--t-color)
  );
  box-shadow: 0 0 8px color-mix(in srgb, var(--t-color) 24%, transparent);
  transform: scaleX(var(--t-ratio, 1));
  transform-origin: right center;
  will-change: transform;
}


@media (prefers-reduced-motion: reduce) {
  .offer-overtime-sticker__icon,
  .trade-btn,
  .trade-btn.pending,
  .cancel-own-offer-btn {
    animation: none;
    transition: none;
  }
}

/* ── Body ── */
.offer-body {
  margin-bottom: 7px;
}

.offer-main {
  display: grid;
  gap: 0.4rem;
}

.offer-metrics {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 0.6rem;
  flex-wrap: wrap;
}

.offer-remaining,
.offer-price-block {
  display: grid;
  gap: 0.15rem;
  min-width: 0;
}

.offer-remaining-label,
.offer-price-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--ds-text-secondary);
}

.offer-price-value {
  display: inline-flex;
  align-items: baseline;
  gap: 0.25rem;
}

.offer-price-unit {
  font-size: 11px;
  font-weight: 700;
  color: var(--ds-text-secondary);
}

.commodity {
  font-weight: 700;
  font-size: 14px;
  color: var(--ds-text-primary);
}

.offer-decision-panel {
  display: grid;
  gap: 0.35rem;
  margin: 0 0 0.65rem;
  padding: 0.7rem 0.75rem;
  border-radius: 12px;
  background: var(--ds-bg-inset);
  border: 1px solid var(--ds-border-medium);
}

.offer-decision-panel p {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ds-text-primary);
}

.offer-decision-title,
.offer-decision-selected,
.offer-decision-result,
.offer-decision-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ds-text-primary);
}

.offer-decision-title,
.offer-decision-selected,
.offer-decision-result {
  font-weight: 800;
}

.offer-decision-hint {
  color: var(--ds-primary-500);
}

.offer-decision-cancel {
  justify-self: start;
  min-width: 44px;
  min-height: 44px;
  padding: 0 0.9rem;
  border-radius: 10px;
  border: 1px solid var(--ds-border-medium);
  background: var(--ds-bg-card);
  color: var(--ds-text-secondary);
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}

.offer-decision-cancel:focus-visible {
  outline: 2px solid var(--market-focus-ring);
  outline-offset: 2px;
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
  flex-wrap: wrap;
  gap: 6px;
  width: 100%;
}

.trade-buttons::-webkit-scrollbar {
  display: none;
}

.trade-btn {
  padding: 10px 12px;
  color: white;
  border: 1px solid transparent;
  border-radius: var(--ds-radius-sm);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  flex: 1 1 auto;
  min-width: 44px;
  min-height: 44px;
  max-width: 160px;
  text-align: center;
  transition: all 0.2s ease;
  letter-spacing: 0.02em;
}

.trade-btn:active {
  filter: brightness(0.96);
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

.offer-card-inner.is-decision-focus .trade-btn.buy:not(.pending) {
  background: var(--ds-success-100);
  color: var(--ds-trade-buy-text);
  border-color: var(--ds-trade-buy-text);
}

.offer-card-inner.is-decision-focus .trade-btn.sell:not(.pending) {
  background: var(--ds-danger-50);
  color: var(--ds-danger-600);
  border-color: var(--ds-danger-600);
}

.trade-btn:focus-visible {
  outline: 2px solid var(--market-focus-ring);
  outline-offset: 2px;
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
  min-height: 44px;
  padding: 10px 12px;
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

.cancel-own-offer-btn:focus-visible {
  outline: 2px solid var(--market-focus-ring);
  outline-offset: 2px;
}

.cancel-own-offer-btn:hover {
  background: var(--ds-danger-100);
}

.cancel-own-offer-btn:disabled {
  opacity: 0.6;
  cursor: wait;
}

.active-load-more-row,
.expired-load-more-row {
  display: flex;
  justify-content: center;
  padding: 2px 0 6px;
}

.active-load-more-btn,
.expired-load-more-btn,
.market-page-retry-btn {
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

.active-load-more-btn:disabled,
.expired-load-more-btn:disabled,
.market-page-retry-btn:disabled {
  opacity: 0.68;
  cursor: wait;
}

.active-load-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.75rem;
  color: var(--ds-danger-700);
  font-size: 0.78rem;
  font-weight: 700;
  border: 1px solid var(--ds-danger-200);
  border-radius: var(--ds-radius-md);
  background: var(--ds-danger-50);
}

/* ── Soft pulse for confirm state ── */
@keyframes pulse-soft {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.85; }
}

/* ── Toasts ── */
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

@media (min-width: 1024px) {
  .offers-list {
    gap: 14px;
  }

  .offer-card-inner {
    padding: 14px 16px 14px;
  }

  .offer-main {
    grid-template-columns: minmax(0, 1.15fr) minmax(12rem, 0.85fr);
    align-items: end;
    column-gap: 1.5rem;
  }

  .offer-metrics {
    justify-content: flex-end;
    gap: 1.25rem;
    flex-wrap: nowrap;
  }

  .commodity {
    font-size: 15px;
  }
}
</style>
