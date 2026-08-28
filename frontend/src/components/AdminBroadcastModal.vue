<script setup lang="ts">
import { computed, ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { AppBottomSheet, AppButton, AppTextarea } from './ui'

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'sent'): void
}>()

const targetOptions = [
  { key: 'users', label: 'کاربران' },
  { key: 'managers', label: 'مدیران' },
  { key: 'accountants', label: 'حسابداران' },
  { key: 'customers', label: 'مشتریان' },
]

const content = ref('')
const selectedTargets = ref<string[]>(targetOptions.map((option) => option.key))
const isSubmitting = ref(false)
const error = ref('')
const success = ref('')

const canSubmit = computed(
  () => content.value.trim().length > 0 && selectedTargets.value.length > 0 && !isSubmitting.value,
)

async function submitBroadcast() {
  if (!canSubmit.value) return
  isSubmitting.value = true
  error.value = ''
  success.value = ''
  try {
    const response = await apiFetch('/api/admin-messages/broadcasts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content.value.trim(), target_groups: selectedTargets.value }),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload?.detail || 'ارسال پیام مدیریت ناموفق بود')
    }
    success.value = `پیام برای ${Number(payload.recipient_count || 0).toLocaleString('fa-IR')} نفر ارسال شد.`
    content.value = ''
    emit('sent')
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'ارسال پیام مدیریت ناموفق بود'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <AppBottomSheet
    :open="true"
    title="ارسال پیام مدیریت"
    description="این پیام مستقل از کانال‌ها برای گیرندگان انتخاب‌شده در پیام‌رسان ارسال می‌شود."
    backdrop-class="broadcast-sheet-layer"
    panel-class="broadcast-sheet"
    body-class="broadcast-sheet-body"
    actions-class="broadcast-actions ds-native-actions ds-native-actions--split"
    @close="emit('close')"
  >
    <AppTextarea
      v-model="content"
      class="broadcast-textarea"
      rows="6"
      placeholder="متن پیام مدیریت..."
    />

    <div class="target-grid" aria-label="گروه‌های هدف">
      <label v-for="option in targetOptions" :key="option.key" class="target-option">
        <input v-model="selectedTargets" type="checkbox" :value="option.key" />
        <span>{{ option.label }}</span>
      </label>
    </div>

    <div v-if="error" class="form-alert error">{{ error }}</div>
    <div v-if="success" class="form-alert success">{{ success }}</div>

    <template #actions>
      <AppButton type="button" variant="secondary" @click="emit('close')">بستن</AppButton>
      <AppButton type="button" :disabled="!canSubmit" :loading="isSubmitting" @click="submitBroadcast">
        ارسال
      </AppButton>
    </template>
  </AppBottomSheet>
</template>

<style scoped>
.broadcast-sheet-layer {
  z-index: 1200;
}

.broadcast-textarea {
  width: 100%;
  resize: vertical;
  min-height: 140px;
}

.target-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0;
  margin: 0.85rem 0;
  overflow: hidden;
  border-radius: 12px;
  background: var(--ds-bg-card);
}

.target-option {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  min-height: var(--ds-native-row-min-height, 48px);
  padding: 0.7rem 0.75rem;
  border-block-end: 1px solid var(--ds-native-hairline);
  font-size: var(--ds-font-sm);
  font-weight: 800;
}

.target-option:last-child {
  border-block-end: 0;
}

.form-alert {
  margin-top: 0.75rem;
  padding: 0.75rem 0.85rem;
  border-radius: 12px;
  font-size: var(--ds-font-sm);
  font-weight: 800;
}

.form-alert.error {
  background: var(--ds-danger-50, #fef2f2);
  color: var(--ds-danger-700, #b91c1c);
}

.form-alert.success {
  background: var(--ds-success-50, #ecfdf5);
  color: var(--ds-success-700, #047857);
}

.broadcast-actions {
  margin-top: 0;
}
</style>
