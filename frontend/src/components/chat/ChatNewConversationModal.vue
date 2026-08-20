<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import LoadingSkeleton from '../LoadingSkeleton.vue'
import ChatUserListRow from './ChatUserListRow.vue'
import AppBackButton from '../ui/AppBackButton.vue'
import { UsersRound } from 'lucide-vue-next'
import { apiFetchJson } from '../../utils/auth'
import { getAccountantOwnerBadge, getChatRoleBadge } from '../../utils/chatRoleBadges'
import type { ChatRoleKind } from '../../types/chat'

const props = defineProps<{
  show: boolean
  canStartDirectChat?: boolean
  canCreateGroup?: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'start-chat', user: SearchUser): void
  (e: 'create-group'): void
}>()

type SearchUser = {
  id: number
  account_name: string
  full_name?: string | null
  mobile_number: string
  avatar_file_id?: string | null
  resolved_from_accountant_id?: number | null
  chat_role_kind?: ChatRoleKind | null
  chat_role_label?: string | null
  chat_accountant_owner_name?: string | null
  chat_accountant_owner_label?: string | null
  customer_management_name?: string | null
  customer_tier?: string | null
  highlight_accountant_relation_display_name?: string | null
}

const searchQuery = ref('')
const users = ref<SearchUser[]>([])
const isLoading = ref(false)

function getPrimaryUserName(user: SearchUser) {
  const normalizedCustomerName = (user.customer_management_name || '').trim()
  const normalizedFullName = (user.full_name || '').trim()
  const normalizedAccountName = (user.account_name || '').trim()
  return normalizedCustomerName || normalizedFullName || normalizedAccountName
}

function getAccountantContextLabel(user: SearchUser) {
  if (!user.resolved_from_accountant_id) return ''
  const relationDisplayName = (user.highlight_accountant_relation_display_name || '').trim()
  if (!relationDisplayName) return 'از مسیر حسابدار'
  return `از مسیر حسابدار: ${relationDisplayName}`
}

function getUserBadges(user: SearchUser) {
  const badges = []
  const roleBadge = getChatRoleBadge(user)
  if (roleBadge) badges.push(roleBadge)
  const ownerBadge = getAccountantOwnerBadge(user)
  if (ownerBadge) badges.push(ownerBadge)
  if (user.resolved_from_accountant_id) {
    badges.push({ label: 'مالک', tone: 'info' as const })
  }
  return badges
}

const searchUsers = async (query: string = '') => {
  isLoading.value = true
  try {
    const params = new URLSearchParams()
    if (query) {
      params.set('q', query)
    }
    params.set('limit', '50')
    params.set('chat_targets', 'true')

    users.value = await apiFetchJson(`/api/users-public/search?${params.toString()}`)
  } catch (err) {
    console.error('Failed to search users:', err)
  } finally {
    isLoading.value = false
  }
}

let debounceTimer: ReturnType<typeof setTimeout>
let skipNextSearchWatch = false
const performSearch = (val: string) => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    searchUsers(val)
  }, 300)
}

watch(searchQuery, (newVal) => {
  if (!props.show) return
  if (skipNextSearchWatch) {
    skipNextSearchWatch = false
    return
  }
  performSearch(newVal)
})

watch(() => props.show, (isVisible) => {
  clearTimeout(debounceTimer)
  if (!isVisible) {
    return
  }

  if (isVisible) {
    if (searchQuery.value !== '') {
      skipNextSearchWatch = true
      searchQuery.value = ''
    }
    searchUsers()
  }
})

onMounted(() => {
  if (props.show) {
    searchUsers()
  }
})

function handleCreateGroup() {
  if (props.canCreateGroup === false) return
  emit('create-group')
}

function handleUserClick(user: SearchUser) {
  if (props.canStartDirectChat === false) return
  emit('start-chat', user)
}

</script>

<template>
  <div v-if="show" class="new-chat-modal-overlay">
    <div class="new-chat-container">
      
      <!-- Header -->
      <div class="new-chat-header">
        <AppBackButton class="icon-btn back-btn" aria-label="بازگشت" @click="$emit('close')" />
        <span class="header-title">شروع مکالمه جدید</span>
      </div>

      <!-- Search Input -->
      <div class="search-area">
        <button v-if="canCreateGroup !== false" type="button" class="new-group-action" v-ripple @click="handleCreateGroup">
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
          :interactive="canStartDirectChat !== false"
          :name="getPrimaryUserName(user)"
          :avatar-file-id="user.avatar_file_id || null"
          :badges="getUserBadges(user)"
          @click="handleUserClick(user)"
        >
          <template #subtitle>
            <div class="new-chat-user-subtitle">
              <span dir="ltr">{{ user.mobile_number }}</span>
              <span v-if="getAccountantContextLabel(user)" class="new-chat-user-context">{{ getAccountantContextLabel(user) }}</span>
            </div>
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
  background: var(--messenger-surface-page, #f2f2f7);
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
  height: var(--messenger-header-height, 56px);
  padding: 0 8px;
  background: rgba(242, 242, 247, 0.92);
  box-shadow: none;
  border-bottom: 1px solid var(--messenger-border-subtle, rgba(60, 60, 67, 0.18));
  flex-shrink: 0;
}

.icon-btn {
  background: transparent;
  border: none;
  width: var(--ds-native-row-min-height, 48px);
  height: var(--ds-native-row-min-height, 48px);
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
  background: var(--messenger-surface-page, #f2f2f7);
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
  height: var(--ds-native-row-min-height, 48px);
  border: 0;
  border-radius: 12px;
  background: var(--ds-primary-50, #fffbeb);
  color: var(--ds-primary-700, #b45309);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
  padding: 0 12px;
  text-align: right;
}

.new-group-action:hover {
  background: var(--ds-primary-100, #fef3c7);
}

.new-chat-user-subtitle {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.new-chat-user-context {
  color: var(--ds-primary-700, #b45309);
  font-size: 0.72rem;
  font-weight: 700;
}

.new-group-icon {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--ds-primary-500, #f59e0b);
  color: white;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.new-chat-search-input {
  width: 100%;
  min-height: var(--ds-control-min-height, 48px);
  height: var(--ds-control-min-height, 48px);
  background: var(--ds-control-bg, #f2f2f7);
  border: none;
  border-radius: 12px;
  padding: 0 var(--ds-control-padding-inline, 0.875rem);
  font-size: 16px;
  font-family: inherit;
  outline: none;
  appearance: none;
  -webkit-appearance: none;
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
  background: var(--messenger-surface-panel, #ffffff);
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
