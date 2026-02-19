<script setup lang="ts">

import { ref, onMounted, onUnmounted } from 'vue';
import { Search } from 'lucide-vue-next';

// Define Props
const props = defineProps<{
  offers: any[];
  loading: boolean;
  limit?: number;
  expiryMinutes?: number;
}>();

// --- Shared timer tick ---
const now = ref(Math.floor(Date.now() / 1000))
let tickInterval: number | null = null

onMounted(() => {
  tickInterval = setInterval(() => {
    now.value = Math.floor(Date.now() / 1000)
  }, 1000) as any
})

onUnmounted(() => {
  if (tickInterval) clearInterval(tickInterval)
})

// --- Timer helpers (pure graphical, no numbers) ---
function getTimerPercent(offer: any): number {
  if (!offer.expires_at_ts) return 100
  const remaining = offer.expires_at_ts - now.value
  if (remaining <= 0) return 0
  const total = (props.expiryMinutes || 2) * 60
  return Math.min(Math.max((remaining / total) * 100, 0), 100)
}

function getTimerHSL(pct: number): [number, number, number] {
  // Emerald → Green → Gold → Orange → Red
  if (pct >= 75) {
    const t = (pct - 75) / 25
    return [Math.round(120 + t * 40), 78, 42]
  } else if (pct >= 50) {
    const t = (pct - 50) / 25
    return [Math.round(50 + t * 70), 82, 46]
  } else if (pct >= 25) {
    const t = (pct - 25) / 25
    return [Math.round(25 + t * 25), 88, 48]
  } else {
    const t = pct / 25
    return [Math.round(t * 25), 92, 48]
  }
}

function hsl(c: [number, number, number]) { return `hsl(${c[0]},${c[1]}%,${c[2]}%)` }
function hsla(c: [number, number, number], a: number) { return `hsla(${c[0]},${c[1]}%,${c[2]}%,${a.toFixed(2)})` }

function cardTimerStyle(offer: any): Record<string, string> {
  if (!offer.expires_at_ts) return {}
  const pct = getTimerPercent(offer)
  const c = getTimerHSL(pct)
  return {
    '--t-pct': pct + '%',
    '--t-c': hsl(c),
    '--t-cg': hsla(c, 0.12),
  }
}

function isCritical(offer: any): boolean {
  return !!offer.expires_at_ts && getTimerPercent(offer) < 15
}

function hasTimer(offer: any): boolean {
  return !!offer.expires_at_ts
}

function getStatusBadge(type: string) {
  return type === 'buy' 
    ? { text: 'خرید', bg: 'bg-green-100', color: 'text-green-700' }
    : { text: 'فروش', bg: 'bg-red-100', color: 'text-red-700' }
}

function timeAgo(dateString: string) {
    if (!dateString) return '';
    // Simple placeholder, ideally use date-fns or moment-jalaali
    // If dateString is ISO, convert to relative time
    // For now, return as is (assuming Backend sends processed string or Date)
    return dateString;
}
</script>

<template>
    <div v-if="loading" class="space-y-3">
       <div v-for="i in (limit || 3)" :key="i" class="h-24 bg-white/50 rounded-2xl animate-pulse border border-amber-100/30"></div>
    </div>

    <div v-else-if="offers.length === 0" class="text-center py-10">
       <div class="w-16 h-16 bg-amber-50 rounded-2xl flex items-center justify-center mx-auto mb-4 text-amber-400">
          <Search :size="32" />
       </div>
       <p class="text-gray-400 font-medium">هیچ لفظ فعالی یافت نشد.</p>
    </div>

    <div v-else class="space-y-3">
      <div 
        v-for="offer in (limit && Array.isArray(offers) ? offers.slice(0, limit) : (Array.isArray(offers) ? offers : []))" 
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
        <div class="grid grid-cols-2 gap-2 bg-amber-50/30 p-3 rounded-xl border border-amber-100/30">
           <div class="text-center border-l border-amber-200/30 pl-2">
              <p class="text-xs text-gray-400 mb-1">تعداد</p>
              <p class="font-bold text-gray-900">{{ offer.quantity }}</p>
           </div>
           <div class="text-center">
              <p class="text-xs text-gray-400 mb-1">قیمت واحد</p>
              <p class="font-bold text-gray-900">{{ offer.price ? offer.price.toLocaleString() : '---' }}</p>
           </div>
        </div>

        <!-- Action Button -->
         <button class="w-full py-2.5 rounded-xl font-bold text-sm transition-all active:scale-[0.98]"
           :class="offer.offer_type === 'buy' 
             ? 'bg-red-500 hover:bg-red-600 text-white shadow-sm shadow-red-500/10' 
             : 'bg-green-500 hover:bg-green-600 text-white shadow-sm shadow-green-500/10'">
            {{ offer.offer_type === 'buy' ? 'فروش به این خریدار' : 'خرید از این فروشنده' }}
         </button>

        </div><!-- /offer-card-inner -->
      </div>
    </div>
</template>

<style scoped>
/* ── Card wrapper ── */
.offer-card-wrap {
  position: relative;
  border-radius: 1rem;
  border: 3px solid rgba(229, 231, 235, 0.45);
}

/* ── Timer border via ::before + mask (cannot bleed into content) ── */
.offer-card-wrap.has-timer {
  border-color: transparent;
}

.offer-card-wrap.has-timer::before {
  content: '';
  position: absolute;
  inset: -3px;               /* sit on top of the border area */
  border-radius: 1rem;
  padding: 3px;              /* thickness of the visible border */
  background: conic-gradient(
    from 0deg at 50% 50%,
    var(--t-c) calc(var(--t-pct) - 1%),
    var(--t-cg) var(--t-pct),
    rgba(229, 231, 235, 0.35) var(--t-pct)
  );
  /* Mask trick: cut out the inner area so only the 3px ring is visible */
  -webkit-mask:
    linear-gradient(#fff 0 0) content-box,
    linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
          mask-composite: exclude;
  pointer-events: none;
  z-index: 1;
}

/* ── Inner card — plain white, no transparency ── */
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
  50%      { opacity: 0.5; }
}
</style>
