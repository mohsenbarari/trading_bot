<script setup lang="ts">
import { Loader2, X } from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  show: boolean
  title: string
  introText: string
  offerType: 'buy' | 'sell' | ''
  offerTypeLabel: string
  commodityName: string
  price: number
  remainingQuantity: number
  lotSummary: string
  availableLots: number[]
  busy?: boolean
  busyAmount?: number | null
  autoCloseSeconds?: number
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'select-lot', amount: number): void
}>()

const countdown = ref(0)
let closeTimeout: ReturnType<typeof setTimeout> | null = null
let countdownInterval: ReturnType<typeof setInterval> | null = null

const autoCloseSeconds = computed(() => Math.max(1, props.autoCloseSeconds ?? 10))
const offerTypeClass = computed(() => props.offerType || 'sell')

function clearTimers() {
  if (closeTimeout) {
    clearTimeout(closeTimeout)
    closeTimeout = null
  }
  if (countdownInterval) {
    clearInterval(countdownInterval)
    countdownInterval = null
  }
}

function startAutoClose() {
  clearTimers()
  if (!props.show || props.busy) return
  countdown.value = autoCloseSeconds.value
  countdownInterval = setInterval(() => {
    countdown.value = Math.max(0, countdown.value - 1)
  }, 1000)
  closeTimeout = setTimeout(() => {
    clearTimers()
    emit('close')
  }, autoCloseSeconds.value * 1000)
}

watch(
  () => [props.show, props.busy, props.offerType, props.commodityName, props.remainingQuantity, props.lotSummary, props.introText] as const,
  ([show, busy]) => {
    if (!show) {
      clearTimers()
      countdown.value = 0
      return
    }
    if (busy) {
      clearTimers()
      return
    }
    startAutoClose()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  clearTimers()
})
</script>

<template>
  <Teleport to="body">
    <transition name="trade-suggestion-fade">
      <div v-if="props.show" class="trade-suggestion-overlay" @click.self="emit('close')">
        <div class="trade-suggestion-card" role="alertdialog" aria-modal="true" :aria-label="props.title">
          <div class="trade-suggestion-topbar" :class="offerTypeClass">
            <div class="trade-suggestion-topbar-copy">
              <span class="trade-suggestion-kicker">پیشنهاد معامله</span>
              <span class="trade-suggestion-autoclose">{{ props.busy ? 'در حال ارسال...' : `بستن خودکار تا ${countdown} ثانیه` }}</span>
            </div>
            <button type="button" class="trade-suggestion-close" @click="emit('close')" aria-label="بستن">
              <X :size="18" />
            </button>
          </div>

          <div class="trade-suggestion-body">
            <p class="trade-suggestion-message">{{ props.introText }}</p>

            <div class="trade-offer-card" :class="offerTypeClass">
              <div class="trade-offer-header">
                <span class="trade-offer-badge" :class="offerTypeClass">{{ props.offerTypeLabel }}</span>
                <span class="trade-offer-live">همین الان</span>
              </div>

              <div class="trade-offer-main">
                <span class="trade-offer-commodity">{{ props.commodityName }}</span>
                <span class="trade-offer-quantity">{{ props.remainingQuantity.toLocaleString() }} عدد</span>
                <span class="trade-offer-price">{{ props.price.toLocaleString() }}</span>
              </div>

              <div class="trade-offer-lot-info">🔢 خُرد: {{ props.lotSummary }}</div>
            </div>

            <div v-if="props.availableLots.length > 0" class="trade-suggestion-actions">
              <button
                v-for="amount in props.availableLots"
                :key="amount"
                type="button"
                class="trade-suggestion-lot-btn"
                :disabled="props.busy"
                @click="emit('select-lot', amount)"
              >
                <Loader2 v-if="props.busy && props.busyAmount === amount" class="animate-spin" :size="14" />
                <span>{{ amount.toLocaleString() }} عدد</span>
              </button>
            </div>

            <div class="trade-suggestion-footer">
              <button type="button" class="trade-suggestion-dismiss danger" @click="emit('close')">رد کردن</button>
              <button type="button" class="trade-suggestion-dismiss" @click="emit('close')">بستن</button>
            </div>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.trade-suggestion-overlay {
  position: fixed;
  inset: 0;
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  background: rgba(15, 23, 42, 0.52);
  backdrop-filter: blur(10px);
}

.trade-suggestion-card {
  width: min(100%, 25rem);
  border-radius: 1.45rem;
  background: #ffffff;
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.28);
  overflow: hidden;
  animation: tradeSuggestionScaleIn 0.22s ease-out;
}

.trade-suggestion-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.9rem;
  padding: 1rem 1rem 0.95rem;
  color: #fff;
}

.trade-suggestion-topbar.buy {
  background: linear-gradient(135deg, #16a34a, #10b981);
}

.trade-suggestion-topbar.sell {
  background: linear-gradient(135deg, #dc2626, #ef4444);
}

.trade-suggestion-topbar-copy {
  display: flex;
  flex-direction: column;
  gap: 0.18rem;
}

.trade-suggestion-kicker {
  font-size: 1.05rem;
  font-weight: 800;
}

.trade-suggestion-autoclose {
  font-size: 0.78rem;
  opacity: 0.92;
}

.trade-suggestion-body {
  padding: 1rem;
}

.trade-suggestion-close {
  border: none;
  background: rgba(255, 255, 255, 0.18);
  color: #ffffff;
  width: 2rem;
  height: 2rem;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.trade-suggestion-message {
  margin: 0;
  font-size: 0.93rem;
  line-height: 1.85;
  color: #334155;
}

.trade-offer-card {
  margin-top: 0.9rem;
  border-radius: 1rem;
  border: 1px solid rgba(148, 163, 184, 0.16);
  background: #ffffff;
  padding: 0.95rem;
}

.trade-offer-card.buy {
  box-shadow: 0 1px 4px rgba(16, 185, 129, 0.18), 0 1px 2px rgba(0, 0, 0, 0.04);
}

.trade-offer-card.sell {
  box-shadow: 0 1px 4px rgba(239, 68, 68, 0.18), 0 1px 2px rgba(0, 0, 0, 0.04);
}

.trade-offer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.72rem;
}

.trade-offer-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.65rem;
  border-radius: 0.55rem;
  font-size: 0.76rem;
  font-weight: 800;
}

.trade-offer-badge.buy {
  background: #dcfce7;
  color: #16a34a;
}

.trade-offer-badge.sell {
  background: #fee2e2;
  color: #dc2626;
}

.trade-offer-live {
  font-size: 0.73rem;
  color: #94a3b8;
}

.trade-offer-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.45rem;
}

.trade-offer-commodity {
  font-weight: 800;
  color: #1f2937;
  font-size: 0.95rem;
}

.trade-offer-quantity {
  background: #f3f4f6;
  color: #374151;
  font-size: 0.81rem;
  font-weight: 700;
  padding: 0.28rem 0.62rem;
  border-radius: 0.55rem;
}

.trade-offer-price {
  color: #f59e0b;
  font-weight: 900;
  font-size: 0.94rem;
}

.trade-offer-lot-info {
  margin-top: 0.55rem;
  color: #d97706;
  font-weight: 700;
  font-size: 0.8rem;
}

.trade-suggestion-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  margin-top: 1rem;
}

.trade-suggestion-lot-btn {
  border: none;
  border-radius: 999px;
  padding: 0.8rem 1.1rem;
  background: linear-gradient(135deg, #f59e0b 0%, #ea580c 100%);
  color: white;
  font-weight: 800;
  font-size: 0.9rem;
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  box-shadow: 0 10px 24px rgba(234, 88, 12, 0.22);
}

.trade-suggestion-lot-btn:disabled {
  opacity: 0.75;
}

.trade-suggestion-footer {
  display: flex;
  gap: 0.75rem;
  margin-top: 1rem;
}

.trade-suggestion-dismiss {
  flex: 1;
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 1rem;
  background: rgba(255, 255, 255, 0.92);
  color: #475569;
  font-weight: 700;
  padding: 0.78rem 1rem;
}

.trade-suggestion-dismiss.danger {
  border-color: rgba(248, 113, 113, 0.32);
  color: #dc2626;
  background: #fff5f5;
}

@keyframes tradeSuggestionScaleIn {
  from {
    transform: scale(0.92);
    opacity: 0;
  }
  to {
    transform: scale(1);
    opacity: 1;
  }
}

.trade-suggestion-fade-enter-active,
.trade-suggestion-fade-leave-active {
  transition: opacity 0.18s ease;
}

.trade-suggestion-fade-enter-from,
.trade-suggestion-fade-leave-to {
  opacity: 0;
}

@media (max-width: 480px) {
  .trade-offer-main {
    display: grid;
    grid-template-columns: 1fr auto;
    row-gap: 0.45rem;
  }

  .trade-offer-price {
    grid-column: 1 / -1;
  }
}
</style>