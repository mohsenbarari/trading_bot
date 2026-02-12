<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { User, Shield, MessageCircle, Bell, Store, LogOut, AlertTriangle, Ban } from 'lucide-vue-next'

const router = useRouter()
const user = ref<any>(null)
const loading = ref(true)

const isAdmin = computed(() => {
  return user.value && ['مدیر ارشد', 'مدیر میانی'].includes(user.value.role)
})

const isRestricted = computed(() => {
  if (!user.value?.trading_restricted_until) return false
  return new Date(user.value.trading_restricted_until) > new Date()
})

const isBlocked = computed(() => {
  return user.value && !user.value.has_bot_access
})

const restrictedUntil = computed(() => {
  if (!user.value?.trading_restricted_until) return ''
  const d = new Date(user.value.trading_restricted_until)
  return d.toLocaleDateString('fa-IR', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })
})

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 12) return 'صبح بخیر'
  if (hour < 17) return 'ظهر بخیر'
  return 'عصر بخیر'
})

const userInitial = computed(() => {
  if (!user.value) return ''
  const name = user.value.full_name || user.value.account_name
  return name ? name[0] : '?'
})

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
      localStorage.removeItem('auth_token')
      router.push('/login')
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function logout() {
  localStorage.removeItem('auth_token')
  localStorage.removeItem('refresh_token')
  router.push('/login')
}

onMounted(fetchUser)
</script>

<template>
  <div class="dashboard-page">
    
    <!-- Loading -->
    <div v-if="loading" class="loading-container">
      <div class="loading-spinner"></div>
    </div>

    <div v-else-if="user" class="dashboard-content">

      <!-- ═══ Top Bar ═══ -->
      <header class="top-bar">
        <div class="user-info" @click="router.push('/profile')">
          <div class="avatar">
            <span>{{ userInitial }}</span>
          </div>
          <div class="user-text">
            <span class="greeting">{{ greeting }} 👋</span>
            <span class="user-name">{{ user.full_name || user.account_name }}</span>
          </div>
        </div>
        <div class="top-actions">
          <button class="icon-btn" aria-label="اعلان‌ها">
            <Bell :size="22" />
            <!-- <span class="badge">3</span> -->
          </button>
          <button class="icon-btn" @click="logout" aria-label="خروج">
            <LogOut :size="20" />
          </button>
        </div>
      </header>

      <!-- ═══ Blocked Warning ═══ -->
      <div v-if="isBlocked" class="alert-card alert-blocked">
        <div class="alert-icon blocked-icon">
          <Ban :size="28" />
        </div>
        <div class="alert-body">
          <h3>حساب کاربری مسدود شده</h3>
          <p>دسترسی شما به سیستم توسط مدیریت مسدود شده است. برای اطلاعات بیشتر با پشتیبانی تماس بگیرید.</p>
        </div>
      </div>

      <!-- ═══ Restricted Warning ═══ -->
      <div v-else-if="isRestricted" class="alert-card alert-restricted">
        <div class="alert-icon restricted-icon">
          <AlertTriangle :size="24" />
        </div>
        <div class="alert-body">
          <h3>معاملات محدود شده</h3>
          <p>دسترسی معاملاتی شما تا <strong>{{ restrictedUntil }}</strong> محدود شده است.</p>
        </div>
      </div>

      <!-- ═══ Main Content ═══ -->
      <main class="main-section">

        <!-- Market Entry — Hero Button -->
        <button class="hero-btn" @click="router.push('/market')">
          <div class="hero-btn-bg"></div>
          <div class="hero-btn-content">
            <div class="hero-icon">
              <Store :size="32" />
            </div>
            <div class="hero-text">
              <span class="hero-title">ورود به بازار</span>
              <span class="hero-subtitle">مشاهده و ثبت لفظ‌های خرید و فروش</span>
            </div>
          </div>
          <div class="hero-arrow">←</div>
        </button>

        <!-- Quick Access Grid -->
        <div class="quick-grid">

          <!-- Messenger -->
          <button class="quick-card" @click="router.push('/messenger')">
            <div class="quick-icon messenger-icon">
              <MessageCircle :size="24" />
            </div>
            <span class="quick-label">پیام‌رسان</span>
            <span class="quick-badge coming-soon">بزودی</span>
          </button>

          <!-- Profile -->
          <button class="quick-card" @click="router.push('/profile')">
            <div class="quick-icon profile-icon">
              <User :size="24" />
            </div>
            <span class="quick-label">پنل کاربری</span>
          </button>

          <!-- Admin Panel (role-based) -->
          <button v-if="isAdmin" class="quick-card" @click="router.push('/admin')">
            <div class="quick-icon admin-icon">
              <Shield :size="24" />
            </div>
            <span class="quick-label">پنل مدیریت</span>
          </button>

        </div>

      </main>

      <!-- Footer -->
      <footer class="dashboard-footer">
        <span>نسخه ۲.۵.۰</span>
      </footer>

    </div>
  </div>
</template>

<style scoped>
/* ═══════════════════════════════════
   Dashboard — Clean & Premium
   ═══════════════════════════════════ */

.dashboard-page {
  min-height: 100dvh;
  background: linear-gradient(160deg, #fefce8 0%, #ffffff 40%, #fffbeb 100%);
  position: relative;
  overflow-x: hidden;
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
.dashboard-content {
  padding: 1.25rem;
  max-width: 480px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
}

/* ═══ Top Bar ═══ */
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.5rem;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.avatar {
  width: 48px;
  height: 48px;
  background: linear-gradient(135deg, #f59e0b, #d97706);
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-weight: 800;
  font-size: 1.25rem;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
  transition: transform 0.2s;
}
.user-info:active .avatar {
  transform: scale(0.95);
}

.user-text {
  display: flex;
  flex-direction: column;
}
.greeting {
  font-size: 0.75rem;
  color: #9ca3af;
  font-weight: 500;
}
.user-name {
  font-size: 1rem;
  font-weight: 700;
  color: #1f2937;
}

.top-actions {
  display: flex;
  gap: 0.25rem;
}

.icon-btn {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  border: none;
  background: white;
  color: #6b7280;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  transition: all 0.2s;
  position: relative;
  -webkit-tap-highlight-color: transparent;
}
.icon-btn:active {
  transform: scale(0.92);
  background: #f9fafb;
}
.badge {
  position: absolute;
  top: 6px;
  left: 6px;
  width: 16px;
  height: 16px;
  background: #ef4444;
  color: white;
  font-size: 0.6rem;
  font-weight: 700;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ═══ Alert Cards ═══ */
.alert-card {
  display: flex;
  align-items: flex-start;
  gap: 0.875rem;
  padding: 1rem 1.25rem;
  border-radius: 1rem;
  margin-bottom: 1.5rem;
  animation: slideDown 0.4s ease-out;
}
@keyframes slideDown {
  from { opacity: 0; transform: translateY(-10px); }
  to { opacity: 1; transform: translateY(0); }
}

.alert-blocked {
  background: linear-gradient(135deg, #fef2f2, #fee2e2);
  border: 1px solid #fecaca;
}
.alert-restricted {
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  border: 1px solid #fde68a;
}

.alert-icon {
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.blocked-icon {
  background: #fee2e2;
  color: #dc2626;
}
.restricted-icon {
  background: #fef3c7;
  color: #d97706;
}

.alert-body h3 {
  font-size: 0.9rem;
  font-weight: 700;
  margin: 0 0 0.25rem 0;
}
.alert-blocked .alert-body h3 { color: #991b1b; }
.alert-restricted .alert-body h3 { color: #92400e; }

.alert-body p {
  font-size: 0.78rem;
  margin: 0;
  line-height: 1.6;
}
.alert-blocked .alert-body p { color: #b91c1c; }
.alert-restricted .alert-body p { color: #a16207; }

/* ═══ Main Section ═══ */
.main-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

/* ═══ Hero Button ═══ */
.hero-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.5rem;
  border-radius: 1.25rem;
  border: none;
  cursor: pointer;
  overflow: hidden;
  -webkit-tap-highlight-color: transparent;
  transition: transform 0.2s;
}
.hero-btn:active {
  transform: scale(0.98);
}

.hero-btn-bg {
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, #f59e0b, #d97706, #b45309);
  transition: opacity 0.3s;
}
.hero-btn-bg::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, transparent 30%, rgba(255,255,255,0.1) 50%, transparent 70%);
  animation: shimmer 3s ease-in-out infinite;
}
@keyframes shimmer {
  0%, 100% { transform: translateX(-100%); }
  50% { transform: translateX(100%); }
}

.hero-btn-content {
  display: flex;
  align-items: center;
  gap: 1rem;
  position: relative;
  z-index: 1;
}

.hero-icon {
  width: 56px;
  height: 56px;
  background: rgba(255,255,255,0.2);
  backdrop-filter: blur(10px);
  border-radius: 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
}

.hero-text {
  display: flex;
  flex-direction: column;
  text-align: right;
}
.hero-title {
  font-size: 1.2rem;
  font-weight: 800;
  color: white;
}
.hero-subtitle {
  font-size: 0.75rem;
  color: rgba(255,255,255,0.8);
  margin-top: 0.15rem;
  font-weight: 500;
}

.hero-arrow {
  position: relative;
  z-index: 1;
  color: rgba(255,255,255,0.6);
  font-size: 1.5rem;
  font-weight: 300;
  animation: arrowBounce 2s ease-in-out infinite;
}
@keyframes arrowBounce {
  0%, 100% { transform: translateX(0); }
  50% { transform: translateX(-6px); }
}

/* ═══ Quick Grid ═══ */
.quick-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.875rem;
}

.quick-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 1.5rem 1rem;
  border-radius: 1.25rem;
  border: 1px solid rgba(0,0,0,0.04);
  background: white;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0,0,0,0.03);
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  -webkit-tap-highlight-color: transparent;
}
.quick-card:active {
  transform: scale(0.96);
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}

.quick-icon {
  width: 52px;
  height: 52px;
  border-radius: 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.2s;
}
.quick-card:active .quick-icon {
  transform: scale(0.9);
}

.messenger-icon {
  background: linear-gradient(135deg, #dbeafe, #bfdbfe);
  color: #2563eb;
}
.profile-icon {
  background: linear-gradient(135deg, #f3e8ff, #e9d5ff);
  color: #7c3aed;
}
.admin-icon {
  background: linear-gradient(135deg, #dcfce7, #bbf7d0);
  color: #16a34a;
}

.quick-label {
  font-size: 0.85rem;
  font-weight: 700;
  color: #374151;
}

.quick-badge {
  position: absolute;
  top: 10px;
  left: 10px;
  font-size: 0.6rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 20px;
}
.coming-soon {
  background: #f0f9ff;
  color: #0284c7;
  border: 1px solid #bae6fd;
}

/* ═══ Footer ═══ */
.dashboard-footer {
  text-align: center;
  padding: 1.5rem 0 1rem;
  font-size: 0.7rem;
  color: #d1d5db;
  font-weight: 500;
}
</style>
