<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { 
  Smartphone, Trash2, Loader2, HardDrive, 
  ChevronDown, ChevronLeft, UserX, Search, Unlock, ShieldAlert
} from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { apiFetch, forceLogout } from '../utils/auth'
import { useChatFileHandler } from '../composables/chat/useChatFileHandler'

const router = useRouter()
const { getCacheSize, clearFileCache } = useChatFileHandler()
const cacheSize = ref('0.00 MB')
const cacheBusy = ref(false)
const cacheFeedback = ref<string | null>(null)

const openSections = ref({
  sessions: false,
  storage: false,
  blocks: false
})

function toggleSection(section: 'sessions' | 'storage' | 'blocks') {
  openSections.value[section] = !openSections.value[section]
  if (section === 'blocks' && openSections.value.blocks && blockedUsers.value.length === 0) {
    fetchBlockedUsers()
  }
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

// ----------------- BLOCKS -----------------
const blockedUsers = ref<any[]>([])
const blockSearchQuery = ref('')
const searchResults = ref<any[]>([])
const searchLoading = ref(false)
const blockLoadingId = ref<number | null>(null)

async function fetchBlockedUsers() {
  try {
    const res = await apiFetch('/api/blocks/')
    if (res.ok) {
      blockedUsers.value = await res.json()
    }
  } catch(e) {
    console.error(e)
  }
}

async function searchUsersToBlock() {
  if (blockSearchQuery.value.trim().length < 2) {
    searchResults.value = []
    return
  }
  searchLoading.value = true
  try {
    const res = await apiFetch(`/api/blocks/search?q=${encodeURIComponent(blockSearchQuery.value)}&limit=5`)
    if (res.ok) {
      searchResults.value = await res.json()
    }
  } catch (e) {
    console.error(e)
  } finally {
    searchLoading.value = false
  }
}

async function blockUser(userId: number) {
  blockLoadingId.value = userId
  try {
    const res = await apiFetch(`/api/blocks/${userId}`, { method: 'POST' })
    if (res.ok) {
      await fetchBlockedUsers()
      blockSearchQuery.value = ''
      searchResults.value = []
      alert('کاربر با موفقیت مسدود شد.')
    } else {
      const data = await res.json()
      alert(data.detail || 'خطا در بلاک کاربر')
    }
  } catch(e) {
    console.error(e)
    alert('خطا در برقراری ارتباط')
  } finally {
    blockLoadingId.value = null
  }
}

async function unblockUser(userId: number) {
  if (!confirm('آیا از رفع مسدودیت این کاربر اطمینان دارید؟')) return
  
  blockLoadingId.value = userId
  try {
    const res = await apiFetch(`/api/blocks/${userId}`, { method: 'DELETE' })
    if (res.ok) {
      await fetchBlockedUsers()
    } else {
      const data = await res.json()
      alert(data.detail || 'خطا در رفع مسدودیت')
    }
  } catch(e) {
    console.error(e)
    alert('خطا در برقراری ارتباط')
  } finally {
    blockLoadingId.value = null
  }
}

onMounted(() => {
  fetchSessions()
  refreshCacheSize()
})
</script>

<template>
  <div class="settings-page">
    
    <div class="header-row">
      <div class="header-spacer"></div>
      <div class="header-title">
         <h2>⚙️ تنظیمات</h2>
      </div>
      <button class="back-button" @click="router.back()"><ChevronLeft :size="24" /></button>
    </div>

    <div class="settings-content">

      <!-- Active Sessions Accordion -->
      <div class="accordion-section">
        <div class="accordion-header" @click="toggleSection('sessions')">
          <div class="header-info">
            <Smartphone :size="18" class="text-amber-600" />
            <h2>نشست‌های فعال</h2>
          </div>
          <component :is="openSections.sessions ? ChevronDown : ChevronLeft" :size="20" class="accordion-icon" />
        </div>
        <div v-show="openSections.sessions" class="accordion-content">
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
                v-if="!session.is_current && !session.is_primary && sessions.some(s => s.is_current && s.is_primary)"
                @click="terminateSession(session.id)"
                class="p-2 text-red-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors shrink-0"
                title="پایان نشست"
              >
                <Trash2 :size="16" />
              </button>
            </div>
            
            <button
              v-if="sessions.length > 1 && sessions.some(s => s.is_current && s.is_primary)"
              @click="logoutAll"
              class="w-full mt-3 py-2.5 text-sm text-red-500 font-bold border border-red-200 rounded-xl hover:bg-red-50 transition-colors"
            >
              خروج از همه نشست‌ها
            </button>
          </div>
        </div>
      </div>

      <!-- Storage Management Accordion -->
      <div class="accordion-section">
        <div class="accordion-header" @click="toggleSection('storage')">
          <div class="header-info">
            <HardDrive :size="18" class="text-amber-600" />
            <h2>مدیریت حافظه و داده‌ها</h2>
          </div>
          <component :is="openSections.storage ? ChevronDown : ChevronLeft" :size="20" class="accordion-icon" />
        </div>
        <div v-show="openSections.storage" class="accordion-content">
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
              <Loader2 v-if="cacheBusy" :size="16" class="animate-spin" />
              <Trash2 v-else :size="16" />
              <span>حذف فایل‌های دانلود شده</span>
            </button>
            <p v-if="cacheFeedback" class="storage-feedback">{{ cacheFeedback }}</p>
          </div>
        </div>
      </div>

      <!-- Blocked Users Accordion -->
      <div class="accordion-section">
        <div class="accordion-header" @click="toggleSection('blocks')">
          <div class="header-info">
            <UserX :size="18" class="text-amber-600" />
            <h2>لیست مسدودشدگان</h2>
          </div>
          <component :is="openSections.blocks ? ChevronDown : ChevronLeft" :size="20" class="accordion-icon" />
        </div>
        <div v-show="openSections.blocks" class="accordion-content">
          
          <div class="block-search-box">
            <p class="section-hint">کاربران مسدود شده تنها از انجام معامله در بازار با شما محروم می‌شوند و هیچ محدودیتی در پیام‌رسان نخواهند داشت. شخص مسدود شده متوجه مسدود شدنش نخواهد شد.</p>
            <div class="search-input-wrapper">
              <Search :size="18" class="search-icon" />
              <input 
                type="text" 
                v-model="blockSearchQuery" 
                placeholder="جستجوی نام کاربری یا شماره موبایل..." 
                @input="searchUsersToBlock"
              />
              <Loader2 v-if="searchLoading" :size="18" class="search-loading animate-spin" />
            </div>

            <!-- Search Results -->
            <div v-if="searchResults.length > 0" class="search-results">
              <div v-for="user in searchResults" :key="user.id" class="user-row">
                <div class="user-info">
                  <span class="user-name">{{ user.full_name || user.account_name }}</span>
                  <span class="user-phone">{{ user.mobile_number }}</span>
                </div>
                <button 
                  v-if="!user.is_blocked" 
                  class="btn-block" 
                  @click="blockUser(user.id)"
                  :disabled="blockLoadingId === user.id"
                >
                  <Loader2 v-if="blockLoadingId === user.id" :size="14" class="animate-spin" />
                  <span v-else>مسدود کن</span>
                </button>
                <span v-else class="already-blocked">مسدود شده</span>
              </div>
            </div>
          </div>

          <hr class="divider" />

          <!-- Blocked List -->
          <div class="blocked-list">
            <h4 class="list-title">کاربران مسدود شده ({{ blockedUsers.length }})</h4>
            <div v-if="blockedUsers.length === 0" class="empty-list">
              لیست مسدودشدگان شما خالی است.
            </div>
            <div v-else class="space-y-2">
              <div v-for="user in blockedUsers" :key="user.id" class="user-row blocked-user-row">
                <div class="user-info">
                  <span class="user-name">{{ user.full_name || user.account_name }}</span>
                  <span class="user-phone">{{ user.mobile_number }}</span>
                </div>
                <button 
                  class="btn-unblock" 
                  @click="unblockUser(user.id)"
                  :disabled="blockLoadingId === user.id"
                >
                  <Loader2 v-if="blockLoadingId === user.id" :size="14" class="animate-spin" />
                  <Unlock v-else :size="14" />
                  <span>رفع مسدودیت</span>
                </button>
              </div>
            </div>
          </div>

        </div>
      </div>

      <!-- Logout Button -->
      <button class="logout-btn" @click="logout">
        خروج از حساب کاربری
      </button>

    </div>
  </div>
</template>

<style scoped>
.settings-page {
  min-height: 100dvh;
  padding: 16px;
}



.settings-content {
  padding: 1.25rem 0;
  width: 100%;
  max-width: 480px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
}

/* Accordion Styles */
.accordion-section {
  margin-bottom: 0.75rem;
  background: white;
  border: 1px solid rgba(245, 158, 11, 0.12);
  border-radius: 1rem;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0,0,0,0.02);
}

.accordion-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem;
  background: linear-gradient(135deg, #fffbeb, #fef9f0);
  cursor: pointer;
  transition: background 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.accordion-header:active {
  background: #fef3c7;
}

.header-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.accordion-header h2 {
  font-size: 0.9rem;
  font-weight: 700;
  margin: 0;
  color: #1f2937;
}

.accordion-icon {
  color: #d97706;
  transition: transform 0.2s;
}

.accordion-content {
  padding: 1rem;
  border-top: 1px solid rgba(245, 158, 11, 0.08);
  background: white;
  animation: slideDown 0.2s ease-out;
}
@keyframes slideDown {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Sessions Inside Accordion */
.session-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem;
  background: #f9fafb;
  border: 1px solid #f3f4f6;
  border-radius: 0.75rem;
}

/* Storage Inside Accordion */
.storage-card {
  background: #f9fafb;
  border: 1px solid #f3f4f6;
  border-radius: 0.75rem;
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
  color: #4b5563;
}
.storage-value {
  font-size: 0.85rem;
  font-weight: 700;
  color: #111827;
}
.storage-clear-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.6rem 0.875rem;
  border-radius: 0.75rem;
  border: 1px solid #fecaca;
  background: #fff5f5;
  color: #dc2626;
  font-size: 0.85rem;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.2s, transform 0.15s;
}
.storage-clear-btn:hover:not(:disabled) { background: #fee2e2; }
.storage-clear-btn:active:not(:disabled) { transform: scale(0.98); }
.storage-clear-btn:disabled { opacity: 0.6; cursor: progress; }
.storage-feedback {
  font-size: 0.75rem;
  color: #059669;
  margin: 0;
  text-align: center;
}

/* Blocks Section */
.section-hint {
  font-size: 0.75rem;
  color: #6b7280;
  margin-bottom: 0.75rem;
  line-height: 1.5;
}

.search-input-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  margin-bottom: 0.75rem;
}
.search-icon {
  position: absolute;
  right: 0.75rem;
  color: #9ca3af;
}
.search-loading {
  position: absolute;
  left: 0.75rem;
  color: #f59e0b;
}
.search-input-wrapper input {
  width: 100%;
  padding: 0.6rem 2.25rem 0.6rem 0.75rem;
  border: 1px solid #d1d5db;
  border-radius: 0.75rem;
  font-size: 0.85rem;
  outline: none;
  transition: border-color 0.2s;
}
.search-input-wrapper input:focus {
  border-color: #f59e0b;
  box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.1);
}

.search-results {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 0.75rem;
  overflow: hidden;
  margin-bottom: 1rem;
}
.user-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #f3f4f6;
}
.user-row:last-child {
  border-bottom: none;
}
.user-info {
  display: flex;
  flex-direction: column;
}
.user-name {
  font-size: 0.85rem;
  font-weight: 600;
  color: #1f2937;
}
.user-phone {
  font-size: 0.75rem;
  color: #6b7280;
  direction: ltr;
  text-align: right;
}

.btn-block {
  padding: 0.35rem 0.6rem;
  background: #fef2f2;
  color: #ef4444;
  border: 1px solid #fecaca;
  border-radius: 0.375rem;
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
}
.btn-block:hover { background: #fee2e2; }
.btn-block:disabled { opacity: 0.5; cursor: not-allowed; }

.already-blocked {
  font-size: 0.7rem;
  color: #9ca3af;
  font-weight: 500;
}

.divider {
  border: 0;
  height: 1px;
  background: rgba(245, 158, 11, 0.1);
  margin: 1.25rem 0;
}

.blocked-list {
  display: flex;
  flex-direction: column;
}
.list-title {
  font-size: 0.85rem;
  font-weight: 700;
  color: #374151;
  margin: 0 0 0.75rem 0;
}
.empty-list {
  font-size: 0.8rem;
  color: #9ca3af;
  text-align: center;
  padding: 1rem 0;
}
.blocked-user-row {
  background: #fff;
  border: 1px solid #f3f4f6;
  border-radius: 0.75rem;
}
.btn-unblock {
  padding: 0.35rem 0.6rem;
  background: #f3f4f6;
  color: #4b5563;
  border: 1px solid #d1d5db;
  border-radius: 0.375rem;
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  white-space: nowrap;
}
.btn-unblock:hover { background: #e5e7eb; }
.btn-unblock:disabled { opacity: 0.5; cursor: not-allowed; }

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
  margin-top: 1rem;
}
.logout-btn:active {
  transform: scale(0.98);
  background: #fee2e2;
}
</style>
