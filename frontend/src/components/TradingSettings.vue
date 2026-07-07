<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { apiFetch } from '../utils/auth'
import { Loader2, ChevronLeft, Save, RotateCcw, Mail, ClipboardList, Clock, ShieldCheck, AlertCircle } from 'lucide-vue-next'
import { formatIranDateTime } from '../utils/iranTime'
import JalaliDatePicker from './JalaliDatePicker.vue'
import { AppButton, AppInput, AppSelect } from './ui'

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>()

const loading = ref(true)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'danger'>('success')
const viewportToast = ref<{ type: 'success' | 'danger'; text: string } | null>(null)
let viewportToastTimer: number | null = null

const openSections = ref({
  invitation: false,
  offer: false,
  expire: false,
  security: false,
  marketSchedule: false,
  marketCalendar: false,
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

const showViewportToast = (type: 'success' | 'danger', text: string, timeoutMs = 4200) => {
  viewportToast.value = { type, text }
  if (viewportToastTimer !== null && typeof window !== 'undefined') {
    window.clearTimeout(viewportToastTimer)
  }
  viewportToastTimer = typeof window !== 'undefined'
    ? window.setTimeout(() => {
        viewportToast.value = null
        viewportToastTimer = null
      }, timeoutMs)
    : null
}

const clearViewportToast = () => {
  viewportToast.value = null
  if (viewportToastTimer !== null && typeof window !== 'undefined') {
    window.clearTimeout(viewportToastTimer)
  }
  viewportToastTimer = null
}

const defaultSchedule = {
  market_schedule_enabled: false,
  market_timezone: 'Asia/Tehran',
  market_open_time_local: '09:00',
  market_close_time_local: '18:00',
  market_closed_weekdays: [] as number[],
}

const weekdayOptions = [
  { value: 5, label: 'شنبه' },
  { value: 6, label: 'یکشنبه' },
  { value: 0, label: 'دوشنبه' },
  { value: 1, label: 'سه‌شنبه' },
  { value: 2, label: 'چهارشنبه' },
  { value: 3, label: 'پنج‌شنبه' },
  { value: 4, label: 'جمعه' },
]

const overrideTypeOptions = [
  { value: 'closed_all_day', label: 'تعطیل کامل' },
  { value: 'open_all_day', label: 'باز در تمام روز' },
  { value: 'custom_hours', label: 'ساعات سفارشی' },
]

type MarketRuntimeState = {
  is_open: boolean
  active_web_notice_visible: boolean
  offers_since_last_open: number
  last_transition_at: string | null
  next_transition_at: string | null
}

type MarketScheduleOverrideRow = {
  id: number
  date: string
  override_type: 'closed_all_day' | 'open_all_day' | 'custom_hours'
  open_time_local: string | null
  close_time_local: string | null
  note: string | null
}

type OverrideFormState = {
  date: string
  override_type: MarketScheduleOverrideRow['override_type']
  open_time_local: string
  close_time_local: string
  note: string
}

const createEmptyOverrideForm = (): OverrideFormState => ({
  date: '',
  override_type: 'closed_all_day',
  open_time_local: '09:00',
  close_time_local: '18:00',
  note: '',
})

// تنظیمات اصلی که از سرور می‌آیند (برای نمایش در Placeholder)
const settings = ref<Record<EditableSettingKey, number>>({ ...defaultVals })
// مقادیری که کاربر در حال ویرایش آن‌هاست
const form = ref<Partial<Record<EditableSettingKey, number | ''>>>({})
const scheduleForm = ref({ ...defaultSchedule })
const marketState = ref<MarketRuntimeState | null>(null)
const overrides = ref<MarketScheduleOverrideRow[]>([])
const overridesLoading = ref(false)
const overrideSaving = ref(false)
const overrideDeletingId = ref<number | null>(null)
const editingOverrideId = ref<number | null>(null)
const overrideForm = ref<OverrideFormState>(createEmptyOverrideForm())

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

const sortedOverrides = computed(() => [...overrides.value].sort((a, b) => a.date.localeCompare(b.date)))
const isCustomHoursOverride = computed(() => overrideForm.value.override_type === 'custom_hours')
const marketStatusLabel = computed(() => {
  if (!marketState.value) return 'نامشخص'
  return marketState.value.is_open ? 'باز' : 'بسته'
})
const marketNoticeLabel = computed(() => {
  if (!marketState.value) return 'نامشخص'
  return marketState.value.active_web_notice_visible ? 'یادآور فعال' : 'یادآور مخفی'
})
const formattedNextTransition = computed(() => {
  if (!marketState.value?.next_transition_at) return 'ثبت نشده'
  const value = formatIranDateTime(marketState.value.next_transition_at, {
    dateStyle: 'short',
    timeStyle: 'short',
  })
  return value || 'ثبت نشده'
})

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

const applySettingsResponse = (data: Record<string, any>) => {
  settings.value = { ...defaultVals, ...data }
  scheduleForm.value = {
    market_schedule_enabled: Boolean(data.market_schedule_enabled ?? defaultSchedule.market_schedule_enabled),
    market_timezone: data.market_timezone || defaultSchedule.market_timezone,
    market_open_time_local: data.market_open_time_local || defaultSchedule.market_open_time_local,
    market_close_time_local: data.market_close_time_local || defaultSchedule.market_close_time_local,
    market_closed_weekdays: Array.isArray(data.market_closed_weekdays)
      ? [...data.market_closed_weekdays].sort((a, b) => a - b)
      : [...defaultSchedule.market_closed_weekdays],
  }
}

const loadSettings = async () => {
  try {
    loading.value = true
    const data = await fetchApi('GET', '/trading-settings/')
    applySettingsResponse(data)
    form.value = {} // پاک کردن فرم بعد از لود جدید
  } catch (error) {
    message.value = 'خطا در بارگذاری تنظیمات'
    messageType.value = 'danger'
  } finally {
    loading.value = false
  }
}

const loadMarketState = async () => {
  try {
    marketState.value = await fetchApi('GET', '/trading-settings/market-state')
  } catch (error) {
    message.value = 'خطا در دریافت وضعیت فعلی بازار'
    messageType.value = 'danger'
  }
}

const loadOverrides = async () => {
  try {
    overridesLoading.value = true
    const data = await fetchApi('GET', '/trading-settings/market-overrides')
    overrides.value = Array.isArray(data) ? data : []
  } catch (error) {
    message.value = 'خطا در دریافت استثناهای تقویمی بازار'
    messageType.value = 'danger'
  } finally {
    overridesLoading.value = false
  }
}

const toggleClosedWeekday = (weekday: number) => {
  if (scheduleForm.value.market_closed_weekdays.includes(weekday)) {
    scheduleForm.value.market_closed_weekdays = scheduleForm.value.market_closed_weekdays.filter((value) => value !== weekday)
    return
  }
  scheduleForm.value.market_closed_weekdays = [...scheduleForm.value.market_closed_weekdays, weekday].sort((a, b) => a - b)
}

const resetOverrideForm = () => {
  editingOverrideId.value = null
  overrideForm.value = createEmptyOverrideForm()
}

const startEditingOverride = (item: MarketScheduleOverrideRow) => {
  editingOverrideId.value = item.id
  overrideForm.value = {
    date: item.date,
    override_type: item.override_type,
    open_time_local: item.open_time_local?.slice(0, 5) || '09:00',
    close_time_local: item.close_time_local?.slice(0, 5) || '18:00',
    note: item.note || '',
  }
}

const saveOverride = async () => {
  try {
    overrideSaving.value = true
    message.value = ''

    const payload = {
      date: overrideForm.value.date,
      override_type: overrideForm.value.override_type,
      open_time_local: isCustomHoursOverride.value ? overrideForm.value.open_time_local : null,
      close_time_local: isCustomHoursOverride.value ? overrideForm.value.close_time_local : null,
      note: overrideForm.value.note || null,
    }

    if (editingOverrideId.value) {
      await fetchApi('PUT', `/trading-settings/market-overrides/${editingOverrideId.value}`, payload)
      message.value = 'استثنای تقویمی با موفقیت ویرایش شد'
    } else {
      await fetchApi('POST', '/trading-settings/market-overrides', payload)
      message.value = 'استثنای تقویمی با موفقیت ثبت شد'
    }
    messageType.value = 'success'
    showViewportToast('success', message.value)

    resetOverrideForm()
    await Promise.all([loadOverrides(), loadMarketState()])
  } catch (error: any) {
    message.value = error.message || 'خطا در ذخیره استثنای تقویمی'
    messageType.value = 'danger'
    showViewportToast('danger', message.value)
  } finally {
    overrideSaving.value = false
  }
}

const deleteOverride = async (overrideId: number) => {
  if (!confirm('آیا از حذف این استثنای تقویمی مطمئن هستید؟')) {
    return
  }

  try {
    overrideDeletingId.value = overrideId
    message.value = ''
    await fetchApi('DELETE', `/trading-settings/market-overrides/${overrideId}`)
    if (editingOverrideId.value === overrideId) {
      resetOverrideForm()
    }
    message.value = 'استثنای تقویمی حذف شد'
    messageType.value = 'success'
    showViewportToast('success', message.value)
    await Promise.all([loadOverrides(), loadMarketState()])
  } catch (error: any) {
    message.value = error.message || 'خطا در حذف استثنای تقویمی'
    messageType.value = 'danger'
    showViewportToast('danger', message.value)
  } finally {
    overrideDeletingId.value = null
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
    const fullPayload = {
      ...payload,
      market_schedule_enabled: scheduleForm.value.market_schedule_enabled,
      market_open_time_local: scheduleForm.value.market_open_time_local,
      market_close_time_local: scheduleForm.value.market_close_time_local,
      market_closed_weekdays: [...scheduleForm.value.market_closed_weekdays].sort((a, b) => a - b),
    }

    const data = await fetchApi('PUT', '/trading-settings/', fullPayload)
    applySettingsResponse(data)
    form.value = {} // بعد از ذخیره، فرم را خالی کن تا دوباره Placeholderها نمایش داده شوند
    await loadMarketState()

    message.value = 'تنظیمات با موفقیت ذخیره شد'
    messageType.value = 'success'
    showViewportToast('success', message.value)
  } catch (error: any) {
    message.value = error.message || 'خطا در ذخیره تنظیمات'
    messageType.value = 'danger'
    showViewportToast('danger', message.value)
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
    applySettingsResponse(data)
    form.value = {}
    resetOverrideForm()
    await loadMarketState()

    message.value = 'تنظیمات به مقادیر پیش‌فرض بازنشانی شد'
    messageType.value = 'success'
    showViewportToast('success', message.value)
  } catch (error) {
    message.value = 'خطا در بازنشانی تنظیمات'
    messageType.value = 'danger'
    showViewportToast('danger', message.value)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadSettings()
  loadMarketState()
  loadOverrides()
})

onBeforeUnmount(() => {
  clearViewportToast()
})
</script>

<template>
  <div class="trading-settings ds-page-content">
    <div
      v-if="viewportToast"
      class="settings-viewport-toast"
      :class="`settings-viewport-toast--${viewportToast.type}`"
      role="status"
      aria-live="polite"
    >
      {{ viewportToast.text }}
    </div>

    <div v-if="loading" class="ds-loading-state">
       <Loader2 class="ds-spinner" :size="32" />
       <p>در حال بارگذاری تنظیمات...</p>
    </div>

    <div v-else class="settings-container">
      <div v-if="message" class="ds-message" :class="messageType">
        {{ message }}
      </div>

      <!-- دعوت‌نامه -->
      <div class="settings-section" :class="{ open: openSections.invitation }">
        <AppButton
          id="trading-settings-invitation-header"
          class="settings-section__header"
          type="button"
          variant="ghost"
          :aria-expanded="openSections.invitation"
          aria-controls="trading-settings-invitation-panel"
          @click="toggleSection('invitation')"
        >
          <div class="settings-section__header-info">
            <Mail class="section-icon" :size="18" />
            <h2 class="section-title">دعوت‌نامه</h2>
          </div>
          <ChevronLeft class="settings-section__chevron" :size="18" />
        </AppButton>
        <div
          id="trading-settings-invitation-panel"
          v-show="openSections.invitation"
          class="settings-section__body"
          role="region"
          aria-labelledby="trading-settings-invitation-header"
        >
          <div class="ds-form-group">
            <label class="ds-label">مدت اعتبار لینک دعوت (روز)</label>
            <AppInput
              type="number"
              v-model.number="form.invitation_expiry_days"
              :placeholder="settings.invitation_expiry_days?.toString()"
              min="1"
              class="settings-input"
              :class="{'is-default': isDefault('invitation_expiry_days')}"
            />
          </div>
        </div>
      </div>

      <!-- لفظ -->
      <div class="settings-section" :class="{ open: openSections.offer }">
        <AppButton
          id="trading-settings-offer-header"
          class="settings-section__header"
          type="button"
          variant="ghost"
          :aria-expanded="openSections.offer"
          aria-controls="trading-settings-offer-panel"
          @click="toggleSection('offer')"
        >
          <div class="settings-section__header-info">
            <ClipboardList class="section-icon" :size="18" />
            <h2 class="section-title">لفظ معاملاتی</h2>
          </div>
          <ChevronLeft class="settings-section__chevron" :size="18" />
        </AppButton>
        <div
          id="trading-settings-offer-panel"
          v-show="openSections.offer"
          class="settings-section__body"
          role="region"
          aria-labelledby="trading-settings-offer-header"
        >
          <div class="ds-form-group">
            <label class="ds-label">مدت اعتبار لفظ (دقیقه)</label>
            <AppInput
              type="number"
              v-model.number="form.offer_expiry_minutes"
              :placeholder="settings.offer_expiry_minutes?.toString()"
              min="1"
              class="settings-input"
              :class="{'is-default': isDefault('offer_expiry_minutes')}"
            />
          </div>
          <div class="form-row">
            <div class="ds-form-group">
              <label class="ds-label">حداقل تعداد کالا</label>
              <AppInput
                type="number"
                v-model.number="form.offer_min_quantity"
                :placeholder="settings.offer_min_quantity?.toString()"
                min="1"
                class="settings-input"
                :class="{'is-default': isDefault('offer_min_quantity')}"
              />
            </div>
            <div class="ds-form-group">
              <label class="ds-label">حداکثر تعداد کالا</label>
              <AppInput
                type="number"
                v-model.number="form.offer_max_quantity"
                :placeholder="settings.offer_max_quantity?.toString()"
                min="1"
                class="settings-input"
                :class="{'is-default': isDefault('offer_max_quantity')}"
              />
            </div>
          </div>
          <div class="ds-form-group">
            <label class="ds-label">حداکثر لفظ‌های فعال همزمان</label>
            <AppInput
              type="number"
              v-model.number="form.max_active_offers"
              :placeholder="settings.max_active_offers?.toString()"
              min="1"
              max="20"
              class="settings-input"
              :class="{'is-default': isDefault('max_active_offers')}"
            />
          </div>
        </div>
      </div>

      <!-- محدودیت منقضی کردن -->
      <div class="settings-section" :class="{ open: openSections.expire }">
        <AppButton
          id="trading-settings-expire-header"
          class="settings-section__header"
          type="button"
          variant="ghost"
          :aria-expanded="openSections.expire"
          aria-controls="trading-settings-expire-panel"
          @click="toggleSection('expire')"
        >
          <div class="settings-section__header-info">
            <Clock class="section-icon" :size="18" />
            <h2 class="section-title">محدودیت منقضی کردن</h2>
          </div>
          <ChevronLeft class="settings-section__chevron" :size="18" />
        </AppButton>
        <div
          id="trading-settings-expire-panel"
          v-show="openSections.expire"
          class="settings-section__body"
          role="region"
          aria-labelledby="trading-settings-expire-header"
        >
          <div class="ds-form-group">
            <label class="ds-label">حداکثر منقضی شدن در دقیقه</label>
            <AppInput
              type="number"
              v-model.number="form.offer_expire_rate_per_minute"
              :placeholder="settings.offer_expire_rate_per_minute?.toString()"
              min="1"
              max="10"
              class="settings-input"
              :class="{'is-default': isDefault('offer_expire_rate_per_minute')}"
            />
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه منقضی شدن روزانه</label>
            <AppInput
              type="number"
              v-model.number="form.offer_expire_daily_limit_after_threshold"
              :placeholder="settings.offer_expire_daily_limit_after_threshold?.toString()"
              min="1"
              class="settings-input"
              :class="{'is-default': isDefault('offer_expire_daily_limit_after_threshold')}"
            />
          </div>
        </div>
      </div>

      <!-- امنیت -->
      <div class="settings-section" :class="{ open: openSections.security }">
        <AppButton
          id="trading-settings-security-header"
          class="settings-section__header"
          type="button"
          variant="ghost"
          :aria-expanded="openSections.security"
          aria-controls="trading-settings-security-panel"
          @click="toggleSection('security')"
        >
          <div class="settings-section__header-info">
            <ShieldCheck class="section-icon" :size="18" />
            <h2 class="section-title">امنیت و نشست‌ها</h2>
          </div>
          <ChevronLeft class="settings-section__chevron" :size="18" />
        </AppButton>
        <div
          id="trading-settings-security-panel"
          v-show="openSections.security"
          class="settings-section__body"
          role="region"
          aria-labelledby="trading-settings-security-header"
        >
          <div class="info-note">
            <AlertCircle :size="16" />
            <span>این مقادیر آستانه پایه برای جلوگیری از سوءاستفاده (Anti-Abuse) هستند. برای کاربرانی با بیش از یک نشست فعال، سیستم به صورت خودکار آستانه را افزایش می‌دهد.</span>
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه پایه روزانه</label>
            <AppInput
              type="number"
              v-model.number="form.anti_abuse_daily_base"
              :placeholder="settings.anti_abuse_daily_base?.toString()"
              min="1"
              class="settings-input"
              :class="{'is-default': isDefault('anti_abuse_daily_base')}"
            />
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه پایه هفتگی</label>
            <AppInput
              type="number"
              v-model.number="form.anti_abuse_weekly_base"
              :placeholder="settings.anti_abuse_weekly_base?.toString()"
              min="1"
              class="settings-input"
              :class="{'is-default': isDefault('anti_abuse_weekly_base')}"
            />
          </div>
          <div class="ds-form-group">
            <label class="ds-label">آستانه پایه ماهانه</label>
            <AppInput
              type="number"
              v-model.number="form.anti_abuse_monthly_base"
              :placeholder="settings.anti_abuse_monthly_base?.toString()"
              min="1"
              class="settings-input"
              :class="{'is-default': isDefault('anti_abuse_monthly_base')}"
            />
          </div>
        </div>
      </div>

      <!-- برنامه بازار -->
      <div class="settings-section" :class="{ open: openSections.marketSchedule }">
        <AppButton
          id="trading-settings-market-schedule-header"
          class="settings-section__header"
          type="button"
          variant="ghost"
          :aria-expanded="openSections.marketSchedule"
          aria-controls="trading-settings-market-schedule-panel"
          @click="toggleSection('marketSchedule')"
        >
          <div class="settings-section__header-info">
            <Clock class="section-icon" :size="18" />
            <h2 class="section-title">برنامه بازار</h2>
          </div>
          <ChevronLeft class="settings-section__chevron" :size="18" />
        </AppButton>
        <div
          id="trading-settings-market-schedule-panel"
          v-show="openSections.marketSchedule"
          class="settings-section__body"
          role="region"
          aria-labelledby="trading-settings-market-schedule-header"
        >
          <div class="market-preview-card" data-testid="market-state-card">
            <div class="market-preview-row">
              <span class="preview-label">وضعیت فعلی</span>
              <strong class="preview-value" :class="marketState?.is_open ? 'open' : 'closed'">{{ marketStatusLabel }}</strong>
            </div>
            <div class="market-preview-row">
              <span class="preview-label">یادآور وب</span>
              <span class="preview-value">{{ marketNoticeLabel }}</span>
            </div>
            <div class="market-preview-row">
              <span class="preview-label">نوبت بعدی تغییر</span>
              <span class="preview-value">{{ formattedNextTransition }}</span>
            </div>
            <div class="market-preview-row muted">
              <span class="preview-label">منطقه زمانی</span>
              <span class="preview-value">{{ scheduleForm.market_timezone }}</span>
            </div>
          </div>

          <label class="schedule-toggle" data-testid="market-schedule-enabled-row">
            <input
              data-testid="market-schedule-enabled"
              type="checkbox"
              :checked="scheduleForm.market_schedule_enabled"
              @change="scheduleForm.market_schedule_enabled = ($event.target as HTMLInputElement).checked"
            />
            <span>زمان‌بندی خودکار بازار فعال باشد</span>
          </label>

          <div class="form-row">
            <div class="ds-form-group">
              <label class="ds-label">ساعت شروع روزانه</label>
              <AppInput data-testid="market-open-time" type="time" v-model="scheduleForm.market_open_time_local" class="settings-input" />
            </div>
            <div class="ds-form-group">
              <label class="ds-label">ساعت پایان روزانه</label>
              <AppInput data-testid="market-close-time" type="time" v-model="scheduleForm.market_close_time_local" class="settings-input" />
            </div>
          </div>

          <div class="ds-form-group">
            <label class="ds-label">روزهای بسته هفتگی</label>
            <div class="weekday-grid">
              <label
                v-for="option in weekdayOptions"
                :key="option.value"
                class="weekday-chip"
                :class="{ active: scheduleForm.market_closed_weekdays.includes(option.value) }"
              >
                <input
                  :data-testid="`weekday-${option.value}`"
                  type="checkbox"
                  class="weekday-checkbox"
                  :checked="scheduleForm.market_closed_weekdays.includes(option.value)"
                  @change="toggleClosedWeekday(option.value)"
                />
                <span>{{ option.label }}</span>
              </label>
            </div>
          </div>
        </div>
      </div>

      <!-- استثناهای تقویمی -->
      <div class="settings-section" :class="{ open: openSections.marketCalendar }">
        <AppButton
          id="trading-settings-market-calendar-header"
          class="settings-section__header"
          type="button"
          variant="ghost"
          :aria-expanded="openSections.marketCalendar"
          aria-controls="trading-settings-market-calendar-panel"
          @click="toggleSection('marketCalendar')"
        >
          <div class="settings-section__header-info">
            <ClipboardList class="section-icon" :size="18" />
            <h2 class="section-title">استثناهای تقویمی بازار</h2>
          </div>
          <ChevronLeft class="settings-section__chevron" :size="18" />
        </AppButton>
        <div
          id="trading-settings-market-calendar-panel"
          v-show="openSections.marketCalendar"
          class="settings-section__body"
          role="region"
          aria-labelledby="trading-settings-market-calendar-header"
        >
          <div class="form-row">
            <div class="ds-form-group">
              <label class="ds-label">تاریخ</label>
              <JalaliDatePicker
                v-model="overrideForm.date"
                value-type="gregorian"
                placeholder="انتخاب تاریخ"
                trigger-test-id="override-date"
              />
            </div>
            <div class="ds-form-group">
              <label class="ds-label">نوع استثنا</label>
              <AppSelect data-testid="override-type" v-model="overrideForm.override_type" class="settings-input" :options="overrideTypeOptions" />
            </div>
          </div>

          <div v-if="isCustomHoursOverride" class="form-row">
            <div class="ds-form-group">
              <label class="ds-label">ساعت شروع استثنا</label>
              <AppInput data-testid="override-open-time" type="time" v-model="overrideForm.open_time_local" class="settings-input" />
            </div>
            <div class="ds-form-group">
              <label class="ds-label">ساعت پایان استثنا</label>
              <AppInput data-testid="override-close-time" type="time" v-model="overrideForm.close_time_local" class="settings-input" />
            </div>
          </div>

          <div class="ds-form-group">
            <label class="ds-label">یادداشت</label>
            <AppInput data-testid="override-note" type="text" v-model="overrideForm.note" class="settings-input" placeholder="مثلاً تعطیلی مناسبت یا نیمه‌وقت" />
          </div>

          <div class="override-actions">
            <AppButton
              type="button"
              data-testid="override-save"
              class="settings-button settings-button--primary"
              @click="saveOverride"
              :disabled="overrideSaving"
            >
              <Loader2 v-if="overrideSaving" class="animate-spin" :size="16" />
              <span>{{ editingOverrideId ? 'ذخیره ویرایش استثنا' : 'ثبت استثنا' }}</span>
            </AppButton>
            <AppButton
              v-if="editingOverrideId"
              type="button"
              data-testid="override-cancel"
              class="settings-button settings-button--secondary"
              variant="secondary"
              @click="resetOverrideForm"
              :disabled="overrideSaving"
            >
              <span>لغو ویرایش</span>
            </AppButton>
          </div>

          <div v-if="overridesLoading" class="inline-loading">
            <Loader2 class="ds-spinner" :size="18" />
            <span>در حال دریافت استثناها...</span>
          </div>
          <div v-else-if="sortedOverrides.length === 0" class="empty-overrides">
            هنوز استثنای تقویمی ثبت نشده است.
          </div>
          <div v-else class="override-list">
            <div v-for="item in sortedOverrides" :key="item.id" class="override-card" data-testid="market-override-row">
              <div class="override-card__main">
                <div class="override-card__title-row">
                  <strong>{{ item.date }}</strong>
                  <span class="override-type-chip">{{ overrideTypeOptions.find((option) => option.value === item.override_type)?.label }}</span>
                </div>
                <div v-if="item.override_type === 'custom_hours'" class="override-hours">
                  {{ item.open_time_local }} تا {{ item.close_time_local }}
                </div>
                <div v-if="item.note" class="override-note">{{ item.note }}</div>
              </div>
              <div class="override-card__actions">
                <AppButton type="button" :data-testid="`override-edit-${item.id}`" class="mini-footer-control" variant="secondary" @click="startEditingOverride(item)">ویرایش</AppButton>
                <AppButton type="button" :data-testid="`override-delete-${item.id}`" class="mini-footer-control danger" variant="danger" @click="deleteOverride(item.id)" :disabled="overrideDeletingId === item.id">
                  {{ overrideDeletingId === item.id ? 'در حال حذف...' : 'حذف' }}
                </AppButton>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- دکمه‌ها -->
      <div class="footer-actions">
        <AppButton type="button" class="settings-button settings-button--primary footer-control" @click="saveSettings" :disabled="saving">
          <Loader2 v-if="saving" class="animate-spin" :size="18" />
          <Save v-else :size="18" />
          <span>ذخیره تنظیمات</span>
        </AppButton>
        <AppButton type="button" class="settings-button settings-button--danger footer-control" variant="danger" @click="resetSettings" :disabled="saving">
          <RotateCcw :size="18" />
          <span>بازنشانی به پیش‌فرض</span>
        </AppButton>
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

.settings-viewport-toast {
  position: fixed;
  top: calc(env(safe-area-inset-top, 0px) + 14px);
  left: 50%;
  z-index: 1305;
  width: min(520px, calc(100vw - 28px));
  transform: translateX(-50%);
  border-radius: 18px;
  padding: 0.85rem 1rem;
  direction: rtl;
  text-align: right;
  font-size: 0.88rem;
  font-weight: 850;
  line-height: 1.8;
  box-shadow: 0 18px 44px rgba(15, 23, 42, 0.24);
  backdrop-filter: blur(12px);
}

.settings-viewport-toast--success {
  border: 1px solid rgba(16, 185, 129, 0.28);
  background: rgba(240, 253, 244, 0.96);
  color: #047857;
}

.settings-viewport-toast--danger {
  border: 1px solid rgba(239, 68, 68, 0.26);
  background: rgba(254, 242, 242, 0.96);
  color: #b91c1c;
}

.section-icon {
  color: var(--ds-primary-500);
}

.settings-section__header {
  width: 100%;
  padding: 0.85rem 0.95rem;
  border-radius: var(--ds-radius-lg);
  text-align: right;
  box-shadow: none;
}

.settings-section__header :deep(.ui-button__label) {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.settings-section__header-info {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
}

.settings-section__chevron {
  flex: 0 0 auto;
  transition: transform 0.18s ease;
}

.settings-section.open .settings-section__chevron {
  transform: rotate(-90deg);
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

.footer-control {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.85rem;
}

.market-preview-card {
  display: grid;
  gap: 0.65rem;
  padding: 0.95rem;
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-subtle);
  margin-bottom: 1rem;
}

.market-preview-row {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  font-size: 0.82rem;
}

.market-preview-row.muted {
  color: var(--ds-text-secondary);
}

.preview-label {
  color: var(--ds-text-secondary);
}

.preview-value {
  font-weight: 700;
  text-align: left;
}

.preview-value.open {
  color: var(--ds-success-700);
}

.preview-value.closed {
  color: var(--ds-danger-700);
}

.schedule-toggle {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.85rem 0.9rem;
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-card);
  margin-bottom: 1rem;
  font-weight: 700;
}

.weekday-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
}

.weekday-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.55rem 0.75rem;
  border-radius: 999px;
  border: 1px solid var(--ds-border-light);
  background: var(--ds-bg-card);
  cursor: pointer;
  font-size: 0.78rem;
  font-weight: 700;
}

.weekday-chip.active {
  border-color: var(--ds-primary-300);
  background: var(--ds-primary-50);
  color: var(--ds-primary-800);
}

.weekday-checkbox {
  margin: 0;
}

.override-actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 1rem;
}

.inline-loading,
.empty-overrides {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font-size: 0.82rem;
  color: var(--ds-text-secondary);
  padding: 0.75rem 0;
}

.override-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: 1rem;
}

.override-card {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.9rem;
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-card);
}

.override-card__main {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.override-card__title-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.override-type-chip {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  background: var(--ds-bg-subtle);
  color: var(--ds-text-secondary);
  font-size: 0.72rem;
  font-weight: 700;
}

.override-hours,
.override-note {
  font-size: 0.8rem;
  color: var(--ds-text-secondary);
}

.override-card__actions {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  align-items: flex-end;
}

.mini-footer-control {
  border: 1px solid var(--ds-border-light);
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  border-radius: var(--ds-radius-md);
  padding: 0.45rem 0.7rem;
  font-size: 0.76rem;
  font-weight: 700;
}

.mini-footer-control.danger {
  color: var(--ds-danger-700);
}

@media (max-width: 720px) {
  .override-card,
  .footer-actions,
  .override-actions,
  .form-row {
    grid-template-columns: 1fr;
    flex-direction: column;
  }

  .override-card__actions {
    align-items: stretch;
  }
}
</style>
