<script setup lang="ts">
type Candidate = {
  commodity_id: number
  commodity_name: string
  center_project_price: number
  lower_project_price: number
  upper_project_price: number
}

defineProps<{
  candidates: Candidate[]
  lowDateHint?: boolean
}>()

const emit = defineEmits<{
  (e: 'select', candidate: Candidate): void
  (e: 'edit'): void
  (e: 'cancel'): void
}>()

function price(value: number) {
  return value.toLocaleString()
}
</script>

<template>
  <div class="commodity-inference-overlay" @click.self="emit('cancel')">
    <section class="commodity-inference-card" data-test="commodity-inference-selector" role="dialog" aria-modal="true" aria-labelledby="commodity-inference-title">
      <div class="commodity-inference-heading">
        <div>
          <p>تشخیص از روی قیمت</p>
          <h2 id="commodity-inference-title">کالای آفر را انتخاب کنید</h2>
        </div>
        <button type="button" aria-label="بستن" @click="emit('cancel')">×</button>
      </div>
      <p class="commodity-inference-copy">
        <template v-if="lowDateHint">با توجه به «پ»، فقط گزینه‌های تاریخ پایین نمایش داده شده‌اند.</template>
        <template v-else>قیمت آفر در بازهٔ بیش از یک کالای هم‌گروه قرار دارد. انتخاب شما پیش از ثبت نهایی دوباره با نرخ لحظه‌ای سنجیده می‌شود.</template>
      </p>
      <div class="commodity-inference-options">
        <button
          v-for="candidate in candidates"
          :key="candidate.commodity_id"
          type="button"
          class="commodity-inference-option"
          :data-test="`commodity-inference-option-${candidate.commodity_id}`"
          @click="emit('select', candidate)"
        >
          <strong>{{ candidate.commodity_name }}</strong>
          <span>بازهٔ مدل: {{ price(candidate.lower_project_price) }} تا {{ price(candidate.upper_project_price) }}</span>
        </button>
      </div>
      <div class="commodity-inference-actions">
        <button type="button" @click="emit('cancel')">انصراف</button>
        <button type="button" class="commodity-inference-edit" @click="emit('edit')">ویرایش متن</button>
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
.commodity-inference-options { display: grid; gap: .65rem; }
.commodity-inference-option { display: grid; gap: .25rem; width: 100%; padding: .8rem .9rem; border: 1px solid var(--ds-border-light, #e2e8f0); border-radius: .8rem; background: var(--ds-bg-page, #f8fafc); text-align: right; color: var(--ds-text-primary, #0f172a); cursor: pointer; }
.commodity-inference-option:hover { border-color: var(--ds-primary, #2563eb); background: var(--ds-primary-50, #eff6ff); }
.commodity-inference-option span { color: var(--ds-text-secondary, #475569); font-size: .82rem; }
.commodity-inference-actions { display: flex; justify-content: flex-end; gap: .65rem; margin-top: 1.15rem; }
.commodity-inference-actions button { padding: .6rem .9rem; border: 1px solid var(--ds-border-light, #e2e8f0); border-radius: .65rem; background: transparent; color: var(--ds-text-secondary, #475569); }
.commodity-inference-actions .commodity-inference-edit { border-color: var(--ds-primary, #2563eb); background: var(--ds-primary, #2563eb); color: #fff; }
</style>
