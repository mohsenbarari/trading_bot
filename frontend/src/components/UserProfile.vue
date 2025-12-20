<script setup lang="ts">
import { ref, computed, watchEffect, onUnmounted, watch } from 'vue';
import moment from 'moment-jalaali';
import DatePicker from 'vue3-persian-datetime-picker';

const props = defineProps<{
  user: any;
  isAdminView?: boolean;
  apiBaseUrl?: string;
  jwtToken?: string | null;
}>();

const emit = defineEmits(['navigate']);

const isEditingRole = ref(false);
const showSettings = ref(false);
const showBlockModal = ref(false);
const showLimitationsModal = ref(false);
const isLoading = ref(false);

// Limitation Refs
const limitMaxTrades = ref<number | null>(null);
const limitMaxCommodities = ref<number | null>(null);
const limitMaxRequests = ref<number | null>(null);
const limitDurationMinutes = ref(0); // 0 = Unlimited
const showCustomLimitDateInput = ref(false);
const showLimitDateModal = ref(false);
const customLimitDate = ref('');
const selectedRole = ref(props.user?.role || 'تماشا');
const hasBotAccess = ref(props.user?.has_bot_access ?? true);

// --- Date Picker Logic ---
const showCustomDateInput = ref(false);
const showBlockDateModal = ref(false);
const customDate = ref('');
const tempDateRef = ref(''); // Intermediate ref
const blockDatePicker = ref<any>(null); // Ref for block date picker
const limitDatePicker = ref<any>(null); // Ref for limit date picker

const pickerStep = ref(1);
const tempDatePart = ref('');
const tempTimePart = ref('');
const blockTimePickerRef = ref<any>(null);
const limitTimePickerRef = ref<any>(null);

// Watch tempTimePart for debugging
watch(tempTimePart, (newVal, oldVal) => {
    console.log('tempTimePart changed via watch:', oldVal, '->', newVal);
});

function initDatePicker(currentValue: string) {
    pickerStep.value = 1;
    if (currentValue) {
        // Try to parse existing value
        const m = moment(currentValue, 'jYYYY/jMM/jDD HH:mm');
        if (m.isValid()) {
            tempDatePart.value = m.format('jYYYY/jMM/jDD');
            tempTimePart.value = m.format('HH:mm');
        } else {
            tempDatePart.value = moment().format('jYYYY/jMM/jDD');
            tempTimePart.value = moment().format('HH:mm');
        }
    } else {
        tempDatePart.value = moment().format('jYYYY/jMM/jDD');
        tempTimePart.value = moment().format('HH:mm');
    }
}

// Update temp ref on any change (click day, time, etc)
function onDateChange(val: any) {
    console.log('Date Change:', val);
    if (val) tempDateRef.value = val;
}

// Final submission handler
function handleNextStep() {
    if (!tempDatePart.value) return;
    pickerStep.value = 2;
}

function handleFinalSubmit() {
    // Log current state for debugging
    console.log('tempDatePart before submit:', tempDatePart.value);
    console.log('tempTimePart before submit:', tempTimePart.value);
    
    if (!tempDatePart.value) {
        alert('لطفاً تاریخ را انتخاب کنید.');
        return;
    }
    
    // Try to get time from picker ref if tempTimePart is empty
    let timePart = tempTimePart.value;
    
    if (!timePart || timePart === '') {
        // Try to read from picker component directly
        const pickerRef = showBlockDateModal.value ? blockTimePickerRef.value : limitTimePickerRef.value;
        if (pickerRef && pickerRef.modelValue) {
            const pickerVal = pickerRef.modelValue;
            console.log('Reading from picker ref:', pickerVal);
            if (typeof pickerVal === 'string') {
                timePart = pickerVal;
            } else if (pickerVal instanceof Date) {
                timePart = moment(pickerVal).format('HH:mm');
            }
        }
    }
    
    // Final fallback to current time
    if (!timePart || timePart === '') {
        timePart = moment().format('HH:mm');
        console.log('Using fallback current time:', timePart);
    }
    
    const finalVal = `${tempDatePart.value} ${timePart}`;
    console.log('Final Submit (Jalali):', finalVal);
    
    if (showBlockDateModal.value) {
        customDate.value = finalVal;
        showBlockDateModal.value = false;
    } else if (showLimitDateModal.value) {
        customLimitDate.value = finalVal;
        showLimitDateModal.value = false;
    }
}

// Legacy handler - can be removed or kept as alias
// Explicit update handlers to ensure v-model sync works for custom integration
function updateDatePart(val: any) {
    console.log('updateDatePart received:', val, 'type:', typeof val);
    // Do NOT modify tempDatePart here - v-model handles it.
    // This handler is only for logging/debugging.
}

function updateTimePart(val: any) {
    console.log('updateTimePart received:', val, 'type:', typeof val);
    if (!val) return;
    
    // Handle different formats the picker might return
    if (typeof val === 'string') {
        // If it's already HH:mm format
        if (/^\d{2}:\d{2}$/.test(val)) {
            tempTimePart.value = val;
        } else {
            // Try to parse with moment
            const m = moment(val, ['HH:mm', 'HH:mm:ss', 'h:mm A']);
            if (m.isValid()) {
                tempTimePart.value = m.format('HH:mm');
            } else {
                tempTimePart.value = val;
            }
        }
    } else if (val instanceof Date) {
        tempTimePart.value = moment(val).format('HH:mm');
    } else {
        tempTimePart.value = String(val);
    }
    console.log('tempTimePart set to:', tempTimePart.value);
}

const roles = [
  { value: 'تماشا', label: 'تماشا' },
  { value: 'عادی', label: 'عادی' },
  { value: 'پلیس', label: 'پلیس' },
  { value: 'مدیر میانی', label: 'مدیر میانی' },
  { value: 'مدیر ارشد', label: 'مدیر ارشد' }
];

const blockDurations = [
  { label: '1 ساعت', minutes: 60 },
  { label: '6 ساعت', minutes: 360 },
  { label: '12 ساعت', minutes: 720 },
  { label: '1 روز', minutes: 1440 },
  { label: '3 روز', minutes: 4320 },
  { label: '1 هفته', minutes: 10080 },
  { label: 'نامحدود', minutes: 0 },
  { label: 'انتخاب زمان دلخواه', minutes: -1 } // -1 برای حالت کاستوم
];



// Lock body scroll when any modal is open
watchEffect(() => {
    const anyModalOpen = showBlockModal.value || showLimitationsModal.value || showBlockDateModal.value || showLimitDateModal.value;
    document.body.style.overflow = anyModalOpen ? 'hidden' : '';
});

// Cleanup on unmount
onUnmounted(() => {
    document.body.style.overflow = '';
});

const isRestricted = computed(() => {
  if (!props.user.trading_restricted_until) return false;
  // Use moment to handle both naive (which assumes local) and aware strings safely
  // Since backend typically sends UTC ISO, we parse as UTC
  const restrictionTime = moment.utc(props.user.trading_restricted_until);
  return restrictionTime.isValid() && restrictionTime.isAfter(moment.utc());
});

const restrictionText = computed(() => {
  if (!isRestricted.value) return '✅ آزاد';
  const restrictionTime = moment.utc(props.user.trading_restricted_until);
  if (restrictionTime.year() > 2100) return '⛔ مسدود دائم';
  return `⛔ تا ${props.user.trading_restricted_until_jalali}`;
});

// --- Countdown Timer Logic ---
const countdownRestriction = ref('');
const countdownLimitation = ref('');
let countdownInterval: ReturnType<typeof setInterval> | null = null;

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return 'منقضی شده';
  
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  
  if (days > 0) {
    return `${days} روز ${hours} ساعت ${minutes} دقیقه`;
  } else if (hours > 0) {
    return `${hours} ساعت ${minutes} دقیقه ${secs} ثانیه`;
  } else if (minutes > 0) {
    return `${minutes} دقیقه ${secs} ثانیه`;
  } else {
    return `${secs} ثانیه`;
  }
}

function updateCountdowns() {
  const now = moment.utc();
  
  // Restriction countdown
  if (props.user.trading_restricted_until) {
    const restrictionTime = moment.utc(props.user.trading_restricted_until);
    if (restrictionTime.isValid() && restrictionTime.year() <= 2100) {
      const diffSeconds = restrictionTime.diff(now, 'seconds');
      countdownRestriction.value = formatCountdown(diffSeconds);
    } else if (restrictionTime.year() > 2100) {
      countdownRestriction.value = 'دائمی';
    } else {
      countdownRestriction.value = '';
    }
  } else {
    countdownRestriction.value = '';
  }
  
  // Limitation countdown
  if (props.user.limitations_expire_at) {
    const limitTime = moment.utc(props.user.limitations_expire_at);
    if (limitTime.isValid()) {
      const diffSeconds = limitTime.diff(now, 'seconds');
      countdownLimitation.value = formatCountdown(diffSeconds);
    } else {
      countdownLimitation.value = '';
    }
  } else {
    countdownLimitation.value = '';
  }
}

// Start countdown interval on component mount
watchEffect(() => {
  // Clear existing interval
  if (countdownInterval) {
    clearInterval(countdownInterval);
    countdownInterval = null;
  }
  
  // Only start if there's something to count
  if (props.user.trading_restricted_until || props.user.limitations_expire_at) {
    updateCountdowns();
    countdownInterval = setInterval(updateCountdowns, 1000);
  }
});

onUnmounted(() => {
  if (countdownInterval) {
    clearInterval(countdownInterval);
  }
});

async function saveRole() {
  if (!props.apiBaseUrl || !props.jwtToken) return;
  isLoading.value = true;
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/users/${props.user.id}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${props.jwtToken}`
      },
      body: JSON.stringify({ role: selectedRole.value })
    });
    if (!response.ok) throw new Error('خطا در ذخیره نقش');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
    isEditingRole.value = false;
    alert('نقش کاربر تغییر کرد.');
  } catch (e) {
    alert('خطا در ذخیره تغییرات');
  } finally {
    isLoading.value = false;
  }
}

async function toggleBotAccess() {
  if (!props.apiBaseUrl || !props.jwtToken) return;
  if (!confirm(`آیا از ${hasBotAccess.value ? 'غیرفعال' : 'فعال'} کردن دسترسی بات اطمینان دارید؟`)) return;
  
  isLoading.value = true;
  try {
    const newValue = !hasBotAccess.value;
    const response = await fetch(`${props.apiBaseUrl}/api/users/${props.user.id}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${props.jwtToken}`
      },
      body: JSON.stringify({ has_bot_access: newValue })
    });
    if (!response.ok) throw new Error('خطا در تغییر دسترسی');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
    hasBotAccess.value = newValue;
    alert(`دسترسی بات ${newValue ? 'فعال' : 'غیرفعال'} شد.`);
  } catch (e) {
    alert('خطا در تغییر دسترسی');
  } finally {
    isLoading.value = false;
  }
}

async function blockUser(minutes: number) {
  if (!props.apiBaseUrl || !props.jwtToken) return;

  if (minutes === -1) {
      customDate.value = ''; // Reset custom date
      showCustomDateInput.value = true;
      return;
  }

  isLoading.value = true;
  try {
    let restrictedUntil = null;
    if (minutes === 0) {
        // نامحدود (100 سال)
        const date = new Date();
        date.setFullYear(date.getFullYear() + 100);
        restrictedUntil = date.toISOString();
    } else {
        const date = new Date();
        date.setMinutes(date.getMinutes() + minutes);
        restrictedUntil = date.toISOString();
    }
    
    await sendBlockRequest(restrictedUntil);
  } catch (e) {
    alert('خطا در انجام عملیات');
  } finally {
    isLoading.value = false;
  }
}

const toEnglishDigits = (str: string) => {
  if (!str) return str;
  return str.replace(/[۰-۹]/g, (d) => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d).toString());
};

// Helper to interpret Jalali input as Iran Standard Time (+03:30)
const parseJalaliToIranISO = (jalaliStr: string) => {
    const normalize = toEnglishDigits(jalaliStr);
    // Parse as Jalaali, then FORCE the offset to +03:30 (Iran Standard Time)
    // We use utcOffset(210, true) -> 210 mins = 3h 30m. 
    // The 'true' flag keeps the local time (HH:mm) and adjusts the underlying UTC.
    const m = moment(normalize, 'jYYYY/jMM/jDD HH:mm');
    if (!m.isValid()) return null;
    m.utcOffset(210, true);
    return m.toISOString(); // Returns equivalent UTC time
};

async function blockUserCustom() {
    if (!customDate.value) {
        alert('لطفاً یک تاریخ معتبر انتخاب کنید.');
        return;
    }
    isLoading.value = true;
    try {
        // Normalize digits (Persian to English) before parsing
        const normalizedDate = toEnglishDigits(customDate.value);
        console.log('Raw Date:', customDate.value);
        console.log('Normalized Date:', normalizedDate);

        // Parse Jalali date string strictly as Iran Time
        const isoDate = parseJalaliToIranISO(normalizedDate);
        
        if (!isoDate) {
             console.error('Date Invalid:', normalizedDate);
             alert('تاریخ نامعتبر است.');
             isLoading.value = false;
             return;
        }
        
        console.log('Sending ISO (Iran Time -> UTC):', isoDate);
        await sendBlockRequest(isoDate);

    } catch (e) {
        console.error('Custom Block Error:', e);
        alert('خطا در انجام عملیات');
    } finally {
        isLoading.value = false;
    }
}

async function sendBlockRequest(restrictedUntil: string) {
    try {
        const response = await fetch(`${props.apiBaseUrl}/api/users/${props.user.id}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${props.jwtToken}`
          },
          body: JSON.stringify({ trading_restricted_until: restrictedUntil })
        });
        
        if (!response.ok) throw new Error('خطا در مسدودسازی');
        const updatedUser = await response.json();
        console.log('Block User Response:', updatedUser); 
        Object.assign(props.user, updatedUser);
        console.log('Props User Restricted Until:', props.user.trading_restricted_until);
        
        showBlockModal.value = false;
        showCustomDateInput.value = false;
        alert('کاربر مسدود شد.');
    } catch (e) {
        console.error('Block Error:', e);
        alert('خطا در اعمال مسدودیت');
    }
}

async function saveLimitations() {
    if (!props.apiBaseUrl || !props.jwtToken) return;
    isLoading.value = true;
    try {
        let expireAt = null;
        if (limitDurationMinutes.value === -1) {
             if (!customLimitDate.value) {
                 alert('لطفاً تاریخ پایان محدودیت را انتخاب کنید.');
                 isLoading.value = false;
                 return;
             }
             expireAt = parseJalaliToIranISO(customLimitDate.value);
             if (!expireAt) {
                 alert('تاریخ نامعتبر است.');
                 isLoading.value = false;
                 return;
             }
        } else if (limitDurationMinutes.value > 0) {
             const date = new Date();
             date.setMinutes(date.getMinutes() + limitDurationMinutes.value);
             expireAt = date.toISOString();
        }
        // If 0 (Unlimited), expireAt remains null (permanent limitation until removed?) 
        // Or maybe we want to set it to far future? 
        // Let's assume null means "Permanent" for limitations too, or we can use the same logic as block.
        // For now, let's treat 0 as "Permanent" (null in DB implies no expiry, so it's always active if values are set).
        
        const body = {
            max_daily_trades: limitMaxTrades.value,
            max_active_commodities: limitMaxCommodities.value,
            max_daily_requests: limitMaxRequests.value,
            limitations_expire_at: expireAt
        };

        const response = await fetch(`${props.apiBaseUrl}/api/users/${props.user.id}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${props.jwtToken}`
            },
            body: JSON.stringify(body)
        });

        if (!response.ok) throw new Error('خطا در ذخیره محدودیت‌ها');
        const updatedUser = await response.json();
        Object.assign(props.user, updatedUser);
        showLimitationsModal.value = false;
        alert('محدودیت‌ها اعمال شد.');
    } catch (e) {
        alert('خطا در ذخیره محدودیت‌ها');
    } finally {
        isLoading.value = false;
    }
}

function openLimitationsModal() {
    limitMaxTrades.value = props.user.max_daily_trades;
    limitMaxCommodities.value = props.user.max_active_commodities;
    limitMaxRequests.value = props.user.max_daily_requests;
    // We don't easily know the duration from expire_at, so reset duration to default
    limitDurationMinutes.value = 0; 
    customLimitDate.value = ''; // Reset custom date
    showLimitationsModal.value = true;
}



async function unblockUser() {
  if (!confirm('آیا از رفع مسدودیت اطمینان دارید؟')) return;
  if (!props.apiBaseUrl || !props.jwtToken) return;
  isLoading.value = true;
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/users/${props.user.id}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${props.jwtToken}`
      },
      body: JSON.stringify({ trading_restricted_until: null }) // ارسال null برای رفع مسدودیت
    });
    
    if (!response.ok) throw new Error('خطا در رفع مسدودیت');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
    customDate.value = ''; // Reset custom date
    alert('رفع مسدودیت انجام شد.');
  } catch (e) {
    alert('خطا در انجام عملیات');
  } finally {
    isLoading.value = false;
  }
}

// Check if user has active limitations
const hasLimitations = computed(() => {
    return props.user.max_daily_trades != null || 
           props.user.max_active_commodities != null || 
           props.user.max_daily_requests != null;
});

async function removeLimitations() {
  if (!confirm('آیا از رفع محدودیت‌ها اطمینان دارید؟')) return;
  if (!props.apiBaseUrl || !props.jwtToken) return;
  isLoading.value = true;
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/users/${props.user.id}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${props.jwtToken}`
      },
      body: JSON.stringify({ 
        max_daily_trades: null,
        max_active_commodities: null,
        max_daily_requests: null,
        limitations_expire_at: null
      })
    });
    
    if (!response.ok) throw new Error('خطا در رفع محدودیت‌ها');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
    alert('محدودیت‌ها برداشته شد.');
  } catch (e) {
    alert('خطا در انجام عملیات');
  } finally {
    isLoading.value = false;
  }
}

async function deleteUser() {
  if (!confirm('آیا از حذف این کاربر اطمینان دارید؟')) return;
  if (!props.apiBaseUrl || !props.jwtToken) return;
  isLoading.value = true;
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/users/${props.user.id}`, {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${props.jwtToken}`
      }
    });
    if (!response.ok) throw new Error('خطا در حذف کاربر');
    alert('کاربر حذف شد.');
    emit('navigate', 'manage_users');
  } catch (e) {
    alert('خطا در حذف کاربر');
  } finally {
    isLoading.value = false;
  }
}
</script>

<template>
  <div class="card">
    <div class="header-row">
        <h2>{{ isAdminView ? 'مدیریت کاربر' : 'پروفایل کاربری' }}</h2>
        <button v-if="isAdminView" class="back-button" @click="$emit('navigate', 'manage_users')">🔙</button>
    </div>

    <div class="profile-details">
      <div class="detail-item">
        <span class="label">نام کاربری</span>
        <span class="value">{{ user.account_name }}</span>
      </div>
      <div class="detail-item">
        <span class="label">موبایل</span>
        <span class="value">{{ user.mobile_number }}</span>
      </div>
      <div class="detail-item">
          <span class="label">نقش</span>
          <span class="value">{{ user.role }}</span>
      </div>
      <div class="detail-item">
          <span class="label">دسترسی بات</span>
          <span class="value">{{ user.has_bot_access ? '✅ فعال' : '❌ غیرفعال' }}</span>
      </div>
      <div class="detail-item">
          <span class="label">وضعیت حساب</span>
          <span class="value" :class="{ 'text-red': isRestricted }">{{ restrictionText }}</span>
      </div>
      
      <!-- تایمر شمارش معکوس مسدودیت -->
      <div v-if="isRestricted && countdownRestriction" class="countdown-box restriction-countdown">
          <span class="countdown-icon">⏱️</span>
          <span class="countdown-label">زمان باقی‌مانده مسدودیت:</span>
          <span class="countdown-value">{{ countdownRestriction }}</span>
      </div>
      
      <!-- نمایش محدودیت‌ها -->
      <div v-if="user.max_daily_trades || user.max_active_commodities || user.max_daily_requests" class="limitations-box">
          <h4>⚠️ محدودیت‌های فعال:</h4>
          <div v-if="user.max_daily_trades" class="limit-item">
              <span>مجموع معاملات:</span> <span class="usage-ratio">{{ user.trades_count ?? 0 }} / {{ user.max_daily_trades }}</span>
          </div>
          <div v-if="user.max_active_commodities" class="limit-item">
              <span>مجموع کالا:</span> <span class="usage-ratio">{{ user.commodities_traded_count ?? 0 }} / {{ user.max_active_commodities }}</span>
          </div>
          <div v-if="user.max_daily_requests" class="limit-item">
              <span>مجموع لفظ:</span> <span class="usage-ratio">{{ user.channel_messages_count ?? 0 }} / {{ user.max_daily_requests }}</span>
          </div>
          <div v-if="user.limitations_expire_at" class="limit-expiry">
              <span>انقضا:</span> <span>{{ user.limitations_expire_at_jalali }}</span>
          </div>
          <!-- تایمر شمارش معکوس محدودیت -->
          <div v-if="countdownLimitation" class="countdown-inline">
              <span class="countdown-icon">⏱️</span>
              <span class="countdown-label">باقی‌مانده:</span>
              <span class="countdown-value">{{ countdownLimitation }}</span>
          </div>
      </div>

      <!-- ویرایش نقش (مودال داخلی) -->
      <div v-if="isEditingRole" class="edit-section">
        <div class="form-group">
            <label>انتخاب نقش جدید:</label>
            <select v-model="selectedRole" class="form-select">
                <option v-for="role in roles" :key="role.value" :value="role.value">{{ role.label }}</option>
            </select>
        </div>
        <div class="action-buttons">
            <button @click="saveRole" :disabled="isLoading" class="save-btn">ذخیره</button>
            <button @click="isEditingRole = false" class="cancel-btn">انصراف</button>
        </div>
      </div>
      
      <!-- منوی مدیریت (فقط ادمین) -->
      <template v-if="isAdminView">
        <div v-if="!showSettings" class="main-actions">
            <button @click="showSettings = true" class="menu-button settings-btn">⚙️ تنظیمات کاربر</button>
            <button @click="deleteUser" class="menu-button delete-btn">❌ حذف کاربر</button>
            <button @click="$emit('navigate', 'manage_users')" class="menu-button back-btn">🔙 بازگشت به لیست</button>
        </div>

        <div v-else class="settings-menu">
            <button @click="toggleBotAccess" class="menu-button">
                🤖 تغییر دسترسی بات ({{ hasBotAccess ? 'فعال' : 'غیرفعال' }})
            </button>
            <button @click="isEditingRole = true" class="menu-button">✏️ ویرایش نقش</button>
            
            <button v-if="!hasLimitations" @click="openLimitationsModal" class="menu-button">⚠️ اعمال محدودیت</button>
            <button v-else @click="removeLimitations" class="menu-button unlimit-btn">
                ✅ رفع محدودیت
            </button>
            
            <button v-if="!isRestricted" @click="showBlockModal = true" class="menu-button block-btn">
                ⛔ مسدود کردن
            </button>
            <button v-else @click="unblockUser" class="menu-button unblock-btn">
                🔓 رفع مسدودیت
            </button>

            <button @click="showSettings = false" class="menu-button back-btn">🔙 بازگشت</button>
        </div>
      </template>
    </div>

    <!-- مودال انتخاب مدت زمان مسدودیت -->
    <Teleport to="body">
        <div v-if="showBlockModal" class="modal-overlay">
            <div class="modal-content">
                <h3>⏳ مدت زمان مسدودیت</h3>
                
                <div v-if="!showCustomDateInput">
                    <div class="duration-list">
                        <button v-for="duration in blockDurations" :key="duration.minutes" 
                                @click="blockUser(duration.minutes)" class="duration-btn">
                            {{ duration.label }}
                        </button>
                    </div>
                </div>
                
                <div v-else class="custom-date-section">
                    <label>تاریخ و زمان پایان مسدودیت:</label>
                    <div 
                        class="custom-date-trigger"
                        @click="initDatePicker(customDate); showBlockDateModal = true"
                    >
                        {{ customDate || 'انتخاب تاریخ...' }}
                    </div>
                    


                    <div class="action-buttons">
                         <button @click="blockUserCustom" class="save-btn">تایید نهایی</button>
                         <button @click="showCustomDateInput = false" class="cancel-btn">بازگشت</button>
                    </div>
                </div>

                <button v-if="!showCustomDateInput" @click="showBlockModal = false" class="cancel-btn full-width">انصراف</button>
            </div>
        </div>
    </Teleport>

    <!-- مودال اعمال محدودیت -->
    <Teleport to="body">
        <div v-if="showLimitationsModal" class="modal-overlay">
            <div class="modal-content">
                <h3>⚠️ اعمال محدودیت</h3>
                
                <div class="form-group">
                    <label>مجموع تعداد معاملات:</label>
                    <input type="number" v-model.number="limitMaxTrades" class="form-input" min="0" placeholder="نامحدود (خالی)" />
                </div>
                <div class="form-group">
                    <label>مجموع تعداد کالای معامله شده:</label>
                    <input type="number" v-model.number="limitMaxCommodities" class="form-input" min="0" placeholder="نامحدود (خالی)" />
                </div>
                <div class="form-group">
                    <label>مجموع ارسال لفظ در کانال:</label>
                    <input type="number" v-model.number="limitMaxRequests" class="form-input" min="0" placeholder="نامحدود (خالی)" />
                </div>

                <div class="form-group">
                    <label>مدت زمان محدودیت:</label>
                    <select v-model="limitDurationMinutes" class="form-select">
                        <option v-for="duration in blockDurations" :key="duration.minutes" :value="duration.minutes">
                            {{ duration.label }}
                        </option>
                    </select>
                </div>
                
                <div v-if="limitDurationMinutes === -1" class="custom-date-section">
                    <label>تاریخ پایان:</label>
                    <div 
                        class="custom-date-trigger"
                        @click="initDatePicker(customLimitDate); showLimitDateModal = true"
                    >
                        {{ customLimitDate || 'انتخاب تاریخ...' }}
                    </div>
                </div>
                
                <div class="action-buttons">
                    <button @click="saveLimitations" :disabled="isLoading" class="save-btn">ذخیره</button>
                    <button @click="showLimitationsModal = false" class="cancel-btn">انصراف</button>
                </div>
            </div>
        </div>
    </Teleport>

    <template v-if="!isAdminView">
        <button class="menu-button notification-btn" @click="emit('navigate', 'notifications')">
          🔔 صندوق پیام‌ها
        </button>
        <!-- دکمه تنظیمات فقط برای نقش‌های غیر عادی -->
        <button 
          v-if="user.role !== 'عادی'" 
          class="menu-button settings-btn" 
          @click="emit('navigate', 'user_settings')"
        >
          ⚙️ تنظیمات
        </button>
    </template>
  </div>

    <!-- Moved Block Date Modal -->
    <Teleport to="body">
        <div v-if="showBlockDateModal" class="modal-overlay" style="z-index: 2010;">
            <div class="modal-content date-modal-content">
                <h3>{{ pickerStep === 1 ? '📅 انتخاب تاریخ' : '⏰ انتخاب ساعت' }}</h3>
                
                <div class="date-picker-wrapper">
                    <!-- Step 1: Date -->
                    <DatePicker 
                        v-if="pickerStep === 1"
                        v-model="tempDatePart" 
                        type="date" 
                        format="jYYYY/jMM/jDD"
                        inline 
                        :auto-submit="false" 
                        :editable="false" 
                        @change="updateDatePart"
                    />
                    <!-- Step 2: Time - Using native HTML5 input for reliability -->
                    <div v-if="pickerStep === 2" class="native-time-picker">
                        <label>ساعت مورد نظر:</label>
                        <input 
                            type="time" 
                            v-model="tempTimePart" 
                            class="time-input"
                        />
                    </div>
                </div>
                <!-- Footer moved outside wrapper to ensure visibility -->
                <div class="integrated-footer">
                        <button @click="showBlockDateModal = false" class="integrated-cancel-btn">انصراف</button>
                        
                        <button v-if="pickerStep === 1" @click="handleNextStep" class="integrated-save-btn">ادامه</button>
                        <button v-if="pickerStep === 2" @click="handleFinalSubmit" class="integrated-save-btn">تایید نهایی</button>
                </div>
            </div>
        </div>
    </Teleport>

    <!-- Moved Limit Date Modal -->
    <Teleport to="body">
        <div v-if="showLimitDateModal" class="modal-overlay" style="z-index: 2010;">
            <div class="modal-content date-modal-content">
                <h3>{{ pickerStep === 1 ? '📅 انتخاب تاریخ' : '⏰ انتخاب ساعت' }}</h3>
                
                <div class="date-picker-wrapper">
                    <!-- Step 1: Date -->
                    <DatePicker 
                        v-if="pickerStep === 1"
                        v-model="tempDatePart" 
                        type="date" 
                        format="jYYYY/jMM/jDD"
                        inline 
                        :auto-submit="false" 
                        :editable="false" 
                        @change="updateDatePart"
                    />
                    <!-- Step 2: Time - Using native HTML5 input for reliability -->
                    <div v-if="pickerStep === 2" class="native-time-picker">
                        <label>ساعت مورد نظر:</label>
                        <input 
                            type="time" 
                            v-model="tempTimePart" 
                            class="time-input"
                        />
                    </div>
                </div>
                <!-- Footer moved outside wrapper to ensure visibility -->
                <div class="integrated-footer">
                        <button @click="showLimitDateModal = false" class="integrated-cancel-btn">انصراف</button>
                        
                        <button v-if="pickerStep === 1" @click="handleNextStep" class="integrated-save-btn">ادامه</button>
                        <button v-if="pickerStep === 2" @click="handleFinalSubmit" class="integrated-save-btn">تایید نهایی</button>
                </div>
            </div>
        </div>
    </Teleport>
</template>

<style>
/* Integrated Footer Styles */
.integrated-footer {
    width: 100%;
    max-width: 320px;
    padding: 10px;
    border-top: 1px solid #eee;
    background: #fff;
    text-align: center;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
    margin-top: -5px; /* Pull it up slightly to connect with picker */
    z-index: 10;
    display: flex;
    justify-content: space-between;
    gap: 10px;
}

.integrated-save-btn {
    flex: 1;
    background-color: #007aff;
    color: white;
    border: none;
    padding: 10px;
    border-radius: 8px;
    font-size: 16px;
    font-weight: bold;
    cursor: pointer;
}

.integrated-cancel-btn {
    flex: 1;
    background-color: #f0f0f0;
    color: #333;
    border: 1px solid #ddd;
    padding: 10px;
    border-radius: 8px;
    font-size: 16px;
    font-weight: bold;
    cursor: pointer;
}

/* Native time picker styles */
.native-time-picker {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 20px;
    background: #f8f9fa;
    border-radius: 12px;
    margin: 10px 0;
}

.native-time-picker label {
    font-size: 18px;
    font-weight: bold;
    color: #333;
    margin-bottom: 15px;
}

.native-time-picker .time-input {
    font-size: 32px;
    padding: 15px 25px;
    border: 2px solid #007aff;
    border-radius: 12px;
    text-align: center;
    background: white;
    color: #333;
    width: auto;
    min-width: 150px;
}

.native-time-picker .time-input:focus {
    outline: none;
    border-color: #0056b3;
    box-shadow: 0 0 10px rgba(0, 122, 255, 0.3);
}


/* Hide original actions just in case they appear */
.vpd-actions {
    display: none !important;
}

.vpd-main {
    height: auto !important;
    overflow: visible !important;
    flex: 1 !important;
}

.vpd-days {
    height: auto !important;
    overflow: visible !important;
    padding-bottom: 10px !important;
}

/* Hide header submit/check buttons to prevent reset bug */
.vpd-header .vpd-icon-btn svg {
    /* We cannot verify the SVG path easily in CSS, but checking for specific classes usually helps */
}

/* Specific fix for the top-left button in Time View (which resets date) */
/* In RTL, :last-child is usually the left-most element in a flex row */
.vpd-content.vpd-is-time .vpd-header .vpd-icon-btn:last-child,
.vpd-time .vpd-header .vpd-icon-btn:last-child {
    display: none !important;
}

/* Hide any button explicitly named 'submit' or having check icon class in header */
.vpd-header .vpd-icon-btn.vpd-check, 
.vpd-header .vpd-icon-btn.vpd-tick {
    display: none !important;
}

/* Hide library footer to use custom buttons */
.vpd-actions {
    display: none !important;
}

/* More specific width control to prevent full-width ugly look */
.date-picker-wrapper {
    width: 100%;
    max-width: 320px; /* Force standard calendar width */
    margin: 0 auto; /* Center it */
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}

.date-modal-content {
    width: auto !important;
    min-width: 300px;
    max-width: 360px !important;
    padding: 0 !important; /* Remove padding to let picker fill nicely */
    background: transparent !important; /* Let picker bg show */
    box-shadow: none !important; /* Remove double shadow */
    display: flex;
    flex-direction: column;
    align-items: center;
}

/* Hide redundant input field (white box with date) in inline mode */
.date-picker-wrapper .vpd-input-group {
    display: none !important;
}

.date-modal-content h3 {
    margin-bottom: 15px;
    color: white; /* Make title visible on overlay or add bg */
    background: #fff;
    width: 100%;
    text-align: center;
    padding: 10px;
    border-radius: 8px;
    color: #333;
    max-width: 320px;
}

/* Stronger layout fix for width */
.vpd-container, .vpd-wrapper {
    width: 100% !important;
    max-width: 320px !important;
    margin: 0 auto !important;
}

/* Fix for top-left header button in Time View */
/* Hide ALL icon buttons in header in time view to prevent accidental resets */
.vpd-content.vpd-is-time .vpd-header .vpd-icon-btn,
.vpd-time .vpd-header .vpd-icon-btn {
    display: none !important;
}

/* Just in case, explicit hide for check/tick class if present */
.vpd-header .vpd-icon-btn.vpd-check, 
.vpd-header .vpd-icon-btn.vpd-tick {
    display: none !important;
}

/* Neutral trigger that looks like an input but acts like a div */
.custom-date-trigger {
    display: block;
    width: 100%;
    padding: 0.375rem 0.75rem; /* Match Bootstrap/form-select padding */
    font-size: 1rem;
    font-weight: 400;
    line-height: 1.5;
    color: #212529;
    background-color: #fff;
    background-clip: padding-box;
    border: 1px solid #ced4da;
    border-radius: 0.25rem;
    cursor: pointer;
    user-select: none;
    -webkit-user-select: none;
    -webkit-tap-highlight-color: transparent;
    transition: border-color .15s ease-in-out,box-shadow .15s ease-in-out;
    text-align: center; /* Center text for better mobile look */
}

.custom-date-trigger:active {
    background-color: #f8f9fa;
    border-color: #86b7fe;
}

.date-picker-wrapper {
    display: flex;
    justify-content: center;
    margin-bottom: 20px;
}
</style>

<style scoped>
.card {
  background-color: var(--card-bg);
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}
h2 {
  margin-top: 0;
  margin-bottom: 24px;
  font-size: 20px;
  color: var(--text-color);
}
.profile-details {
  display: grid;
  gap: 16px;
  margin-bottom: 24px;
}
.detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  background-color: #f7f7f7;
  border-radius: 8px;
}
.label {
  font-weight: 500;
  color: var(--text-secondary);
  font-size: 14px;
}
.value {
  font-weight: 500;
  color: var(--text-color);
  font-size: 15px;
}
.value.code {
  direction: ltr;
  font-family: monospace;
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}
.header-row h2 { margin: 0; }
.back-button {
  background: transparent;
  border: none;
  font-size: 20px;
  cursor: pointer;
}

.edit-section {
  background-color: #f0f9ff;
  padding: 16px;
  border-radius: 10px;
  border: 1px solid #bae6fd;
  margin-top: 16px;
}
.form-group {
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pointer-cursor {
    cursor: pointer;
}
.form-group label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}
.form-select {
  padding: 10px;
  border-radius: 8px;
  border: 1px solid #ddd;
  background-color: #fff;
  font-family: inherit;
  font-size: 14px;
  width: 100%; /* Ensure full width for inputs */
  box-sizing: border-box;
}
/* Ensure number inputs are editable */
input[type="number"].form-select {
  -moz-appearance: textfield;
  appearance: textfield;
  cursor: text;
  user-select: text;
  pointer-events: auto;
}
input[type="number"].form-select::-webkit-outer-spin-button,
input[type="number"].form-select::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
/* Number input styling */
.form-input {
  padding: 10px;
  border-radius: 8px;
  border: 1px solid #ddd;
  background-color: #fff;
  font-family: inherit;
  font-size: 14px;
  width: 100%;
  box-sizing: border-box;
  cursor: text;
  user-select: text;
  -moz-appearance: textfield;
  appearance: textfield;
}
.form-input:focus {
  outline: none;
  border-color: var(--accent-gold);
  box-shadow: 0 0 0 2px rgba(212, 175, 55, 0.2);
}
.form-input::-webkit-outer-spin-button,
.form-input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.checkbox-group {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
}
.action-buttons {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}
.save-btn {
  flex: 1;
  padding: 10px;
  background-color: var(--primary-color);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}
.cancel-btn {
  flex: 1;
  padding: 10px;
  background-color: #e5e7eb; /* Slightly darker gray for better contrast */
  color: #1f2937; /* Darker text */
  border: none;
  border-radius: 8px;
  cursor: pointer;
}

.edit-btn {
  background-color: #fffbeb !important;
  color: #b45309 !important;
  border-color: #fcd34d !important;
}
.delete-btn {
  background-color: #fef2f2 !important;
  color: #b91c1c !important;
  border-color: #fecaca !important;
}

.menu-button {
  width: 100%;
  padding: 14px;
  font-size: 15px;
  font-weight: 600;
  background-color: var(--card-bg);
  color: var(--text-color);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.2s ease-in-out;
  margin-bottom: 12px;
}
.menu-button:hover {
  border-color: var(--primary-color);
  color: var(--primary-color);
  background-color: #f0f9ff;
}
.menu-button:active {
  background-color: #e0f2fe;
  transform: translateY(1px);
}
/* حذف فاصله از آخرین دکمه */
.menu-button:last-child {
  margin-bottom: 0;
}

.text-red {
    color: #ef4444;
    font-weight: bold;
}

.block-btn {
    background-color: #fee2e2 !important;
    color: #991b1b !important;
    border-color: #fecaca !important;
}
.unblock-btn {
    background-color: #dcfce7 !important;
    color: #166534 !important;
    border-color: #bbf7d0 !important;
}
.settings-btn {
    background-color: #f3f4f6;
    color: #374151;
}

/* Modal Styles */
/* Modal Styles */
.modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: rgba(0,0,0,0.5);
    display: flex;
    z-index: 1000;
    padding: 20px;
    overflow-y: auto; /* Allow scrolling if content is tall */
}
.modal-content {
    background-color: white;
    border-radius: 16px;
    padding: 20px;
    width: 100%;
    max-width: 320px;
    min-height: 450px; /* Increased to Stabilize height during content switch */
    box-shadow: 0 10px 25px rgba(0,0,0,0.2);
    text-align: center;
    margin: auto; /* Centers the modal vertically and horizontally in flex container */
}
.modal-content h3 {
    margin-top: 0;
    margin-bottom: 16px;
    font-size: 18px;
    color: #111827; /* Darker title */
}
.duration-list {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-bottom: 16px;
}
.duration-btn {
    padding: 12px;
    border: 1px solid #d1d5db; /* Darker border */
    border-radius: 8px;
    background-color: #f3f4f6; /* Light gray bg */
    color: #1f2937; /* Dark text for contrast */
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    transition: all 0.2s;
}
.duration-btn:hover {
    background-color: #e0f2fe;
    border-color: #3b82f6;
    color: #1d4ed8;
}
.full-width {
    width: 100%;
}
.custom-date-section {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin-bottom: 16px;
    text-align: right;
}

.limitations-box {
    background-color: #fff7ed;
    border: 1px solid #fed7aa;
    border-radius: 8px;
    padding: 12px;
    margin-top: 8px;
}
.limitations-box h4 {
    margin: 0 0 8px 0;
    font-size: 14px;
    color: #c2410c;
}
.limit-item {
    display: flex;
    justify-content: space-between;
    font-size: 13px;
    margin-bottom: 4px;
    color: #9a3412;
}
.limit-expiry {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px dashed #fed7aa;
    font-size: 12px;
    color: #ea580c;
    display: flex;
    justify-content: space-between;
}
.usage-ratio {
    font-family: 'Vazirmatn', tahoma, sans-serif;
    font-weight: 600;
    color: #c2410c;
    direction: ltr;
}

/* Countdown Timer Styles */
.countdown-box {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 16px;
    border-radius: 12px;
    margin: 12px 0;
    animation: pulse 2s infinite;
}
.restriction-countdown {
    background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%);
    border: 1px solid #fecaca;
}
.countdown-icon {
    font-size: 18px;
}
.countdown-label {
    font-size: 13px;
    color: #7f1d1d;
}
.countdown-value {
    font-family: 'Vazirmatn', monospace;
    font-size: 14px;
    font-weight: 700;
    color: #dc2626;
    background: rgba(255, 255, 255, 0.7);
    padding: 4px 10px;
    border-radius: 8px;
    margin-right: auto;
    direction: ltr;
}
.countdown-inline {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px dashed #fbbf24;
}
.countdown-inline .countdown-icon {
    font-size: 14px;
}
.countdown-inline .countdown-label {
    font-size: 12px;
    color: #92400e;
}
.countdown-inline .countdown-value {
    font-size: 13px;
    font-weight: 600;
    color: #d97706;
    background: rgba(254, 243, 199, 0.8);
    padding: 3px 8px;
    border-radius: 6px;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.85; }
}
</style>