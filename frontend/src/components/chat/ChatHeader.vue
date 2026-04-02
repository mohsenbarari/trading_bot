<template>
  <div class="chat-header">
    <template v-if="!isSelectionMode">
      <!-- Back Button -->
      <button class="header-btn back-btn" v-ripple @click="$emit('back')">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M19 12H5M12 19l-7-7 7-7"/>
        </svg>
      </button>
      
      <!-- Avatar + User Info (when in chat) -->
      <template v-if="selectedUserId">
        <div class="header-avatar" @click="$emit('view-profile')">{{ selectedUserName.charAt(0) }}</div>
        <div class="header-user-info" @click="$emit('view-profile')">
          <span class="header-name">
            {{ selectedUserName }}
            <span v-if="isDeleted" class="deleted-badge-small">غیرفعال</span>
          </span>
          <span class="header-status" :class="{ 'online': targetUserStatus.includes('آنلاین') && !isDeleted || isTyping }">
            <template v-if="isDeleted">
              حساب کاربری غیرفعال است
            </template>
            <template v-else-if="isTyping">
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
         <input 
            id="search-input"
            v-model="internalSearchQuery" 
            @input="onSearchInput" 
            placeholder="جستجو..." 
            class="header-search-input"
         />
         
         <!-- In-Chat Search Navigation -->
         <template v-if="selectedUserId && searchResults.length > 0">
           <span class="search-counter">{{ currentSearchIndex + 1 }} از {{ searchResults.length }}</span>
           <button class="nav-btn" v-ripple @click="$emit('prev-search-result')">
             <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"></polyline></svg>
           </button>
           <button class="nav-btn" v-ripple @click="$emit('next-search-result')">
             <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
           </button>
         </template>

         <button class="header-btn" v-ripple @click="$emit('toggle-search')">✕</button>
      </div>
      
      <!-- Spacer -->
      <div class="header-spacer"></div>
      
      <!-- Action Buttons (only in chat view) -->
      <template v-if="selectedUserId && !isSearchActive">
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
      
      <!-- Conversation List Actions -->
      <template v-else-if="!selectedUserId && !isSearchActive">
         <button class="header-btn" v-ripple @click="$emit('toggle-search')">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </button>
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
import { ref, watch } from 'vue'

const props = defineProps<{
  isSelectionMode: boolean
  selectedUserId: number | null
  selectedUserName: string
  targetUserStatus: string
  isTyping: boolean
  totalUnread: number
  isSearchActive: boolean
  searchQuery: string
  searchResults: any[]
  currentSearchIndex: number
  selectedMessagesCount: number
  isDeleted?: boolean
}>()

const emit = defineEmits<{
  (e: 'back'): void
  (e: 'view-profile'): void
  (e: 'toggle-search'): void
  (e: 'search', query: string): void
  (e: 'result-click', result: any): void
  (e: 'next-search-result'): void
  (e: 'prev-search-result'): void
  (e: 'call'): void
  (e: 'clear-selection'): void
}>()

const isMenuOpen = ref(false)
const internalSearchQuery = ref(props.searchQuery)

watch(() => props.searchQuery, (newVal) => {
  internalSearchQuery.value = newVal
})

const onSearchInput = () => {
  emit('search', internalSearchQuery.value)
}

const toggleMenu = () => {
  isMenuOpen.value = !isMenuOpen.value
}

const closeMenu = () => {
  isMenuOpen.value = false
}

const handleMenuSearch = () => {
  closeMenu()
  emit('toggle-search')
}

const handleMenuViewProfile = () => {
  closeMenu()
  emit('view-profile')
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

.search-bar-container {
  display: flex;
  align-items: center;
  flex: 1;
  gap: 8px;
  position: relative;
}

.header-search-input {
  flex: 1;
  height: 36px;
  border: none;
  border-radius: 18px;
  background: #f3f4f6;
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
  background: white;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  min-width: 180px;
  padding: 4px 0;
  z-index: 1001;
}
.header-menu-item {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 12px 16px;
  cursor: pointer;
  color: #111827;
  font-size: 15px;
}
.header-menu-item:hover { background: #f3f4f6; }
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
