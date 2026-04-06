<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import LoadingSkeleton from '../LoadingSkeleton.vue'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'start-chat', userId: number, userName: string): void
}>()

const token = localStorage.getItem('auth_token') || ''

const searchQuery = ref('')
const users = ref<any[]>([])
const isLoading = ref(false)

const searchUsers = async (query: string = '') => {
  isLoading.value = true
  try {
    const url = new URL('/api/v1/users/public/search', window.location.origin)
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

const getAvatarInitial = (name: string) => {
  return name ? name.charAt(0).toUpperCase() : '?'
}
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

        <div v-else class="user-item" v-for="user in users" :key="user.id" v-ripple @click="$emit('start-chat', user.id, user.account_name)">
          <div class="user-avatar">{{ getAvatarInitial(user.account_name) }}</div>
          <div class="user-details">
            <span class="user-name">{{ user.account_name }}</span>
            <span class="user-phone" dir="ltr">{{ user.mobile_number }}</span>
          </div>
        </div>
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
  animation: slideUp 0.3s ease-out;
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
  background: white;
}

.user-item {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  cursor: pointer;
  transition: background 0.2s;
}

.user-item:hover {
  background: #f5f5f5;
}

.user-avatar {
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: #3390ec;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 500;
  flex-shrink: 0;
  margin-left: 12px;
}

.user-details {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 16px;
  font-weight: 500;
  color: #000;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-phone {
  font-size: 14px;
  color: #707579;
  margin-top: 2px;
  text-align: right;
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
