<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PublicProfile from '../components/PublicProfile.vue'
import { AppButton, AppErrorState, AppLoadingState, AppPage } from '../components/ui'
import { routeRequestJson } from '../utils/routeRequest'

const router = useRouter()
const route = useRoute()
const jwtToken = computed(() => localStorage.getItem('auth_token'))
const apiBaseUrl = computed(() => import.meta.env.VITE_API_BASE_URL || '')
const initialOwnerWorkspace = computed<'customers' | 'accountants' | null>(() => {
  const workspace = route.query.workspace
  return workspace === 'customers' || workspace === 'accountants' ? workspace : null
})

const currentUser = ref<{ id: number; account_name: string } | null>(null)
const currentUserLoading = ref(true)
const currentUserError = ref('')
let currentUserRequestInFlight = false

function handleNavigate(view: string, payload?: any) {
  if (view === 'settings') {
    router.push({ name: 'account-storage' })
  } else if (view === 'operations_customers') {
    router.push({ name: 'operations-customers' })
  } else if (view === 'operations_accountants') {
    router.push({ name: 'operations-accountants' })
  } else if (view === 'chat' && payload?.userId) {
    router.push({
      name: 'messenger',
      query: { user_id: String(payload.userId), user_name: payload.userName || '' }
    })
  } else if ((view === 'public_profile' || view === 'profile') && Number.isInteger(Number(payload?.id ?? payload?.user_id))) {
    const profileId = Number(payload.id ?? payload.user_id)
    router.push({
      name: 'public-profile',
      params: { id: String(profileId) },
    })
  } else if (view === 'home') {
    router.push({ name: 'account' })
  }
}

async function loadCurrentUser() {
  if (currentUserRequestInFlight) return
  currentUserRequestInFlight = true
  currentUserLoading.value = true
  currentUserError.value = ''
  try {
    const data = await routeRequestJson<any>('/api/auth/me', {
      errorContext: {
        surface: 'public-profile',
        scope: 'page',
        operation: 'initial-load',
        fallbackMessage: 'دریافت پروفایل ممکن نشد.',
      },
    })
    const id = Number(data?.id)
    if (!Number.isInteger(id) || id <= 0) {
      throw new Error('current_user_payload_invalid')
    }

    currentUser.value = {
      id,
      account_name: data.account_name || data.full_name || 'کاربر'
    }
  } catch {
    currentUserError.value = 'دریافت پروفایل ممکن نشد. لطفاً دوباره تلاش کنید.'
  } finally {
    currentUserLoading.value = false
    currentUserRequestInFlight = false
  }
}

onMounted(loadCurrentUser)
</script>

<template>
  <AppPage>
    <div class="profile-view">
      <PublicProfile
        v-if="currentUser"
        :key="currentUser.id"
        :user="currentUser"
        :viewerUserId="currentUser.id"
        :apiBaseUrl="apiBaseUrl"
        :jwtToken="jwtToken"
        :initialOwnerWorkspace="initialOwnerWorkspace"
        @navigate="handleNavigate"
      />
      <AppErrorState
        v-else-if="currentUserError"
        class="profile-load-error"
        title="پروفایل بارگذاری نشد"
        :message="currentUserError"
      >
        <template #actions>
          <AppButton type="button" class="profile-load-retry" @click="loadCurrentUser">تلاش دوباره</AppButton>
        </template>
      </AppErrorState>
      <div v-else-if="currentUserLoading" class="loading-container">
        <AppLoadingState label="در حال دریافت پروفایل" />
      </div>
    </div>
  </AppPage>
</template>

<style scoped>
.profile-view {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
  min-height: 100%;
}
.loading-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 16rem;
}
</style>
