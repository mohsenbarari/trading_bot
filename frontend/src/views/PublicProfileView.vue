<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PublicProfile from '../components/PublicProfile.vue'
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

function getQueryString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function getQueryPositiveInt(value: unknown): number | null {
  const normalized = Number(value)
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null
}

const profileUser = computed(() => {
  const rawId = route.params.id
  const id = Number(rawId)
  if (!Number.isInteger(id) || id <= 0) {
    return null
  }

  const accountName = typeof route.query.account_name === 'string' ? route.query.account_name : ''
  return {
    id,
    account_name: accountName,
  }
})

const highlightAccountantUserId = computed(() => getQueryPositiveInt(route.query.highlight_accountant_user_id))
const highlightAccountantRelationDisplayName = computed(() => getQueryString(route.query.highlight_accountant_relation_display_name))
const profileViewKey = computed(() => `${profileUser.value?.id || 'invalid-profile'}:${highlightAccountantUserId.value || 'no-highlight'}`)

function handleNavigate(
  view: string,
  payload?: {
    userId?: number
    userName?: string
    id?: number
    user_id?: number
    account_name?: string
    highlight_accountant_user_id?: number | null
    highlight_accountant_relation_display_name?: string | null
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
      query: {
        ...(payload?.account_name ? { account_name: payload.account_name } : {}),
        ...(payload?.highlight_accountant_user_id ? { highlight_accountant_user_id: String(payload.highlight_accountant_user_id) } : {}),
        ...(payload?.highlight_accountant_relation_display_name
          ? { highlight_accountant_relation_display_name: payload.highlight_accountant_relation_display_name }
          : {}),
      },
    })
    return
  }

  if (view === 'settings' && payload?.userId) {
    router.push({
      name: 'admin-user-profile',
      params: { id: String(payload.userId) },
      query: {
        ...(payload.userName ? { account_name: payload.userName } : {}),
      },
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
  <div class="public-profile-view">
    <PublicProfile
      :key="profileViewKey"
      :user="profileUser"
      :viewerUserId="viewerUserId"
      :apiBaseUrl="apiBaseUrl"
      :jwtToken="jwtToken"
      :highlightAccountantUserId="highlightAccountantUserId"
      :highlightAccountantRelationDisplayName="highlightAccountantRelationDisplayName"
      @navigate="handleNavigate"
    />
  </div>
</template>

<style scoped>
.public-profile-view {
  min-height: 100%;
  padding: 16px;
}
</style>
