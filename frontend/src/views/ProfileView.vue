<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { User, Phone, Shield, Smartphone, Trash2, Loader2 } from 'lucide-vue-next'
import { apiFetch, forceLogout } from '../utils/auth'
const user = ref<any>(null)
const loading = ref(true)
const sessions = ref<any[]>([])
const sessionsLoading = ref(false)

const userInitial = computed(() => {
  if (!user.value) return '?'
  const name = user.value.full_name || user.value.account_name
  return name ? name[0] : '?'
})

const memberSince = computed(() => {
  if (!user.value?.created_at) return ''
  const d = new Date(user.value.created_at)
  return d.toLocaleDateString('fa-IR', { year: 'numeric', month: 'long', day: 'numeric' })
})

async function fetchUser() {
  try {
    const res = await apiFetch('/api/auth/me')
    if (res.ok) {
      user.value = await res.json()
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function logout() {
  forceLogout()
}

async function fetchSessions() {
  sessionsLoading.value = true
  try {
    const res = await apiFetch('/api/sessions/active')
    if (res.ok) {
      sessions.value = await res.json()
    }
  } catch (e) {
    console.error(e)
  } finally {
    sessionsLoading.value = false
  }
}

async function terminateSession(sessionId: string) {
  try {
    const res = await apiFetch(`/api/sessions/${sessionId}`, { method: 'DELETE' })
    if (res.ok) {
      sessions.value = sessions.value.filter(s => s.id !== sessionId)
    }
  } catch (e) {
    console.error(e)
  }
}

async function logoutAll() {
  try {
    await apiFetch('/api/sessions/logout-all', { method: 'POST' })
    forceLogout()
  } catch (e) {
    console.error(e)
  }
}

onMounted(() => {
  fetchUser()
  fetchSessions()
})
</script>

<template>
  <div class="profile-page">
    
    <!-- Loading -->
    <div v-if="loading" class="loading-container">
      <div class="loading-spinner"></div>
    </div>

    <div v-else-if="user" class="profile-content">

      <!-- Spacer -->
      <div style="height: 0.5rem;"></div>

      <!-- Avatar Section -->
      <div class="avatar-section">
        <div class="avatar-large">
          <span>{{ userInitial }}</span>
        </div>
        <h2 class="profile-name">{{ user.full_name || user.account_name || 'کاربر' }}</h2>
        <span v-if="user.role === 'admin'" class="role-badge">مدیر سیستم</span>
        <span v-else class="role-badge role-user">کاربر</span>
      </div>

      <!-- Info Cards -->
      <div class="info-cards">

        <div class="info-card">
          <div class="info-icon">
            <Phone :size="20" />
          </div>
          <div class="info-body">
            <span class="info-label">شماره موبایل</span>
            <span class="info-value" dir="ltr">{{ user.mobile_number || '---' }}</span>
          </div>
        </div>

        <div class="info-card">
          <div class="info-icon">
            <User :size="20" />
          </div>
          <div class="info-body">
            <span class="info-label">نام حساب</span>
            <span class="info-value">{{ user.account_name || '---' }}</span>
          </div>
        </div>

        <div class="info-card">
          <div class="info-icon">
            <Shield :size="20" />
          </div>
          <div class="info-body">
            <span class="info-label">عضویت از</span>
            <span class="info-value">{{ memberSince || '---' }}</span>
          </div>
        </div>

      </div>

      <!-- Active Sessions -->
      <div class="sessions-section">
        <h3 class="section-title">
          <Smartphone :size="18" />
          <span>نشست‌های فعال</span>
        </h3>
        
        <div v-if="sessionsLoading" class="text-center py-4">
          <Loader2 class="w-5 h-5 text-amber-500 animate-spin mx-auto" />
        </div>
        
        <div v-else-if="sessions.length === 0" class="text-center text-sm text-gray-400 py-4">
          هیچ نشست فعالی یافت نشد
        </div>
        
        <div v-else class="space-y-2">
          <div v-for="session in sessions" :key="session.id" class="session-card">
            <div class="flex items-center gap-3 flex-1 min-w-0">
              <div class="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
                   :class="session.is_primary ? 'bg-amber-100 text-amber-600' : 'bg-gray-100 text-gray-500'">
                <Smartphone :size="18" />
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  <span class="text-sm font-medium text-gray-800 truncate">{{ session.device_name }}</span>
                  <span v-if="session.is_primary" class="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded font-bold shrink-0">اصلی</span>
                  <span v-if="session.is_current" class="text-[10px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded font-bold shrink-0">این دستگاه</span>
                </div>
                <div class="text-xs text-gray-400 mt-0.5 dir-ltr text-right">
                  {{ session.platform }} · {{ session.device_ip || '—' }}
                </div>
              </div>
            </div>
            <button
              v-if="!session.is_primary && !session.is_current"
              @click="terminateSession(session.id)"
              class="p-2 text-red-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors shrink-0"
              title="پایان نشست"
            >
              <Trash2 :size="16" />
            </button>
          </div>
          
          <button
            v-if="sessions.length > 1"
            @click="logoutAll"
            class="w-full mt-3 py-2.5 text-sm text-red-500 font-bold border border-red-200 rounded-xl hover:bg-red-50 transition-colors"
          >
            خروج از همه نشست‌ها
          </button>
        </div>
      </div>

      <!-- Logout Button -->
      <button class="logout-btn" @click="logout">
        خروج از حساب کاربری
      </button>

      <!-- Footer -->
      <footer class="profile-footer">
        <span>نسخه ۲.۵.۰</span>
      </footer>

    </div>
  </div>
</template>

<style scoped>
.profile-page {
  min-height: 100dvh;
}

/* Loading */
.loading-container {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100dvh;
}
.loading-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid #f59e0b;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Content */
.profile-content {
  padding: 1.25rem;
  padding-bottom: 6rem;
  max-width: 480px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
}

/* Avatar Section */
.avatar-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 2rem;
}

.avatar-large {
  width: 80px;
  height: 80px;
  background: linear-gradient(135deg, #f59e0b, #d97706);
  border-radius: 1.25rem;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-weight: 800;
  font-size: 2rem;
  box-shadow: 0 8px 24px rgba(245, 158, 11, 0.3);
  margin-bottom: 1rem;
}

.profile-name {
  font-size: 1.25rem;
  font-weight: 800;
  color: #1f2937;
  margin: 0 0 0.5rem 0;
}

.role-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.75rem;
  font-size: 0.7rem;
  font-weight: 700;
  border-radius: 2rem;
  background: linear-gradient(135deg, #fef3c7, #fde68a);
  color: #92400e;
  border: 1px solid rgba(245, 158, 11, 0.2);
}
.role-user {
  background: linear-gradient(135deg, #f3f4f6, #e5e7eb);
  color: #6b7280;
  border-color: rgba(107, 114, 128, 0.15);
}

/* Info Cards */
.info-cards {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-bottom: 2rem;
}

.info-card {
  display: flex;
  align-items: center;
  gap: 0.875rem;
  padding: 1rem 1.25rem;
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(245, 158, 11, 0.1);
  border-radius: 1rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}

.info-icon {
  flex-shrink: 0;
  width: 42px;
  height: 42px;
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #d97706;
}

.info-body {
  display: flex;
  flex-direction: column;
}

.info-label {
  font-size: 0.7rem;
  color: #9ca3af;
  font-weight: 500;
  margin-bottom: 0.15rem;
}

.info-value {
  font-size: 0.9rem;
  font-weight: 700;
  color: #1f2937;
}

/* Sessions Section */
.sessions-section {
  margin-bottom: 1.5rem;
}
.section-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
  font-weight: 700;
  color: #374151;
  margin-bottom: 0.75rem;
}
.session-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem;
  background: #f9fafb;
  border: 1px solid #f3f4f6;
  border-radius: 0.75rem;
}

/* Logout Button */
.logout-btn {
  width: 100%;
  padding: 0.875rem;
  border-radius: 1rem;
  border: 1px solid #fecaca;
  background: linear-gradient(135deg, #fef2f2, #fee2e2);
  color: #dc2626;
  font-weight: 700;
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.logout-btn:active {
  transform: scale(0.98);
  background: #fee2e2;
}

/* Footer */
.profile-footer {
  text-align: center;
  padding: 1.5rem 0 1rem;
  font-size: 0.7rem;
  color: #d1d5db;
  font-weight: 500;
  margin-top: auto;
}
</style>
