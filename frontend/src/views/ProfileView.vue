<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { User, Phone, Shield } from 'lucide-vue-next'
import { apiFetch, forceLogout } from '../utils/auth'
const user = ref<any>(null)
const loading = ref(true)

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

onMounted(fetchUser)
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
