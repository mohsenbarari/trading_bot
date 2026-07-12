<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Globe2, Send } from 'lucide-vue-next'
import { AppButton, AppCard, AppErrorState, AppLoadingState, AppPage, AppPageHeader, AppStatusBadge } from '../components/ui'
import { invitationTerminalMessage, normalizeInvitationContract } from '../utils/invitationContract'
import { formatIranDateTime } from '../utils/iranTime'

const route = useRoute()
const router = useRouter()
const shortCode = route.params.code as string

const loading = ref(true)
const redirecting = ref(false)
const error = ref('')
const token = ref('')
const botLink = ref('')
const botAvailable = ref(false)
const webAvailable = ref(false)
const expiresAt = ref('')
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || ''

onMounted(async () => {
  try {
    const res = await fetch(`${apiBaseUrl}/api/invitations/lookup/${shortCode}`)
    if (!res.ok) throw new Error('دعوت‌نامه نامعتبر یا منقضی شده است.')

    const data = await res.json()
    const contract = normalizeInvitationContract(data)
    if (contract.state === 'completed') {
      redirecting.value = true
      await router.replace({ name: 'login', query: { registration: 'complete' } })
      return
    }
    if (contract.state !== 'pending' || data.valid === false) {
      throw new Error(invitationTerminalMessage(contract.state))
    }
    if (!contract.token) throw new Error('دعوت‌نامه نامعتبر یا منقضی شده است.')

    token.value = contract.token
    botAvailable.value = contract.botAvailable
    webAvailable.value = contract.webAvailable
    expiresAt.value = contract.expiresAt

    if (botAvailable.value) {
      try {
        const configRes = await fetch(`${apiBaseUrl}/api/config`)
        if (!configRes.ok) throw new Error('bot_config_unavailable')
        const config = await configRes.json()
        if (!config.bot_username) throw new Error('bot_config_unavailable')
        botLink.value = `https://t.me/${config.bot_username}?start=${token.value}`
      } catch {
        botAvailable.value = false
      }
    }
  } catch (e: any) {
    redirecting.value = false
    error.value = e.message
  } finally {
    loading.value = false
  }
})

function goToWebRegister() {
  router.push(`/register?token=${token.value}`)
}
</script>

<template>
  <AppPage narrow>
    <div class="invite-landing-view">
      <AppPageHeader
        eyebrow="دعوت‌نامه"
        title="دعوت‌نامه اختصاصی"
        description="برای ورود به سامانه، یکی از مسیرهای ثبت‌نام تلگرام یا وب را انتخاب کنید."
      />

      <AppCard class="invite-card">
        <AppLoadingState v-if="loading || redirecting" :label="redirecting ? 'در حال انتقال به ورود' : 'در حال بررسی دعوت‌نامه'" />

        <AppErrorState v-else-if="error" title="دعوت‌نامه قابل استفاده نیست" :message="error" />

        <div v-else class="actions">
          <div class="invite-intro">
            <AppStatusBadge tone="success">دعوت‌نامه معتبر</AppStatusBadge>
            <p class="welcome-text">شما به سامانه معاملاتی دعوت شده‌اید.</p>
            <p class="instruction">یکی از مسیرهای فعال ثبت‌نام را انتخاب کنید.</p>
            <p v-if="expiresAt" class="invitation-expiry">مهلت ثبت‌نام: {{ formatIranDateTime(expiresAt) }}</p>
          </div>

          <a v-if="botAvailable && botLink" :href="botLink" class="invite-action telegram-btn">
            <span class="invite-action-icon" aria-hidden="true">
              <Send :size="18" />
            </span>
            <span class="invite-action-copy">
              <strong>ثبت‌نام با تلگرام</strong>
              <small>ورود مستقیم به ربات</small>
            </span>
          </a>

          <AppButton v-if="webAvailable" variant="secondary" block @click="goToWebRegister">
            <template #icon>
              <Globe2 :size="18" />
            </template>
            ثبت‌نام از طریق وب
          </AppButton>
        </div>
      </AppCard>
    </div>
  </AppPage>
</template>

<style scoped>
.invite-landing-view {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
  min-height: 100%;
}

.invite-card {
  text-align: right;
}

.actions {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.invite-intro {
  display: grid;
  gap: 0.55rem;
}

.welcome-text {
  margin: 0;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 900;
}

.instruction {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.invitation-expiry {
  margin: 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}

.invite-action {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  min-height: var(--ds-touch-target);
  padding: 0.95rem 1rem;
  border-radius: var(--ds-radius-lg);
  text-decoration: none;
  transition: transform 0.12s ease, box-shadow 0.18s ease;
}

.invite-action:active {
  transform: scale(0.985);
}

.invite-action-icon {
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 0.85rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.16);
  flex: 0 0 auto;
}

.invite-action-copy {
  min-width: 0;
  display: grid;
  gap: 0.15rem;
}

.invite-action-copy strong {
  font-size: var(--ds-font-sm);
  font-weight: 900;
}

.invite-action-copy small {
  font-size: var(--ds-font-xs);
  line-height: 1.6;
  opacity: 0.9;
}

.telegram-btn {
  background: linear-gradient(135deg, var(--ds-info-500), var(--ds-telegram-700));
  color: white;
  box-shadow: 0 14px 30px var(--ds-telegram-shadow);
}

.telegram-btn:focus-visible {
  outline: 3px solid var(--ds-telegram-focus);
  outline-offset: 2px;
}
</style>
