<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PublicProfile from '../components/PublicProfile.vue'
import { AppButton, AppErrorState, AppLoadingState, AppPage, AppPageHeader } from '../components/ui'
import { apiFetch } from '../utils/auth'
import { routeRequestJson } from '../utils/routeRequest'

const router = useRouter()
const route = useRoute()

const jwtToken = computed(() => localStorage.getItem('auth_token'))
const apiBaseUrl = computed(() => import.meta.env.VITE_API_BASE_URL || '')
const isSelfRoute = computed(() => route.name === 'profile')

const initialOwnerWorkspace = computed<'customers' | 'accountants' | null>(() => {
  const workspace = route.query.workspace
  return workspace === 'customers' || workspace === 'accountants' ? workspace : null
})

function getViewerIdFromToken(token: string | null): number | null {
  if (!token) return null
  try {
    const payloadPart = token.split('.')[1]
    if (!payloadPart) return null
    const base64 = payloadPart.replace(/-/g, '+').replace(/_/g, '/')
    const jsonPayload = decodeURIComponent(
      window.atob(base64).split('').map((char) => `%${(`00${char.charCodeAt(0).toString(16)}`).slice(-2)}`).join(''),
    )
    const payload = JSON.parse(jsonPayload)
    const subject = Number(payload?.sub)
    return Number.isInteger(subject) && subject > 0 ? subject : null
  } catch {
    return null
  }
}

const currentUser = ref<{ id: number; account_name: string } | null>(null)
const currentUserLoading = ref(false)
const currentUserError = ref('')
let currentUserRequestInFlight = false
const viewerUserId = ref<number | null>(getViewerIdFromToken(jwtToken.value))

const publicProfileUser = computed(() => {
  const rawId = route.params.id
  const id = Number(rawId)
  if (!Number.isInteger(id) || id <= 0) return null
  return {
    id,
    account_name: '',
  }
})

const profileUser = computed(() => (
  isSelfRoute.value ? currentUser.value : publicProfileUser.value
))

const invalidPublicProfile = computed(() => (
  !isSelfRoute.value && publicProfileUser.value === null
))

const hasUnsafePublicProfileQuery = computed(() => (
  !isSelfRoute.value
  && publicProfileUser.value !== null
  && Object.keys(route.query).length > 0
))

function canonicalizeUnsafePublicProfileQuery() {
  if (!hasUnsafePublicProfileQuery.value || !publicProfileUser.value) return
  void router.replace({
    name: 'public-profile',
    params: { id: String(publicProfileUser.value.id) },
  })
}

watch(hasUnsafePublicProfileQuery, (shouldCanonicalize) => {
  if (shouldCanonicalize) canonicalizeUnsafePublicProfileQuery()
}, { immediate: true })

watch(isSelfRoute, (self) => {
  if (self && !currentUser.value && !currentUserRequestInFlight) {
    void loadCurrentUser()
  }
})

function handleNavigate(
  view: string,
  payload?: {
    userId?: number
    userName?: string
    id?: number
    user_id?: number
  },
) {
  if (view === 'settings') {
    if (!isSelfRoute.value && payload?.userId) {
      router.push({
        name: 'admin-user-profile',
        params: { id: String(payload.userId) },
      })
      return
    }
    router.push({ name: 'account-storage' })
    return
  }

  if (view === 'operations_customers') {
    router.push({ name: 'operations-customers' })
    return
  }

  if (view === 'operations_accountants') {
    router.push({ name: 'operations-accountants' })
    return
  }

  if (view === 'chat' && payload?.userId) {
    router.push({
      name: 'messenger',
      query: {
        user_id: String(payload.userId),
        user_name: payload.userName || '',
      },
    })
    return
  }

  const profileId = Number(payload?.id ?? payload?.user_id)
  if ((view === 'public_profile' || view === 'profile') && Number.isInteger(profileId) && profileId > 0) {
    router.push({
      name: 'public-profile',
      params: { id: String(profileId) },
    })
    return
  }

  if (view === 'home') {
    if (isSelfRoute.value) {
      router.push({ name: 'account' })
      return
    }
    const canGoBack = typeof window !== 'undefined' && Boolean(window.history.state?.back)
    if (canGoBack) {
      router.back()
      return
    }
    router.push('/')
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
      account_name: data.account_name || data.full_name || 'کاربر',
    }
    viewerUserId.value = id
  } catch {
    if (isSelfRoute.value) {
      currentUserError.value = 'دریافت پروفایل ممکن نشد. لطفاً دوباره تلاش کنید.'
    }
  } finally {
    currentUserLoading.value = false
    currentUserRequestInFlight = false
  }
}

onMounted(async () => {
  if (isSelfRoute.value || !viewerUserId.value) {
    if (isSelfRoute.value) {
      await loadCurrentUser()
      return
    }
    try {
      const response = await apiFetch('/api/auth/me')
      if (!response.ok) return
      const data = await response.json()
      viewerUserId.value = Number.isInteger(Number(data?.id)) ? Number(data.id) : null
    } catch {
      viewerUserId.value = null
    }
  }
})
</script>

<template>
  <AppPage>
    <div
      class="profile-workspace-view"
      :class="isSelfRoute ? 'profile-view' : 'public-profile-view'"
      data-test="profile-workspace-root"
    >
      <AppPageHeader
        v-if="!isSelfRoute"
        eyebrow="پروفایل عمومی"
        title="مشاهده پروفایل"
        description="اطلاعات عمومی و راه‌های ارتباطی مجاز این کاربر را از این صفحه دنبال کنید."
      />
      <PublicProfile
        v-if="profileUser"
        :user="profileUser"
        :viewer-user-id="isSelfRoute ? profileUser.id : viewerUserId"
        :api-base-url="apiBaseUrl"
        :jwt-token="jwtToken"
        :initial-owner-workspace="isSelfRoute ? initialOwnerWorkspace : null"
        @navigate="handleNavigate"
      />
      <AppErrorState
        v-else-if="isSelfRoute && currentUserError"
        class="profile-load-error"
        title="پروفایل بارگذاری نشد"
        :message="currentUserError"
      >
        <template #actions>
          <AppButton type="button" class="profile-load-retry" @click="loadCurrentUser">تلاش دوباره</AppButton>
        </template>
      </AppErrorState>
      <AppErrorState
        v-else-if="invalidPublicProfile"
        class="profile-load-error"
        title="پروفایل معتبر نیست"
        message="اطلاعات کاربر نامعتبر است."
      >
        <template #actions>
          <AppButton type="button" @click="router.push('/')">بازگشت به خانه</AppButton>
        </template>
      </AppErrorState>
      <div v-else-if="isSelfRoute && currentUserLoading" class="loading-container">
        <AppLoadingState label="در حال دریافت پروفایل" />
      </div>
    </div>
  </AppPage>
</template>

<style scoped>
.profile-workspace-view,
.profile-view,
.public-profile-view {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
  min-height: 100%;
  min-width: 0;
}

.loading-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 16rem;
}
</style>
