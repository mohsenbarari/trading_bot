<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { apiFetch } from '../utils/auth';
import LoadingSkeleton from './LoadingSkeleton.vue';
import CustomerNameWithBadge from './CustomerNameWithBadge.vue';
import { Search, X, ChevronLeft } from 'lucide-vue-next';

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
  is_customer?: boolean;
  customer_owner_account_name?: string | null;
  customer_management_name?: string | null;
  is_accountant?: boolean;
  accountant_owner_account_name?: string | null;
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

function getUserDisplayName(user: User) {
  return user.customer_management_name?.trim() || user.account_name || 'کاربر';
}

onMounted(fetchUsers);
</script>

<template>
  <div class="user-manager ds-page-content">
    
    <div class="ds-card">
      <div class="user-search-toolbar">
        <button class="search-toggle-btn" :class="{ active: showSearch }" @click="toggleSearch">
          <Search v-if="!showSearch" :size="20" />
          <X v-else :size="20" />
          <span class="btn-text">{{ showSearch ? 'بستن جستجو' : 'جستجوی کاربر' }}</span>
        </button>
      </div>

      <transition name="slide">
        <div v-if="showSearch" class="search-box">
          <div class="search-input-wrapper">
            <input 
              v-model="searchQuery" 
              @keyup.enter="fetchUsers" 
              placeholder="نام، نام کاربری یا موبایل..." 
              class="user-search-input"
            />
            <button @click="fetchUsers" class="user-search-submit search-submit-btn">جستجو</button>
          </div>
        </div>
      </transition>

      <div v-if="isLoading" class="loading-state">
         <LoadingSkeleton :count="6" :height="70" />
      </div>
      
      <div v-else-if="errorMessage" class="ds-message danger">{{ errorMessage }}</div>
      
      <div v-else class="users-list">
        <div v-if="users.length === 0" class="no-results">
          <div class="no-results-icon">🤷</div>
          <p>کاربری یافت نشد.</p>
        </div>
        
        <div v-for="user in users" :key="user.id" class="user-item" @click="selectUser(user)">
          <div class="user-main-info">
            <div class="user-avatar">
              {{ getUserDisplayName(user)[0] || '?' }}
            </div>
            <div class="user-details">
              <span class="user-name">
                <CustomerNameWithBadge
                  v-if="user.is_customer || user.customer_management_name"
                  :name="getUserDisplayName(user)"
                  compact
                />
                <template v-else>{{ getUserDisplayName(user) }}</template>
              </span>
              <span
                v-if="user.customer_owner_account_name || user.is_accountant || user.accountant_owner_account_name"
                class="user-relation-tags"
              >
                <span v-if="user.customer_owner_account_name" class="relation-badge relation-badge--owner">
                  سرگروه: {{ user.customer_owner_account_name }}
                </span>
                <span v-if="user.is_accountant" class="relation-badge relation-badge--accountant">
                  حسابدار
                </span>
                <span v-if="user.accountant_owner_account_name" class="relation-badge relation-badge--owner">
                  سرگروه: {{ user.accountant_owner_account_name }}
                </span>
              </span>
              <span class="user-subtext ltr">{{ user.mobile_number }}</span>
            </div>
          </div>
          <div class="user-meta">
            <span class="role-badge" :class="user.role">{{ user.role }}</span>
            <ChevronLeft class="chevron-icon" :size="20" />
          </div>
        </div>
      </div>
    </div>

  </div>
</template>

<style scoped>
.user-manager {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.user-search-toolbar {
  margin-bottom: 1rem;
}

.search-toggle-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 0.85rem;
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
  border: 1px solid var(--ds-primary-100);
  border-radius: var(--ds-radius-lg);
  font-weight: 700;
  transition: all 0.2s;
  cursor: pointer;
}

.search-toggle-btn.active {
  background: var(--ds-bg-inset);
  color: var(--ds-text-secondary);
  border-color: var(--ds-border-light);
}

.search-toggle-btn:active {
  transform: scale(0.98);
}

.search-box {
  margin-bottom: 1.5rem;
  overflow: hidden;
}

.search-input-wrapper {
  display: flex;
  gap: 0.5rem;
}

.search-submit-btn {
  flex-shrink: 0;
  padding: 0 1.25rem;
}

.loading-state {
  padding: 0.5rem 0;
}

.users-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.no-results {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--ds-text-placeholder);
}

.no-results-icon {
  font-size: 2.5rem;
  margin-bottom: 1rem;
}

.user-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  background: var(--ds-bg-card);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  cursor: pointer;
  transition: all 0.2s;
}

.user-item:hover {
  background: var(--ds-bg-hover);
  border-color: var(--ds-primary-300);
}

.user-item:active {
  transform: scale(0.98);
}

.user-main-info {
  display: flex;
  align-items: center;
  gap: 1rem;
  min-width: 0;
}

.user-avatar {
  width: 44px;
  height: 44px;
  background: var(--ds-gradient-primary);
  color: white;
  border-radius: var(--ds-radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 1.2rem;
  flex-shrink: 0;
}

.user-details {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.user-name {
  font-weight: 700;
  color: var(--ds-text-primary);
  font-size: 0.95rem;
}

.user-relation-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-top: 0.25rem;
}

.relation-badge {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 0.62rem;
  font-weight: 900;
  line-height: 1;
  white-space: nowrap;
}

.relation-badge--owner {
  border: 1px solid rgba(37, 99, 235, 0.22);
  background: rgba(37, 99, 235, 0.09);
  color: #1d4ed8;
}

.relation-badge--accountant {
  border: 1px solid rgba(124, 58, 237, 0.2);
  background: rgba(124, 58, 237, 0.09);
  color: #6d28d9;
}

.user-subtext {
  font-size: 0.75rem;
  color: var(--ds-text-placeholder);
  margin-top: 0.1rem;
  font-family: var(--ds-font-mono);
}

.user-meta {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.role-badge {
  padding: 0.25rem 0.6rem;
  border-radius: var(--ds-radius-sm);
  font-size: 0.7rem;
  font-weight: 800;
  background: var(--ds-bg-inset);
  color: var(--ds-text-muted);
}

.role-badge.مدیر { background: #fef3c7; color: #92400e; }
.role-badge.پلیس { background: #ede9fe; color: #5b21b6; }
.role-badge.عادی { background: #d1fae5; color: #065f46; }
.role-badge.تماشا { background: var(--ds-bg-inset); color: var(--ds-text-muted); }

.chevron-icon {
  color: var(--ds-text-disabled);
}

.slide-enter-active, .slide-leave-active {
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  max-height: 100px;
}
.slide-enter-from, .slide-leave-to {
  max-height: 0;
  opacity: 0;
}

.ltr { direction: ltr; }
</style>
