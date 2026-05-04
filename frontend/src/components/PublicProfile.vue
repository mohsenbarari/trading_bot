<script setup lang="ts">
import { computed, ref, onMounted } from 'vue';
import LoadingSkeleton from './LoadingSkeleton.vue';

const props = defineProps<{
  user: { id: number; account_name: string } | null;
  viewerUserId?: number | null;
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

interface MutualTradePreview {
  id: number;
  created_at: string;
  offer_user_id: number;
  quantity: number;
  commodity_name: string;
  price: number;
}

interface ProfileStatCard {
  key: string;
  icon: string;
  label: string;
  value: string;
}

interface ProfileActionCard {
  key: 'message';
  icon: string;
  label: string;
}

const profileData = ref<PublicUser | null>(null);
const mutualTrades = ref<MutualTradePreview[]>([]);
const isLoading = ref(true);
const error = ref('');
const isHistoryLoading = ref(false);
const showHistory = ref(false);
const isOwnProfile = computed(() => {
  if (!profileData.value) return false;
  return Number(profileData.value.id) === Number(props.viewerUserId);
});
const showVisitorSections = computed(() => !isOwnProfile.value);
const showOwnerSections = computed(() => isOwnProfile.value);
const sharedStatCards = computed<ProfileStatCard[]>(() => {
  if (!profileData.value) return [];

  return [
    {
      key: 'member-since',
      icon: '📅',
      label: 'عضویت',
      value: profileData.value.created_at_jalali,
    },
  ];
});
const visitorActionCards = computed<ProfileActionCard[]>(() => {
  if (!showVisitorSections.value) return [];

  return [
    {
      key: 'message',
      icon: '💬',
      label: 'ارسال پیام',
    },
  ];
});
const ownerOnlyActions = computed<ProfileActionCard[]>(() => {
  // Future owner-specific controls (for example edit/manage actions) should be
  // added here. Keeping the seam explicit prevents visitor-only tools such as
  // block/unblock from leaking into the owner's own view later.
  return [];
});

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

function handleVisitorActionClick(action: ProfileActionCard) {
  if (!profileData.value) return;

  if (action.key === 'message') {
    emit('navigate', 'chat', { userId: profileData.value.id, userName: profileData.value.account_name });
  }
}

function getTradeBadgeClass(trade: MutualTradePreview) {
  return trade.offer_user_id === profileData.value?.id ? 'sell' : 'buy';
}

function getTradeBadgeLabel(trade: MutualTradePreview) {
  return trade.offer_user_id === profileData.value?.id ? '🔴 فروش به شما' : '🟢 خرید از شما';
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
      <button class="back-button" @click="$emit('navigate', 'home')">🔙 بازگشت</button>
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
      <section class="profile-section shared-profile-section">
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

        <div class="stats-grid" :class="{ 'single-column': sharedStatCards.length === 1 }">
          <div v-for="stat in sharedStatCards" :key="stat.key" class="stat-card">
              <span class="stat-icon">{{ stat.icon }}</span>
              <span class="stat-label">{{ stat.label }}</span>
              <span class="stat-value">{{ stat.value }}</span>
          </div>
        </div>
      </section>

      <section v-if="showVisitorSections" class="profile-section visitor-profile-section">
        <div class="action-grid" :class="{ 'single-column': visitorActionCards.length === 1 }">
          <button
            v-for="action in visitorActionCards"
            :key="action.key"
            class="message-btn"
            @click="handleVisitorActionClick(action)"
          >
              <span class="stat-icon">{{ action.icon }}</span>
              <span class="stat-label">{{ action.label }}</span>
          </button>
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
                        :class="getTradeBadgeClass(trade)"
                      >
                        {{ getTradeBadgeLabel(trade) }}
                      </span>
                  </div>
                  <div class="trade-details">
                      <span>{{ trade.quantity }} {{ trade.commodity_name }}</span>
                      <span>فی: {{ trade.price.toLocaleString() }}</span>
                  </div>
              </div>
          </div>
        </div>
      </section>

      <section v-if="showOwnerSections && ownerOnlyActions.length > 0" class="profile-section owner-profile-section">
        <!-- Intentionally empty for now. Future owner-only controls belong here. -->
      </section>
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

.profile-section {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
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

.action-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
  width: 100%;
}

.stats-grid.single-column {
  grid-template-columns: 1fr;
}

.action-grid.single-column {
  grid-template-columns: 1fr;
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

.message-btn {
  background: linear-gradient(135deg, #007aff, #0056b3);
  color: white;
  padding: 12px;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  border: none;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 122, 255, 0.3);
  transition: all 0.2s;
}

.message-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 122, 255, 0.4);
}

.message-btn .stat-icon {
  font-size: 20px;
}

.message-btn .stat-label {
  font-weight: 600;
  font-size: 13px;
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
