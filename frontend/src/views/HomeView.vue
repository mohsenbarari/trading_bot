<template>
  <div>
    <h1 class="text-center">پنل کاربری</h1>

    <UserInfo :user="user" />

    <div class="grid">
      <div class="card">
        <h2 style="margin-top:0">عملیات</h2>
        <p class="hint">اینجا می‌توانید با نقش مناسب لینک دعوت ایجاد کنید.</p>
      </div>

      <AdminPanel v-if="showAdminPanel" @invite-created="handleInvite" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import UserInfo from '../components/UserInfo.vue'
import AdminPanel from '../components/AdminPanel.vue'

const API_BASE_URL = 'https://telegram.362514.ir'
const tg = (window as any).Telegram?.WebApp
const user = ref<any>(null)
const showAdminPanel = ref(false)
const jwtToken = ref<string | null>(null)

function handleInvite(link: string) {
  alert('لینک دعوت ایجاد شد:\n' + link)
}

onMounted(async () => {
  if (tg) { try { tg.ready(); tg.expand(); } catch(e){} }

  // احراز هویت
  const resp = await fetch(`${API_BASE_URL}/api/auth/webapp-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ init_data: tg?.initData })
  })

  if (resp.ok) {
    const data = await resp.json()
    jwtToken.value = data.access_token

    const meResp = await fetch(`${API_BASE_URL}/api/auth/me`, {
      headers: { Authorization: `Bearer ${jwtToken.value}` }
    })
    user.value = await meResp.json()

    if (user.value.role && user.value.role !== 'تماشا') {
      showAdminPanel.value = true
    }
  } else {
    user.value = { full_name: 'خطا در ورود', role: '' }
  }
})
</script>

