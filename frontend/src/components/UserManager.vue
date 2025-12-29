<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
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

const API_HEADERS = computed(() => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${props.jwtToken}`,
}));

async function fetchUsers() {
  isLoading.value = true;
  errorMessage.value = '';
  try {
    let url = `${props.apiBaseUrl}/api/users/`;
    if (searchQuery.value.trim()) {
      url += `?search=${encodeURIComponent(searchQuery.value.trim())}`;
    }
    
    const response = await fetch(url, {
      headers: API_HEADERS.value
    });
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
      <div class="header-row">
        <h2>👥 کاربران</h2>
        <button class="back-button" @click="$emit('navigate', 'admin_panel')">🔙</button>
      </div>

      <button class="search-button" @click="toggleSearch">
        {{ showSearch ? '❌ بستن جستجو' : '🔍 جستجوی کاربر' }}
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
            <div class="name">👤 {{ user.account_name }}</div>
            <div class="details">
              <span>📱 {{ user.mobile_number }}</span>
            </div>
            <div class="role-badge" :class="user.role">{{ user.role }}</div>
          </div>
          <div class="arrow">👈</div>
        </div>
      </div>
    </div>

  </div>
</template>

<style scoped>
.user-manager-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.card {
  background-color: var(--card-bg);
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
h2 { margin: 0; font-size: 18px; }
.back-button {
  background: transparent;
  border: none;
  font-size: 20px;
  cursor: pointer;
}

/* استایل دکمه جستجو */
.search-button {
  width: 100%;
  padding: 12px;
  background-color: #e0f2fe;
  color: #007aff;
  border: 1px solid #bae6fd;
  border-radius: 10px;
  font-weight: 600;
  cursor: pointer;
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: background 0.2s;
}
.search-button:hover {
  background-color: #bae6fd;
}

.search-box {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  animation: fadeIn 0.3s ease;
}
.search-input {
  flex: 1;
  padding: 10px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-family: inherit;
}
.do-search-btn {
  padding: 0 16px;
  background-color: var(--primary-color);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}

.users-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.user-item {
  background-color: #f9fafb;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  transition: background-color 0.2s;
}
.user-item:hover {
  background-color: #f0f9ff;
  border-color: #bae6fd;
}
.user-item:active {
  transform: scale(0.99);
}

.user-info {
  display: flex;
  flex-direction: column;
}

.arrow {
  font-size: 18px;
  opacity: 0.5;
}

.name {
  font-weight: 700;
  font-size: 15px;
  margin-bottom: 4px;
}
.details {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 8px;
  font-family: monospace; /* برای نمایش بهتر اعداد */
}
.role-badge {
  align-self: flex-start;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 12px;
  background-color: #eee;
  color: #555;
}
/* استایل‌های رنگی برای نقش‌ها */
.role-badge.مدیر.ارشد { background-color: #fee2e2; color: #991b1b; }
.role-badge.مدیر.میانی { background-color: #fef3c7; color: #92400e; }
.role-badge.پلیس { background-color: #e0e7ff; color: #3730a3; }
.role-badge.عادی { background-color: #d1fae5; color: #065f46; }

.loading, .error, .no-data { text-align: center; padding: 20px; color: var(--text-secondary); }
.error { color: #ef4444; }

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-5px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>