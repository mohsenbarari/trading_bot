<script setup lang="ts">
import { computed, ref } from 'vue'
import { X } from 'lucide-vue-next'
import { apiFetch } from '../utils/auth'

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

const canSubmit = computed(() => content.value.trim().length > 0 && selectedTargets.value.length > 0 && !isSubmitting.value)

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
  <Teleport to="body">
    <div class="broadcast-modal-backdrop" @click.self="emit('close')">
      <section class="broadcast-modal" role="dialog" aria-modal="true" aria-labelledby="broadcast-modal-title">
        <header class="broadcast-modal-header">
          <div>
            <h2 id="broadcast-modal-title">ارسال پیام مدیریت</h2>
            <p>این پیام مستقل از کانال‌ها برای گیرندگان انتخاب‌شده در پیام‌رسان ارسال می‌شود.</p>
          </div>
          <button type="button" class="icon-btn" aria-label="بستن" @click="emit('close')">
            <X :size="20" />
          </button>
        </header>

        <textarea v-model="content" class="broadcast-textarea" rows="6" placeholder="متن پیام مدیریت..."></textarea>

        <div class="target-grid" aria-label="گروه‌های هدف">
          <label v-for="option in targetOptions" :key="option.key" class="target-option">
            <input v-model="selectedTargets" type="checkbox" :value="option.key" />
            <span>{{ option.label }}</span>
          </label>
        </div>

        <div v-if="error" class="form-alert error">{{ error }}</div>
        <div v-if="success" class="form-alert success">{{ success }}</div>

        <footer class="broadcast-actions">
          <button type="button" class="ghost-btn" @click="emit('close')">بستن</button>
          <button type="button" class="primary-btn" :disabled="!canSubmit" @click="submitBroadcast">
            {{ isSubmitting ? 'در حال ارسال...' : 'ارسال' }}
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.broadcast-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 1rem;
  background: rgba(15, 23, 42, 0.42);
  backdrop-filter: blur(8px);
}

.broadcast-modal {
  width: min(100%, 560px);
  max-height: min(88dvh, 720px);
  overflow-y: auto;
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid rgba(15, 118, 110, 0.16);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.24);
  padding: 1rem;
}

.broadcast-modal-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.85rem;
}

.broadcast-modal-header h2 {
  margin: 0 0 0.25rem;
  font-size: 1.05rem;
  font-weight: 950;
  color: #0f766e;
}

.broadcast-modal-header p {
  margin: 0;
  font-size: 0.78rem;
  line-height: 1.7;
  color: #64748b;
}

.icon-btn,
.ghost-btn,
.primary-btn {
  border: 0;
  font: inherit;
  cursor: pointer;
}

.icon-btn {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.06);
  color: #0f172a;
}

.broadcast-textarea {
  width: 100%;
  resize: vertical;
  min-height: 140px;
  border: 1px solid rgba(15, 118, 110, 0.2);
  border-radius: 16px;
  padding: 0.85rem;
  font: inherit;
  line-height: 1.8;
  color: #0f172a;
  background: #fff;
}

.target-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.6rem;
  margin: 0.85rem 0;
}

.target-option {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.7rem 0.75rem;
  border-radius: 14px;
  background: rgba(236, 253, 245, 0.72);
  border: 1px solid rgba(15, 118, 110, 0.14);
  font-size: 0.86rem;
  font-weight: 800;
}

.form-alert {
  margin-top: 0.75rem;
  padding: 0.75rem 0.85rem;
  border-radius: 14px;
  font-size: 0.82rem;
  font-weight: 800;
}

.form-alert.error {
  background: rgba(254, 226, 226, 0.92);
  color: #b91c1c;
}

.form-alert.success {
  background: rgba(220, 252, 231, 0.92);
  color: #047857;
}

.broadcast-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.65rem;
  margin-top: 1rem;
}

.ghost-btn,
.primary-btn {
  min-height: 42px;
  padding: 0 1rem;
  border-radius: 999px;
  font-weight: 900;
}

.ghost-btn {
  background: rgba(15, 23, 42, 0.06);
  color: #334155;
}

.primary-btn {
  background: linear-gradient(135deg, #0f766e, #f59e0b);
  color: #fff;
}

.primary-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

@media (min-width: 640px) {
  .broadcast-modal-backdrop {
    align-items: center;
  }
}
</style>
