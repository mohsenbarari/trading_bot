<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PublicProfile from '../components/PublicProfile.vue'
import { AppPage, AppPageHeader } from '../components/ui'
import { apiFetch } from '../utils/auth'

const route = useRoute()
const router = useRouter()

const jwtToken = computed(() => localStorage.getItem('auth_token'))
const apiBaseUrl = computed(() => import.meta.env.VITE_API_BASE_URL || '')

function getViewerIdFromToken(token: string | null): number | null {
  if (!token) return null

  try {
    const payloadPart = token.split('.')[1]
    if (!payloadPart) return null
    const base64 = payloadPart.replace(/-/g, '+').replace(/_/g, '/')
    const jsonPayload = decodeURIComponent(
      window.atob(base64).split('').map((char) => `%${(`00${char.charCodeAt(0).toString(16)}`).slice(-2)}`).join('')
    )
    const payload = JSON.parse(jsonPayload)
    const subject = Number(payload?.sub)
    return Number.isInteger(subject) && subject > 0 ? subject : null
  } catch {
    return null
  }
}

const viewerUserId = ref<number | null>(getViewerIdFromToken(jwtToken.value))

const profileUser = computed(() => {
  const rawId = route.params.id
  const id = Number(rawId)
  if (!Number.isInteger(id) || id <= 0) {
    return null
  }

  return {
    id,
    // The server is authoritative for the public profile name. Never use a
    // query-string value as a fallback, because it can retain stale PII.
    account_name: '',
  }
})

const profileViewKey = computed(() => String(profileUser.value?.id || 'invalid-profile'))
const hasUnsafePublicProfileQuery = computed(() => (
  profileUser.value !== null
  && Object.keys(route.query).length > 0
))

function canonicalizeUnsafePublicProfileQuery() {
  if (!hasUnsafePublicProfileQuery.value || !profileUser.value) return
  void router.replace({
    name: 'public-profile',
    params: { id: String(profileUser.value.id) },
  })
}

watch(hasUnsafePublicProfileQuery, (shouldCanonicalize) => {
  if (shouldCanonicalize) canonicalizeUnsafePublicProfileQuery()
}, { immediate: true })

function handleNavigate(
  view: string,
  payload?: {
    userId?: number
    userName?: string
    id?: number
    user_id?: number
  },
) {
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

  if (view === 'operations_customers') {
    router.push({ name: 'operations-customers' })
    return
  }

  if (view === 'operations_accountants') {
    router.push({ name: 'operations-accountants' })
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

  if (view === 'settings' && payload?.userId) {
    router.push({
      name: 'admin-user-profile',
      params: { id: String(payload.userId) },
    })
    return
  }

  const canGoBack = typeof window !== 'undefined' && Boolean(window.history.state?.back)
  if (canGoBack) {
    router.back()
    return
  }

  router.push('/')
}

onMounted(async () => {
  if (viewerUserId.value) {
    return
  }

  try {
    const response = await apiFetch('/api/auth/me')
    if (!response.ok) {
      return
    }

    const currentUser = await response.json()
    viewerUserId.value = Number.isInteger(Number(currentUser?.id)) ? Number(currentUser.id) : null
  } catch {
    viewerUserId.value = null
  }
})
</script>

<template>
  <AppPage>
    <div class="public-profile-view">
      <AppPageHeader
        title="مشاهده پروفایل"
      />
      <PublicProfile
        :key="profileViewKey"
        :user="profileUser"
        :viewerUserId="viewerUserId"
        :apiBaseUrl="apiBaseUrl"
        :jwtToken="jwtToken"
        @navigate="handleNavigate"
      />
    </div>
  </AppPage>
</template>

<style scoped>
.public-profile-view {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
  min-height: 100%;
}
</style>
