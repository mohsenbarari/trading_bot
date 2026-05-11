<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { apiFetch } from '../utils/auth'
import { Loader2, ChevronLeft, Save, RotateCcw, Mail, ClipboardList, Clock, ShieldCheck, AlertCircle } from 'lucide-vue-next'

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>()

const loading = ref(true)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'danger'>('success')

const openSections = ref({
  invitation: false,
  offer: false,
  expire: false,
  security: false
})

const toggleSection = (section: keyof typeof openSections.value) => {
  openSections.value[section] = !openSections.value[section]
}

const defaultVals = {
  invitation_expiry_days: 2,
  offer_expiry_minutes: 2,
  offer_min_quantity: 5,
  offer_max_quantity: 50,
  max_active_offers: 4,
  offer_expire_rate_per_minute: 2,
  offer_expire_daily_limit_after_threshold: 10,
  anti_abuse_daily_base: 2,
  anti_abuse_weekly_base: 5,
  anti_abuse_monthly_base: 7
}

type EditableSettingKey = keyof typeof defaultVals

const editableSettingKeys = Object.keys(defaultVals) as EditableSettingKey[]

// تنظیمات اصلی که از سرور می‌آیند (برای نمایش در Placeholder)
const settings = ref<Record<EditableSettingKey, number>>({ ...defaultVals })
// مقادیری که کاربر در حال ویرایش آن‌هاست
const form = ref<Partial<Record<EditableSettingKey, number | ''>>>({})

const getResolvedSettingValue = (key: EditableSettingKey) => {
  const draftValue = form.value[key]
  if (typeof draftValue === 'number' && Number.isFinite(draftValue)) {
    return draftValue
  }
  return settings.value[key]
}

const isDefault = (key: EditableSettingKey) => {
  return getResolvedSettingValue(key) === defaultVals[key]
}

const fetchApi = async (method: string, endpoint: string, body: any = null) => {
  const options: RequestInit = { method }
  if (body) {
    options.body = JSON.stringify(body)
  }
  const response = await apiFetch(`/api${endpoint}`, options)
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
    settings.value = { ...defaultVals, ...data }
    form.value = {} // پاک کردن فرم بعد از لود جدید
  } catch (error) {
    message.value = 'خطا در بارگذاری تنظیمات'
    messageType.value = 'danger'
  } finally {
    loading.value = false
  }
}

const saveSettings = async () => {
  try {
    saving.value = true
    message.value = ''
    
    // فقط کلیدهای قابل ویرایش را بفرست تا به فیلدهای محاسباتی/اضافی تکیه نکنیم.
    const payload = editableSettingKeys.reduce((acc, key) => {
      acc[key] = getResolvedSettingValue(key)
      return acc
    }, {} as Record<EditableSettingKey, number>)
    
    const data = await fetchApi('PUT', '/trading-settings/', payload)
    settings.value = { ...defaultVals, ...data }
    form.value = {} // بعد از ذخیره، فرم را خالی کن تا دوباره Placeholderها نمایش داده شوند
    
    message.value = 'تنظیمات با موفقیت ذخیره شد'
    messageType.value = 'success'
  } catch (error: any) {
    message.value = error.message || 'خطا در ذخیره تنظیمات'
    messageType.value = 'danger'
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
    settings.value = { ...defaultVals, ...data }
    form.value = {}
    
    message.value = 'تنظیمات به مقادیر پیش‌فرض بازنشانی شد'
    messageType.value = 'success'
  } catch (error) {
    message.value = 'خطا در بازنشانی تنظیمات'
    messageType.value = 'danger'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadSettings()
})
</script>

<template>
  <div class="trading-settings ds-page-content">
    
    <div v-if="loading" class="ds-loading-state">
       <Loader2 class="ds-spinner" :size="32" />
       <p>در حال بارگذاری تنظیمات...</p>
    </div>

    <div v-else class="settings-container">
      <div v-if="message" class="ds-message" :class="messageType">
        {{ message }}
      </div>

      <!-- دعوت‌نامه -->
      <div class="ds-accordion" :class="{ open: openSections.invitation }">
        <div class="ds-accordion-header" @click="toggleSection('invitation')">
          <div class="ds-accordion-header-info">
            <Mail class="section-icon" :size="18" />
            <h2 class="section-title">دعوت‌نامه</h2>
          </div>
          <ChevronLeft class="ds-accordion-icon" :size="18" />
        </div>
        <div v-show="openSections.invitation" class="ds-accordion-body">
          <div class="ds-form-group">
            <label class="ds-label">مدت اعتبار لینک دعوت (روز)</label>
            <input 
              type="number" 
              v-model.number="form.invitation_expiry_days" 
              :placeholder="settings.invitation_expiry_days?.toString()" 
              min="1" 
              class="ds-input" 
              :class="{'is-default': isDefault('invitation_expiry_days')}" 
            />
          </div>
        </div>
      </div>

      <!-- لفظ -->
      <div class="ds-accordion" :class="{ open: openSections.offer }">
        <div class="ds-accordion-header" @click="toggleSection('offer')">
          <div class="ds-accordion-header-info">
            <ClipboardList class="section-icon" :size="18" />
            <h2 class="section-title">لفظ معاملاتی</h2>
          </div>
          <ChevronLeft class="ds-accordion-icon" :size="18" />
        </div>
        <div v-show="openSections.offer" class="ds-accordion-body">
          <div class="ds-form-group">
            <label class="ds-label">مدت اعتبار لفظ (دقیقه)</label>
            <input 
              type="number" 
              v-model.number="form.offer_expiry_minutes" 
              :placeholder="settings.offer_expiry_minutes?.toString()" 
              min="1" 
              class="ds-input" 
              :class="{'is-default': isDefault('offer_expiry_minutes')}" 
            />
          </div>
          <div class="form-row">
            <div class="ds-form-group">
              <label class="ds-label">حداقل تعداد کالا</label>
              <input 
                type="number" 
                v-model.number="form.offer_min_quantity" 
                :placeholder="settings.offer_min_quantity?.toString()" 
                min="1" 
                class="ds-input" 
                :class="{'is-default': isDefault('offer_min_quantity')}" 
              />
            </div>
            <div class="ds-form-group">
              <label class="ds-label">حداکثر تعداد کالا</label>
              <input 
                type="number" 
                v-model.number="form.offer_max_quantity" 
                :placeholder="settings.offer_max_quantity?.toString()" 
                min="1" 
                class="ds-input" 
                :class="{'is-default': isDefault('offer_max_quantity')}" 
              />
            </div>
          </div>
          <div class="ds-form-group">
            <label class="ds-label">حداکثر لفظ‌های فعال همزمان</label>
            <input 
              type="number" 
              v-model.number="form.max_active_offers" 
              :placeholder="settings.max_active_offers?.toString()" 
              min="1" 
              max="20" 
              class="ds-input" 
              :class="{'is-default': isDefault('max_active_offers')}" 
            />
          </div>
        </div>
      </div>

      <!-- محدودیت منقضی کردن -->
      <div class="ds-accordion" :class="{ open: openSections.expire }">
        <div class="ds-accordion-header" @click="toggleSection('expire')">
          <div class="ds-accordion-header-info">
            <Clock class="section-icon" :size="18" />
            <h2 class="section-title">محدودیت منقضی کردن</h2>
          </div>
          <ChevronLeft class="ds-accordion-icon" :size="18" />
        </div>
        <div v-show="openSections.expire" class="ds-accordion-body">
          <div class="ds-form-group">
            <label class="ds-label">حداکثر منقضی شدن در دقیقه</label>
            <input 
              type="number" 
              v-model.number="form.offer_expire_rate_per_minute" 
              :placeholder="settings.offer_expire_rate_per_minute?.toString()" 
              min="1" 
              max="10" 
              class="ds-input" 
              :class="{'is-default': isDefault('offer_expire_rate_per_minute')}" 
            />
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه منقضی شدن روزانه</label>
            <input 
              type="number" 
              v-model.number="form.offer_expire_daily_limit_after_threshold" 
              :placeholder="settings.offer_expire_daily_limit_after_threshold?.toString()" 
              min="1" 
              class="ds-input" 
              :class="{'is-default': isDefault('offer_expire_daily_limit_after_threshold')}" 
            />
          </div>
        </div>
      </div>

      <!-- امنیت -->
      <div class="ds-accordion" :class="{ open: openSections.security }">
        <div class="ds-accordion-header" @click="toggleSection('security')">
          <div class="ds-accordion-header-info">
            <ShieldCheck class="section-icon" :size="18" />
            <h2 class="section-title">امنیت و نشست‌ها</h2>
          </div>
          <ChevronLeft class="ds-accordion-icon" :size="18" />
        </div>
        <div v-show="openSections.security" class="ds-accordion-body">
          <div class="info-note">
            <AlertCircle :size="16" />
            <span>این مقادیر آستانه پایه برای جلوگیری از سوءاستفاده (Anti-Abuse) هستند. برای کاربرانی با بیش از یک نشست فعال، سیستم به صورت خودکار آستانه را افزایش می‌دهد.</span>
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه پایه روزانه</label>
            <input 
              type="number" 
              v-model.number="form.anti_abuse_daily_base" 
              :placeholder="settings.anti_abuse_daily_base?.toString()" 
              min="1" 
              class="ds-input" 
              :class="{'is-default': isDefault('anti_abuse_daily_base')}" 
            />
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه پایه هفتگی</label>
            <input 
              type="number" 
              v-model.number="form.anti_abuse_weekly_base" 
              :placeholder="settings.anti_abuse_weekly_base?.toString()" 
              min="1" 
              class="ds-input" 
              :class="{'is-default': isDefault('anti_abuse_weekly_base')}" 
            />
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه پایه ماهانه</label>
            <input 
              type="number" 
              v-model.number="form.anti_abuse_monthly_base" 
              :placeholder="settings.anti_abuse_monthly_base?.toString()" 
              min="1" 
              class="ds-input" 
              :class="{'is-default': isDefault('anti_abuse_monthly_base')}" 
            />
          </div>
        </div>
      </div>

      <!-- دکمه‌ها -->
      <div class="footer-actions">
        <button class="ds-btn primary action-btn" @click="saveSettings" :disabled="saving">
          <Loader2 v-if="saving" class="animate-spin" :size="18" />
          <Save v-else :size="18" />
          <span>ذخیره تنظیمات</span>
        </button>
        <button class="ds-btn danger action-btn" @click="resetSettings" :disabled="saving">
          <RotateCcw :size="18" />
          <span>بازنشانی به پیش‌فرض</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trading-settings {
  display: flex;
  flex-direction: column;
}

.settings-container {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.section-icon {
  color: var(--ds-primary-500);
}

.section-title {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 800;
  color: var(--ds-text-primary);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.is-default {
  color: var(--ds-text-placeholder) !important;
}

.info-note {
  display: flex;
  gap: 0.75rem;
  padding: 0.85rem;
  background: var(--ds-primary-50);
  border: 1px solid var(--ds-primary-100);
  border-radius: var(--ds-radius-lg);
  font-size: 0.75rem;
  line-height: 1.6;
  color: var(--ds-primary-800);
  margin-bottom: 1.5rem;
}

.info-note svg {
  flex-shrink: 0;
  margin-top: 2px;
}

.footer-actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 1.5rem;
  padding-top: 1rem;
  border-top: 1px solid var(--ds-border-light);
}

.action-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.85rem;
}
</style>
