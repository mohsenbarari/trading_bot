<script setup lang="ts">
import { ref, computed, watchEffect, onUnmounted, watch, toRef } from 'vue';
import moment from 'moment-jalaali';
import DatePicker from 'vue3-persian-datetime-picker';
import { ArrowRight, ChevronLeft } from 'lucide-vue-next';
import { apiFetch } from '../utils/auth';
import { useUserProfileTiming } from '../composables/useUserProfileTiming';
import { isCachedMiddleManager } from '../utils/adminAccess';
import { formatIranDateTime } from '../utils/iranTime';
import HelpPopover from './HelpPopover.vue';

const props = defineProps<{
  user: any;
  isAdminView?: boolean;
  apiBaseUrl?: string;
  jwtToken?: string | null;
}>();

const emit = defineEmits(['navigate']);

const {
  countdownRestriction,
  countdownLimitation,
  parseJalaliToIranISO,
  toEnglishDigits,
} = useUserProfileTiming(toRef(props, 'user'));

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
const editMaxSessions = ref(props.user?.max_sessions ?? 1);
const editMaxAccountants = ref(props.user?.max_accountants ?? 3);
const editMaxCustomers = ref(props.user?.max_customers ?? 5);
const canBlockUsers = ref(props.user?.can_block_users ?? true);
const editMaxBlockedUsers = ref(props.user?.max_blocked_users ?? 10);
const accountStatus = ref(props.user?.account_status ?? 'active');
const isTerminatingSessions = ref(false);
const showCustomerContext = computed(() => Boolean(
  props.user?.is_customer
  || props.user?.customer_management_name
  || props.user?.customer_owner_account_name
  || props.user?.customer_tier,
));
const canEditRole = !isCachedMiddleManager();

function getCustomerTierLabel(value: string | null | undefined) {
  if (value === 'tier2') return 'سطح 2';
  if (value === 'tier1') return 'سطح 1';
  return '---';
}

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

watch(
  () => props.user?.max_sessions,
  (value) => {
    editMaxSessions.value = value ?? 1;
  }
);

watch(
  () => props.user?.max_accountants,
  (value) => {
    editMaxAccountants.value = value ?? 3;
  }
);

watch(
  () => props.user?.max_customers,
  (value) => {
    editMaxCustomers.value = value ?? 5;
  }
);

watch(
  () => props.user?.can_block_users,
  (value) => {
    canBlockUsers.value = value ?? true;
  }
);

watch(
  () => props.user?.max_blocked_users,
  (value) => {
    editMaxBlockedUsers.value = value ?? 10;
  }
);

watch(
  () => props.user?.account_status,
  (value) => {
    accountStatus.value = value ?? 'active';
  }
);

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

const isAccountInactive = computed(() => (props.user?.account_status ?? accountStatus.value) === 'inactive');

function formatAccountStatusDate(value: string | null | undefined) {
  if (!value) return null;
  return formatIranDateTime(value, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }) || null;
}

const globalLockGraceExpiresAtText = computed(() => formatAccountStatusDate(props.user?.global_lock_grace_expires_at));
const globalWebLockedAtText = computed(() => formatAccountStatusDate(props.user?.global_web_locked_at));

const accountStatusText = computed(() => {
  if (!isAccountInactive.value) return '✅ فعال';
  if (globalWebLockedAtText.value) return '⛔ غیرفعال (قفل کامل)';
  if (globalLockGraceExpiresAtText.value) return '⛔ غیرفعال (در مهلت فعال‌سازی)';
  return '⛔ غیرفعال';
});

const accountStatusDetailText = computed(() => {
  if (!isAccountInactive.value) return null;
  if (globalWebLockedAtText.value) {
    return `قفل سراسری وب/پیام‌رسان از ${globalWebLockedAtText.value}`;
  }
  if (globalLockGraceExpiresAtText.value) {
    return `پایان مهلت فعال‌سازی: ${globalLockGraceExpiresAtText.value}`;
  }
  return 'این حساب از بازار خارج شده و تا فعال‌سازی مجدد، دسترسی معاملاتی ندارد.';
});

async function saveRole() {
  if (!canEditRole) return;
  if (!props.jwtToken) return;
  isLoading.value = true;
  try {
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'PUT',
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

async function toggleAccountStatus() {
  if (!props.jwtToken) return;
  if (!confirm(`آیا از ${isAccountInactive.value ? 'فعال' : 'غیرفعال'} کردن حساب اطمینان دارید؟`)) return;
  
  isLoading.value = true;
  try {
    const newValue = isAccountInactive.value ? 'active' : 'inactive';
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'PUT',
      body: JSON.stringify({ account_status: newValue })
    });
    if (!response.ok) throw new Error('خطا در تغییر دسترسی');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
    accountStatus.value = newValue;
    alert(`وضعیت حساب ${newValue === 'active' ? 'فعال' : 'غیرفعال'} شد.`);
  } catch (e) {
    alert('خطا در تغییر دسترسی');
  } finally {
    isLoading.value = false;
  }
}

async function blockUser(minutes: number) {
  if (!props.jwtToken) return;

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
        const response = await apiFetch(`/api/users/${props.user.id}`, {
          method: 'PUT',
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
    if (!props.jwtToken) return;
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

        const response = await apiFetch(`/api/users/${props.user.id}`, {
            method: 'PUT',
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
  if (!props.jwtToken) return;
  isLoading.value = true;
  try {
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'PUT',
      body: JSON.stringify({ trading_restricted_until: null })
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
  if (!props.jwtToken) return;
  isLoading.value = true;
  try {
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'PUT',
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

async function saveMaxSessions() {
  try {
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'PUT',
      body: JSON.stringify({ max_sessions: editMaxSessions.value })
    });
    if (!response.ok) throw new Error('خطا');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
  } catch (e) {
    alert('خطا در ذخیره تنظیمات نشست');
    editMaxSessions.value = props.user.max_sessions ?? 1;
  }
}

async function saveMaxAccountants() {
  const normalizedValue = Number.isFinite(editMaxAccountants.value)
    ? Math.max(0, Math.trunc(editMaxAccountants.value))
    : 0;
  editMaxAccountants.value = normalizedValue;

  try {
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'PUT',
      body: JSON.stringify({ max_accountants: normalizedValue })
    });
    if (!response.ok) throw new Error('خطا');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
  } catch (e) {
    alert('خطا در ذخیره سقف حسابداران');
    editMaxAccountants.value = props.user.max_accountants ?? 3;
  }
}

async function saveMaxCustomers() {
  const normalizedValue = Number.isFinite(editMaxCustomers.value)
    ? Math.max(0, Math.trunc(editMaxCustomers.value))
    : 0;
  editMaxCustomers.value = normalizedValue;

  try {
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'PUT',
      body: JSON.stringify({ max_customers: normalizedValue })
    });
    if (!response.ok) throw new Error('خطا');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
  } catch (e) {
    alert('خطا در ذخیره سقف مشتریان');
    editMaxCustomers.value = props.user.max_customers ?? 5;
  }
}

async function toggleBlockCapability() {
  const nextValue = !canBlockUsers.value;

  try {
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'PUT',
      body: JSON.stringify({ can_block_users: nextValue })
    });
    if (!response.ok) throw new Error('خطا');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
    canBlockUsers.value = updatedUser.can_block_users ?? nextValue;
    alert(`حق بلاک‌کردن دیگران ${canBlockUsers.value ? 'فعال' : 'غیرفعال'} شد.`);
  } catch (e) {
    alert('خطا در ذخیره مجوز بلاک');
  }
}

async function saveMaxBlockedUsers() {
  const normalizedValue = Number.isFinite(editMaxBlockedUsers.value)
    ? Math.min(100, Math.max(1, Math.trunc(editMaxBlockedUsers.value)))
    : 10;
  editMaxBlockedUsers.value = normalizedValue;

  try {
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'PUT',
      body: JSON.stringify({ max_blocked_users: normalizedValue })
    });
    if (!response.ok) throw new Error('خطا');
    const updatedUser = await response.json();
    Object.assign(props.user, updatedUser);
  } catch (e) {
    alert('خطا در ذخیره سقف بلاک');
    editMaxBlockedUsers.value = props.user.max_blocked_users ?? 10;
  }
}

async function terminateAllSessions() {
  if (!confirm('آیا از پایان دادن فوری به همه نشست‌های فعال این کاربر اطمینان دارید؟')) return;

  isTerminatingSessions.value = true;
  try {
    const response = await apiFetch(`/api/users/${props.user.id}/sessions/terminate-all`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('خطا');

    const result = await response.json();
    const terminatedSessions = Number(result.terminated_sessions ?? 0);
    alert(
      terminatedSessions > 0
        ? `${terminatedSessions} نشست پایان یافت.`
        : 'نشست فعالی برای پایان دادن وجود نداشت.'
    );
  } catch (e) {
    alert('خطا در پایان دادن به نشست‌های فعال');
  } finally {
    isTerminatingSessions.value = false;
  }
}

function handleAdminSessionClick() {
  if (props.user.role === 'مدیر ارشد' || props.user.role === 'مدیر میانی') {
    alert('به دلایل امنیتی، تعداد نشست‌های مجاز برای مدیران سایت نمی‌تواند بیش از ۱ باشد.');
  }
}

async function deleteUser() {
  if (!confirm('آیا از حذف این کاربر اطمینان دارید؟')) return;
  if (!props.jwtToken) return;
  isLoading.value = true;
  try {
    const response = await apiFetch(`/api/users/${props.user.id}`, {
      method: 'DELETE',
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
        <div class="header-spacer"></div>
        <div class="header-title">
            <h2>{{ isAdminView ? 'مدیریت کاربر' : 'پروفایل کاربری' }}</h2>
        </div>
        <button v-if="isAdminView" class="back-button" @click="$emit('navigate', 'manage_users')"><ChevronLeft :size="24" /></button>
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
          <span class="label">وضعیت حساب</span>
          <span class="value">{{ accountStatusText }}</span>
      </div>
        <div v-if="accountStatusDetailText" class="detail-item account-status-detail-row">
          <span class="label">جزئیات وضعیت</span>
          <span class="value">{{ accountStatusDetailText }}</span>
        </div>
      <div class="detail-item">
          <span class="label">وضعیت معاملات</span>
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

          <div v-if="showCustomerContext" class="limitations-box customer-context-box">
            <h4>👥 اطلاعات مشتری</h4>
            <div class="limit-item">
              <span>نام مدیریتی:</span>
              <span>{{ user.customer_management_name || '---' }}</span>
            </div>
            <div class="limit-item">
              <span>مالک:</span>
              <span>{{ user.customer_owner_account_name || '---' }}</span>
            </div>
            <div class="limit-item">
              <span>سطح مشتری:</span>
              <span>{{ getCustomerTierLabel(user.customer_tier) }}</span>
            </div>
          </div>

      <!-- تنظیمات نشست -->
      <div v-if="isAdminView" class="sessions-config-box">
        <div class="detail-item">
          <span class="label">حداکثر نشست همزمان</span>
          <div class="inline-edit" @click="handleAdminSessionClick">
            <select v-model.number="editMaxSessions" class="form-select-sm" @change="saveMaxSessions" :disabled="user.role === 'مدیر ارشد' || user.role === 'مدیر میانی'" :style="{ pointerEvents: (user.role === 'مدیر ارشد' || user.role === 'مدیر میانی') ? 'none' : 'auto' }">
              <option :value="1">۱</option>
              <option :value="2">۲</option>
              <option :value="3">۳</option>
            </select>
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">حداکثر حسابداران مجاز</span>
          <div class="inline-edit">
            <input
              v-model.number="editMaxAccountants"
              type="number"
              min="0"
              step="1"
              class="form-input-sm max-accountants-input"
              @change="saveMaxAccountants"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">حداکثر مشتریان مجاز</span>
          <div class="inline-edit">
            <input
              v-model.number="editMaxCustomers"
              type="number"
              min="0"
              step="1"
              class="form-input-sm max-customers-input"
              @change="saveMaxCustomers"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">حق بلاک‌کردن دیگران</span>
          <div class="inline-edit">
            <button
              type="button"
              class="inline-action-btn toggle-block-capability-btn"
              :class="{ 'is-disabled': !canBlockUsers }"
              @click="toggleBlockCapability"
            >
              {{ canBlockUsers ? 'فعال' : 'غیرفعال' }}
            </button>
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">سقف کاربران قابل بلاک</span>
          <div class="inline-edit">
            <input
              v-model.number="editMaxBlockedUsers"
              type="number"
              min="1"
              max="100"
              step="1"
              class="form-input-sm max-blocked-users-input"
              @change="saveMaxBlockedUsers"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row terminate-sessions-row">
          <span class="label">نشست‌های فعال کاربر</span>
          <div class="inline-edit">
            <button
              type="button"
              class="inline-action-btn danger-inline-btn terminate-sessions-btn"
              :disabled="isTerminatingSessions"
              @click="terminateAllSessions"
            >
              {{ isTerminatingSessions ? 'در حال قطع...' : 'پایان فوری همه نشست‌ها' }}
            </button>
          </div>
        </div>
      </div>

      <!-- ویرایش نقش (مودال داخلی) -->
      <div v-if="isEditingRole && canEditRole" class="edit-section">
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
        <div v-if="!showSettings" class="main-actions profile-menu-card card-with-help">
            <HelpPopover
              floating
              button-test="user-profile-admin-menu-help"
              note-test="user-profile-admin-menu-help-note"
              label="راهنمای منوی مدیریت کاربر"
              text="عملیات این بخش فقط روی همین کاربر اعمال می‌شود. حذف کاربر، نشست‌ها و دسترسی‌های فعال او را هم مدیریت می‌کند."
            />
            <button @click="showSettings = true" class="menu-button settings-btn">
              <span class="menu-button-icon">⚙</span>
              <span class="menu-button-label">تنظیمات کاربر</span>
            </button>
            <button @click="deleteUser" class="menu-button delete-btn">
              <span class="menu-button-icon">✕</span>
              <span class="menu-button-label">حذف کاربر</span>
            </button>
            <button @click="$emit('navigate', 'manage_users')" class="menu-button back-btn">
              <span class="menu-button-icon">‹</span>
              <span class="menu-button-label">بازگشت به لیست</span>
            </button>
        </div>

        <div v-else class="settings-menu profile-menu-card card-with-help">
          <HelpPopover
            floating
            button-test="user-profile-settings-menu-help"
            note-test="user-profile-settings-menu-help-note"
            label="راهنمای زیرمنوی تنظیمات کاربر"
            text="این زیرمنو برای تغییر وضعیت حساب، نقش، محدودیت و مسدودیت کاربر است. گزینه‌های حذف یا بازگشت در منوی قبلی قرار دارند."
          />
          <button @click="toggleAccountStatus" class="menu-button">
            <span class="menu-button-icon">↻</span>
            <span class="menu-button-label">تغییر وضعیت حساب ({{ isAccountInactive ? 'غیرفعال' : 'فعال' }})</span>
            </button>
            <button v-if="canEditRole" @click="isEditingRole = true" class="menu-button">
              <span class="menu-button-icon">✎</span>
              <span class="menu-button-label">ویرایش نقش</span>
            </button>
            
            <button v-if="!hasLimitations" @click="openLimitationsModal" class="menu-button">
              <span class="menu-button-icon">!</span>
              <span class="menu-button-label">اعمال محدودیت</span>
            </button>
            <button v-else @click="removeLimitations" class="menu-button unlimit-btn">
                <span class="menu-button-icon">✓</span>
                <span class="menu-button-label">رفع محدودیت</span>
            </button>
            
            <button v-if="!isRestricted" @click="showBlockModal = true" class="menu-button block-btn">
                <span class="menu-button-icon">⊘</span>
                <span class="menu-button-label">مسدود کردن</span>
            </button>
            <button v-else @click="unblockUser" class="menu-button unblock-btn">
                <span class="menu-button-icon">⌁</span>
                <span class="menu-button-label">رفع مسدودیت</span>
            </button>

            <button @click="showSettings = false" class="menu-button back-btn">
              <span class="menu-button-icon">‹</span>
              <span class="menu-button-label">بازگشت</span>
            </button>
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
      <div class="profile-user-actions profile-menu-card card-with-help">
        <HelpPopover
          floating
          button-test="profile-user-actions-help"
          note-test="profile-user-actions-help-note"
          label="راهنمای پنل کاربری"
          text="از این بخش به پیام‌های سیستمی و تنظیمات مجاز همین حساب دسترسی داری. گزینه‌های مدیریتی فقط برای ادمین‌ها نمایش داده می‌شود."
        />
        <button class="menu-button notification-btn" @click="emit('navigate', 'notifications')">
          <span class="menu-button-icon">🔔</span>
          <span class="menu-button-label">صندوق پیام‌ها</span>
        </button>
        <!-- دکمه تنظیمات فقط برای نقش‌های غیر عادی -->
        <button 
          v-if="user.role !== 'عادی'" 
          class="menu-button settings-btn" 
          @click="emit('navigate', 'user_settings')"
        >
          <span class="menu-button-icon">⚙</span>
          <span class="menu-button-label">تنظیمات</span>
        </button>
      </div>
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
    background: linear-gradient(135deg, #f59e0b, #d97706);
    color: white;
    border: none;
    padding: 10px;
    border-radius: 10px;
    font-size: 15px;
    font-weight: bold;
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(245, 158, 11, 0.25);
}

.integrated-cancel-btn {
    flex: 1;
    background-color: white;
    color: #6b7280;
    border: 1px solid rgba(245, 158, 11, 0.15);
    padding: 10px;
    border-radius: 10px;
    font-size: 15px;
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
    border: 2px solid #f59e0b;
    border-radius: 12px;
    text-align: center;
    background: white;
    color: #1f2937;
    width: auto;
    min-width: 150px;
}

.native-time-picker .time-input:focus {
    outline: none;
    border-color: #d97706;
    box-shadow: 0 0 10px rgba(245, 158, 11, 0.3);
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
    display: block;
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
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(245, 158, 11, 0.1);
  border-radius: 1.25rem;
  padding: 1.25rem;
  box-shadow: 0 4px 16px rgba(0,0,0,0.04);
}
h2 {
  margin-top: 0;
  margin-bottom: 1.25rem;
  font-size: 1rem;
  font-weight: 800;
  color: #1f2937;
}
.profile-details {
  display: grid;
  gap: 0.625rem;
  margin-bottom: 1.25rem;
}
.detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 0.875rem;
  background: white;
  border: 1px solid rgba(245, 158, 11, 0.08);
  border-radius: 0.875rem;
}
.label {
  font-weight: 700;
  color: #9ca3af;
  font-size: 0.75rem;
}
.value {
  font-weight: 700;
  color: #1f2937;
  font-size: 0.85rem;
}
.value.code {
  direction: ltr;
  font-family: monospace;
}




.edit-section {
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  padding: 1rem;
  border-radius: 1rem;
  border: 1px solid rgba(245, 158, 11, 0.15);
  margin-top: 0.875rem;
}
.form-group {
  margin-bottom: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}
.form-group label {
  font-size: 0.75rem;
  font-weight: 700;
  color: #6b7280;
}
.form-select, .form-input {
  padding: 0.625rem;
  border-radius: 0.75rem;
  border: 1px solid rgba(245, 158, 11, 0.15);
  background: white;
  font-family: inherit;
  font-size: 0.85rem;
  width: 100%;
  box-sizing: border-box;
  outline: none;
  transition: all 0.2s;
}
.form-select:focus, .form-input:focus {
  border-color: #f59e0b;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1);
}
input[type="number"].form-select, input[type="number"].form-input {
  -moz-appearance: textfield;
  appearance: textfield;
  cursor: text;
  user-select: text;
}
input[type="number"].form-select::-webkit-outer-spin-button,
input[type="number"].form-select::-webkit-inner-spin-button,
input[type="number"].form-input::-webkit-outer-spin-button,
input[type="number"].form-input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.action-buttons {
  display: flex;
  gap: 0.625rem;
  margin-top: 1rem;
}
.save-btn {
  flex: 1;
  padding: 0.625rem;
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white;
  border: none;
  border-radius: 0.75rem;
  cursor: pointer;
  font-weight: 700;
  font-size: 0.85rem;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.save-btn:active { transform: scale(0.98); }
.cancel-btn {
  flex: 1;
  padding: 0.625rem;
  background: white;
  color: #6b7280;
  border: 1px solid rgba(245, 158, 11, 0.15);
  border-radius: 0.75rem;
  cursor: pointer;
  font-weight: 700;
  font-size: 0.85rem;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.cancel-btn:active { background: #f9fafb; }

.profile-menu-card {
  position: relative;
  margin-top: 0.875rem;
  padding: 1rem;
  padding-left: 3.8rem;
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 1.25rem;
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.72), rgba(255, 255, 255, 0.96));
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.07);
}

.profile-user-actions {
  margin-top: 1rem;
}

.menu-button {
  width: 100%;
  min-height: 3.15rem;
  padding: 0.72rem 0.8rem;
  font-size: 0.85rem;
  font-weight: 850;
  background: rgba(255, 255, 255, 0.94);
  color: #1f2937;
  border: 1px solid rgba(15, 23, 42, 0.07);
  border-radius: 1rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.72rem;
  transition: all 0.2s;
  margin-bottom: 0.625rem;
  text-align: right;
  -webkit-tap-highlight-color: transparent;
}
.menu-button:hover {
  border-color: rgba(245, 158, 11, 0.3);
  background: #fffbeb;
}
.menu-button:active {
  transform: scale(0.98);
}
.menu-button:last-child {
  margin-bottom: 0;
}

.menu-button-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 0.8rem;
  background: rgba(245, 158, 11, 0.12);
  color: #92400e;
  font-size: 0.95rem;
  line-height: 1;
  flex: 0 0 auto;
}

.menu-button-label {
  flex: 1;
  min-width: 0;
}

.text-red { color: #ef4444; font-weight: bold; }

.block-btn {
  background: #fef2f2 !important;
  color: #991b1b !important;
  border-color: #fecaca !important;
}
.block-btn .menu-button-icon,
.delete-btn .menu-button-icon {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
}
.unblock-btn {
  background: #f0fdf4 !important;
  color: #166534 !important;
  border-color: #bbf7d0 !important;
}
.unlimit-btn {
  background: #f0fdf4 !important;
  color: #166534 !important;
  border-color: #bbf7d0 !important;
}
.unblock-btn .menu-button-icon,
.unlimit-btn .menu-button-icon {
  background: rgba(34, 197, 94, 0.14);
  color: #166534;
}
.settings-btn {
  background: linear-gradient(135deg, #fffbeb, #fef3c7) !important;
  color: #92400e !important;
  border-color: rgba(245, 158, 11, 0.2) !important;
}
.delete-btn {
  background: #fef2f2 !important;
  color: #dc2626 !important;
  border-color: #fecaca !important;
}
.back-btn {
  background: white !important;
  color: #6b7280 !important;
  border-color: rgba(245, 158, 11, 0.12) !important;
}

/* Modal Styles */
.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  backdrop-filter: blur(4px);
  display: flex;
  z-index: 1000;
  padding: 1.25rem;
  overflow-y: auto;
}
.modal-content {
  background: white;
  border-radius: 1.5rem;
  padding: 1.25rem;
  width: 100%;
  max-width: 320px;
  min-height: 420px;
  box-shadow: 0 16px 40px rgba(0,0,0,0.15);
  text-align: center;
  margin: auto;
}
.modal-content h3 {
  margin-top: 0;
  margin-bottom: 1rem;
  font-size: 1rem;
  font-weight: 800;
  color: #1f2937;
}
.duration-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem;
  margin-bottom: 1rem;
}
.duration-btn {
  padding: 0.75rem;
  border: 1px solid rgba(245, 158, 11, 0.15);
  border-radius: 0.875rem;
  background: white;
  color: #1f2937;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.duration-btn:hover {
  background: #fffbeb;
  border-color: rgba(245, 158, 11, 0.3);
  color: #92400e;
}
.duration-btn:active { transform: scale(0.95); }
.full-width { width: 100%; }
.custom-date-section {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-bottom: 1rem;
  text-align: right;
}

.limitations-box {
  background: linear-gradient(135deg, #fffbeb, #fff7ed);
  border: 1px solid #fed7aa;
  border-radius: 0.875rem;
  padding: 0.75rem;
  margin-top: 0.5rem;
}
.limitations-box h4 {
  margin: 0 0 0.5rem 0;
  font-size: 0.8rem;
  font-weight: 700;
  color: #c2410c;
}
.limit-item {
  display: flex; justify-content: space-between;
  font-size: 0.75rem; margin-bottom: 0.25rem; color: #9a3412;
}
.limit-expiry {
  margin-top: 0.5rem; padding-top: 0.5rem;
  border-top: 1px dashed #fed7aa;
  font-size: 0.7rem; color: #ea580c;
  display: flex; justify-content: space-between;
}
.usage-ratio {
  font-family: 'Vazirmatn', tahoma, sans-serif;
  font-weight: 700; color: #c2410c; direction: ltr;
}

/* Countdown Timer Styles */
.countdown-box {
  display: flex; align-items: center; gap: 0.5rem;
  padding: 0.75rem 1rem; border-radius: 0.875rem; margin: 0.75rem 0;
  animation: pulse 2s infinite;
}
.restriction-countdown {
  background: linear-gradient(135deg, #fef2f2, #fee2e2);
  border: 1px solid #fecaca;
}
.countdown-icon { font-size: 1rem; }
.countdown-label { font-size: 0.75rem; color: #7f1d1d; }
.countdown-value {
  font-family: 'Vazirmatn', monospace;
  font-size: 0.8rem; font-weight: 700; color: #dc2626;
  background: rgba(255,255,255,0.7);
  padding: 0.25rem 0.625rem; border-radius: 0.5rem;
  margin-right: auto; direction: ltr;
}
.countdown-inline {
  display: flex; align-items: center; gap: 0.375rem;
  margin-top: 0.625rem; padding-top: 0.625rem;
  border-top: 1px dashed #fbbf24;
}
.countdown-inline .countdown-icon { font-size: 0.8rem; }
.countdown-inline .countdown-label { font-size: 0.7rem; color: #92400e; }
.countdown-inline .countdown-value {
  font-size: 0.75rem; font-weight: 700; color: #d97706;
  background: rgba(254, 243, 199, 0.8);
  padding: 0.15rem 0.5rem; border-radius: 0.375rem;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.85; }
}

.sessions-config-box {
  margin: 0.75rem 0;
  padding: 0.75rem;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 0.75rem;
}
.sessions-config-box .detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.owner-limit-row {
  margin-top: 0.5rem;
}
.inline-edit {
  display: flex;
  align-items: center;
}
.form-select-sm {
  padding: 0.25rem 0.5rem;
  border-radius: 0.5rem;
  border: 1px solid #d1d5db;
  font-size: 0.8rem;
  background: white;
}
.form-input-sm {
  width: 4.5rem;
  padding: 0.25rem 0.5rem;
  border-radius: 0.5rem;
  border: 1px solid #d1d5db;
  font-size: 0.8rem;
  background: white;
  text-align: center;
}
.inline-action-btn {
  border: 1px solid #cbd5e1;
  background: white;
  color: #0f172a;
  border-radius: 0.625rem;
  padding: 0.4rem 0.75rem;
  font-size: 0.8rem;
  font-weight: 700;
  cursor: pointer;
}
.inline-action-btn.is-disabled {
  color: #991b1b;
  background: #fef2f2;
  border-color: #fecaca;
}
.danger-inline-btn {
  color: #b91c1c;
  background: #fff1f2;
  border-color: #fecdd3;
}
.danger-inline-btn:disabled {
  opacity: 0.7;
  cursor: wait;
}
.terminate-sessions-row {
  align-items: flex-start;
}
</style><style scoped>
.admin-lock-note {
  font-size: 0.75rem;
  color: #8f9296;
  margin-right: 0.5rem;
}
</style>
