<script setup lang="ts">
import { computed, ref, watch } from 'vue'

type Candidate = {
  commodity_id: number
  commodity_code?: string
  commodity_name: string
  center_project_price?: number
  lower_project_price?: number
  upper_project_price?: number
}

const props = defineProps<{
  candidates: Candidate[]
  editCandidates?: Candidate[]
  lowDateHint?: boolean
  startEditing?: boolean
}>()

const emit = defineEmits<{
  (e: 'select', candidate: Candidate, explicitCorrection: boolean): void
  (e: 'cancel'): void
}>()

const editing = ref(props.startEditing === true)
const isSingleSuggestion = computed(() => props.candidates.length === 1 && !editing.value)
const visibleCandidates = computed(() => (
  editing.value ? (props.editCandidates ?? []) : props.candidates
))
const canEditSuggestion = computed(() => {
  const suggestedId = props.candidates[0]?.commodity_id
  return (props.editCandidates ?? []).some((candidate) => candidate.commodity_id !== suggestedId)
})

watch(
  () => [props.candidates, props.startEditing] as const,
  () => {
    editing.value = props.startEditing === true
  },
)

function confirmSuggestion() {
  const candidate = props.candidates[0]
  if (candidate) emit('select', candidate, false)
}

function selectCandidate(candidate: Candidate) {
  emit('select', candidate, editing.value)
}
</script>

<template>
  <div class="commodity-inference-overlay" @click.self="emit('cancel')">
    <section class="commodity-inference-card" data-test="commodity-inference-selector" role="dialog" aria-modal="true" aria-labelledby="commodity-inference-title">
      <div class="commodity-inference-heading">
        <div>
          <p>تشخیص کالا</p>
          <h2 id="commodity-inference-title">
            {{ isSingleSuggestion ? 'کالای پیشنهادی' : 'کالا را انتخاب کنید' }}
          </h2>
        </div>
        <button type="button" aria-label="بستن" @click="emit('cancel')">×</button>
      </div>
      <p class="commodity-inference-copy">
        <template v-if="editing">کالای درست را از گزینه‌های نزدیک به قیمت لفظ (تا ۱۰٪ اختلاف) انتخاب کنید.</template>
        <template v-else-if="isSingleSuggestion">مدل این کالا را پیشنهاد می‌دهد. درستی آن را تأیید کنید.</template>
        <template v-else>یکی از کالاهای پیشنهادی را انتخاب کنید.</template>
        <span v-if="lowDateHint" class="commodity-inference-hint">فقط کالاهای تاریخ پایین نمایش داده شده‌اند.</span>
      </p>
      <div
        v-if="isSingleSuggestion"
        class="commodity-inference-suggestion"
        data-test="commodity-inference-suggestion"
      >
        <strong>{{ props.candidates[0]?.commodity_name }}</strong>
      </div>
      <div v-else class="commodity-inference-options">
        <button
          v-for="candidate in visibleCandidates"
          :key="candidate.commodity_id"
          type="button"
          class="commodity-inference-option"
          :data-test="`commodity-inference-option-${candidate.commodity_id}`"
          @click="selectCandidate(candidate)"
        >
          <strong>{{ candidate.commodity_name }}</strong>
        </button>
      </div>
      <div class="commodity-inference-actions">
        <button
          v-if="isSingleSuggestion"
          type="button"
          class="commodity-inference-confirm"
          data-test="commodity-inference-confirm"
          @click="confirmSuggestion"
        >
          تأیید
        </button>
        <button
          v-if="isSingleSuggestion && canEditSuggestion"
          type="button"
          class="commodity-inference-edit"
          data-test="commodity-inference-edit"
          @click="editing = true"
        >
          ویرایش
        </button>
        <button type="button" data-test="commodity-inference-cancel" @click="emit('cancel')">انصراف</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.commodity-inference-overlay { position: fixed; inset: 0; z-index: 1200; display: flex; align-items: center; justify-content: center; padding: 1rem; background: rgba(15, 23, 42, .52); backdrop-filter: blur(10px); }
.commodity-inference-card { width: min(100%, 31rem); padding: 1.25rem; border: 1px solid var(--ds-border-light, #e2e8f0); border-radius: 1.25rem; background: var(--ds-bg-card, #fff); box-shadow: 0 28px 80px rgba(15, 23, 42, .24); }
.commodity-inference-heading { display: flex; justify-content: space-between; gap: 1rem; }
.commodity-inference-heading p { margin: 0 0 .2rem; color: var(--ds-accent, #b45309); font-size: .8rem; font-weight: 700; }
.commodity-inference-heading h2 { margin: 0; color: var(--ds-text-primary, #0f172a); font-size: 1.05rem; }
.commodity-inference-heading button { width: 2rem; height: 2rem; border: 1px solid var(--ds-border-light, #e2e8f0); border-radius: 50%; background: transparent; font-size: 1.2rem; }
.commodity-inference-copy { margin: 1rem 0; color: var(--ds-text-secondary, #475569); font-size: .9rem; line-height: 1.8; }
.commodity-inference-hint { display: block; margin-top: .3rem; font-size: .8rem; }
.commodity-inference-suggestion { display: grid; place-items: center; min-height: 5rem; padding: 1rem; border: 1px solid var(--ds-primary, #f59e0b); border-radius: .9rem; background: var(--ds-primary-50, #fffbeb); color: var(--ds-text-primary, #0f172a); }
.commodity-inference-suggestion strong { font-size: 1.15rem; }
.commodity-inference-options { display: grid; gap: .65rem; }
.commodity-inference-option { display: grid; gap: .25rem; width: 100%; min-height: 2.75rem; padding: .7rem .9rem; border: 1px solid var(--ds-border-light, #e2e8f0); border-radius: .8rem; background: var(--ds-bg-page, #f8fafc); text-align: right; color: var(--ds-text-primary, #0f172a); cursor: pointer; }
.commodity-inference-option:hover { border-color: var(--ds-primary, #f59e0b); background: var(--ds-primary-50, #fffbeb); }
.commodity-inference-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .65rem; margin-top: 1.15rem; }
.commodity-inference-actions button { min-height: 2.75rem; padding: .6rem .9rem; border: 1px solid var(--ds-border-light, #e2e8f0); border-radius: .65rem; background: transparent; color: var(--ds-text-secondary, #475569); }
.commodity-inference-actions .commodity-inference-confirm { border-color: var(--ds-primary, #f59e0b); background: var(--ds-primary, #f59e0b); color: #fff; }
.commodity-inference-actions .commodity-inference-edit { border-color: var(--ds-primary, #f59e0b); color: var(--ds-primary, #f59e0b); }
</style>
