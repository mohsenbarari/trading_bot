<script setup lang="ts">

import { ref, computed, onMounted, onUnmounted } from 'vue';
import { Search, Loader2 } from 'lucide-vue-next';
import { apiFetch } from '../utils/auth';

// Define Props
const props = defineProps<{
  offers: any[];
  loading: boolean;
  limit?: number;
  expiryMinutes?: number;
  currentUserId?: number;
}>();

const emit = defineEmits<{
  (e: 'trade-completed'): void;
}>();

// Trade execution state
const tradingOfferId = ref<number | null>(null);
const tradingAmount = ref<number | null>(null);
const tradeError = ref('');
const tradeSuccess = ref('');

// Confirmation state (double-tap like Telegram)
const pendingConfirm = ref<string | null>(null); // "offerId:amount"
let confirmTimeout: any = null;

// --- Low-frequency tick: only for isCritical boolean (not for animation) ---
const now = ref(Date.now() / 1000)
let tickInterval: number | null = null

onMounted(() => {
  // Clear stale timer styles from previous mount so animations restart correctly
  styleCache.clear()
  tickInterval = setInterval(() => {
    now.value = Date.now() / 1000
  }, 1000) as any
})

onUnmounted(() => {
  if (tickInterval) clearInterval(tickInterval)
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

// --- Cached timer styles ---
const styleCache = new Map<number, Record<string, string>>()

function cardTimerStyle(offer: any): Record<string, string> {
  if (!offer.expires_at_ts) return {}
  if (styleCache.has(offer.id)) return styleCache.get(offer.id)!
  const remainingSec = offer.expires_at_ts - Date.now() / 1000
  if (remainingSec <= 0) return { '--t-pct': '0', '--t-total-dur': '0s', '--t-delay': '0s' }
  const total = (props.expiryMinutes || 2) * 60
  const elapsed = Math.max(total - remainingSec, 0)
  const pct = Math.min(Math.max((remainingSec / total) * 100, 0), 100)
  const style: Record<string, string> = {
    '--t-pct': String(pct),
    '--t-total-dur': total + 's',
    '--t-delay': `-${elapsed.toFixed(1)}s`,
  }
  styleCache.set(offer.id, style)
  return style
}

function isCritical(offer: any): boolean {
  return !!offer.expires_at_ts && getTimerPercent(offer) < 15
}

function hasTimer(offer: any): boolean {
  return !!offer.expires_at_ts
}

// --- Filter out expired offers ---
const filteredOffers = computed(() => {
  const nowSec = now.value
  const source = Array.isArray(props.offers) ? props.offers : []
  const alive = source.filter(o => !o.expires_at_ts || o.expires_at_ts > nowSec)
  const aliveIds = new Set(alive.map(o => o.id))
  for (const id of styleCache.keys()) {
    if (!aliveIds.has(id)) styleCache.delete(id)
  }
  return props.limit ? alive.slice(0, props.limit) : alive
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
  
  // Retail: total + individual lots (filter valid ones)
  const allAmounts = [remaining, ...offer.lot_sizes.filter((l: number) => l <= remaining)];
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

function isOwnOffer(offer: any): boolean {
  return props.currentUserId ? offer.user_id === props.currentUserId : false;
}

// --- Trade execution with double-tap confirm ---
function handleLotClick(offerId: number, amount: number) {
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

async function executeTrade(offerId: number, quantity: number) {
  tradingOfferId.value = offerId;
  tradingAmount.value = quantity;
  tradeError.value = '';
  
  try {
    const response = await apiFetch('/api/trades/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offer_id: offerId, quantity })
    });
    
    if (response.ok) {
      tradeSuccess.value = `معامله ${quantity} عدد با موفقیت انجام شد ✅`;
      setTimeout(() => tradeSuccess.value = '', 4000);
      emit('trade-completed');
    } else {
      const data = await response.json();
      tradeError.value = data.detail || 'خطا در انجام معامله';
      setTimeout(() => tradeError.value = '', 5000);
    }
  } catch (e: any) {
    tradeError.value = e.message || 'خطا در ارتباط با سرور';
    setTimeout(() => tradeError.value = '', 5000);
  } finally {
    tradingOfferId.value = null;
    tradingAmount.value = null;
  }
}
</script>

<template>
    <!-- Trade Success Toast -->
    <transition name="fade">
        <div v-if="tradeSuccess" class="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-gradient-to-r from-green-500 to-green-600 text-white px-5 py-2.5 rounded-2xl text-sm font-bold shadow-lg shadow-green-500/25">
            {{ tradeSuccess }}
        </div>
    </transition>
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
        :class="{ 'timer-critical': isCritical(offer), 'has-timer': hasTimer(offer) }"
        :style="cardTimerStyle(offer)"
      >
        <div class="offer-card-inner" :class="[offer.offer_type]">

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
              <span class="price">{{ offer.price ? offer.price.toLocaleString() : '---' }}</span>
            </div>
            <p v-if="offer.notes" class="offer-notes">
              توضیحات: {{ offer.notes }}
            </p>
          </div>

          <!-- Footer: lot buttons or own offer -->
          <div class="offer-footer">
            <div v-if="!isOwnOffer(offer) && offer.remaining_quantity > 0" class="trade-buttons">
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
            <div v-else-if="isOwnOffer(offer)" class="own-offer-indicator">
              لفظ شما
            </div>
          </div>

        </div><!-- /offer-card-inner -->
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
  height: 90px;
  background: rgba(255,255,255,0.5);
  border-radius: 12px;
  border: 1px solid rgba(245,158,11,0.12);
  animation: pulse-skeleton 1.5s ease-in-out infinite;
}
@keyframes pulse-skeleton {
  0%, 100% { opacity: 0.6; }
  50%      { opacity: 1; }
}

/* ── Empty state ── */
.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: #9ca3af;
}
.empty-icon {
  width: 56px;
  height: 56px;
  background: #fffbeb;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 12px;
  color: #f59e0b;
}

/* ── Card wrapper (timer border ring) ── */
.offer-card-wrap {
  position: relative;
  border-radius: 12px;
  border: 3px solid rgba(229, 231, 235, 0.45);
}

.offer-card-wrap.has-timer {
  border-color: transparent;
  animation: timer-drain var(--t-total-dur, 120s) linear forwards;
  animation-delay: var(--t-delay, 0s);
}

@keyframes timer-drain {
  from { --t-pct: 100; }
  to { --t-pct: 0; }
}

.offer-card-wrap.has-timer::before {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 12px;
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
  background: #ffffff;
  border-radius: calc(12px - 3px);
  padding: 14px;
  z-index: 0;
  overflow: hidden;
}

/* Subtle outer shadow for depth (no inset color strip — conflicts with timer ring) */
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
  margin-bottom: 10px;
}

.role-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
}

.role-badge.buy {
  background: #dcfce7;
  color: #16a34a;
}

.role-badge.sell {
  background: #fee2e2;
  color: #dc2626;
}

.offer-time {
  font-size: 11px;
  color: #9ca3af;
}

/* ── Body ── */
.offer-body {
  margin-bottom: 10px;
}

.offer-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.commodity {
  font-weight: 700;
  font-size: 14px;
  color: #1f2937;
}

.quantity-badge {
  background: #f3f4f6;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  color: #374151;
}

.price {
  font-weight: 800;
  font-size: 14px;
  color: #f59e0b;
}

.offer-notes {
  margin-top: 8px;
  font-size: 12px;
  color: #6b7280;
  background: #f9fafb;
  padding: 6px 10px;
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
  gap: 6px;
  width: 100%;
}

.trade-buttons::-webkit-scrollbar {
  display: none;
}

.trade-btn {
  padding: 8px 12px;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 13px;
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
  background: linear-gradient(135deg, #10b981, #059669);
}

.trade-btn.sell {
  background: linear-gradient(135deg, #ef4444, #dc2626);
}

.trade-btn.pending {
  background: #f59e0b;
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
.own-offer-indicator {
  width: 100%;
  text-align: center;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  color: #9ca3af;
  background: #f3f4f6;
  font-weight: 500;
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
