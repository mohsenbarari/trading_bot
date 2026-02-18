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
  const gO = Math.max(0.15, (pct / 100) * 0.45)
  const gS = Math.round(2 + (pct / 100) * 8)
  return {
    '--t-pct': pct + '%',
    '--t-c': hsl(c),
    '--t-cg': hsla(c, gO),
    '--t-cgi': hsla(c, gO * 0.5),
    '--t-cl': hsla(c, 0.7),
    '--t-gs': gS + 'px',
    '--t-cgs': hsla(c, Math.min(gO * 1.8, 0.6)),
    '--t-cgsb': hsla(c, 0.15)
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
/* Card wrapper — acts as the conic-gradient border frame */
.offer-card-wrap {
  position: relative;
  padding: 2px;
  border-radius: 1rem;
  background: rgba(251, 191, 36, 0.15);
  transition: box-shadow 1s linear;
}

/* Conic-gradient border: fills clockwise from top, shrinks with time */
.offer-card-wrap.has-timer {
  background: conic-gradient(
    from 0deg at 50% 50%,
    var(--t-c) calc(var(--t-pct) - 1%),
    var(--t-cgi) var(--t-pct),
    rgba(200, 200, 200, 0.1) var(--t-pct)
  );
  box-shadow:
    0 0 var(--t-gs, 0px) var(--t-cg, transparent);
}

/* Inner card content */
.offer-card-inner {
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(8px);
  border-radius: calc(1rem - 2px);
  transition: background 0.2s ease;
}

.offer-card-wrap:hover .offer-card-inner {
  background: rgba(255, 255, 255, 0.95);
}

/* Critical pulsing (<15% remaining) */
.offer-card-wrap.timer-critical {
  animation: card-pulse 1s ease-in-out infinite;
}

@keyframes card-pulse {
  0%, 100% {
    box-shadow: 0 0 var(--t-gs, 4px) var(--t-cg, rgba(239,68,68,0.3));
  }
  50% {
    box-shadow:
      0 0 calc(var(--t-gs, 4px) * 3) var(--t-cgs, rgba(239,68,68,0.5)),
      0 0 calc(var(--t-gs, 4px) * 6) var(--t-cgsb, rgba(239,68,68,0.15));
  }
}
</style>
