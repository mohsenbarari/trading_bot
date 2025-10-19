<template>
  <form @submit.prevent="submitForm">
    <div class="form-group">
      <label for="inviteeName">نام و نام خانوادگی</label>
      <input id="inviteeName" v-model="name" type="text" required placeholder="مثلاً علی رضایی" />
    </div>

    <div class="form-group">
      <label for="inviteePhone">شماره موبایل (ایران)</label>
      <input id="inviteePhone" v-model="phone" type="tel" pattern="^09[0-9]{9}$" placeholder="مثلاً 09123456789" required />
      <div class="meta">فرمت صحیح: 09xxxxxxxxx</div>
    </div>

    <div class="form-group">
      <label for="inviteeRole">نقش</label>
      <select id="inviteeRole" v-model="role">
        <option value="تماشا">تماشا</option>
        <option value="عادی">عادی</option>
        <option value="پلیس">پلیس</option>
        <option value="مدیر میانی">مدیر میانی</option>
      </select>
    </div>

    <div style="display:flex; gap:10px;">
      <button type="submit">ایجاد لینک دعوت</button>
      <button type="button" class="secondary" @click="resetForm">بازنشانی</button>
    </div>

    <div v-if="result" class="result">{{ result }}</div>
  </form>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits(['invite-created'])
const API_BASE_URL = 'https://telegram.362514.ir'
const name = ref('')
const phone = ref('')
const role = ref('عادی')
const result = ref('')
const tg = (window as any).Telegram?.WebApp

async function submitForm() {
  result.value = 'در حال ارسال...'

  try {
    const tokenResp = await fetch(`${API_BASE_URL}/api/auth/webapp-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: tg?.initData })
    })
    const tokenData = await tokenResp.json()
    const jwt = tokenData.access_token

    const resp = await fetch(`${API_BASE_URL}/api/invitations/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${jwt}`
      },
      body: JSON.stringify({ invitee_name: name.value, phone_number: phone.value, role: role.value })
    })

    if (!resp.ok) throw new Error('خطا در ایجاد دعوت‌نامه')
    const data = await resp.json()
    result.value = `✅ لینک دعوت: ${data.invite_link}`
    emit('invite-created', data.invite_link)
  } catch (err: any) {
    result.value = '❌ ' + err.message
  }
}

function resetForm() {
  name.value = ''
  phone.value = ''
  role.value = 'عادی'
  result.value = ''
}
</script>
