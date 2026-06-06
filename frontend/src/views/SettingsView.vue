<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { 
  Smartphone, Trash2, Loader2, HardDrive, 
  ChevronDown, ChevronLeft
} from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { apiFetch, forceLogout } from '../utils/auth'
import { useChatFileHandler } from '../composables/chat/useChatFileHandler'
import { currentUserSummary, primeCurrentUserSummary } from '../utils/currentUser'

const router = useRouter()
const { getCacheSize, clearFileCache } = useChatFileHandler()
const cacheSize = ref('0.00 MB')
const cacheBusy = ref(false)
const cacheFeedback = ref<string | null>(null)

const openSections = ref({
  sessions: false,
  storage: false
})
const isAccountant = computed(() => currentUserSummary.value?.is_accountant === true)

function toggleSection(section: 'sessions' | 'storage') {
  openSections.value[section] = !openSections.value[section]
}

// ----------------- STORAGE -----------------
async function refreshCacheSize() {
  try {
    cacheSize.value = await getCacheSize()
  } catch {
    cacheSize.value = '0.00 MB'
  }
}

async function clearCache() {
  if (cacheBusy.value) return
  cacheBusy.value = true
  cacheFeedback.value = null
  try {
    await clearFileCache()
    cacheSize.value = '0.00 MB'
    cacheFeedback.value = 'حافظه با موفقیت پاک شد.'
  } catch (err) {
    console.error(err)
    cacheFeedback.value = 'پاک‌سازی حافظه ناموفق بود.'
  } finally {
    cacheBusy.value = false
    setTimeout(() => { cacheFeedback.value = null }, 3500)
  }
}

// ----------------- SESSIONS -----------------
const sessions = ref<any[]>([])
const sessionsLoading = ref(false)

async function fetchSessions() {
  if (isAccountant.value) {
    sessions.value = []
    return
  }
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
    fetchSessions()
  } catch (e) {
    console.error(e)
  }
}

async function logout() {
  const currentSession = sessions.value.find(s => s.is_current)
  if (currentSession) {
    try {
      await apiFetch(`/api/sessions/${currentSession.id}`, { method: 'DELETE' })
    } catch (e) {
      console.error(e)
    }
  }
  forceLogout()
}

onMounted(() => {
  void primeCurrentUserSummary().finally(() => {
    if (!isAccountant.value) {
      void fetchSessions()
    }
  })
  refreshCacheSize()
})
</script>

<template>
  <div class="ds-page settings-page">
    
    <div class="header-row">
      <div class="header-spacer"></div>
      <div class="header-title">
         <h2>⚙️ تنظیمات</h2>
      </div>
      <button class="back-button" @click="router.back()"><ChevronLeft :size="24" /></button>
    </div>

    <div class="settings-content">

      <!-- Active Sessions Accordion -->
      <div v-if="!isAccountant" class="ds-accordion">
        <div class="ds-accordion-header" @click="toggleSection('sessions')">
          <div class="ds-accordion-header-info">
            <Smartphone :size="18" class="icon-primary" />
            <h2>نشست‌های فعال</h2>
          </div>
          <component :is="openSections.sessions ? ChevronDown : ChevronLeft" :size="20" class="ds-accordion-icon" />
        </div>
        <div v-show="openSections.sessions" class="ds-accordion-body">
          <div v-if="sessionsLoading" class="loading-inline">
            <Loader2 class="spin-icon" :size="20" />
          </div>
          
          <div v-else-if="sessions.length === 0" class="empty-inline">
            هیچ نشست فعالی یافت نشد
          </div>
          
          <div v-else class="sessions-list">
            <div v-for="session in sessions" :key="session.id" class="session-card">
              <div class="session-info">
                <div class="session-icon" :class="{ 'session-icon-primary': session.is_primary }">
                  <Smartphone :size="18" />
                </div>
                <div class="session-details">
                  <div class="session-name-row">
                    <span class="session-name">{{ session.device_name }}</span>
                    <span v-if="session.is_primary" class="session-tag tag-primary">اصلی</span>
                    <span v-if="session.is_current" class="session-tag tag-current">این دستگاه</span>
                  </div>
                  <div class="session-meta">
                    {{ session.platform }} · {{ session.device_ip || '—' }}
                  </div>
                </div>
              </div>
              <button
                v-if="!session.is_current && !session.is_primary && sessions.some(s => s.is_current && s.is_primary)"
                @click="terminateSession(session.id)"
                class="session-delete-btn"
                title="پایان نشست"
              >
                <Trash2 :size="16" />
              </button>
            </div>
            
            <button
              v-if="sessions.length > 1 && sessions.some(s => s.is_current && s.is_primary)"
              @click="logoutAll"
              class="logout-all-btn"
            >
              خروج از همه نشست‌ها
            </button>
          </div>
        </div>
      </div>

      <!-- Storage Management Accordion -->
      <div class="ds-accordion">
        <div class="ds-accordion-header" @click="toggleSection('storage')">
          <div class="ds-accordion-header-info">
            <HardDrive :size="18" class="icon-primary" />
            <h2>مدیریت حافظه و داده‌ها</h2>
          </div>
          <component :is="openSections.storage ? ChevronDown : ChevronLeft" :size="20" class="ds-accordion-icon" />
        </div>
        <div v-show="openSections.storage" class="ds-accordion-body">
          <div class="storage-card">
            <div class="storage-info">
              <span class="storage-label">فضای اشغال‌شده توسط فایل‌های دانلود‌شده</span>
              <span class="storage-value" dir="ltr">{{ cacheSize }}</span>
            </div>
            <button
              type="button"
              class="storage-clear-btn"
              :disabled="cacheBusy"
              @click="clearCache"
            >
              <Loader2 v-if="cacheBusy" :size="16" class="spin-icon" />
              <Trash2 v-else :size="16" />
              <span>حذف فایل‌های دانلود شده</span>
            </button>
            <p v-if="cacheFeedback" class="storage-feedback">{{ cacheFeedback }}</p>
          </div>
        </div>
      </div>

      <!-- Logout Button -->
      <button v-if="!isAccountant" class="logout-btn" @click="logout">
        خروج از حساب کاربری
      </button>

    </div>
  </div>
</template>

<style scoped>
.settings-page {
  padding: var(--ds-page-padding);
}

.settings-content {
  padding: 1.25rem 0;
  width: 100%;
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
}

/* Icon color utility */
.icon-primary { color: var(--ds-primary-600); }
.spin-icon { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Inline loading/empty */
.loading-inline {
  text-align: center;
  padding: 1rem 0;
  display: flex;
  justify-content: center;
}
.loading-inline .spin-icon { color: var(--ds-primary-500); }

.empty-inline {
  text-align: center;
  font-size: var(--ds-font-base);
  color: var(--ds-text-placeholder);
  padding: 1rem 0;
}

/* Sessions */
.sessions-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.session-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem;
  background: var(--ds-bg-inset);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-md);
}

.session-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex: 1;
  min-width: 0;
}

.session-icon {
  width: 36px;
  height: 36px;
  border-radius: var(--ds-radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--ds-bg-hover);
  color: var(--ds-text-muted);
}
.session-icon-primary {
  background: var(--ds-primary-100);
  color: var(--ds-primary-600);
}

.session-details {
  min-width: 0;
  flex: 1;
}

.session-name-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.session-name {
  font-size: var(--ds-font-base);
  font-weight: 600;
  color: var(--ds-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-tag {
  font-size: 0.625rem;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 700;
  flex-shrink: 0;
}
.tag-primary {
  background: var(--ds-primary-100);
  color: var(--ds-primary-700);
}
.tag-current {
  background: var(--ds-success-100);
  color: var(--ds-success-700);
}

.session-meta {
  font-size: var(--ds-font-xs);
  color: var(--ds-text-placeholder);
  margin-top: 2px;
  direction: ltr;
  text-align: right;
}

.session-delete-btn {
  padding: 0.5rem;
  color: var(--ds-danger-500);
  background: transparent;
  border: none;
  border-radius: var(--ds-radius-sm);
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}
.session-delete-btn:hover {
  color: var(--ds-danger-600);
  background: var(--ds-danger-50);
}

.logout-all-btn {
  width: 100%;
  margin-top: 0.75rem;
  padding: 0.625rem;
  font-size: var(--ds-font-base);
  color: var(--ds-danger-500);
  font-weight: 700;
  border: 1px solid var(--ds-danger-200);
  border-radius: var(--ds-radius-md);
  background: transparent;
  cursor: pointer;
  transition: all 0.2s;
}
.logout-all-btn:hover {
  background: var(--ds-danger-50);
}

/* Storage Card */
.storage-card {
  background: var(--ds-bg-inset);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-md);
  padding: 0.875rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.storage-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.storage-label {
  font-size: 0.8rem;
  color: var(--ds-text-secondary);
}
.storage-value {
  font-size: var(--ds-font-base);
  font-weight: 700;
  color: var(--ds-text-primary);
}
.storage-clear-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.6rem 0.875rem;
  border-radius: var(--ds-radius-md);
  border: 1px solid var(--ds-danger-200);
  background: #fff5f5;
  color: var(--ds-danger-600);
  font-size: var(--ds-font-base);
  font-weight: 700;
  cursor: pointer;
  transition: background 0.2s, transform 0.15s;
}
.storage-clear-btn:hover:not(:disabled) { background: var(--ds-danger-100); }
.storage-clear-btn:active:not(:disabled) { transform: scale(0.98); }
.storage-clear-btn:disabled { opacity: 0.6; cursor: progress; }
.storage-feedback {
  font-size: var(--ds-font-sm);
  color: var(--ds-success-600);
  margin: 0;
  text-align: center;
}

.logout-btn {
  width: 100%;
  padding: 0.875rem;
  border-radius: var(--ds-radius-lg);
  border: 1px solid var(--ds-danger-200);
  background: linear-gradient(135deg, var(--ds-danger-50), var(--ds-danger-100));
  color: var(--ds-danger-600);
  font-weight: 700;
  font-size: var(--ds-font-md);
  cursor: pointer;
  transition: all 0.2s;
  margin-top: 1rem;
}
.logout-btn:active {
  transform: scale(0.98);
  background: var(--ds-danger-100);
}
</style>
