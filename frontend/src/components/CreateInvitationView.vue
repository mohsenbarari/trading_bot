<script setup lang="ts">
import { reactive, ref } from 'vue';

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>();

const emit = defineEmits(['invite-created']);

const invite = reactive({
  account_name: '',
  mobile_number: '',
  role: 'عادی',
});

const resultMessage = ref('');
const isLoading = ref(false);

function resetForm() {
  invite.account_name = '';
  invite.mobile_number = '';
  invite.role = 'عادی';
  resultMessage.value = '';
}

async function createInvite() {
  if (!props.jwtToken) {
    resultMessage.value = '❌ خطا: شما احراز هویت نشده‌اید.';
    return;
  }
  if (!/^09[0-9]{9}$/.test(invite.mobile_number)) {
    resultMessage.value = '❌ شماره موبایل نامعتبر است. فرمت: 09xxxxxxxxx';
    return;
  }
  
  isLoading.value = true;
  resultMessage.value = 'در حال ارسال...';

  try {
    const resp = await fetch(`${props.apiBaseUrl}/api/invitations/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${props.jwtToken}`,
      },
      body: JSON.stringify(invite),
    });
    
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || 'خطا در ایجاد دعوت‌نامه');
    }

    const configResp = await fetch(`${props.apiBaseUrl}/api/config`);
    const config = await configResp.json();

    const inviteLink = `https://t.me/${config.bot_username}?start=${data.token}`;
    resultMessage.value = `✅ لینک دعوت ایجاد شد:<br><a href="${inviteLink}" target="_blank" rel="noopener noreferrer">${inviteLink}</a>`;
    emit('invite-created', resultMessage.value);
    
  } catch (e: any) {
    resultMessage.value = `❌ ${e.message}`;
  } finally {
    isLoading.value = false;
  }
}
</script>

<template>
  <div class="card">
    <h2>ایجاد لینک دعوت جدید</h2>
    <form @submit.prevent="createInvite" autocomplete="off">
      <div class="form-group">
        <label for="account_name">نام کاربری (Account Name)</label>
        <input v-model="invite.account_name" id="account_name" type="text" placeholder="مثلاً alireza" required />
      </div>

      <div class="form-group">
        <label for="mobile_number">شماره موبایل (ایران)</label>
        <input v-model="invite.mobile_number" id="mobile_number" type="tel" placeholder="09123456789" pattern="^09[0-9]{9}$" required />
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
          {{ isLoading ? 'در حال ساخت...' : 'ایجاد لینک دعوت' }}
        </button>
        <button type="button" class="secondary" @click="resetForm" :disabled="isLoading">بازنشانی</button>
      </div>
    </form>

    <div v-if="resultMessage" class="result-box" v-html="resultMessage"></div>
  </div>
</template>

<style scoped>
.card {
  background-color: var(--card-bg);
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}
h2 {
  margin-top: 0; margin-bottom: 24px; font-size: 20px;
}
.form-group { margin-bottom: 16px; }
label { display: block; margin-bottom: 8px; font-weight: 500; font-size: 14px; }
input, select {
  width: 100%; padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--border-color); background: #f7f7f7;
  font-size: 15px; font-family: inherit;
  transition: all 0.2s ease;
}
input:focus, select:focus {
  outline: none; border-color: var(--primary-color);
  background: white; box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.1);
}
.form-actions { display: flex; gap: 12px; margin-top: 24px; }
button {
  flex-grow: 1; background: var(--primary-color); color: white; border: none;
  cursor: pointer; font-weight: 600; transition: background-color 0.2s ease;
  padding: 12px; border-radius: 8px; font-size: 15px;
}
button:hover { background-color: #0056b3; }
button:disabled { background-color: #a0a0a0; cursor: not-allowed; }
button.secondary {
  background: transparent; color: var(--primary-color);
  border: 1px solid var(--primary-color);
}
.result-box {
  margin-top: 20px; padding: 12px; border-radius: 8px;
  background: #f0f9ff; color: #0c5460;
  border: 1px solid #bde5f8;
  font-size: 14px; word-break: break-all;
}
.result-box :deep(a) {
    color: var(--primary-color);
    font-weight: 500;
}
</style>

