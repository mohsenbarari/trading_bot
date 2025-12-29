<script setup lang="ts">
import { ref, onMounted } from 'vue';
import LoadingSkeleton from './LoadingSkeleton.vue';

const props = defineProps<{
  user: { id: number; account_name: string } | null;
  apiBaseUrl: string;
  jwtToken: string | null;
}>();

const emit = defineEmits(['navigate']);

interface PublicUser {
  id: number;
  account_name: string;
  mobile_number: string;
  address: string;
  created_at_jalali: string;
  trades_count: number;
}

const profileData = ref<PublicUser | null>(null);
const mutualTrades = ref<any[]>([]);
const isLoading = ref(true);
const error = ref('');
const isHistoryLoading = ref(false);
const showHistory = ref(false);

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

async function loadMutualTrades() {
    if (!profileData.value || isHistoryLoading.value) return;
    
    showHistory.value = !showHistory.value;
    if (!showHistory.value || mutualTrades.value.length > 0) return;

    isHistoryLoading.value = true;
    try {
        const response = await fetch(`${props.apiBaseUrl}/api/trades/with/${profileData.value.id}`, {
            headers: { 'Authorization': `Bearer ${props.jwtToken}` }
        });
        if (response.ok) {
            mutualTrades.value = await response.json();
        }
    } catch (e) {
        console.error("Failed to load history", e);
    } finally {
        isHistoryLoading.value = false;
    }
}
</script>

<template>
  <div class="card">
    <div class="header-row">
      <div class="header-spacer"></div>
      <div class="header-title">
         <h2 v-if="profileData">👤 {{ profileData.account_name }}</h2>
         <h2 v-else-if="isLoading" class="skeleton-text-header">
           <!-- Skeleton for Title -->
           <div class="skeleton-box" style="width: 120px; height: 24px;"></div>
         </h2>
         <h2 v-else>👤 پروفایل</h2>
      </div>
      <button class="back-button" @click="$emit('navigate', 'trade', { tab: 'my_trades' })">🔙 بازگشت</button>
    </div>

    <div v-if="isLoading" class="loading-state-skeleton">
       <LoadingSkeleton :count="1" :height="100" /> <!-- Info Section -->
       <div style="margin-top: 12px;"></div>
       <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
         <LoadingSkeleton :count="1" :height="80" />
         <LoadingSkeleton :count="1" :height="80" />
       </div>
       <div style="margin-top: 12px;"></div>
       <LoadingSkeleton :count="1" :height="50" /> <!-- Button -->
    </div>

    <div v-else-if="error" class="error-state">
      <p>❌ {{ error }}</p>
      <button class="retry-btn" @click="$emit('navigate', 'home')">بازگشت به خانه</button>
    </div>

    <div v-else-if="profileData" class="profile-content">
      <!-- Avatar Removed -->
      
      <div class="info-section">
        <div class="info-row">
            <span class="label">📞 موبایل:</span>
            <span class="value">{{ profileData.mobile_number }}</span>
        </div>
        <div class="info-row address-row">
            <span class="label">📍 آدرس:</span>
            <span class="value">{{ profileData.address }}</span>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
            <span class="stat-icon">📅</span>
            <span class="stat-label">عضویت</span>
            <span class="stat-value">{{ profileData.created_at_jalali }}</span>
        </div>
        <div class="stat-card">
            <span class="stat-icon">🤝</span>
            <span class="stat-label">معاملات</span>
            <span class="stat-value">{{ profileData.trades_count }}</span>
        </div>
      </div>

      <div class="history-section">
        <button class="history-toggle-btn" @click="loadMutualTrades">
            📝 تاریخچه معاملات مشترک
            <span v-if="showHistory">🔽</span>
            <span v-else>◀️</span>
        </button>

        <div v-if="showHistory" class="history-list">
            <div v-if="isHistoryLoading">
               <LoadingSkeleton :count="3" :height="60" />
            </div>
            <p v-else-if="mutualTrades.length === 0" class="empty-text">هیچ معامله مشترکی یافت نشد.</p>
            <div v-else v-for="trade in mutualTrades" :key="trade.id" class="mini-trade-card">
                <div class="trade-row">
                    <span class="trade-date">{{ trade.created_at }}</span>
                    <span 
                      class="trade-badge"
                      :class="trade.offer_user_id === profileData.id ? 'sell' : 'buy'"
                    >
                      {{ trade.offer_user_id === profileData.id ? '🔴 فروش به شما' : '🟢 خرید از شما' }}
                    </span>
                </div>
                <div class="trade-details">
                    <span>{{ trade.quantity }} {{ trade.commodity_name }}</span>
                    <span>فی: {{ trade.price.toLocaleString() }}</span>
                </div>
            </div>
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
  gap: 20px;
  align-items: center;
  padding: 10px 0;
}

.info-section {
  width: 100%;
  background: #f8fafc;
  border-radius: 12px;
  padding: 16px;
  border: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
}

.address-row {
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.label {
    color: var(--text-secondary);
}
.value {
    font-weight: 600;
    color: var(--text-color);
}

.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  width: 100%;
}

.stat-card {
  background: white;
  padding: 12px;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border-color);
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}

.stat-icon {
    font-size: 20px;
}

.stat-value {
  font-weight: 700;
  font-size: 15px;
  color: var(--primary-color);
  direction: ltr; /* Fix number direction */
}

/* Updated Header Layout using Flexbox or Grid */
.header-row {
  display: grid;
  grid-template-columns: 80px 1fr 80px; /* Right(Spacer), Center(Title), Left(Back) in RTL */
  align-items: center;
  margin-bottom: 0px;
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 12px;
}



.header-title {
    display: flex;
    justify-content: center;
    white-space: nowrap;
}

/* User name in header */
.header-title h2 {
    font-size: 18px;
    margin: 0;
    font-weight: 700;
}

.back-button {
  justify-self: end; /* Moves to Left end in RTL */
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-secondary);
  padding: 8px 0;
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
}

.back-button:hover {
    color: var(--primary-color);
}


/* History Section - Same as before */
.history-section {
    width: 100%;
    margin-top: 10px;
}

.history-toggle-btn {
    width: 100%;
    background: white;
    border: 1px solid var(--border-color);
    padding: 12px;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 600;
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    color: var(--text-color);
}

.history-list {
    margin-top: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.mini-trade-card {
    background: white;
    border: 1px solid var(--border-color);
    padding: 10px;
    border-radius: 8px;
    font-size: 13px;
}

.trade-row {
    display: flex;
    justify-content: space-between;
    margin-bottom: 6px;
}

.trade-date {
    color: var(--text-secondary);
    font-size: 11px;
}

.trade-badge {
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 11px;
}

.trade-badge.buy { background: #dcfce7; color: #166534; }
.trade-badge.sell { background: #fee2e2; color: #991b1b; }

.trade-details {
    display: flex;
    justify-content: space-between;
    font-weight: 500;
}

.spinner-small {
    width: 20px;
    height: 20px;
    border: 2px solid #f3f3f3;
    border-top: 2px solid var(--primary-color);
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin: 10px auto;
}

.empty-text {
    text-align: center;
    color: var(--text-secondary);
    font-size: 13px;
    padding: 10px;
}
</style>
