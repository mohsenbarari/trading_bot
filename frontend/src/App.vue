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
import { ref, onMounted, reactive } from 'vue' // reactive را اضافه کنید

const API_BASE_URL = 'https://telegram.362514.ir'
const tg = (window as any).Telegram?.WebApp
const jwtToken = ref<string | null>(null)
const user = ref<any>(null)
const loading = ref(true)
const showAdmin = ref(false)
const inviteResult = ref('')

// از reactive برای آبجکت‌ها استفاده می‌کنیم که خواناتر است
const invite = reactive({
  name: '',
  phone: '',
  role: 'عادی'
})

function resetForm() {
  invite.name = ''
  invite.phone = ''
  invite.role = 'عادی'
  inviteResult.value = ''
}

async function createInvite() {
  if (!/^09[0-9]{9}$/.test(invite.phone)) {
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
      // --- بخش اصلاح شده ---
      body: JSON.stringify({
        account_name: invite.name,      // <-- اصلاح شد
        mobile_number: invite.phone,    // <-- اصلاح شد
        role: invite.role,
      }),
    })

    const data = await resp.json(); // همیشه پاسخ را بخوانید
    if (!resp.ok) {
        // از پیام خطای سرور استفاده کنید
        throw new Error(data.detail || 'خطا در ایجاد دعوت‌نامه');
    }

    // دریافت نام کاربری ربات برای ساخت لینک
    const configResp = await fetch(`/api/config`);
    const config = await configResp.json();

    const inviteLink = `https://t.me/${config.bot_username}?start=${data.token}`;
    inviteResult.value = `✅ لینک دعوت ایجاد شد:<br><a href="${inviteLink}" target="_blank">${inviteLink}</a>`
  } catch (e: any) {
    inviteResult.value = `❌ ${e.message}`
  }
}

function showAdminIfAllowed(role: string) {
  showAdmin.value = role && role !== 'تماشا'
}

onMounted(async () => {
    // اعمال تم روشن
    setTimeout(() => {
        const root = document.documentElement;
        const applyLightTheme = () => {
            root.style.setProperty('--tg-theme-bg-color', '#ffffff', 'important');
            root.style.setProperty('--tg-theme-text-color', '#111827', 'important');
            document.body.style.backgroundColor = '#ffffff';
            document.body.style.color = '#111827';
        };
        applyLightTheme();
        if (tg && tg.onEvent) tg.onEvent('themeChanged', applyLightTheme);
    }, 100);

    if (tg) {
        try { tg.ready(); tg.expand(); } catch (e) {}
    }

    try {
        if (!tg || !tg.initData) {
            throw new Error("لطفاً این صفحه را از داخل تلگرام باز کنید.");
        }
        
        const loginResp = await fetch(`${API_BASE_URL}/api/auth/webapp-login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ init_data: tg.initData }),
        })
        if (!loginResp.ok) throw new Error("خطا در احراز هویت اولیه.");
        const loginJson = await loginResp.json()
        jwtToken.value = loginJson.access_token

        const userResp = await fetch(`${API_BASE_URL}/api/auth/me`, {
            headers: { Authorization: `Bearer ${jwtToken.value}` },
        })
        if (!userResp.ok) throw new Error("خطا در دریافت اطلاعات کاربر.");
        user.value = await userResp.json()
        showAdminIfAllowed(user.value.role)
    } catch (e: any) {
        user.value = { full_name: e.message, role: 'خطا' };
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
