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

function handleNavigate(view: string, payload?: { userId?: number; userName?: string }) {
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
      :key="profileUser?.id || 'invalid-profile'"
      :user="profileUser"
      :viewerUserId="viewerUserId"
      :apiBaseUrl="apiBaseUrl"
      :jwtToken="jwtToken"
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
