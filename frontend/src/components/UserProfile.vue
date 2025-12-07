<script setup lang="ts">
import { ref, computed } from 'vue';
import DatePicker from 'vue3-persian-datetime-picker';
import moment from 'moment-jalaali';

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
const customLimitDate = ref('');
const selectedRole = ref(props.user?.role || 'تماشا');
const hasBotAccess = ref(props.user?.has_bot_access ?? true);

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

const showCustomDateInput = ref(false);
const customDate = ref('');

const isRestricted = computed(() => {
  if (!props.user.trading_restricted_until) return false;
  // Backend returns naive UTC string (e.g., "2023-01-01T12:00:00").
  // Append 'Z' to force browser to interpret it as UTC.
  return new Date(props.user.trading_restricted_until + 'Z') > new Date();
});

const restrictionText = computed(() => {
  if (!isRestricted.value) return '✅ آزاد';
  const date = new Date(props.user.trading_restricted_until + 'Z');
  if (date.getFullYear() > 2100) return '⛔ مسدود دائم';
  return `⛔ تا ${props.user.trading_restricted_until_jalali}`;
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
        // Parse Jalali date string to ISO
        const date = moment(customDate.value, 'jYYYY/jMM/jDD HH:mm').utc();
        
        if (!date.isValid()) {
             alert('تاریخ نامعتبر است.');
             return;
        }
        await sendBlockRequest(date.toISOString());
    } catch (e) {
        alert('خطا در انجام عملیات');
    } finally {
        isLoading.value = false;
    }
}

async function sendBlockRequest(restrictedUntil: string) {
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
    Object.assign(props.user, updatedUser);
    showBlockModal.value = false;
    showCustomDateInput.value = false;
    alert('کاربر مسدود شد.');
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
             expireAt = moment(customLimitDate.value, 'jYYYY/jMM/jDD HH:mm').utc().toISOString();
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
    alert('رفع مسدودیت انجام شد.');
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
      
      <!-- نمایش محدودیت‌ها -->
      <div v-if="user.max_daily_trades || user.max_active_commodities || user.max_daily_requests" class="limitations-box">
          <h4>⚠️ محدودیت‌های فعال:</h4>
          <div v-if="user.max_daily_trades" class="limit-item">
              <span>معاملات روزانه:</span> <span>{{ user.max_daily_trades }}</span>
          </div>
          <div v-if="user.max_active_commodities" class="limit-item">
              <span>کالاهای فعال:</span> <span>{{ user.max_active_commodities }}</span>
          </div>
          <div v-if="user.max_daily_requests" class="limit-item">
              <span>درخواست‌های روزانه:</span> <span>{{ user.max_daily_requests }}</span>
          </div>
          <div v-if="user.limitations_expire_at" class="limit-expiry">
              <span>انقضا:</span> <span>{{ user.limitations_expire_at_jalali }}</span>
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
            
            <button @click="openLimitationsModal" class="menu-button">⚠️ اعمال محدودیت</button>
            
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
                <date-picker v-model="customDate" type="datetime" format="jYYYY/jMM/jDD HH:mm" display-format="jYYYY/jMM/jDD HH:mm" :editable="false" :auto-submit="false" append-to="body" />
                <div class="action-buttons">
                     <button @click="blockUserCustom" class="save-btn">تایید</button>
                     <button @click="showCustomDateInput = false" class="cancel-btn">بازگشت</button>
                </div>
            </div>

            <button v-if="!showCustomDateInput" @click="showBlockModal = false" class="cancel-btn full-width">انصراف</button>
        </div>
    </div>

    <!-- مودال اعمال محدودیت -->
    <div v-if="showLimitationsModal" class="modal-overlay">
        <div class="modal-content">
            <h3>⚠️ اعمال محدودیت</h3>
            
            <div class="form-group">
                <label>حداکثر تعداد معاملات روزانه:</label>
                <input type="number" v-model="limitMaxTrades" class="form-select" placeholder="نامحدود (خالی)" />
            </div>
            <div class="form-group">
                <label>حداکثر کالاهای فعال:</label>
                <input type="number" v-model="limitMaxCommodities" class="form-select" placeholder="نامحدود (خالی)" />
            </div>
            <div class="form-group">
                <label>حداکثر درخواست‌های روزانه:</label>
                <input type="number" v-model="limitMaxRequests" class="form-select" placeholder="نامحدود (خالی)" />
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
                <date-picker v-model="customLimitDate" type="datetime" format="jYYYY/jMM/jDD HH:mm" display-format="jYYYY/jMM/jDD HH:mm" :editable="false" :auto-submit="false" append-to="body" />
            </div>
            
            <div class="action-buttons">
                <button @click="saveLimitations" :disabled="isLoading" class="save-btn">ذخیره</button>
                <button @click="showLimitationsModal = false" class="cancel-btn">انصراف</button>
            </div>
        </div>
    </div>

    <template v-if="!isAdminView">
        <button class="menu-button notification-btn" @click="emit('navigate', 'notifications')">
          🔔 صندوق پیام‌ها
        </button>
        <button class="menu-button settings-btn" @click="emit('navigate', 'settings')">
          ⚙️ تنظیمات
        </button>
    </template>
  </div>
</template>

<style>
/* Global fix for datepicker buttons */
.vpd-actions {
    display: flex !important;
    justify-content: space-between !important;
    min-height: 40px !important;
    padding: 10px !important;
    background: #fff;
    border-top: 1px solid #eee;
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
.form-group label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}
.form-select {
  padding: 10px;
  border-radius: 8px;
  border: 1px solid #ddd;
  font-family: inherit;
  font-size: 14px;
  width: 100%; /* Ensure full width for inputs */
  box-sizing: border-box;
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
.modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: 20px;
}
.modal-content {
    background-color: white;
    border-radius: 16px;
    padding: 20px;
    width: 100%;
    max-width: 320px;
    box-shadow: 0 10px 25px rgba(0,0,0,0.2);
    text-align: center;
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
</style>