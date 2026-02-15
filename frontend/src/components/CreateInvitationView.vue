<script setup lang="ts">
import { reactive, ref } from 'vue';

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>();

// emit دیگر استفاده نمی‌شود
// const emit = defineEmits(['invite-created']);

const invite = reactive({
  account_name: '',
  mobile_number: '',
  role: 'عادی',
});

const resultMessage = ref('');
const isLoading = ref(false);
const inviteLink = ref('');
const copyMessage = ref('');

function resetForm() {
  invite.account_name = '';
  invite.mobile_number = '';
  invite.role = 'عادی';
  resultMessage.value = '';
  inviteLink.value = '';
  copyMessage.value = '';
}

function copyToClipboard() {
  if (!inviteLink.value) return;
  try {
    navigator.clipboard.writeText(inviteLink.value);
    copyMessage.value = 'کپی شد!';
    setTimeout(() => { copyMessage.value = ''; }, 2000);
  } catch (e) {
    copyMessage.value = 'خطا';
    setTimeout(() => { copyMessage.value = ''; }, 2000);
  }
}

async function createInvite() {
  if (!props.jwtToken) {
    resultMessage.value = '❌ خطا: شما احراز هویت نشده‌اید.';
    return;
  }
  const normalizedMobile = normalizeMobile(invite.mobile_number);
  if (!/^09[0-9]{9}$/.test(normalizedMobile)) {
    resultMessage.value = '❌ شماره موبایل نامعتبر است. فرمت: 09xxxxxxxxx (فارسی یا انگلیسی)';
    return;
  }
  
  isLoading.value = true;
  resultMessage.value = '';
  inviteLink.value = '';
  copyMessage.value = '';

  try {
    const resp = await fetch(`${props.apiBaseUrl}/api/invitations/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${props.jwtToken}`,
      },
      body: JSON.stringify({ ...invite, mobile_number: normalizedMobile }),
    });
    
    const data = await resp.json();
    if (!resp.ok) {
      const detail = data.detail || 'خطا در ایجاد دعوت‌نامه';
      resultMessage.value = `❌ ${detail.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')}`;
      throw new Error(detail);
    }

    const configResp = await fetch(`${props.apiBaseUrl}/api/config`);
    const config = await configResp.json();
    const linkText = `https://t.me/${config.bot_username}?start=${data.token}`;
    
    inviteLink.value = linkText;
    resultMessage.value = '✅ لینک دعوت با موفقیت ایجاد شد.';
    
    // emit('invite-created', plainTextMessage); // (حذف شد)
    
  } catch (e: any) {
    if (!resultMessage.value.startsWith('❌')) {
       resultMessage.value = `❌ ${e.message}`;
    }
  } finally {
    isLoading.value = false;
  }
}

function normalizeMobile(mobile: string): string {
  if (!mobile) return "";
  const persianMap = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
  };
  return mobile.replace(/[۰-۹٠-٩]/g, (match) => (persianMap as any)[match]);
}
</script>

<template>
  <div class="card">
    <h2>ارسال لینک دعوت جدید</h2>
    <form @submit.prevent="createInvite" autocomplete="off">
      <div class="form-group">
        <label for="account_name">نام کاربری (Account Name)</label>
        <input v-model="invite.account_name" id="account_name" type="text" placeholder="مثلاً alireza" required />
      </div>
      <div class="form-group">
        <label for="mobile_number">شماره موبایل (ایران)</label>
        <input v-model="invite.mobile_number" id="mobile_number" type="tel" placeholder="09123456789" required />
      </div>
      <div class="form-group">
        <label for="role">نقش</label>
        <select v-model="invite.role" id="role">
          <option value="تماشا">تماشا</option>
          <option value="عادی">عادی</option>
          <option value="پلیس">پلیس</option>
          <option value="مدیر میانی">مدیر میانی</option>
        </select>
      </div>
      <div class="form-actions">
        <button type="submit" :disabled="isLoading">
          {{ isLoading ? 'در حال ساخت...' : 'ارسال لینک دعوت' }}
        </button>
        <button type="button" class="secondary" @click="resetForm" :disabled="isLoading">بازنشانی</button>
      </div>
    </form>

    <div v-if="resultMessage && !inviteLink" class="result-box error" v-html="resultMessage">
    </div>

    <div v-if="inviteLink" class="success-box">
      <div class="result-message">✅ لینک دعوت با موفقیت ایجاد شد:</div>
      <div class="copy-container">
        <input type="text" :value="inviteLink" @click="copyToClipboard" readonly />
        <button type="button" @click="copyToClipboard" class="copy-btn">
          {{ copyMessage ? copyMessage : 'کپی' }}
        </button>
      </div>
    </div>

  </div>
</template>

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
h2 { margin-top: 0; margin-bottom: 1.25rem; font-size: 1rem; font-weight: 800; color: #1f2937; }
.form-group { margin-bottom: 1rem; }
label { display: block; margin-bottom: 0.375rem; font-weight: 700; font-size: 0.78rem; color: #6b7280; }
input, select {
  width: 100%; padding: 0.625rem 0.875rem; border-radius: 0.75rem;
  border: 1px solid rgba(245, 158, 11, 0.15); background: white;
  font-size: 0.9rem; font-family: inherit; outline: none;
  transition: all 0.2s;
}
input:focus, select:focus {
  border-color: #f59e0b; background: white;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1);
}
.form-actions { display: flex; gap: 0.75rem; margin-top: 1.5rem; }
.form-actions button {
  flex-grow: 1; background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white; border: none; cursor: pointer; font-weight: 700;
  transition: all 0.2s; padding: 0.75rem; border-radius: 0.75rem;
  font-size: 0.9rem; -webkit-tap-highlight-color: transparent;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.25);
}
.form-actions button:active { transform: scale(0.98); }
.form-actions button:disabled { background: #d1d5db; box-shadow: none; cursor: not-allowed; color: white; }
.form-actions button.secondary {
  background: white; color: #6b7280; box-shadow: none;
  border: 1px solid rgba(245, 158, 11, 0.15); flex-grow: 0;
}
.form-actions button.secondary:active { background: #f9fafb; }

.result-box.error {
  margin-top: 1.25rem; padding: 0.75rem; border-radius: 0.75rem;
  background: #fef2f2; color: #991b1b; border: 1px solid #fecaca;
  font-size: 0.8rem; word-break: break-all;
}
.result-box :deep(strong) { color: #dc2626; }

.success-box {
  margin-top: 1.25rem; padding: 1rem; border-radius: 1rem;
  background: linear-gradient(135deg, #f0fdf4, #dcfce7);
  border: 1px solid #bbf7d0;
}
.result-message {
  color: #166534; font-size: 0.8rem; font-weight: 700; margin-bottom: 0.75rem;
}
.copy-container {
  display: flex;
  gap: 0.5rem;
}
.copy-container input[type="text"] {
  width: 0; flex: 1 1 0;
  direction: ltr; font-family: monospace; font-size: 0.8rem;
  background: white; color: #166534;
  border: 1px solid #bbf7d0; cursor: pointer;
  border-radius: 0.625rem; padding: 0.5rem 0.75rem;
}
.copy-container .copy-btn {
  flex: 0 0 auto; width: auto;
  font-weight: 700; font-size: 0.8rem; padding: 0.5rem 0.875rem;
  background: linear-gradient(135deg, #f59e0b, #d97706); color: white;
  border-radius: 0.625rem;
}
.copy-container .copy-btn:disabled { background: #d1d5db; }
</style>