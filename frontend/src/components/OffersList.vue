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
  if (remainingSec <= 0) return { '--t-pct': '0', '--t-dur': '0s' }
  const total = (props.expiryMinutes || 2) * 60
  const pct = Math.min(Math.max((remainingSec / total) * 100, 0), 100)
  const style: Record<string, string> = {
    '--t-pct': String(pct),
    '--t-dur': remainingSec.toFixed(1) + 's',
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

function getStatusBadge(type: string) {
  return type === 'buy' 
    ? { text: 'خرید', bg: 'bg-green-100', color: 'text-green-700' }
    : { text: 'فروش', bg: 'bg-red-100', color: 'text-red-700' }
}

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
  // Deduplicate preserving order
  const seen = new Set<number>();
  const unique: number[] = [];
  for (const a of allAmounts) {
    if (!seen.has(a) && a > 0) {
      seen.add(a);
      unique.push(a);
    }
  }
  return unique;
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

    <div v-if="loading" class="space-y-3">
       <div v-for="i in (limit || 3)" :key="i" class="h-24 bg-white/50 rounded-2xl animate-pulse border border-amber-100/30"></div>
    </div>

    <div v-else-if="filteredOffers.length === 0" class="text-center py-10">
       <div class="w-16 h-16 bg-amber-50 rounded-2xl flex items-center justify-center mx-auto mb-4 text-amber-400">
          <Search :size="32" />
       </div>
       <p class="text-gray-400 font-medium">هیچ لفظ فعالی یافت نشد.</p>
    </div>

    <div v-else class="space-y-3">
      <div 
        v-for="offer in filteredOffers" 
        :key="offer.id"
        class="offer-card-wrap"
        :class="{ 'timer-critical': isCritical(offer), 'has-timer': hasTimer(offer) }"
        :style="cardTimerStyle(offer)"
      >
        <div class="offer-card-inner p-4 flex flex-col gap-3">
        <!-- Top Row -->
        <div class="flex justify-between items-start">
           <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl flex items-center justify-center font-bold"
                 :class="offer.offer_type === 'buy' ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'">
                 {{ offer.offer_type === 'buy' ? 'خ' : 'ف' }}
              </div>
              <div>
                 <h3 class="font-bold text-gray-800">{{ offer.commodity_name }}</h3>
                 <span class="text-xs text-gray-400">{{ timeAgo(offer.created_at) }}</span>
              </div>
           </div>
           <span class="px-2.5 py-1 rounded-lg text-xs font-bold" :class="getStatusBadge(offer.offer_type).bg + ' ' + getStatusBadge(offer.offer_type).color">
              {{ getStatusBadge(offer.offer_type).text }}
           </span>
        </div>

        <!-- Details Row -->
        <div class="grid grid-cols-3 gap-2 bg-amber-50/30 p-3 rounded-xl border border-amber-100/30">
           <div class="text-center border-l border-amber-200/30 pl-2">
              <p class="text-xs text-gray-400 mb-1">تعداد</p>
              <p class="font-bold text-gray-900">{{ offer.quantity }}</p>
           </div>
           <div class="text-center border-l border-amber-200/30 pl-2">
              <p class="text-xs text-gray-400 mb-1">باقیمانده</p>
              <p class="font-bold" :class="offer.remaining_quantity < offer.quantity ? 'text-amber-600' : 'text-gray-900'">{{ offer.remaining_quantity }}</p>
           </div>
           <div class="text-center">
              <p class="text-xs text-gray-400 mb-1">قیمت واحد</p>
              <p class="font-bold text-gray-900">{{ offer.price ? offer.price.toLocaleString() : '---' }}</p>
           </div>
        </div>

        <!-- Notes -->
        <p v-if="offer.notes" class="text-xs text-gray-500 bg-gray-50 px-3 py-2 rounded-lg">
           توضیحات: {{ offer.notes }}
        </p>

        <!-- Lot Buttons (matching Telegram channel behavior) -->
        <div v-if="!isOwnOffer(offer) && offer.remaining_quantity > 0" class="flex flex-wrap gap-2">
          <button
            v-for="amount in getLotButtons(offer)"
            :key="amount"
            @click="handleLotClick(offer.id, amount)"
            :disabled="tradingOfferId === offer.id"
            class="lot-btn flex-1 min-w-[70px] py-2.5 rounded-xl font-bold text-sm transition-all active:scale-[0.96]"
            :class="[
              isPending(offer.id, amount)
                ? 'bg-amber-500 text-white shadow-md shadow-amber-500/20 animate-pulse-soft'
                : (offer.offer_type === 'buy' 
                  ? 'bg-red-500 hover:bg-red-600 text-white shadow-sm shadow-red-500/10' 
                  : 'bg-green-500 hover:bg-green-600 text-white shadow-sm shadow-green-500/10'),
              tradingOfferId === offer.id ? 'opacity-60 cursor-wait' : ''
            ]"
          >
            <Loader2 v-if="tradingOfferId === offer.id && tradingAmount === amount" class="inline animate-spin mr-1" :size="14" />
            <span v-if="isPending(offer.id, amount)">تایید {{ amount }} عدد؟</span>
            <span v-else>{{ amount }} عدد</span>
          </button>
        </div>

        <!-- Own offer indicator -->
        <div v-else-if="isOwnOffer(offer)" class="text-center py-2 text-xs text-gray-400 font-medium bg-gray-50 rounded-xl">
          لفظ شما
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
/* ── Card wrapper ── */
.offer-card-wrap {
  position: relative;
  border-radius: 1rem;
  border: 3px solid rgba(229, 231, 235, 0.45);
}

/* ── Timer: pure CSS animation drives --t-pct from current value → 0 ── */
.offer-card-wrap.has-timer {
  border-color: transparent;
  animation: timer-drain var(--t-dur, 120s) linear forwards;
}

@keyframes timer-drain {
  to { --t-pct: 0; }
}

/* ── Border ring via ::before + mask ── */
.offer-card-wrap.has-timer::before {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 1rem;
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

/* ── Inner card ── */
.offer-card-inner {
  position: relative;
  background: #ffffff;
  border-radius: calc(1rem - 3px);
  z-index: 0;
}

/* ── Critical pulse (<15%) ── */
.offer-card-wrap.timer-critical::before {
  animation: ring-pulse 1.2s ease-in-out infinite;
}

@keyframes ring-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.4; }
}

/* ── Lot buttons ── */
.lot-btn {
  letter-spacing: 0.02em;
}

/* ── Soft pulse for confirm state ── */
@keyframes pulse-soft {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.85; transform: scale(0.98); }
}
.animate-pulse-soft {
  animation: pulse-soft 1s ease-in-out infinite;
}

/* ── Toasts ── */
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
