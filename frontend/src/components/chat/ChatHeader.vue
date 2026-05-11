<template>
  <div class="chat-header">
    <template v-if="!isSelectionMode">
      <!-- Back Button -->
      <button class="header-btn back-btn" v-ripple @click="$emit('back')" v-if="!isSearchActive">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M19 12H5M12 19l-7-7 7-7"/>
        </svg>
      </button>
      
      <!-- Avatar + User Info (when in chat and not searching) -->
      <template v-if="selectedUserId && !isSearchActive">
        <div
          class="header-avatar"
          :class="{
            'room-avatar': selectedRoomKind !== 'direct',
            'channel-avatar': selectedRoomKind === 'channel',
            'group-avatar': selectedRoomKind === 'group',
          }"
          @click="handleTitleClick"
        >
          <img v-if="headerAvatarUrl" :src="headerAvatarUrl" :alt="selectedUserName" class="header-avatar-image" />
          <Megaphone v-else-if="selectedRoomKind === 'channel'" :size="21" />
          <UsersRound v-else-if="selectedRoomKind === 'group'" :size="21" />
          <template v-else>{{ getAvatarInitial(selectedUserName) }}</template>
        </div>
        <div class="header-user-info" @click="handleTitleClick">
          <span class="header-name">
            {{ selectedUserName }}
            <span v-if="isDeleted" class="deleted-badge-small">غیرفعال</span>
          </span>
          <span class="header-status" :class="{ 'online': selectedRoomKind === 'direct' && ((targetUserStatus.includes('آنلاین') && !isDeleted) || isTyping) }">
            <template v-if="isDeleted">
              حساب کاربری غیرفعال است
            </template>
            <template v-else-if="selectedRoomKind === 'direct' && isTyping">
              در حال نوشتن<span class="typing-dots"><span>.</span><span>.</span><span>.</span></span>
            </template>
            <template v-else>
              {{ targetUserStatus }}
            </template>
          </span>
        </div>
      </template>
      
      <!-- Title (for conversation list) -->
      <template v-else>
        <div v-if="!isSearchActive" class="header-title">
          پیام‌ها
          <span v-if="totalUnread > 0" class="badge">{{ totalUnread }}</span>
        </div>
      </template>
      
      <!-- Search Bar Overlay -->
      <div v-if="isSearchActive" class="search-bar-container">
         <button class="header-btn mobile-back-btn" v-ripple @click="$emit('toggle-search')">
           <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
             <line x1="19" y1="12" x2="5" y2="12"></line>
             <polyline points="12 19 5 12 12 5"></polyline>
           </svg>
         </button>
         <input 
            id="search-input"
            v-model="internalSearchQuery" 
            @input="onSearchInput" 
            placeholder="جستجو..." 
            class="header-search-input full-width-search"
         />
      </div>
      
      <!-- Spacer -->
      <div class="header-spacer"></div>
      
      <!-- Action Buttons (only in chat view) -->
      <template v-if="selectedUserId && !isSearchActive && selectedRoomKind === 'direct'">
        <button class="header-btn" v-ripple @click="$emit('call')">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
             <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
          </svg>
        </button>
        <!-- Three-dot Menu -->
        <div class="header-menu-container" style="position: relative;">
            <button class="header-btn" v-ripple @click.stop="toggleMenu">
              <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="1"></circle>
                <circle cx="12" cy="5" r="1"></circle>
                <circle cx="12" cy="19" r="1"></circle>
              </svg>
            </button>
            <div v-if="isMenuOpen" class="header-dropdown-menu" v-click-outside="closeMenu">
               <div class="header-menu-item" @click="handleMenuSearch">
                  <span>جستجو</span>
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                    <path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
                  </svg>
               </div>
               <div class="header-menu-item" @click="handleMenuViewProfile">
                  <span>اطلاعات فرد</span>
               </div>
            </div>
            <div v-if="isMenuOpen" class="menu-overlay" @click="closeMenu"></div>
        </div>
      </template>

      <template v-else-if="selectedUserId && !isSearchActive && selectedRoomKind !== 'direct'">
        <div class="header-menu-container" style="position: relative;">
          <button class="header-btn" v-ripple @click.stop="toggleMenu">
            <MoreVertical :size="22" />
          </button>
          <div v-if="isMenuOpen" class="header-dropdown-menu" v-click-outside="closeMenu">
            <div class="header-menu-item" @click="handleMenuSearch">
              <span>جستجو</span>
              <Search :size="18" />
            </div>
            <div class="header-menu-item" @click="handleMenuManageRoom">
              <span>{{ selectedRoomKind === 'group' ? 'مدیریت گروه' : 'تنظیمات کانال' }}</span>
              <UsersRound :size="18" />
            </div>
          </div>
          <div v-if="isMenuOpen" class="menu-overlay" @click="closeMenu"></div>
        </div>
      </template>
      
      <!-- Conversation List Actions -->
      <template v-else-if="!selectedUserId && !isSearchActive">
         <button class="header-btn" v-ripple @click="$emit('toggle-search')">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </button>
        <div class="header-menu-container" style="position: relative;">
          <button class="header-btn" v-ripple @click.stop="toggleMenu">
            <MoreVertical :size="22" />
          </button>
          <div v-if="isMenuOpen" class="header-dropdown-menu" v-click-outside="closeMenu">
            <div class="header-menu-item" @click="handleMenuViewProfile">
              <span>پروفایل عمومی من</span>
              <UsersRound :size="18" />
            </div>
            <div class="header-menu-item" @click="handleMenuCreateGroup">
              <span>ساخت گروه جدید</span>
              <UsersRound :size="18" />
            </div>
            <div v-if="canCreateChannel" class="header-menu-item" @click="handleMenuCreateChannel">
              <span>ساخت کانال</span>
              <Megaphone :size="18" />
            </div>
          </div>
          <div v-if="isMenuOpen" class="menu-overlay" @click="closeMenu"></div>
        </div>
      </template>
    </template>
    
    <!-- Selection Mode Header -->
    <template v-else>
      <button class="header-btn" v-ripple @click="$emit('clear-selection')">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
      <div class="header-title" style="flex: 1; margin-right: 16px;">
        {{ selectedMessagesCount }}
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Megaphone, MoreVertical, Search, UsersRound } from 'lucide-vue-next'
import { popBackState, pushBackState } from '../../composables/useBackButton'
import { buildChatFileUrl, getAvatarInitial } from '../../utils/chatFiles'

const props = defineProps<{
  isSelectionMode: boolean
  selectedUserId: number | null
  selectedUserName: string
  selectedAvatarFileId?: string | null
  selectedRoomKind?: 'direct' | 'channel' | 'group' | null
  apiBaseUrl?: string
  targetUserStatus: string
  isTyping: boolean
  totalUnread: number
  isSearchActive: boolean
  searchQuery: string
  searchResults: any[]
  currentSearchIndex: number
  selectedMessagesCount: number
  isDeleted?: boolean
  roomMemberCount?: number | null
  isRoomMandatory?: boolean
  isRoomSystem?: boolean
  canCreateChannel?: boolean
}>()

const emit = defineEmits<{
  (e: 'back'): void
  (e: 'view-profile'): void
  (e: 'toggle-search'): void
  (e: 'search', query: string): void
  (e: 'result-click', result: any): void
  (e: 'call'): void
  (e: 'clear-selection'): void
  (e: 'manage-room'): void
  (e: 'create-group'): void
  (e: 'create-channel'): void
}>()

const isMenuOpen = ref(false)
const internalSearchQuery = ref(props.searchQuery)
const menuBackStateActive = ref(false)
let closingMenuFromBack = false

watch(() => props.searchQuery, (newVal) => {
  internalSearchQuery.value = newVal
})

const roomMemberCountText = computed(() => {
  const count = Number(props.roomMemberCount || 0)
  if (props.selectedRoomKind === 'direct' || count <= 0) return ''
  return `${count.toLocaleString('fa-IR')} عضو`
})

const headerAvatarUrl = computed(() => buildChatFileUrl(props.selectedAvatarFileId ?? null, props.apiBaseUrl ?? ''))

const onSearchInput = () => {
  emit('search', internalSearchQuery.value)
}

const toggleMenu = () => {
  isMenuOpen.value = !isMenuOpen.value
}

const closeMenu = () => {
  isMenuOpen.value = false
}

watch(isMenuOpen, (isOpen) => {
  if (isOpen) {
    if (!menuBackStateActive.value) {
      menuBackStateActive.value = true
      pushBackState(() => {
        menuBackStateActive.value = false
        closingMenuFromBack = true
        closeMenu()
        closingMenuFromBack = false
      })
    }
    return
  }

  if (menuBackStateActive.value) {
    menuBackStateActive.value = false
    if (!closingMenuFromBack) {
      popBackState()
    }
  }
})

watch(
  () => [props.selectedUserId, props.isSearchActive, props.isSelectionMode] as const,
  () => {
    if (isMenuOpen.value) {
      closeMenu()
    }
  },
)

const handleMenuSearch = () => {
  closeMenu()
  emit('toggle-search')
}

const handleMenuViewProfile = () => {
  closeMenu()
  emit('view-profile')
}

const handleMenuManageRoom = () => {
  closeMenu()
  emit('manage-room')
}

const handleMenuCreateGroup = () => {
  closeMenu()
  emit('create-group')
}

const handleMenuCreateChannel = () => {
  closeMenu()
  emit('create-channel')
}

const handleTitleClick = () => {
  if (props.selectedRoomKind === 'direct') {
    emit('view-profile')
    return
  }
  if (props.selectedRoomKind === 'group' || props.selectedRoomKind === 'channel') {
    emit('manage-room')
  }
}

function formatDateForSeparator(dateString: string) {
  if (!dateString) return ''
  const date = new Date(dateString)
  return new Intl.DateTimeFormat('fa-IR', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date)
}
</script>

<style scoped>
.chat-header {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 56px;
  z-index: 1000;
  display: flex;
  align-items: center;
  padding: 0 8px;
  background: #ffffff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  border-bottom: none;
  gap: 8px;
  direction: ltr; /* Force LTR layout */
}

.header-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  transition: background 0.2s;
  color: #000000;
  width: 40px;
  height: 40px;
  flex-shrink: 0;
}

.header-btn svg { width: 24px; height: 24px; }
.header-btn:hover { background: rgba(0, 0, 0, 0.05); }
.header-btn:active { background: rgba(0, 0, 0, 0.1); }

.header-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: linear-gradient(135deg, #fbbf24, #f59e0b);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 600;
  flex-shrink: 0;
  cursor: pointer;
}

.header-avatar.room-avatar {
  cursor: pointer;
}

.header-avatar.channel-avatar {
  background: linear-gradient(135deg, #0f766e, #0ea5a4);
}

.header-avatar.group-avatar {
  background: linear-gradient(135deg, #2563eb, #06b6d4);
  cursor: pointer;
}

.header-avatar svg {
  stroke-width: 2.2;
}

.header-avatar-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}

.header-user-info {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  flex: 1;
  align-items: flex-start;
  padding-left: 4px;
  cursor: pointer;
}

.header-name {
  font-size: 16px;
  font-weight: 600;
  color: #000000;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
  text-align: left;
}

.header-status {
  font-size: 13px;
  color: #8E8E93;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
  text-align: left;
}

.header-status.online { color: #f59e0b; }
.header-spacer { display: none; }
.header-title {
  font-size: 17px;
  font-weight: 600;
  color: #000000;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  flex: 1;
}

.badge {
  background: #f59e0b;
  color: white;
  border-radius: 12px;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: bold;
}

.deleted-badge-small {
  background: #fef2f2;
  color: #ef4444;
  border: 1px solid #fecaca;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 10px;
  margin-right: 6px;
  vertical-align: middle;
}

.room-badge-small,
.header-room-meta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  padding: 2px 6px;
  font-size: 10px;
  font-weight: 700;
  line-height: 1.2;
  margin-right: 6px;
  vertical-align: middle;
}

.room-badge-small.channel {
  background: rgba(15, 118, 110, 0.12);
  color: #0f766e;
}

.room-badge-small.group {
  background: rgba(37, 99, 235, 0.12);
  color: #2563eb;
}

.header-room-meta {
  background: rgba(148, 163, 184, 0.14);
  color: #64748b;
}

.header-room-meta.mandatory {
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
}

.header-room-meta.system {
  background: rgba(124, 58, 237, 0.12);
  color: #6d28d9;
}

.search-bar-container {
  display: flex;
  align-items: center;
  flex: 1;
  gap: 8px;
  background: white;
  width: 100%;
}

.header-search-input {
  flex: 1;
  height: 38px;
  background: #f1f2f6;
  border: none;
  border-radius: 19px;
  padding: 0 16px;
  font-size: 14px;
  font-family: inherit;
  outline: none;
  width: 100%;
  padding: 0 16px;
  font-size: 15px;
  outline: none;
  font-family: inherit;
  direction: rtl; /* User inputs persian generally */
}

.search-results-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: white;
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
  border-radius: 8px;
  max-height: 300px;
  overflow-y: auto;
  z-index: 1001;
  margin-top: 4px;
}

.search-result-item {
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  cursor: pointer;
  border-bottom: 1px solid #f3f4f6;
}
.search-result-item:hover { background: #f9fafb; }
.search-res-text { font-size: 14px; color: #111827; }
.search-res-date { font-size: 12px; color: #6b7280; }

.header-menu-container { position: relative; }
.header-dropdown-menu {
  position: absolute;
  top: 100%;
  right: 0;
  background: rgba(255, 255, 255, 0.96);
  border-radius: 16px;
  border: 1px solid rgba(226, 232, 240, 0.92);
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.14);
  min-width: 196px;
  padding: 6px;
  z-index: 1001;
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
}
.header-menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  padding: 10px 14px;
  border-radius: 12px;
  cursor: pointer;
  color: #111827;
  font-size: 14px;
  font-weight: 700;
}
.header-menu-item:hover { background: rgba(15, 23, 42, 0.05); }
.header-menu-item svg { color: #64748b; }
.menu-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  z-index: 1000;
}

/* Typing Indicator Animation */
.typing-dots { margin-left: 2px; }
.typing-dots span {
  animation: typing 1.4s infinite;
  animation-fill-mode: both;
}
.typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.typing-dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes typing {
  0% { opacity: 0.2; }
  20% { opacity: 1; }
  100% { opacity: 0.2; }
}
</style>
