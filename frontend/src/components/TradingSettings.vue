<template>
  <div class="settings-page">
    <div class="page-header">
      <h1>⚙️ تنظیمات سیستم</h1>
      <p>تنظیمات قابل تغییر سیستم معاملاتی</p>
    </div>

    <div v-if="loading" class="loading">
      <div class="spinner"></div>
      <p>در حال بارگذاری...</p>
    </div>

    <div v-else class="settings-form">
      <!-- دعوت‌نامه -->
      <div class="accordion-section">
        <div class="accordion-header" @click="toggleSection('invitation')">
          <h2>📨 دعوت‌نامه</h2>
          <span class="accordion-icon">{{ openSections.invitation ? '▼' : '◀' }}</span>
        </div>
        <div v-show="openSections.invitation" class="accordion-content">
          <div class="form-group">
            <label>مدت اعتبار لینک دعوت (روز)</label>
            <input type="number" v-model.number="settings.invitation_expiry_days" min="1" />
            <span class="hint">پیش‌فرض: 2 روز</span>
          </div>
        </div>
      </div>

      <!-- لفظ -->
      <div class="accordion-section">
        <div class="accordion-header" @click="toggleSection('offer')">
          <h2>📋 لفظ معاملاتی</h2>
          <span class="accordion-icon">{{ openSections.offer ? '▼' : '◀' }}</span>
        </div>
        <div v-show="openSections.offer" class="accordion-content">
          <div class="form-group">
            <label>مدت اعتبار لفظ (دقیقه)</label>
            <input type="number" v-model.number="settings.offer_expiry_minutes" min="1" />
            <span class="hint">پیش‌فرض: 2 دقیقه</span>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>حداقل تعداد کالا</label>
              <input type="number" v-model.number="settings.offer_min_quantity" min="1" />
              <span class="hint">پیش‌فرض: 5</span>
            </div>
            <div class="form-group">
              <label>حداکثر تعداد کالا</label>
              <input type="number" v-model.number="settings.offer_max_quantity" min="1" />
              <span class="hint">پیش‌فرض: 50</span>
            </div>
          </div>
          <div class="form-group">
            <label>حداکثر لفظ‌های فعال همزمان</label>
            <input type="number" v-model.number="settings.max_active_offers" min="1" max="20" />
            <span class="hint">پیش‌فرض: 4</span>
          </div>
        </div>
      </div>

      <!-- محدودیت منقضی شدن -->
      <div class="accordion-section">
        <div class="accordion-header" @click="toggleSection('expire')">
          <h2>⏰ محدودیت منقضی کردن</h2>
          <span class="accordion-icon">{{ openSections.expire ? '▼' : '◀' }}</span>
        </div>
        <div v-show="openSections.expire" class="accordion-content">
          <div class="form-group">
            <label>حداکثر منقضی شدن در دقیقه</label>
            <input type="number" v-model.number="settings.offer_expire_rate_per_minute" min="1" max="10" />
            <span class="hint">پیش‌فرض: 2 بار</span>
          </div>
          <div class="form-group">
            <label>آستانه منقضی شدن روزانه</label>
            <input type="number" v-model.number="settings.offer_expire_daily_limit_after_threshold" min="1" />
            <span class="hint">پیش‌فرض: 10 (بعد از این تعداد، محدودیت 1/3 اعمال می‌شود)</span>
          </div>
        </div>
      </div>

      <!-- دکمه‌ها -->
      <div class="form-actions">
        <button class="btn btn-primary" @click="saveSettings" :disabled="saving">
          {{ saving ? 'در حال ذخیره...' : '💾 ذخیره تنظیمات' }}
        </button>
        <button class="btn btn-secondary" @click="resetSettings" :disabled="saving">
          🔄 بازنشانی به پیش‌فرض
        </button>
      </div>

      <div v-if="message" :class="['message', messageType]">
        {{ message }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const props = defineProps({
  apiBaseUrl: { type: String, required: true },
  jwtToken: { type: String, required: true }
})

const loading = ref(true)
const saving = ref(false)
const message = ref('')
const messageType = ref('success')

const openSections = ref({
  invitation: true,
  offer: false,
  expire: false
})

const toggleSection = (section) => {
  openSections.value[section] = !openSections.value[section]
}

const settings = ref({
  invitation_expiry_days: 2,
  offer_expiry_minutes: 2,
  offer_min_quantity: 5,
  offer_max_quantity: 50,
  max_active_offers: 4,
  offer_expire_rate_per_minute: 2,
  offer_expire_daily_limit_after_threshold: 10
})

const fetchApi = async (method, endpoint, body = null) => {
  const options = {
    method,
    headers: {
      'Authorization': `Bearer ${props.jwtToken}`,
      'Content-Type': 'application/json'
    }
  }
  if (body) {
    options.body = JSON.stringify(body)
  }
  const response = await fetch(`${props.apiBaseUrl}/api${endpoint}`, options)
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || 'خطا در ارتباط با سرور')
  }
  return response.json()
}

const loadSettings = async () => {
  try {
    loading.value = true
    const data = await fetchApi('GET', '/trading-settings/')
    settings.value = data
  } catch (error) {
    message.value = 'خطا در بارگذاری تنظیمات'
    messageType.value = 'error'
  } finally {
    loading.value = false
  }
}

const saveSettings = async () => {
  try {
    saving.value = true
    message.value = ''
    
    const data = await fetchApi('PUT', '/trading-settings/', settings.value)
    settings.value = data
    
    message.value = 'تنظیمات با موفقیت ذخیره شد'
    messageType.value = 'success'
  } catch (error) {
    message.value = error.message || 'خطا در ذخیره تنظیمات'
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}

const resetSettings = async () => {
  if (!confirm('آیا از بازنشانی تنظیمات به مقادیر پیش‌فرض مطمئن هستید؟')) {
    return
  }
  
  try {
    saving.value = true
    message.value = ''
    
    const data = await fetchApi('POST', '/trading-settings/reset')
    settings.value = data
    
    message.value = 'تنظیمات به مقادیر پیش‌فرض بازنشانی شد'
    messageType.value = 'success'
  } catch (error) {
    message.value = 'خطا در بازنشانی تنظیمات'
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadSettings()
})
</script>

<style scoped>
.settings-page {
  max-width: 100%;
  margin: 0 auto;
}

.page-header {
  text-align: center;
  margin-bottom: 1.5rem;
}
.page-header h1 {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 800;
  color: #1f2937;
}
.page-header p {
  color: #9ca3af;
  margin-top: 0.375rem;
  font-size: 0.8rem;
}

.loading {
  text-align: center;
  padding: 2.5rem;
}
.spinner {
  width: 36px;
  height: 36px;
  border: 3px solid #f59e0b;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 0.875rem;
}
@keyframes spin { to { transform: rotate(360deg); } }

.settings-form {
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(245, 158, 11, 0.1);
  border-radius: 1.25rem;
  padding: 1.25rem;
  box-shadow: 0 4px 16px rgba(0,0,0,0.04);
}

.accordion-section {
  margin-bottom: 0.75rem;
  border: 1px solid rgba(245, 158, 11, 0.12);
  border-radius: 1rem;
  overflow: hidden;
}

.accordion-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.875rem 1rem;
  background: linear-gradient(135deg, #fffbeb, #fef9f0);
  cursor: pointer;
  transition: background 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.accordion-header:active {
  background: #fef3c7;
}
.accordion-header h2 {
  font-size: 0.85rem;
  font-weight: 700;
  margin: 0;
  color: #1f2937;
}
.accordion-icon {
  font-size: 0.7rem;
  color: #d97706;
  transition: transform 0.2s;
}

.accordion-content {
  padding: 1rem;
  border-top: 1px solid rgba(245, 158, 11, 0.08);
  background: white;
  animation: slideDown 0.2s ease-out;
}
@keyframes slideDown {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}

.form-group { margin-bottom: 0.875rem; }
.form-group label {
  display: block; margin-bottom: 0.375rem;
  font-weight: 700; font-size: 0.78rem; color: #6b7280;
}
.form-group input {
  width: 100%; padding: 0.625rem 0.875rem;
  border: 1px solid rgba(245, 158, 11, 0.15); border-radius: 0.75rem;
  font-size: 0.9rem; background: white; color: #1f2937; outline: none;
  transition: all 0.2s;
}
.form-group input:focus {
  border-color: #f59e0b;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1);
}

.hint {
  display: block; font-size: 0.7rem; color: #d1d5db;
  margin-top: 0.25rem;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.875rem;
}

.form-actions {
  display: flex;
  gap: 0.625rem;
  margin-top: 1.25rem;
}

.btn {
  padding: 0.75rem 1.25rem;
  border: none;
  border-radius: 0.75rem;
  font-size: 0.85rem;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.btn:active { transform: scale(0.98); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-primary {
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white;
  flex: 1;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.25);
}

.btn-secondary {
  background: white;
  color: #6b7280;
  border: 1px solid rgba(245, 158, 11, 0.15);
}
.btn-secondary:active { background: #f9fafb; }

.message {
  margin-top: 1rem;
  padding: 0.75rem;
  border-radius: 0.75rem;
  text-align: center;
  font-size: 0.8rem;
  font-weight: 600;
}
.message.success { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }
.message.error { background: #fef2f2; color: #ef4444; border: 1px solid #fecaca; }
</style>
