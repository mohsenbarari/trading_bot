<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { AppSettlementBadge, AppStatusBadge } from './ui'
import type { SettlementType } from '../utils/settlementType'

type ParsedOfferPreview = {
  trade_type: 'buy' | 'sell'
  settlement_type: SettlementType
  commodity_name: string
  quantity: number
  price: number
  is_wholesale: boolean
  lot_sizes: number[] | null
  notes: string | null
}

type OfferPriceWarning = {
  title: string
  detail: string
  message: string
  reference_label: string
  reference_price: number
  proposed_price: number
  difference_percent: number
}

const props = defineProps<{
  offer: ParsedOfferPreview
  submitting?: boolean
  error?: string
  warning?: OfferPriceWarning | null
}>()

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'edit'): void
  (e: 'cancel'): void
}>()

const tradeLabel = computed(() => (props.offer.trade_type === 'buy' ? 'خرید' : 'فروش'))
const formattedPrice = computed(() => props.offer.price.toLocaleString())
const referencePrice = computed(() => props.warning?.reference_price?.toLocaleString() ?? '')
const proposedWarningPrice = computed(() => props.warning?.proposed_price?.toLocaleString() ?? '')
const lotSummary = computed(() => {
  if (props.offer.is_wholesale) return 'یکجا'
  if (!props.offer.lot_sizes?.length) return 'خُرد'
  return `خُرد ${props.offer.lot_sizes.join(' + ')}`
})
const confirmButtonText = computed(() => {
  if (props.submitting) return 'در حال ارسال...'
  return props.warning ? 'با وجود هشدار منتشر کن' : 'تایید و ارسال'
})
const confirmClickLocked = ref(false)

watch(() => [props.submitting, props.error, props.warning], ([submitting, error, warning]) => {
  if (!submitting || error || warning) confirmClickLocked.value = false
})

function handleConfirmClick() {
  if (props.submitting || confirmClickLocked.value) return
  confirmClickLocked.value = true
  emit('confirm')
}
</script>

<template>
  <div class="offer-preview-overlay" @click.self="emit('cancel')">
    <div class="offer-preview-card" data-test="offer-preview-card" role="dialog" aria-modal="true" aria-labelledby="offer-preview-title">
      <div class="offer-preview-header">
        <div>
          <p class="offer-preview-kicker">{{ warning ? 'هشدار قبل از انتشار' : 'قبل از انتشار' }}</p>
          <h2 id="offer-preview-title">{{ warning ? warning.title : 'پیش‌نمایش لفظ' }}</h2>
        </div>
        <button type="button" class="offer-preview-close" data-test="offer-preview-close" @click="emit('cancel')" aria-label="بستن پیش‌نمایش">×</button>
      </div>

      <div class="offer-preview-body">
        <div class="offer-preview-bubble">
          <div class="offer-preview-badges">
            <AppStatusBadge :tone="offer.trade_type === 'buy' ? 'success' : 'danger'">
              {{ tradeLabel }}
            </AppStatusBadge>
            <AppSettlementBadge :settlement-type="offer.settlement_type" />
            <AppStatusBadge tone="neutral">{{ lotSummary }}</AppStatusBadge>
          </div>
          <div class="offer-preview-line">
            {{ offer.commodity_name }} {{ offer.quantity }} عدد {{ formattedPrice }}
          </div>
          <div v-if="offer.notes" class="offer-preview-notes">
            توضیحات: {{ offer.notes }}
          </div>
        </div>

        <div v-if="warning" class="offer-preview-warning" data-test="offer-preview-warning">
          <div class="offer-preview-warning-title">{{ warning.detail }}</div>
          <div class="offer-preview-warning-meta">
            <span>{{ warning.reference_label }}: <strong>{{ referencePrice }}</strong></span>
            <span>قیمت شما: <strong>{{ proposedWarningPrice }}</strong></span>
            <span>اختلاف: <strong>{{ warning.difference_percent }}%</strong></span>
          </div>
          <p class="offer-preview-warning-hint">در صورت تایید دوباره، لفظ منتشر می‌شود اما در نرخ منصفانه لحاظ نخواهد شد.</p>
        </div>

        <div v-if="error" class="offer-preview-error" data-test="offer-preview-error">{{ error }}</div>
      </div>

      <div class="offer-preview-actions">
        <button type="button" class="offer-preview-cancel" :disabled="submitting" @click="emit('cancel')">
          انصراف
        </button>
        <button type="button" class="offer-preview-edit" :disabled="submitting" @click="emit('edit')">
          ویرایش
        </button>
        <button type="button" class="offer-preview-confirm" data-test="offer-preview-confirm" :disabled="submitting || confirmClickLocked" @click="handleConfirmClick">
          {{ confirmButtonText }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.offer-preview-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  background: rgba(15, 23, 42, 0.52);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

.offer-preview-card {
  width: min(100%, 30rem);
  border-radius: 1.25rem;
  background: var(--ds-bg-card, #ffffff);
  border: 1px solid var(--ds-border-light, rgba(148, 163, 184, 0.25));
  box-shadow: 0 28px 80px rgba(15, 23, 42, 0.24);
  overflow: hidden;
}

.offer-preview-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.25rem 1.25rem 1rem;
}

.offer-preview-kicker {
  margin: 0 0 0.2rem;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--ds-accent, #b45309);
}

.offer-preview-header h2 {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 800;
  color: var(--ds-text-primary, #0f172a);
}

.offer-preview-close {
  width: 2rem;
  height: 2rem;
  border-radius: 999px;
  border: 1px solid var(--ds-border-light, rgba(148, 163, 184, 0.25));
  background: var(--ds-bg-page, #f8fafc);
  color: var(--ds-text-secondary, #475569);
  font-size: 1.2rem;
}

.offer-preview-body {
  padding: 0 1.25rem 1.25rem;
}

.offer-preview-bubble {
  padding: 1rem;
  border-radius: 1rem;
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.12), rgba(251, 191, 36, 0.04));
  border: 1px solid rgba(245, 158, 11, 0.16);
}

.offer-preview-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
  margin-bottom: 0.7rem;
}

.offer-preview-line {
  font-size: 1rem;
  font-weight: 800;
  line-height: 1.9;
  color: var(--ds-text-primary, #0f172a);
}

.offer-preview-notes {
  margin-top: 0.65rem;
  font-size: 0.92rem;
  line-height: 1.75;
  color: var(--ds-text-secondary, #475569);
}

.offer-preview-error {
  margin-top: 0.9rem;
  padding: 0.85rem 1rem;
  border-radius: 0.9rem;
  background: rgba(239, 68, 68, 0.1);
  color: #b91c1c;
  font-size: 0.9rem;
  line-height: 1.7;
}

.offer-preview-warning {
  margin-top: 0.9rem;
  padding: 0.95rem 1rem;
  border-radius: 0.95rem;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.24);
  color: #92400e;
}

.offer-preview-warning-title {
  font-size: 0.93rem;
  font-weight: 700;
  line-height: 1.8;
}

.offer-preview-warning-meta {
  display: grid;
  gap: 0.35rem;
  margin-top: 0.65rem;
  font-size: 0.88rem;
  line-height: 1.7;
}

.offer-preview-warning-meta strong {
  color: #78350f;
}

.offer-preview-warning-hint {
  margin: 0.7rem 0 0;
  font-size: 0.85rem;
  line-height: 1.75;
}

.offer-preview-actions {
  display: flex;
  gap: 0.75rem;
  padding: 0 1.25rem 1.25rem;
}

.offer-preview-actions button {
  flex: 1;
  min-height: 3rem;
  border-radius: 0.95rem;
  font-weight: 800;
}

.offer-preview-cancel {
  background: var(--ds-bg-page, #f8fafc);
  border: 1px solid var(--ds-border-light, rgba(148, 163, 184, 0.25));
  color: var(--ds-text-secondary, #475569);
}

.offer-preview-edit {
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.16);
  color: #b45309;
}

.offer-preview-confirm {
  background: linear-gradient(135deg, #d97706, #f59e0b);
  color: #fff;
  border: none;
  box-shadow: 0 18px 40px rgba(217, 119, 6, 0.28);
}

.offer-preview-actions button:disabled {
  opacity: 0.65;
}
</style>
