<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { apiFetch } from '../utils/auth'
import { Loader2, ChevronLeft, ChevronDown, Save, RotateCcw, Mail, ClipboardList, Clock, ShieldCheck, AlertCircle } from 'lucide-vue-next'

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

const settings = ref({ ...defaultVals })

const isDefault = (key: keyof typeof defaultVals) => {
  return settings.value[key] === defaultVals[key] || settings.value[key] == null || settings.value[key] === ''
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
    settings.value = data
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
    
    const data = await fetchApi('PUT', '/trading-settings/', settings.value)
    settings.value = data
    
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
    settings.value = data
    
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
          <div class="header-main">
            <Mail class="section-icon" :size="18" />
            <h2 class="section-title">دعوت‌نامه</h2>
          </div>
          <ChevronLeft class="arrow-icon" :size="18" />
        </div>
        <div v-show="openSections.invitation" class="ds-accordion-content">
          <div class="ds-form-group">
            <label class="ds-label">مدت اعتبار لینک دعوت (روز)</label>
            <input type="number" v-model.number="settings.invitation_expiry_days" min="1" class="ds-input" :class="{'is-default': isDefault('invitation_expiry_days')}" />
            <span class="ds-hint">پیش‌فرض: 2 روز</span>
          </div>
        </div>
      </div>

      <!-- لفظ -->
      <div class="ds-accordion" :class="{ open: openSections.offer }">
        <div class="ds-accordion-header" @click="toggleSection('offer')">
          <div class="header-main">
            <ClipboardList class="section-icon" :size="18" />
            <h2 class="section-title">لفظ معاملاتی</h2>
          </div>
          <ChevronLeft class="arrow-icon" :size="18" />
        </div>
        <div v-show="openSections.offer" class="ds-accordion-content">
          <div class="ds-form-group">
            <label class="ds-label">مدت اعتبار لفظ (دقیقه)</label>
            <input type="number" v-model.number="settings.offer_expiry_minutes" min="1" class="ds-input" :class="{'is-default': isDefault('offer_expiry_minutes')}" />
            <span class="ds-hint">پیش‌فرض: 2 دقیقه</span>
          </div>
          <div class="form-row">
            <div class="ds-form-group">
              <label class="ds-label">حداقل تعداد کالا</label>
              <input type="number" v-model.number="settings.offer_min_quantity" min="1" class="ds-input" :class="{'is-default': isDefault('offer_min_quantity')}" />
              <span class="ds-hint">پیش‌فرض: 5</span>
            </div>
            <div class="ds-form-group">
              <label class="ds-label">حداکثر تعداد کالا</label>
              <input type="number" v-model.number="settings.offer_max_quantity" min="1" class="ds-input" :class="{'is-default': isDefault('offer_max_quantity')}" />
              <span class="ds-hint">پیش‌فرض: 50</span>
            </div>
          </div>
          <div class="ds-form-group">
            <label class="ds-label">حداکثر لفظ‌های فعال همزمان</label>
            <input type="number" v-model.number="settings.max_active_offers" min="1" max="20" class="ds-input" :class="{'is-default': isDefault('max_active_offers')}" />
            <span class="ds-hint">پیش‌فرض: 4</span>
          </div>
        </div>
      </div>

      <!-- محدودیت منقضی کردن -->
      <div class="ds-accordion" :class="{ open: openSections.expire }">
        <div class="ds-accordion-header" @click="toggleSection('expire')">
          <div class="header-main">
            <Clock class="section-icon" :size="18" />
            <h2 class="section-title">محدودیت منقضی کردن</h2>
          </div>
          <ChevronLeft class="arrow-icon" :size="18" />
        </div>
        <div v-show="openSections.expire" class="ds-accordion-content">
          <div class="ds-form-group">
            <label class="ds-label">حداکثر منقضی شدن در دقیقه</label>
            <input type="number" v-model.number="settings.offer_expire_rate_per_minute" min="1" max="10" class="ds-input" :class="{'is-default': isDefault('offer_expire_rate_per_minute')}" />
            <span class="ds-hint">پیش‌فرض: 2 بار</span>
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه منقضی شدن روزانه</label>
            <input type="number" v-model.number="settings.offer_expire_daily_limit_after_threshold" min="1" class="ds-input" :class="{'is-default': isDefault('offer_expire_daily_limit_after_threshold')}" />
            <span class="ds-hint">پیش‌فرض: 10 (بعد از این تعداد، محدودیت اعمال می‌شود)</span>
          </div>
        </div>
      </div>

      <!-- امنیت -->
      <div class="ds-accordion" :class="{ open: openSections.security }">
        <div class="ds-accordion-header" @click="toggleSection('security')">
          <div class="header-main">
            <ShieldCheck class="section-icon" :size="18" />
            <h2 class="section-title">امنیت و نشست‌ها</h2>
          </div>
          <ChevronLeft class="arrow-icon" :size="18" />
        </div>
        <div v-show="openSections.security" class="ds-accordion-content">
          <div class="info-note">
            <AlertCircle :size="16" />
            <span>این مقادیر آستانه پایه برای جلوگیری از سوءاستفاده (Anti-Abuse) هستند. برای کاربرانی با بیش از یک نشست فعال، سیستم به صورت خودکار آستانه را افزایش می‌دهد.</span>
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه پایه روزانه</label>
            <input type="number" v-model.number="settings.anti_abuse_daily_base" min="1" class="ds-input" :class="{'is-default': isDefault('anti_abuse_daily_base')}" />
            <span class="ds-hint">تعداد لاگین مجاز در ۲۴ ساعت</span>
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه پایه هفتگی</label>
            <input type="number" v-model.number="settings.anti_abuse_weekly_base" min="1" class="ds-input" :class="{'is-default': isDefault('anti_abuse_weekly_base')}" />
            <span class="ds-hint">تعداد لاگین مجاز در ۷ روز</span>
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه پایه ماهانه</label>
            <input type="number" v-model.number="settings.anti_abuse_monthly_base" min="1" class="ds-input" :class="{'is-default': isDefault('anti_abuse_monthly_base')}" />
            <span class="ds-hint">تعداد لاگین مجاز در ۳۰ روز</span>
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
        <button class="ds-btn secondary-soft action-btn" @click="resetSettings" :disabled="saving">
          <RotateCcw :size="18" />
          <span>بازنشانی</span>
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

.secondary-soft {
  background: var(--ds-bg-inset);
  color: var(--ds-text-secondary);
  border: 1px solid var(--ds-border-light);
}
</style>
