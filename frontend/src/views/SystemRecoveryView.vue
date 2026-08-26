<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AuthFlowShell from '../components/auth/AuthFlowShell.vue'
import AppButton from '../components/ui/AppButton.vue'
import {
  resolveSystemRecoveryOutcome,
  SYSTEM_RECOVERY_OUTCOME,
  type SystemRecoveryOutcome,
} from '../router/systemRecovery'
import { forceLogout } from '../utils/auth'

const route = useRoute()
const isOwnedRecoveryPath = computed(() => {
  const pathMatch = route.params.pathMatch
  return (
    Array.isArray(pathMatch) &&
    pathMatch.length === 2 &&
    pathMatch[0] === '__system' &&
    pathMatch[1] === 'recovery'
  )
})
const outcome = computed(() =>
  isOwnedRecoveryPath.value
    ? resolveSystemRecoveryOutcome(route.query.outcome)
    : SYSTEM_RECOVERY_OUTCOME.NOT_FOUND,
)

const outcomeContent: Record<SystemRecoveryOutcome, { title: string; description: string }> = {
  [SYSTEM_RECOVERY_OUTCOME.NOT_FOUND]: {
    title: 'این صفحه پیدا نشد',
    description: 'نشانی را بررسی کنید یا به صفحه اصلی برگردید.',
  },
  [SYSTEM_RECOVERY_OUTCOME.FORBIDDEN]: {
    title: 'دسترسی به این بخش مجاز نیست',
    description: 'حساب فعلی اجازه مشاهده این بخش را ندارد.',
  },
  [SYSTEM_RECOVERY_OUTCOME.DEEP_LINK_FAILURE]: {
    title: 'باز کردن این صفحه ممکن نشد',
    description: 'به صفحه اصلی برگردید و دوباره مسیر را انتخاب کنید.',
  },
}

const content = computed(() => outcomeContent[outcome.value])
</script>

<template>
  <AuthFlowShell
    :title="content.title"
    :description="content.description"
    data-test="route-system-recovery"
    :data-outcome="outcome"
  >
    <section class="ui-v2-auth-login-step ui-v2-auth-login-step--status">
      <RouterLink class="ui-button ui-button--block" to="/"> بازگشت به صفحه اصلی </RouterLink>
      <AppButton
        v-if="outcome === SYSTEM_RECOVERY_OUTCOME.DEEP_LINK_FAILURE"
        block
        variant="secondary"
        data-test="restart-authentication"
        @click="forceLogout"
      >
        خروج از نشست و ورود دوباره
      </AppButton>
    </section>
  </AuthFlowShell>
</template>
