<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import LoadingSkeleton from '../LoadingSkeleton.vue'
import ChatUserListRow from './ChatUserListRow.vue'
import { UsersRound } from 'lucide-vue-next'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'start-chat', userId: number, userName: string): void
  (e: 'create-group'): void
}>()

const token = localStorage.getItem('auth_token') || ''

const searchQuery = ref('')
const users = ref<Array<{ id: number; account_name: string; full_name?: string | null; mobile_number: string; avatar_file_id?: string | null }>>([])
const isLoading = ref(false)

function getPrimaryUserName(accountName: string, fullName?: string | null) {
  const normalizedFullName = (fullName || '').trim()
  return normalizedFullName || accountName
}

const searchUsers = async (query: string = '') => {
  isLoading.value = true
  try {
    const url = new URL('/api/users-public/search', window.location.origin)
    if (query) {
      url.searchParams.append('q', query)
    }
    url.searchParams.append('limit', '50')
    
    const res = await fetch(url.toString(), {
      headers: { 
        'Authorization': `Bearer ${token}` 
      }
    })
    if (!res.ok) throw new Error('Network response was not ok')
    users.value = await res.json()
  } catch (err) {
    console.error('Failed to search users:', err)
  } finally {
    isLoading.value = false
  }
}

let debounceTimer: ReturnType<typeof setTimeout>
const performSearch = (val: string) => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    searchUsers(val)
  }, 300)
}

watch(searchQuery, (newVal) => {
  performSearch(newVal)
})

watch(() => props.show, (isVisible) => {
  if (isVisible) {
    searchQuery.value = ''
    searchUsers()
  }
})

onMounted(() => {
  if (props.show) {
    searchUsers()
  }
})

</script>

<template>
  <div v-if="show" class="new-chat-modal-overlay">
    <div class="new-chat-container">
      
      <!-- Header -->
      <div class="new-chat-header">
        <button class="icon-btn back-btn" v-ripple @click="$emit('close')">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="19" y1="12" x2="5" y2="12"></line>
            <polyline points="12 19 5 12 12 5"></polyline>
          </svg>
        </button>
        <span class="header-title">شروع مکالمه جدید</span>
      </div>

      <!-- Search Input -->
      <div class="search-area">
        <button type="button" class="new-group-action" v-ripple @click="$emit('create-group')">
          <span class="new-group-icon"><UsersRound :size="20" /></span>
          <span>ساخت گروه جدید</span>
        </button>
        <input 
          v-model="searchQuery" 
          type="text" 
          placeholder="جستجو (نام، آیدی، موبایل)..." 
          class="new-chat-search-input"
        />
      </div>

      <!-- Users List -->
      <div class="users-list">
        <div v-if="isLoading" class="loading-state">
           <LoadingSkeleton :count="6" :height="65" />
        </div>
        
        <div v-else-if="users.length === 0" class="empty-state">
          <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="#ccc" stroke-width="1.5">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <p>کاربری یافت نشد</p>
        </div>

        <ChatUserListRow
          v-else
          v-for="user in users"
          :key="user.id"
          tag="button"
          :interactive="true"
          :name="getPrimaryUserName(user.account_name, user.full_name)"
          :avatar-file-id="user.avatar_file_id || null"
          @click="$emit('start-chat', user.id, getPrimaryUserName(user.account_name, user.full_name))"
        >
          <template #subtitle>
            <span dir="ltr">{{ user.mobile_number }}</span>
          </template>
        </ChatUserListRow>
      </div>
      
    </div>
  </div>
</template>

<style scoped>
.new-chat-modal-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: white;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  min-height: 0;
  animation: slideUp 0.3s ease-out;
}

.new-chat-container {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

@keyframes slideUp {
  from { transform: translateY(100%); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}

.new-chat-header {
  display: flex;
  align-items: center;
  height: 56px;
  padding: 0 8px;
  background: white;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  flex-shrink: 0;
}

.icon-btn {
  background: transparent;
  border: none;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #707579;
}

.icon-btn:hover { background: rgba(0,0,0,0.05); }

.header-title {
  font-size: 18px;
  font-weight: 500;
  margin-right: 16px;
  color: #000;
}

.search-area {
  padding: 12px 16px;
  background: white;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.new-group-action {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  height: 44px;
  border: 0;
  border-radius: 12px;
  background: rgba(51, 144, 236, 0.08);
  color: #2586e8;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
  padding: 0 12px;
  text-align: right;
}

.new-group-action:hover {
  background: rgba(51, 144, 236, 0.14);
}

.new-group-icon {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: #3390ec;
  color: white;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.new-chat-search-input {
  width: 100%;
  height: 40px;
  background: #f1f2f6;
  border: none;
  border-radius: 20px;
  padding: 0 16px;
  font-size: 15px;
  font-family: inherit;
  outline: none;
  transition: background 0.2s;
}

.new-chat-search-input:focus {
  background: #e5e6ea;
}

.users-list {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  touch-action: pan-y;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  background: white;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding-bottom: 20%;
  color: #999;
}

.empty-state p {
  margin-top: 12px;
  font-size: 16px;
}

.loading-state {
  padding: 16px;
}
</style>
