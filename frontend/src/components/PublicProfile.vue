<script setup lang="ts">
import { computed, ref, onMounted } from 'vue';
import { ChevronDown, ChevronLeft, User as UserIcon, Activity, ArrowRight, ChevronRight } from 'lucide-vue-next';
import LoadingSkeleton from './LoadingSkeleton.vue';
import OwnerAccountantManagerModal from './OwnerAccountantManagerModal.vue';
import { buildChatFileUrl, getAvatarInitial, uploadAvatarImage } from '../utils/chatFiles';

const props = defineProps<{
  user: { id: number; account_name: string } | null;
  viewerUserId?: number | null;
  apiBaseUrl: string;
  jwtToken: string | null;
  hideBackButton?: boolean;
}>();

const emit = defineEmits(['navigate']);

interface PublicUser {
  id: number;
  account_name: string;
  avatar_file_id?: string | null;
  mobile_number: string;
  address: string;
  created_at_jalali: string;
  trades_count: number;
}

interface MutualTradePreview {
  id: number;
  trade_number: number;
  created_at: string;
  offer_user_id: number;
  offer_user_name?: string;
  responder_user_id: number;
  responder_user_name?: string;
  quantity: number;
  commodity_name: string;
  price: number;
  trade_type?: string;
}

interface ProfileStatCard {
  key: string;
  icon: string;
  label: string;
  value: string;
}

interface ProfileActionCard {
  key: 'message' | 'settings' | 'add_customer' | 'add_accountant';
  icon: string;
  label: string;
}

const profileData = ref<PublicUser | null>(null);
const mutualTrades = ref<MutualTradePreview[]>([]);
const isLoading = ref(true);
const error = ref('');
const isHistoryLoading = ref(false);
const openSections = ref({
  info: false,
  history: false
});
const avatarBusy = ref(false);
const avatarInput = ref<HTMLInputElement | null>(null);
const showAccountantManager = ref(false);
const isOwnProfile = computed(() => {
  if (!profileData.value) return false;
  return Number(profileData.value.id) === Number(props.viewerUserId);
});
const showVisitorSections = computed(() => !isOwnProfile.value);
const showOwnerSections = computed(() => isOwnProfile.value);
const profileAvatarUrl = computed(() => buildChatFileUrl(profileData.value?.avatar_file_id ?? null, props.apiBaseUrl));
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
  if (!showOwnerSections.value) return [];
  return [
    {
      key: 'settings',
      icon: '⚙️',
      label: 'تنظیمات کاربری',
    },
    {
      key: 'add_customer',
      icon: '👥',
      label: 'مشتریان',
    },
    {
      key: 'add_accountant',
      icon: '💼',
      label: 'حسابداران',
    }
  ];
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

function parseApiError(payload: unknown, fallback: string) {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
  }
  return fallback
}

function triggerAvatarPicker() {
  if (avatarBusy.value || !isOwnProfile.value) return
  avatarInput.value?.click()
}

async function updateOwnAvatar(avatarFileId: string | null) {
  if (!props.jwtToken) {
    throw new Error('نشست کاربری معتبر نیست.')
  }

  const response = await fetch(`${props.apiBaseUrl}/api/auth/me/avatar`, {
    method: 'PUT',
    headers: {
      'Authorization': `Bearer ${props.jwtToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ avatar_file_id: avatarFileId }),
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(parseApiError(payload, 'ذخیره آواتار ناموفق بود.'))
  }

  if (profileData.value) {
    profileData.value.avatar_file_id = avatarFileId
  }
}

async function handleAvatarSelected(event: Event) {
  const input = event.target as HTMLInputElement | null
  const file = input?.files?.[0]
  if (!file || !isOwnProfile.value) return

  avatarBusy.value = true
  error.value = ''
  try {
    const uploaded = await uploadAvatarImage(file, props.apiBaseUrl)
    await updateOwnAvatar(uploaded.file_id)
  } catch (e: any) {
    error.value = e?.message || 'آپلود آواتار ناموفق بود.'
  } finally {
    avatarBusy.value = false
    if (input) input.value = ''
  }
}

async function clearAvatar() {
  if (!isOwnProfile.value || avatarBusy.value) return

  avatarBusy.value = true
  error.value = ''
  try {
    await updateOwnAvatar(null)
  } catch (e: any) {
    error.value = e?.message || 'حذف آواتار ناموفق بود.'
  } finally {
    avatarBusy.value = false
  }
}

async function toggleHistory() {
    openSections.value.history = !openSections.value.history;
    if (!openSections.value.history || mutualTrades.value.length > 0) return;
    await loadMutualTrades();
}

async function loadMutualTrades() {
    if (!profileData.value || isHistoryLoading.value) return;

    isHistoryLoading.value = true;
    try {
        const endpoint = isOwnProfile.value 
            ? `${props.apiBaseUrl}/api/trades/my` 
            : `${props.apiBaseUrl}/api/trades/with/${profileData.value.id}`;
            
        const response = await fetch(endpoint, {
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

function handleActionClick(action: ProfileActionCard) {
  if (!profileData.value) return;
  
  if (action.key === 'message') {
    emit('navigate', 'chat', { userId: profileData.value.id, userName: profileData.value.account_name });
  } else if (action.key === 'settings') {
    emit('navigate', 'settings');
  } else if (action.key === 'add_customer') {
    alert('قابلیت افزودن مشتری به زودی اضافه خواهد شد.');
  } else if (action.key === 'add_accountant') {
    showAccountantManager.value = true;
  }
}

function getTradeBadgeClass(trade: MutualTradePreview) {
  const type = trade.trade_type?.toUpperCase();
  // We always show the perspective of the VIEWER (the person logged in)
  const isViewerResponder = Number(trade.responder_user_id) === Number(props.viewerUserId);
  
  if (isViewerResponder) {
    return type === 'BUY' ? 'buy' : 'sell';
  } else {
    return type === 'BUY' ? 'sell' : 'buy';
  }
}

function getTradeBadgeLabel(trade: MutualTradePreview) {
  const type = trade.trade_type?.toUpperCase();
  // We always show the perspective of the VIEWER (the person logged in)
  const isViewerResponder = Number(trade.responder_user_id) === Number(props.viewerUserId);
  
  if (isViewerResponder) {
    return type === 'BUY' ? '🟢 خرید' : '🔴 فروش';
  } else {
    return type === 'BUY' ? '🔴 فروش' : '🟢 خرید';
  }
}
</script>

<template>
  <div class="card">
    <input ref="avatarInput" type="file" accept="image/*" class="hidden-avatar-input" @change="handleAvatarSelected" />
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
      <button class="back-button" @click="$emit('navigate', 'home')"><ChevronLeft :size="24" /></button>
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
        <div class="profile-hero">
          <div class="profile-avatar">
            <img v-if="profileAvatarUrl" :src="profileAvatarUrl" :alt="profileData.account_name" class="profile-avatar-image" />
            <template v-else>{{ getAvatarInitial(profileData.account_name) }}</template>
            <div v-if="avatarBusy" class="profile-avatar-busy">در حال ذخیره...</div>
          </div>
          <div class="profile-hero-copy">
            <h3>{{ profileData.account_name }}</h3>
          </div>
          <div v-if="showOwnerSections" class="profile-avatar-actions">
            <button class="profile-avatar-btn primary" :disabled="avatarBusy" @click="triggerAvatarPicker">
              {{ profileAvatarUrl ? 'تغییر عکس' : 'افزودن عکس' }}
            </button>
            <button v-if="profileAvatarUrl" class="profile-avatar-btn" :disabled="avatarBusy" @click="clearAvatar">
              حذف عکس
            </button>
          </div>
        </div>

        <div class="ds-accordion mt-4" :class="{ open: openSections.info }">
          <div class="ds-accordion-header" @click="openSections.info = !openSections.info">
            <div class="ds-accordion-header-info">
              <UserIcon :size="18" class="text-amber-600" />
              <h2>اطلاعات شخصی و آمار</h2>
            </div>
            <ChevronLeft :size="20" class="ds-accordion-icon" />
          </div>
          
          <div v-show="openSections.info" class="ds-accordion-body">
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
          </div>
        </div>
      </section>

      <section class="profile-section">
        <div class="ds-accordion" :class="{ open: openSections.history }">
          <div class="ds-accordion-header" @click="toggleHistory">
            <div class="ds-accordion-header-info">
              <Activity :size="18" class="text-amber-600" />
              <h2>{{ isOwnProfile ? 'تاریخچه معاملات من' : 'تاریخچه معاملات مشترک' }}</h2>
            </div>
            <ChevronLeft :size="20" class="ds-accordion-icon" />
          </div>

          <div v-show="openSections.history" class="ds-accordion-body">
            <div v-if="isHistoryLoading">
               <LoadingSkeleton :count="3" :height="60" />
            </div>
            <p v-else-if="mutualTrades.length === 0" class="empty-text">
              {{ isOwnProfile ? 'هنوز هیچ معامله‌ای انجام نداده‌اید.' : 'هیچ معامله مشترکی یافت نشد.' }}
            </p>
            <div v-else class="history-list">
                <div v-for="trade in mutualTrades" :key="trade.id" class="mini-trade-card">
                    <div class="trade-row">
                        <span class="trade-date">{{ trade.created_at }}</span>
                        <span class="trade-number">#{{ trade.trade_number }}</span>
                        <span 
                          class="trade-badge"
                          :class="getTradeBadgeClass(trade)"
                        >
                          {{ getTradeBadgeLabel(trade) }}
                        </span>
                    </div>
                    <div class="trade-details">
                        <span class="trade-amount">{{ trade.quantity }} عدد</span>
                        <span class="trade-commodity">{{ trade.commodity_name }}</span>
                        <span class="trade-price">{{ trade.price.toLocaleString() }} ریال</span>
                    </div>
                    <div class="trade-counterparty">
                      <span class="label">طرف معامله:</span>
                      <span class="value">{{ Number(trade.responder_user_id) === Number(profileData?.id) ? trade.offer_user_name : trade.responder_user_name }}</span>
                    </div>
                </div>
            </div>
          </div>
        </div>
      </section>

      <section v-if="showOwnerSections && ownerOnlyActions.length > 0" class="profile-section owner-profile-section">
        <div class="action-grid" :class="{ 'single-column': ownerOnlyActions.length === 1 }">
          <button
            v-for="action in ownerOnlyActions"
            :key="action.key"
            class="settings-btn"
            @click="handleActionClick(action)"
          >
              <span class="stat-icon">{{ action.icon }}</span>
              <span class="stat-label">{{ action.label }}</span>
          </button>
        </div>
      </section>
    </div>

    <OwnerAccountantManagerModal
      v-if="showAccountantManager"
      @close="showAccountantManager = false"
    />
  </div>
</template>

<style scoped>
.loading-state, .error-state {
  text-align: center;
  padding: 40px;
  color: var(--ds-text-secondary);
}

.profile-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
  align-items: center;
  padding: 10px 0 12rem 0;
}

.profile-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  text-align: center;
}

.profile-avatar {
  position: relative;
  width: 92px;
  height: 92px;
  border-radius: 50%;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #3390ec, #0ea5e9 58%, var(--ds-primary-500) 100%);
  color: #fff;
  font-size: 2rem;
  font-weight: 900;
  flex-shrink: 0;
}

.profile-avatar-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.profile-avatar-busy {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.38);
  color: #fff;
  font-size: 0.72rem;
  font-weight: 700;
}

.profile-hero-copy h3 {
  margin: 0;
  font-size: 1.15rem;
  color: var(--ds-text-primary);
}

.profile-avatar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: center;
}

.profile-avatar-btn {
  border: 0;
  border-radius: var(--ds-radius-full);
  min-height: 36px;
  padding: 0 14px;
  font-size: var(--ds-font-sm);
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}

.profile-avatar-btn.primary {
  background: var(--ds-primary-500);
  color: #fff;
}

.profile-avatar-btn.secondary {
  background: var(--ds-bg-hover);
  color: var(--ds-danger-500);
}

.hidden-avatar-input {
  display: none;
}

.profile-section {
  width: 100%;
  max-width: var(--ds-page-max-width);
}

.profile-section.info-section {
  width: 100%;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid var(--ds-border-light);
  gap: 12px;
}

.info-row:last-child {
  border-bottom: none;
}

.address-row {
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.label {
    color: var(--ds-text-secondary);
}
.value {
    font-weight: 600;
    color: var(--ds-text-primary);
}

.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  width: 100%;
}

.action-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  width: 100%;
}

/* If we have 3 buttons, make the first one (Settings) full width 
   and the next two (Customers/Accountants) side-by-side. */
.action-grid > button:first-child:nth-last-child(3) {
  grid-column: span 2;
}

.stats-grid.single-column {
  grid-template-columns: 1fr;
}

.action-grid.single-column {
  grid-template-columns: 1fr;
}

.stat-card {
  background: var(--ds-bg-card);
  padding: 12px;
  border-radius: var(--ds-radius-md);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--ds-border-light);
  box-shadow: var(--ds-shadow-sm);
}

.message-btn {
  background: linear-gradient(135deg, #007aff, #0056b3);
  color: white;
  padding: 12px;
  border-radius: var(--ds-radius-md);
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

.settings-btn {
  background: linear-gradient(135deg, var(--ds-text-secondary), var(--ds-text-primary));
  color: white;
  padding: 12px;
  border-radius: var(--ds-radius-md);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  border: none;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(75, 85, 99, 0.3);
  transition: all 0.2s;
}

.settings-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(75, 85, 99, 0.4);
}

.message-btn .stat-icon, .settings-btn .stat-icon {
  font-size: 20px;
}

.message-btn .stat-label, .settings-btn .stat-label {
  font-weight: 600;
  font-size: 13px;
}

.stat-icon {
    font-size: 20px;
}

.stat-value {
  font-weight: 700;
  font-size: 15px;
  color: var(--ds-primary-500);
  direction: ltr; /* Fix number direction */
}



/* Accordion Styles removed as they are now global ds-accordion */

.mt-4 {
  margin-top: 1rem;
}

.history-list {
    display: flex;
    flex-direction: column;
    gap: var(--ds-section-gap);
}

.mini-trade-card {
    background: var(--ds-bg-inset);
    border: 1px solid var(--ds-border-light);
    padding: var(--ds-card-padding);
    border-radius: var(--ds-radius-md);
    transition: transform 0.15s;
}

.mini-trade-card:active {
  transform: scale(0.98);
}

.trade-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--ds-section-gap);
}

.trade-date {
    color: var(--ds-text-placeholder);
    font-size: var(--ds-font-sm);
    font-weight: 500;
}

.trade-badge {
    padding: 4px 10px;
    border-radius: var(--ds-radius-sm);
    font-weight: 700;
    font-size: var(--ds-font-xs);
    text-transform: uppercase;
}

.trade-badge.buy { 
  background: var(--ds-success-50); 
  color: var(--ds-success-600);
  box-shadow: 0 2px 6px rgba(16, 185, 129, 0.1);
}
.trade-badge.sell { 
  background: var(--ds-danger-50); 
  color: var(--ds-danger-600);
  box-shadow: 0 2px 6px rgba(239, 68, 68, 0.1);
}

.trade-details {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-weight: 600;
    color: var(--ds-text-secondary);
}

.trade-amount {
  font-size: var(--ds-font-md);
}

.trade-commodity {
  font-size: var(--ds-font-base);
  color: var(--ds-text-muted);
}

.trade-price {
  font-size: 0.95rem;
  color: var(--ds-primary-600);
}

.trade-number {
  font-size: var(--ds-font-sm);
  font-weight: 700;
  color: var(--ds-text-muted);
  background: var(--ds-bg-hover);
  padding: 2px 6px;
  border-radius: 4px;
}

.trade-counterparty {
  margin-top: var(--ds-section-gap);
  padding-top: 0.5rem;
  border-top: 1px dashed var(--ds-border-medium);
  display: flex;
  justify-content: space-between;
  font-size: 0.8rem;
}

.trade-counterparty .label {
  color: var(--ds-text-placeholder);
}

.trade-counterparty .value {
  color: var(--ds-text-primary);
  font-weight: 700;
}

.spinner-small {
    width: 20px;
    height: 20px;
    border: 2px solid var(--ds-border-light);
    border-top: 2px solid var(--ds-primary-500);
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin: 10px auto;
}

.empty-text {
    text-align: center;
    color: var(--ds-text-secondary);
    font-size: 13px;
    padding: 10px;
}
</style>


