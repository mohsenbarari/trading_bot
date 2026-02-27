<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { apiFetch } from '../utils/auth';
import LoadingSkeleton from './LoadingSkeleton.vue';

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>();

const emit = defineEmits(['navigate']);

interface User {
  id: number;
  full_name: string;
  telegram_id: number;
  account_name: string;
  role: string;
  mobile_number: string;
}

const users = ref<User[]>([]);
const isLoading = ref(true);
const errorMessage = ref('');
const searchQuery = ref('');
const showSearch = ref(false);

async function fetchUsers() {
  isLoading.value = true;
  errorMessage.value = '';
  try {
    let url = `/api/users/`;
    if (searchQuery.value.trim()) {
      url += `?search=${encodeURIComponent(searchQuery.value.trim())}`;
    }
    
    const response = await apiFetch(url);
    if (!response.ok) throw new Error('خطا در دریافت لیست کاربران');
    users.value = await response.json();
  } catch (e: any) {
    errorMessage.value = e.message || 'خطای ناشناخته';
  } finally {
    isLoading.value = false;
  }
}

function toggleSearch() {
  showSearch.value = !showSearch.value;
  if (!showSearch.value) {
    searchQuery.value = '';
    fetchUsers();
  }
}

function selectUser(user: User) {
  emit('navigate', 'user_profile', user);
}

onMounted(fetchUsers);
</script>

<template>
  <div class="user-manager-container">
    
    <div class="card">
      <button class="search-button" @click="toggleSearch">
        {{ showSearch ? 'بستن جستجو' : '🔍 جستجوی کاربر' }}
      </button>

      <div v-if="showSearch" class="search-box">
        <input 
          v-model="searchQuery" 
          @keyup.enter="fetchUsers" 
          placeholder="نام، نام کاربری یا موبایل..." 
          class="search-input"
        />
        <button @click="fetchUsers" class="do-search-btn">جستجو</button>
      </div>

      <div v-if="isLoading" class="loading-skeleton">
         <LoadingSkeleton :count="6" :height="70" />
      </div>
      <div v-else-if="errorMessage" class="error">{{ errorMessage }}</div>
      
      <div v-else class="users-list">
        <div v-if="users.length === 0" class="no-data">کاربری یافت نشد.</div>
        
        <div v-for="user in users" :key="user.id" class="user-item" @click="selectUser(user)">
          <div class="user-info">
            <div class="user-avatar">{{ user.account_name ? user.account_name[0] : '?' }}</div>
            <div class="user-text">
              <div class="name">
                {{ user.account_name }}
              </div>
              <div class="details" dir="ltr">{{ user.mobile_number }}</div>
            </div>
          </div>
          <div class="user-meta">
            <div class="role-badge" :class="user.role">{{ user.role }}</div>
            <span class="arrow">←</span>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>

<style scoped>
/* ── Layout ── */
.user-manager-container {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

/* ── Card (glass) — identical to CommodityManager ── */
.card {
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(245, 158, 11, 0.1);
  border-radius: 1.25rem;
  padding: 1.25rem;
  box-shadow: 0 4px 16px rgba(0,0,0,0.04);
}

/* ── Search button — matches CommodityManager action buttons ── */
.search-button {
  width: 100%;
  padding: 0.875rem 1rem;
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  color: #92400e;
  border: 1px solid rgba(245, 158, 11, 0.2);
  border-radius: 1rem;
  font-weight: 700;
  font-size: 0.9rem;
  cursor: pointer;
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.search-button:hover {
  background: #fef3c7;
  border-color: #f59e0b;
}
.search-button:active {
  transform: scale(0.98);
}

/* ── Search box ── */
.search-box {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
  animation: fadeIn 0.3s ease;
}
.search-input {
  flex: 1;
  padding: 0.625rem 0.875rem;
  border: 1px solid rgba(245, 158, 11, 0.15);
  border-radius: 0.75rem;
  font-family: inherit;
  font-size: 0.9rem;
  background: white;
  color: #1f2937;
  outline: none;
  transition: all 0.2s;
}
.search-input:focus {
  border-color: #f59e0b;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1);
}
.do-search-btn {
  padding: 0 1.25rem;
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white;
  border: none;
  border-radius: 0.75rem;
  cursor: pointer;
  font-weight: 700;
  font-size: 0.9rem;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.25);
}
.do-search-btn:active {
  transform: scale(0.95);
}

/* ── User list items — matches CommodityManager list items ── */
.users-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.user-item {
  background: white;
  border: 1px solid rgba(245, 158, 11, 0.15);
  border-radius: 1rem;
  padding: 0.875rem 1rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  transition: all 0.2s;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  -webkit-tap-highlight-color: transparent;
}
.user-item:hover {
  background: #fffbeb;
  border-color: #f59e0b;
}
.user-item:active {
  transform: scale(0.98);
}

/* ── User item content ── */
.user-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.user-avatar {
  width: 40px;
  height: 40px;
  background: linear-gradient(135deg, #f59e0b, #d97706);
  border-radius: 0.75rem;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-weight: 800;
  font-size: 1rem;
  flex-shrink: 0;
}
.user-text {
  display: flex;
  flex-direction: column;
}
.user-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.arrow {
  font-size: 1.2rem;
  color: #d1d5db;
  font-weight: 400;
}
.name {
  font-weight: 600;
  font-size: 0.9rem;
  color: #1f2937;
}
.details {
  font-size: 0.8rem;
  color: #6b7280;
  margin-top: 0.15rem;
  font-family: monospace;
  font-weight: 400;
}

/* ── Role badges ── */
.role-badge {
  padding: 0.2rem 0.5rem;
  border-radius: 0.5rem;
  font-size: 0.7rem;
  font-weight: 700;
  background: #f3f4f6;
  color: #6b7280;
}
.role-badge.مدیر { background: #fef3c7; color: #92400e; }
.role-badge.پلیس { background: #ede9fe; color: #5b21b6; }
.role-badge.عادی { background: #d1fae5; color: #065f46; }
.role-badge.تماشا { background: #f3f4f6; color: #6b7280; }


/* ── Status ── */
.loading, .error, .no-data { text-align: center; padding: 1.5rem; color: #9ca3af; font-size: 0.85rem; }
.error { color: #ef4444; }

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-5px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>