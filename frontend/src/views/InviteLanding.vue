<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Globe2, Send } from 'lucide-vue-next'
import { AppButton, AppErrorState, AppLoadingState, AuthFlowShell } from '../components/ui'
import { openTelegramLink } from '../services/telegramLink'
import {
  invitationTerminalMessage,
  normalizeInvitationContract,
  type InvitationContractPayload,
} from '../utils/invitationContract'
import { isAppHttpError } from '../utils/httpErrorPolicy'
import { formatIranDateTime } from '../utils/iranTime'
import { assertSuccessfulNavigation } from '../utils/navigationResult'
import { routeRequestJson } from '../utils/routeRequest'
import {
  clearRegistrationExchangeId,
  getOrCreateRegistrationExchangeId,
} from '../utils/registrationHandoff'

const route = useRoute()
const router = useRouter()
const shortCode = route.params.code as string

const loading = ref(true)
const redirecting = ref(false)
const error = ref('')
const retryAvailable = ref(false)
const token = ref('')
const botUsername = ref('')
const botAvailable = ref(false)
const webAvailable = ref(false)
const expiresAt = ref('')
const outcomeContainer = ref<HTMLElement | null>(null)
let invitationLookupPending = false
let webExchangePending = false

const inviteTitle = computed(() => {
  if (!loading.value && !error.value && webAvailable.value && !botAvailable.value) {
    return 'ثبت‌نام در وب‌اپ'
  }
  return 'دعوت‌نامه اختصاصی'
})

const inviteDescription = computed(() => {
  if (loading.value || redirecting.value) return 'در حال بررسی…'
  if (error.value) return ''
  if (webAvailable.value && !botAvailable.value) {
    return 'شماره و اطلاعات حساب را تکمیل کنید.'
  }
  return 'تا پایان مهلت، روش ثبت‌نام را انتخاب کنید.'
})

class TerminalInvitationError extends Error {}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isTerminalLookupError(cause: unknown) {
  return (
    isAppHttpError(cause) &&
    cause.status !== null &&
    [400, 401, 403, 404, 409, 410, 422].includes(cause.status)
  )
}

async function loadInvitation() {
  if (invitationLookupPending) return
  invitationLookupPending = true
  loading.value = true
  redirecting.value = false
  error.value = ''
  retryAvailable.value = false
  token.value = ''
  botUsername.value = ''
  botAvailable.value = false
  webAvailable.value = false
  expiresAt.value = ''

  try {
    const data = await routeRequestJson<unknown>(
      `/api/invitations/lookup/${encodeURIComponent(shortCode)}`,
      {
        mode: 'public',
        errorContext: {
          surface: 'auth',
          scope: 'page',
          operation: 'initial-load',
          resourceLabel: 'دعوت‌نامه',
        },
      },
    )
    if (!isObject(data)) {
      throw new Error('پاسخ بررسی دعوت‌نامه کامل نیست.')
    }
    const contract = normalizeInvitationContract(data as InvitationContractPayload)
    if (contract.state === 'completed') {
      redirecting.value = true
      assertSuccessfulNavigation(
        await router.replace({ name: 'login', query: { registration: 'complete' } }),
      )
      clearRegistrationExchangeId()
      return
    }
    if (contract.state !== 'pending' || data.valid === false) {
      throw new TerminalInvitationError(invitationTerminalMessage(contract.state))
    }
    if (!contract.token) throw new TerminalInvitationError('دعوت‌نامه نامعتبر یا منقضی شده است.')

    token.value = contract.token
    botAvailable.value = contract.botAvailable
    webAvailable.value = contract.webAvailable
    expiresAt.value = contract.expiresAt

    if (botAvailable.value) {
      try {
        const config = await routeRequestJson<unknown>('/api/config', {
          mode: 'public',
          errorContext: {
            surface: 'auth',
            scope: 'action',
            operation: 'initial-load',
          },
        })
        if (
          !isObject(config) ||
          typeof config.bot_username !== 'string' ||
          !config.bot_username.trim()
        ) {
          throw new Error('bot_config_unavailable')
        }
        botUsername.value = config.bot_username.trim().replace(/^@/, '')
      } catch {
        botAvailable.value = false
        if (!webAvailable.value) {
          throw new Error('مسیر ثبت‌نام تلگرام اکنون در دسترس نیست.')
        }
      }
    }
  } catch (cause: unknown) {
    redirecting.value = false
    if (cause instanceof TerminalInvitationError || isTerminalLookupError(cause)) {
      clearRegistrationExchangeId()
      error.value =
        cause instanceof TerminalInvitationError
          ? cause.message
          : 'دعوت‌نامه نامعتبر یا منقضی شده است.'
      retryAvailable.value = false
    } else {
      error.value = 'بررسی دعوت‌نامه اکنون ممکن نشد. دوباره تلاش کنید.'
      retryAvailable.value = true
    }
  } finally {
    invitationLookupPending = false
    loading.value = false
  }
}

onMounted(() => {
  void loadInvitation()
})

watch(
  () => {
    if (loading.value || redirecting.value) return 'pending'
    return error.value ? 'error' : 'ready'
  },
  async (outcome) => {
    if (outcome === 'pending') return
    await nextTick()
    outcomeContainer.value?.focus()
  },
)

async function goToWebRegister() {
  if (webExchangePending) return
  const invitationToken = token.value
  const exchangeId = getOrCreateRegistrationExchangeId()
  if (!invitationToken || !exchangeId) {
    error.value = 'ادامه ثبت‌نام در این مرورگر ممکن نشد. دوباره تلاش کنید.'
    retryAvailable.value = false
    return
  }
  webExchangePending = true
  loading.value = true
  error.value = ''
  try {
    const context = await routeRequestJson<unknown>('/api/auth/registration-context/exchange', {
      mode: 'public',
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind: 'invitation',
        token: invitationToken,
        exchange_id: exchangeId,
      }),
      errorContext: { surface: 'auth', scope: 'action', operation: 'submit' },
    })
    if (isObject(context) && context.status === 'registration_complete') {
      redirecting.value = true
      assertSuccessfulNavigation(
        await router.replace({ name: 'login', query: { registration: 'complete' } }),
      )
      token.value = ''
      clearRegistrationExchangeId()
      try {
        sessionStorage.removeItem('web_registration_progress_v1')
      } catch {
        // Non-sensitive flow progress is best-effort only.
      }
      try {
        await routeRequestJson<unknown>('/api/auth/registration-context/clear', {
          mode: 'public',
          method: 'POST',
          credentials: 'same-origin',
          errorContext: { surface: 'auth', scope: 'action', operation: 'submit' },
        })
      } catch {
        // The bounded server marker remains safe if acknowledgement is lost.
      }
      return
    }
    if (!isObject(context) || context.kind !== 'invitation') {
      throw new Error('پاسخ ادامه ثبت‌نام کامل نیست.')
    }
    redirecting.value = true
    assertSuccessfulNavigation(await router.push({ name: 'web-register' }))
    token.value = ''
    clearRegistrationExchangeId()
    try {
      sessionStorage.removeItem('web_registration_progress_v1')
    } catch {
      // Non-sensitive flow progress is best-effort only.
    }
  } catch (cause: unknown) {
    token.value = ''
    redirecting.value = false
    if (isTerminalLookupError(cause)) {
      clearRegistrationExchangeId()
      error.value = 'دعوت‌نامه نامعتبر یا منقضی شده است.'
      retryAvailable.value = false
    } else {
      error.value = 'ادامه ثبت‌نام اکنون ممکن نشد. دوباره تلاش کنید.'
      retryAvailable.value = true
    }
  } finally {
    webExchangePending = false
    loading.value = false
  }
}

function goToTelegramRegister() {
  const username = botUsername.value
  const invitationToken = token.value
  if (!username || !invitationToken) return

  const destination = new URL(`https://t.me/${encodeURIComponent(username)}`)
  destination.searchParams.set('start', invitationToken)
  openTelegramLink(destination.toString())
}
</script>

<template>
  <AuthFlowShell fill-viewport :title="inviteTitle" :description="inviteDescription">
    <AppLoadingState
      v-if="loading || redirecting"
      :label="redirecting ? 'در حال انتقال به ورود' : 'در حال بررسی دعوت‌نامه'"
    />

    <div v-else-if="error" ref="outcomeContainer" data-invite-outcome tabindex="-1">
      <AppErrorState
        :title="retryAvailable ? 'بررسی دعوت‌نامه انجام نشد' : 'دعوت‌نامه قابل استفاده نیست'"
        :message="error"
      >
        <template v-if="retryAvailable" #actions>
          <AppButton block :loading="loading" @click="loadInvitation">تلاش مجدد</AppButton>
        </template>
      </AppErrorState>
    </div>

    <section
      v-else
      ref="outcomeContainer"
      class="ui-v2-auth-invite-actions"
      data-invite-outcome
      tabindex="-1"
      aria-live="polite"
    >
      <div class="ui-v2-auth-invite-valid" role="status">
        <strong>دعوت‌نامه معتبر است</strong>
        <span v-if="expiresAt">تا {{ formatIranDateTime(expiresAt) }}</span>
      </div>

      <div class="ui-v2-auth-invite-routes">
        <button
          v-if="botAvailable && botUsername"
          type="button"
          class="ui-v2-auth-invite-route ui-v2-auth-invite-route--telegram"
          aria-label="ثبت‌نام با تلگرام"
          @click="goToTelegramRegister"
        >
          <span class="ui-v2-auth-invite-route__icon" aria-hidden="true">
            <Send :size="20" />
          </span>
          <span class="ui-v2-auth-invite-route__copy">
            <strong>ثبت‌نام با تلگرام</strong>
          </span>
        </button>

        <button
          v-if="webAvailable"
          type="button"
          class="ui-v2-auth-invite-route"
          :class="{ 'ui-v2-auth-invite-route--only': !botAvailable }"
          :aria-label="botAvailable ? 'ثبت‌نام از طریق وب' : 'ادامه ثبت‌نام در وب‌اپ'"
          @click="goToWebRegister"
        >
          <span class="ui-v2-auth-invite-route__icon" aria-hidden="true">
            <Globe2 :size="20" />
          </span>
          <span class="ui-v2-auth-invite-route__copy">
            <strong>{{ botAvailable ? 'ثبت‌نام از طریق وب' : 'ادامه ثبت‌نام' }}</strong>
          </span>
        </button>
      </div>

      <p class="ui-v2-auth-invite-privacy">
        این لینک اختصاصی است؛ آن را با دیگران به اشتراک نگذارید.
      </p>
    </section>
  </AuthFlowShell>
</template>
