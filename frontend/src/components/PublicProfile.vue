<script setup lang="ts">
import { ref, onMounted } from 'vue';

const props = defineProps<{
  user: { id: number; account_name: string } | null;
  apiBaseUrl: string;
  jwtToken: string | null;
}>();

const emit = defineEmits(['navigate']);

interface PublicUser {
  id: number;
  account_name: string;
  role: string;
  created_at_jalali: string;
  trades_count: number;
}

const profileData = ref<PublicUser | null>(null);
const isLoading = ref(true);
const error = ref('');

onMounted(async () => {
  if (!props.user?.id || !props.jwtToken) {
    error.value = 'اطلاعات کاربر نامعتبر است.';
    isLoading.value = false;
    return;
  }

  try {
    const response = await fetch(`${props.apiBaseUrl}/api/users-public/${props.user.id}`, {
      headers: {
        'Authorization': `Bearer ${props.jwtToken}`
      }
    });

    if (!response.ok) throw new Error('خطا در دریافت اطلاعات کاربر');
    
    profileData.value = await response.json();
  } catch (e: any) {
    error.value = e.message || 'خطا در برقراری ارتباط';
  } finally {
    isLoading.value = false;
  }
});
</script>

<template>
  <div class="card">
    <div class="header-row">
      <h2>👤 پروفایل عمومی</h2>
      <button class="back-button" @click="$emit('navigate', 'home')">🔙 بازگشت</button>
    </div>

    <div v-if="isLoading" class="loading-state">
      <div class="spinner"></div>
      <p>در حال دریافت اطلاعات...</p>
    </div>

    <div v-else-if="error" class="error-state">
      <p>❌ {{ error }}</p>
      <button class="retry-btn" @click="$emit('navigate', 'home')">بازگشت به خانه</button>
    </div>

    <div v-else-if="profileData" class="profile-content">
      <div class="profile-header">
        <div class="avatar-placeholder">
          {{ profileData.account_name.charAt(0).toUpperCase() }}
        </div>
        <h3>{{ profileData.account_name }}</h3>
        <span class="role-badge">{{ profileData.role }}</span>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
            <span class="stat-icon">📅</span>
            <span class="stat-label">تاریخ عضویت</span>
            <span class="stat-value">{{ profileData.created_at_jalali }}</span>
        </div>
        <div class="stat-card">
            <span class="stat-icon">🤝</span>
            <span class="stat-label">تعداد معاملات</span>
            <span class="stat-value">{{ profileData.trades_count }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.loading-state, .error-state {
  text-align: center;
  padding: 40px;
  color: var(--text-secondary);
}

.profile-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
  align-items: center;
  padding: 20px 0;
}

.profile-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.avatar-placeholder {
  width: 80px;
  height: 80px;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32px;
  font-weight: 700;
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
}

.role-badge {
  background: #f0f9ff;
  color: #0369a1;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
}

.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  width: 100%;
}

.stat-card {
  background: #f8fafc;
  padding: 16px;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--border-color);
}

.stat-icon {
  font-size: 24px;
}

.stat-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.stat-value {
  font-weight: 700;
  font-size: 14px;
  color: var(--text-color);
}

.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 16px;
}

.back-button {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  color: var(--primary-color);
  font-weight: 500;
}
</style>
