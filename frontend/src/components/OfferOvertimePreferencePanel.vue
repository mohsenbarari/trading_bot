<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { AppButton, AppNumberStepper } from './ui'
import {
  M8_INVALID_VALUE,
  M9_HELPER,
  M9_LABEL,
  OVERTIME_MAX_MINUTES,
  OVERTIME_MIN_MINUTES,
  formatCurrentPreferenceLine,
  formatSaveSuccessDetail,
} from '../constants/offerOvertimeCopy'
import { saveOfferOvertimePreference } from '../services/offerOvertimeApi'
import {
  cacheCurrentUserSummary,
  currentUserSummary,
  canEditOfferOvertimePreference,
} from '../utils/currentUser'

const props = withDefaults(defineProps<{
  compact?: boolean
}>(), {
  compact: false,
})

const draftMinutes = ref(0)
const saving = ref(false)
const detail = ref<string | null>(null)
const error = ref<string | null>(null)

const eligible = computed(() => canEditOfferOvertimePreference(currentUserSummary.value))
const persistedMinutes = computed(() => {
  const value = currentUserSummary.value?.offer_overtime_minutes
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
})
const currentLine = computed(() => formatCurrentPreferenceLine(draftMinutes.value))
const dirty = computed(() => draftMinutes.value !== persistedMinutes.value)

watch(
  persistedMinutes,
  (value) => {
    draftMinutes.value = value
  },
  { immediate: true },
)

function normalizeDraft(value: number) {
  if (!Number.isInteger(value) || value < OVERTIME_MIN_MINUTES || value > OVERTIME_MAX_MINUTES) {
    return null
  }
  return value
}

async function save() {
  if (!eligible.value || saving.value) return
  const minutes = normalizeDraft(Number(draftMinutes.value))
  if (minutes == null) {
    error.value = M8_INVALID_VALUE
    detail.value = null
    return
  }
  saving.value = true
  error.value = null
  detail.value = null
  try {
    const result = await saveOfferOvertimePreference(minutes)
    cacheCurrentUserSummary({
      ...(currentUserSummary.value || { role: 'عادی' }),
      offer_overtime_minutes: result.offer_overtime_minutes,
    })
    draftMinutes.value = result.offer_overtime_minutes
    detail.value = result.detail || formatSaveSuccessDetail(result.offer_overtime_minutes)
  } catch (err) {
    const message = err instanceof Error ? err.message.trim() : ''
    error.value = message || M8_INVALID_VALUE
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section
    v-if="eligible"
    class="overtime-pref"
    :class="{ 'overtime-pref--compact': compact }"
    aria-labelledby="overtime-pref-title"
  >
    <div class="overtime-pref__copy">
      <h3 id="overtime-pref-title">{{ M9_LABEL }}</h3>
      <p>{{ M9_HELPER }}</p>
      <p class="overtime-pref__current">{{ currentLine }}</p>
    </div>

    <div class="overtime-pref__controls">
      <AppNumberStepper
        v-model="draftMinutes"
        :min="OVERTIME_MIN_MINUTES"
        :max="OVERTIME_MAX_MINUTES"
        :step="1"
        :label="M9_LABEL"
        :invalid="Boolean(error)"
      />
      <AppButton
        type="button"
        variant="primary"
        :disabled="saving || !dirty"
        :loading="saving"
        @click="save"
      >
        ذخیره
      </AppButton>
    </div>

    <p v-if="detail" class="overtime-pref__detail" role="status">{{ detail }}</p>
    <p v-if="error" class="overtime-pref__error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.overtime-pref {
  display: grid;
  gap: 0.85rem;
  padding: 1rem;
  border-radius: 0.9rem;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}

.overtime-pref--compact {
  padding: 0.85rem;
}

.overtime-pref__copy h3 {
  margin: 0 0 0.35rem;
  font-size: 0.95rem;
  font-weight: 700;
  color: #0f172a;
}

.overtime-pref__copy p {
  margin: 0;
  font-size: 0.8rem;
  line-height: 1.55;
  color: #475569;
}

.overtime-pref__current {
  margin-top: 0.45rem !important;
  color: var(--ds-primary-700) !important;
  font-weight: 600;
}

.overtime-pref__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}

.overtime-pref__detail,
.overtime-pref__error {
  margin: 0;
  font-size: 0.8rem;
  line-height: 1.5;
}

.overtime-pref__detail {
  color: #047857;
}

.overtime-pref__error {
  color: #b91c1c;
}
</style>
