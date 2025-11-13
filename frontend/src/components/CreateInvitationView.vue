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
/* استایل‌های پایه (بدون تغییر) */
.card { background-color: var(--card-bg); border-radius: 12px; padding: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
h2 { margin-top: 0; margin-bottom: 24px; font-size: 20px; }
.form-group { margin-bottom: 16px; }
label { display: block; margin-bottom: 8px; font-weight: 500; font-size: 14px; }
input, select {
  width: 100%; padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--border-color); background: #f7f7f7;
  font-size: 15px; font-family: inherit;
  transition: all 0.2s ease;
}
input:focus, select:focus { outline: none; border-color: var(--primary-color); background: white; box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.1); }
.form-actions { display: flex; gap: 12px; margin-top: 24px; }
button {
  flex-grow: 1; background: var(--primary-color); color: white; border: none;
  cursor: pointer; font-weight: 600; transition: background-color 0.2s ease;
  padding: 12px; border-radius: 8px; font-size: 15px;
}
button:hover { background-color: #0056b3; }
button:disabled { background-color: #a0a0a0; cursor: not-allowed; }
button.secondary { background: transparent; color: var(--text-secondary); border: 1px solid var(--border-color); flex-grow: 0; }

/* کادر خطا */
.result-box.error {
  margin-top: 20px; padding: 12px; border-radius: 8px;
  background-color: #fef2f2; color: #991b1b;
  border: 1px solid #fecaca;
  font-size: 14px; word-break: break-all;
}
.result-box :deep(strong) { color: #c0392b; }

/* === استایل‌های جدید برای کادر موفقیت (اصلاح شده) === */
.success-box {
  margin-top: 20px; padding: 12px; border-radius: 8px;
  background: #f0f9ff; border: 1px solid #bde5f8;
}
.result-message {
  color: #0c5460; font-size: 14px; font-weight: 500; margin-bottom: 10px;
}
.copy-container {
  display: flex;
  gap: 8px;
}

/* === اصلاحیه اصلی === */
.copy-container input[type="text"] {
  /* لغو width: 100% از استایل عمومی */
  width: 0; 
  /* اجازه می‌دهد input رشد کند */
  flex: 1 1 0; 
  
  direction: ltr; font-family: monospace; font-size: 14px;
  background: #ffffff; color: #0c5460;
  border: 1px solid #bde5f8; cursor: pointer;
}
.copy-container .copy-btn {
  /* لغو flex-grow: 1 از استایل عمومی */
  flex: 0 0 auto; 
  width: auto;
  
  font-weight: 500; font-size: 14px; padding: 8px 14px;
  background-color: var(--primary-color); color: white;
}
/* === پایان اصلاحیه === */

.copy-container .copy-btn:disabled {
  background-color: #a0a0a0;
}
</style>