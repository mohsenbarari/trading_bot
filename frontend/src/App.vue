<template>
  <div class="container">
    <h1 class="text-center">پنل کاربری</h1>

    <div class="card" id="userInfo">
      <p v-if="loading">در حال بارگذاری اطلاعات کاربر...</p>
      <template v-else>
        <p>👋 خوش آمدید <strong>{{ user?.full_name || 'کاربر' }}</strong></p>
        <p class="meta">نقش شما: <strong>{{ user?.role || '—' }}</strong></p>
      </template>
    </div>

    <div class="grid">
      <div class="card">
        <h2 style="margin-top:0">عملیات</h2>
        <p class="hint">اینجا می‌توانید با نقش مناسب لینک دعوت ایجاد کنید.</p>
      </div>

      <div class="card" v-if="showAdmin">
        <h2 style="margin-top:0">ایجاد لینک دعوت</h2>
        <form @submit.prevent="createInvite" autocomplete="off">
          <div class="form-group">
            <label for="inviteeName">نام و نام خانوادگی</label>
            <input id="inviteeName" v-model="invite.name" type="text" placeholder="مثلاً علی رضایی" required />
          </div>

          <div class="form-group">
            <label for="inviteePhone">شماره موبایل (ایران)</label>
            <input
              id="inviteePhone"
              v-model="invite.phone"
              type="tel"
              placeholder="مثلاً 09123456789"
              pattern="^09[0-9]{9}$"
              required
            />
            <div class="meta" style="margin-top:6px">فرمت صحیح: <code>09xxxxxxxxx</code></div>
          </div>

          <div class="form-group">
            <label for="inviteeRole">نقش</label>
            <select id="inviteeRole" v-model="invite.role">
              <option value="تماشا">تماشا</option>
              <option value="عادی">عادی</option>
              <option value="پلیس">پلیس</option>
              <option value="مدیر میانی">مدیر میانی</option>
            </select>
          </div>

          <div style="display:flex;gap:10px">
            <button type="submit">ایجاد لینک دعوت</button>
            <button type="button" class="secondary" @click="resetForm">بازنشانی</button>
          </div>
        </form>

        <div v-if="inviteResult" class="success-box" v-html="inviteResult"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

const API_BASE_URL = 'https://telegram.362514.ir'
const tg = (window as any).Telegram?.WebApp
const jwtToken = ref<string | null>(null)
const user = ref<any>(null)
const loading = ref(true)
const showAdmin = ref(false)
const inviteResult = ref('')
const invite = ref({ name: '', phone: '', role: 'عادی' })

function resetForm() {
  invite.value = { name: '', phone: '', role: 'عادی' }
  inviteResult.value = ''
}

async function createInvite() {
  if (!/^09[0-9]{9}$/.test(invite.value.phone)) {
    inviteResult.value = 'شماره موبایل نامعتبر است. فرمت: 09xxxxxxxxx'
    return
  }
  inviteResult.value = 'در حال ارسال...'
  try {
    const resp = await fetch(`${API_BASE_URL}/api/invitations/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${jwtToken.value}`,
      },
      body: JSON.stringify({
        invitee_name: invite.value.name,
        phone_number: invite.value.phone,
        role: invite.value.role,
      }),
    })
    if (!resp.ok) throw new Error('خطا در ایجاد دعوت‌نامه')
    const data = await resp.json()
    inviteResult.value = `✅ لینک دعوت ایجاد شد:<br><a href="${data.invite_link}" target="_blank">${data.invite_link}</a>`
  } catch (e: any) {
    inviteResult.value = `❌ ${e.message}`
  }
}

function showAdminIfAllowed(role: string) {
  showAdmin.value = role && role !== 'تماشا'
}

onMounted(async () => {
  if (tg) {
    try { tg.ready(); tg.expand(); } catch (e) {}
  }
  try {
    const loginResp = await fetch(`${API_BASE_URL}/api/auth/webapp-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: tg?.initData }),
    })
    const loginJson = await loginResp.json()
    jwtToken.value = loginJson.access_token

    const userResp = await fetch(`${API_BASE_URL}/api/auth/me`, {
      headers: { Authorization: `Bearer ${jwtToken.value}` },
    })
    user.value = await userResp.json()
    showAdminIfAllowed(user.value.role)
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700&display=swap');

:root {
  --bg: #ffffff;
  --text: #111827;
  --card: #f9fafb;
  --muted: #6b7280;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --border: #e5e7eb;
  --radius: 12px;
  --shadow: 0 4px 12px rgba(0,0,0,0.06);
}

body {
  font-family: 'Vazirmatn', system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  margin: 0;
  padding: 20px;
}

.container { max-width:760px; margin: 0 auto; }

.text-center { text-align:center; }
.card {
  background: var(--card);
  border-radius: var(--radius);
  padding: 18px;
  box-shadow: var(--shadow);
  border: 1px solid var(--border);
}
.grid { display:grid; grid-template-columns: 1fr 320px; gap:18px; align-items:start; }
@media (max-width:880px){ .grid{grid-template-columns:1fr} }

.form-group { margin-bottom:12px; }
label { display:block; margin-bottom:6px; font-weight:600; }
input, select, button {
  width:100%; padding:10px 12px; border-radius:10px; border:1px solid var(--border);
  background:#fff; font-size:14px; font-family: inherit;
}
button { background:var(--accent); color:#fff; font-weight:700; border:none; cursor:pointer; }
button.secondary { background:transparent; color:var(--accent); border:1px solid rgba(37,99,235,0.12); }

.meta { color:var(--muted); font-size:13px; margin-top:8px; }
.success-box { margin-top:10px; padding:10px; background:#ecfeff; border:1px solid #c8f7f5; color:#065f46; border-radius:8px; }
</style>
