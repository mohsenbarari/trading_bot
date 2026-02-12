<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const user = ref<any>(null)
const loading = ref(true)

async function fetchUser() {
  try {
    const token = localStorage.getItem('auth_token')
    if (!token) return router.push('/login')
    
    const res = await fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${token}` }
    })
    
    if (res.ok) {
      user.value = await res.json()
    } else {
      router.push('/login')
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

onMounted(fetchUser)
</script>

<template>
  <div class="min-h-[100dvh] p-4">
    
    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center h-[80dvh]">
      <div class="w-8 h-8 border-4 border-amber-500 border-t-transparent rounded-full animate-spin"></div>
    </div>

    <!-- Content (empty - ready for redesign) -->
    <div v-else-if="user">
      
    </div>

  </div>
</template>
