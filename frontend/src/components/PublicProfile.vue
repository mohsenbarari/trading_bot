<script setup lang="ts">
import { computed, ref, onMounted } from 'vue';
import { ChevronDown, ChevronLeft, User as UserIcon, Activity, ArrowRight, ChevronRight } from 'lucide-vue-next';
import LoadingSkeleton from './LoadingSkeleton.vue';
import OwnerAccountantManagerModal from './OwnerAccountantManagerModal.vue';
import OwnerCustomerManagerModal from './OwnerCustomerManagerModal.vue';
import UserProfile from './UserProfile.vue';
import { isAdminRoleValue, readCachedCurrentUserRole, SUPER_ADMIN_ROLE } from '../utils/adminAccess';
import { resolveTradeParticipantProfileTarget } from '../utils/accountantChatIdentity';
import { buildChatFileUrl, getAvatarInitial, uploadAvatarImage } from '../utils/chatFiles';

const props = defineProps<{
  user: { id: number; account_name: string } | null;
  viewerUserId?: number | null;
  apiBaseUrl: string;
  jwtToken: string | null;
  highlightAccountantUserId?: number | null;
  highlightAccountantRelationDisplayName?: string | null;
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
  resolved_from_accountant_id?: number | null;
  highlight_accountant_user_id?: number | null;
  highlight_accountant_relation_display_name?: string | null;
  accountant_relations?: PublicAccountantRelationSummary[];
  customer_owner_user_id?: number | null;
  customer_owner_account_name?: string | null;
  customer_management_name?: string | null;
  customer_tier?: 'tier1' | 'tier2' | null;
  customer_relations?: PublicCustomerRelationSummary[];
}

interface PublicAccountantRelationSummary {
  accountant_user_id?: number | null;
  accountant_account_name?: string | null;
  relation_display_name: string;
  duty_description?: string | null;
}

interface PublicCustomerRelationSummary {
  customer_user_id?: number | null;
  customer_account_name?: string | null;
  management_name: string;
  customer_tier: 'tier1' | 'tier2';
}

interface ProjectUserDirectoryEntry {
  id: number;
  account_name: string;
  mobile_number: string;
}

interface MutualTradePreview {
  id: number;
  trade_number: number;
  created_at: string;
  offer_user_id: number;
  offer_user_name?: string;
  offer_user_profile_user_id?: number | null;
  offer_user_profile_account_name?: string | null;
  offer_user_resolved_from_accountant_id?: number | null;
  offer_user_highlight_accountant_user_id?: number | null;
  offer_user_highlight_accountant_relation_display_name?: string | null;
  responder_user_id: number;
  responder_user_name?: string;
  responder_user_profile_user_id?: number | null;
  responder_user_profile_account_name?: string | null;
  responder_user_resolved_from_accountant_id?: number | null;
  responder_user_highlight_accountant_user_id?: number | null;
  responder_user_highlight_accountant_relation_display_name?: string | null;
  counterparty_user_id?: number | null;
  counterparty_name?: string | null;
  counterparty_profile_user_id?: number | null;
  counterparty_profile_account_name?: string | null;
  counterparty_highlight_accountant_user_id?: number | null;
  counterparty_highlight_accountant_relation_display_name?: string | null;
  customer_context_visible?: boolean;
  customer_context_user_id?: number | null;
  customer_context_management_name?: string | null;
  customer_context_tier?: 'tier1' | 'tier2' | null;
  quantity: number;
  commodity_name: string;
  price: number;
  trade_type?: string;
  trade_path_kind?: string | null;
  trade_path_summary?: string | null;
}

interface ProfileStatCard {
  key: string;
  icon: string;
  label: string;
  value: string;
}

interface ProfileActionCard {
  key: 'message' | 'block_toggle' | 'settings' | 'admin_settings' | 'add_customer' | 'add_accountant';
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
  history: false,
  accountants: false,
  customers: false,
  projectUsers: false,
});
const avatarBusy = ref(false);
const avatarInput = ref<HTMLInputElement | null>(null);
const publicBlockBusy = ref(false);
const publicBlockState = ref<boolean | null>(null);
const showAccountantManager = ref(false);
const showCustomerManager = ref(false);
const showAdminUserManager = ref(false);
const adminUserData = ref<any>(null);
const adminUserLoading = ref(false);
const adminUserError = ref('');
const projectUsers = ref<ProjectUserDirectoryEntry[]>([]);
const projectUsersLoading = ref(false);
const projectUsersError = ref('');
const projectUsersQuery = ref('');
const projectUsersLoaded = ref(false);
const lastLoadedProjectUsersQuery = ref('');
const viewerRole = computed(() => readCachedCurrentUserRole());
const isOwnProfile = computed(() => {
  if (!profileData.value) return false;
  return Number(profileData.value.id) === Number(props.viewerUserId);
});
const viewerIsAdmin = computed(() => isAdminRoleValue(viewerRole.value));
const viewerIsSuperAdmin = computed(() => viewerRole.value === SUPER_ADMIN_ROLE);
const showVisitorSections = computed(() => !isOwnProfile.value);
const showOwnerSections = computed(() => isOwnProfile.value);
const showAdminSections = computed(() => !isOwnProfile.value && viewerIsAdmin.value);
const profileAvatarUrl = computed(() => buildChatFileUrl(profileData.value?.avatar_file_id ?? null, props.apiBaseUrl));
const accountantRelations = computed<PublicAccountantRelationSummary[]>(() => {
  return Array.isArray(profileData.value?.accountant_relations) ? profileData.value!.accountant_relations! : [];
});
const customerRelations = computed<PublicCustomerRelationSummary[]>(() => {
  return Array.isArray(profileData.value?.customer_relations) ? profileData.value!.customer_relations! : [];
});
const highlightedAccountantUserId = computed(() => {
  const profileValue = Number(profileData.value?.highlight_accountant_user_id);
  if (Number.isInteger(profileValue) && profileValue > 0) {
    return profileValue;
  }

  const propValue = Number(props.highlightAccountantUserId);
  return Number.isInteger(propValue) && propValue > 0 ? propValue : null;
});
const resolvedAccountantContext = computed(() => {
  if (!highlightedAccountantUserId.value && !profileData.value?.resolved_from_accountant_id) {
    return null;
  }

  const relationDisplayName = profileData.value?.highlight_accountant_relation_display_name?.trim()
    || props.highlightAccountantRelationDisplayName?.trim()
    || null;
  return {
    relationDisplayName,
    accountantUserId: highlightedAccountantUserId.value,
  };
});
const customerProfileContext = computed(() => {
  if (!profileData.value?.customer_management_name || !profileData.value?.customer_tier) {
    return null;
  }

  return {
    ownerAccountName: profileData.value.customer_owner_account_name || null,
    managementName: profileData.value.customer_management_name,
    customerTier: profileData.value.customer_tier,
  };
});
const showTargetTradeHistory = computed(() => {
  if (!profileData.value) return false;
  if (isOwnProfile.value) return true;
  if (viewerIsSuperAdmin.value) return true;
  return customerProfileContext.value !== null;
});
const tradeHistoryPerspectiveUserId = computed(() => {
  if (showTargetTradeHistory.value) {
    const profileUserId = Number(profileData.value?.id);
    return Number.isInteger(profileUserId) && profileUserId > 0 ? profileUserId : null;
  }

  const viewerUserId = Number(props.viewerUserId);
  return Number.isInteger(viewerUserId) && viewerUserId > 0 ? viewerUserId : null;
});
const tradeHistoryTitle = computed(() => {
  if (isOwnProfile.value) {
    return 'تاریخچه معاملات من';
  }
  return showTargetTradeHistory.value ? 'تاریخچه معاملات این کاربر' : 'تاریخچه معاملات مشترک';
});
const tradeHistoryEmptyText = computed(() => {
  if (isOwnProfile.value) {
    return 'هنوز هیچ معامله‌ای انجام نداده‌اید.';
  }
  return showTargetTradeHistory.value ? 'هیچ معامله‌ای برای این کاربر یافت نشد.' : 'هیچ معامله مشترکی یافت نشد.';
});
const targetCustomerHistoryContext = computed(() => {
  if (isOwnProfile.value || !showTargetTradeHistory.value || !customerProfileContext.value) {
    return null;
  }

  return {
    ownerAccountName: customerProfileContext.value.ownerAccountName,
    customerTier: customerProfileContext.value.customerTier,
  };
});
const showCustomerListSection = computed(() => {
  return customerRelations.value.length > 0 && (showOwnerSections.value || viewerIsSuperAdmin.value);
});
const showProjectUsersSection = computed(() => {
  const routeUserId = Number(props.user?.id);
  const viewerUserId = Number(props.viewerUserId);
  if (!Number.isInteger(routeUserId) || routeUserId <= 0) return false;
  if (!Number.isInteger(viewerUserId) || viewerUserId <= 0) return false;
  if (customerProfileContext.value !== null) return false;
  return routeUserId === viewerUserId;
});
const showPublicBlockAction = computed(() => {
  return showVisitorSections.value && !!profileData.value && customerProfileContext.value === null;
});
const publicBlockActionLabel = computed(() => {
  if (publicBlockBusy.value) {
    return 'در حال بررسی...';
  }
  if (publicBlockState.value === true) {
    return 'رفع بلاک';
  }
  if (publicBlockState.value === false) {
    return 'بلاک کاربر';
  }
  return 'بلاک / رفع بلاک';
});
const publicBlockActionIcon = computed(() => {
  return publicBlockState.value ? '🔓' : '⛔';
});
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

  const actions: ProfileActionCard[] = [
    {
      key: 'message',
      icon: '💬',
      label: 'ارسال پیام',
    },
  ];

  if (showPublicBlockAction.value) {
    actions.push({
      key: 'block_toggle',
      icon: publicBlockActionIcon.value,
      label: publicBlockActionLabel.value,
    });
  }

  return actions;
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
const adminActionCards = computed<ProfileActionCard[]>(() => {
  if (!showAdminSections.value) return [];
  return [
    {
      key: 'admin_settings',
      icon: '🛠️',
      label: 'تنظیمات کاربر',
    },
  ];
});

async function loadProfile() {
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
    if (highlightedAccountantUserId.value && accountantRelations.value.length > 0) {
      openSections.value.accountants = true;
    }
    if (showCustomerListSection.value) {
      openSections.value.customers = true;
    }
  } catch (e: any) {
    error.value = e.message || 'خطا در برقراری ارتباط';
  } finally {
    isLoading.value = false;
  }
}

onMounted(loadProfile);

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

async function loadProjectUsersDirectory(force = false) {
  if (!showProjectUsersSection.value || !props.user?.id || !props.jwtToken || projectUsersLoading.value) {
    return;
  }

  const normalizedQuery = projectUsersQuery.value.trim();
  if (!force && projectUsersLoaded.value && lastLoadedProjectUsersQuery.value === normalizedQuery) {
    return;
  }

  projectUsersLoading.value = true;
  projectUsersError.value = '';
  try {
    const params = new URLSearchParams();
    params.set('limit', '25');
    if (normalizedQuery) {
      params.set('q', normalizedQuery);
    }
    const response = await fetch(`${props.apiBaseUrl}/api/users-public/${props.user.id}/project-users?${params.toString()}`, {
      headers: {
        'Authorization': `Bearer ${props.jwtToken}`,
      },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'خطا در دریافت لیست کاربران پروژه'));
    }

    projectUsers.value = Array.isArray(payload) ? payload as ProjectUserDirectoryEntry[] : [];
    projectUsersLoaded.value = true;
    lastLoadedProjectUsersQuery.value = normalizedQuery;
  } catch (e: any) {
    projectUsersError.value = e?.message || 'خطا در دریافت لیست کاربران پروژه';
  } finally {
    projectUsersLoading.value = false;
  }
}

async function toggleProjectUsersSection() {
  openSections.value.projectUsers = !openSections.value.projectUsers;
  if (!openSections.value.projectUsers) return;
  await loadProjectUsersDirectory();
}

async function submitProjectUsersSearch() {
  projectUsersLoaded.value = false;
  await loadProjectUsersDirectory(true);
}

async function openAdminUserManager() {
  if (!profileData.value || !props.jwtToken || adminUserLoading.value) return;

  adminUserLoading.value = true;
  adminUserError.value = '';
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/users/${profileData.value.id}`, {
      headers: {
        'Authorization': `Bearer ${props.jwtToken}`,
      },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'خطا در دریافت تنظیمات کاربر'));
    }

    adminUserData.value = payload;
    showAdminUserManager.value = true;
  } catch (e: any) {
    adminUserError.value = e?.message || 'خطا در دریافت تنظیمات کاربر';
  } finally {
    adminUserLoading.value = false;
  }
}

async function closeAdminUserManager() {
  showAdminUserManager.value = false;
  adminUserData.value = null;
  await loadProfile();
}

async function closeCustomerManager() {
  showCustomerManager.value = false;
  await loadProfile();
}

async function getCurrentPublicBlockState() {
  if (!profileData.value || !props.jwtToken) {
    throw new Error('نشست کاربری معتبر نیست.');
  }

  const response = await fetch(`${props.apiBaseUrl}/api/blocks/check/${profileData.value.id}`, {
    headers: {
      'Authorization': `Bearer ${props.jwtToken}`,
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(parseApiError(payload, 'خطا در بررسی وضعیت بلاک کاربر'));
  }

  return Boolean((payload as { is_blocked_by_me?: unknown } | null)?.is_blocked_by_me);
}

async function togglePublicProfileBlock() {
  if (!showPublicBlockAction.value || !profileData.value || !props.jwtToken || publicBlockBusy.value) return;

  publicBlockBusy.value = true;
  try {
    const isBlocked = await getCurrentPublicBlockState();
    publicBlockState.value = isBlocked;

    const shouldUnblock = isBlocked;
    const confirmed = window.confirm(
      shouldUnblock
        ? `آیا از رفع بلاک کاربر ${profileData.value.account_name} اطمینان دارید؟`
        : `آیا از بلاک کاربر ${profileData.value.account_name} اطمینان دارید؟`
    );
    if (!confirmed) {
      return;
    }

    const response = await fetch(`${props.apiBaseUrl}/api/blocks/${profileData.value.id}`, {
      method: shouldUnblock ? 'DELETE' : 'POST',
      headers: {
        'Authorization': `Bearer ${props.jwtToken}`,
      },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parseApiError(payload, shouldUnblock ? 'رفع بلاک کاربر ناموفق بود.' : 'بلاک کاربر ناموفق بود.'));
    }

    publicBlockState.value = !shouldUnblock;
    const successMessage = typeof (payload as { message?: unknown } | null)?.message === 'string'
      ? (payload as { message: string }).message
      : (shouldUnblock ? 'رفع بلاک کاربر انجام شد.' : 'کاربر با موفقیت بلاک شد.');
    window.alert(successMessage);
  } catch (e: any) {
    window.alert(e?.message || 'خطا در اجرای عملیات بلاک کاربر.');
  } finally {
    publicBlockBusy.value = false;
  }
}

function handleAdminUserManagerNavigate(view: string) {
  if (view === 'manage_users') {
    void closeAdminUserManager();
    return;
  }

  emit('navigate', view);
}

function handleActionClick(action: ProfileActionCard) {
  if (!profileData.value) return;
  
  if (action.key === 'message') {
    emit('navigate', 'chat', { userId: profileData.value.id, userName: profileData.value.account_name });
  } else if (action.key === 'block_toggle') {
    void togglePublicProfileBlock();
  } else if (action.key === 'settings') {
    emit('navigate', 'settings');
  } else if (action.key === 'admin_settings') {
    void openAdminUserManager();
  } else if (action.key === 'add_customer') {
    showCustomerManager.value = true;
  } else if (action.key === 'add_accountant') {
    showAccountantManager.value = true;
  }
}

function isHighlightedAccountant(relation: PublicAccountantRelationSummary) {
  return Number(relation.accountant_user_id) > 0 && Number(relation.accountant_user_id) === Number(highlightedAccountantUserId.value);
}

function getCustomerTierLabel(tier: PublicCustomerRelationSummary['customer_tier'] | PublicUser['customer_tier']) {
  if (tier === 'tier2') return 'سطح 2';
  if (tier === 'tier1') return 'سطح 1';
  return 'سطح نامشخص';
}

function getTradeBadgeClass(trade: MutualTradePreview) {
  const type = trade.trade_type?.toUpperCase();
  const isPerspectiveResponder = Number(trade.responder_user_id) === Number(tradeHistoryPerspectiveUserId.value);
  
  if (isPerspectiveResponder) {
    return type === 'BUY' ? 'buy' : 'sell';
  } else {
    return type === 'BUY' ? 'sell' : 'buy';
  }
}

function getTradeBadgeLabel(trade: MutualTradePreview) {
  const type = trade.trade_type?.toUpperCase();
  const isPerspectiveResponder = Number(trade.responder_user_id) === Number(tradeHistoryPerspectiveUserId.value);
  
  if (isPerspectiveResponder) {
    return type === 'BUY' ? '🟢 خرید' : '🔴 فروش';
  } else {
    return type === 'BUY' ? '🔴 فروش' : '🟢 خرید';
  }
}

function getTradeCounterpartyLabel(trade: MutualTradePreview) {
  if (typeof trade.counterparty_name === 'string' && trade.counterparty_name.trim()) {
    return trade.counterparty_name;
  }
  return Number(trade.responder_user_id) === Number(profileData.value?.id)
    ? trade.offer_user_name
    : trade.responder_user_name;
}

function getTradeCounterpartyProfileTarget(trade: MutualTradePreview) {
  if (!profileData.value?.id) {
    return null;
  }

  if (
    Number.isInteger(trade.counterparty_profile_user_id)
    && typeof trade.counterparty_profile_account_name === 'string'
    && trade.counterparty_profile_account_name.trim()
  ) {
    return {
      id: Number(trade.counterparty_profile_user_id),
      account_name: trade.counterparty_profile_account_name,
      highlight_accountant_user_id: Number.isInteger(trade.counterparty_highlight_accountant_user_id)
        ? Number(trade.counterparty_highlight_accountant_user_id)
        : null,
      highlight_accountant_relation_display_name:
        typeof trade.counterparty_highlight_accountant_relation_display_name === 'string'
          ? trade.counterparty_highlight_accountant_relation_display_name
          : null,
    };
  }

  return resolveTradeParticipantProfileTarget(
    trade,
    Number(trade.responder_user_id) === Number(profileData.value.id) ? 'offer_user' : 'responder_user',
  );
}

function showTradeCustomerContext(trade: MutualTradePreview) {
  if (!trade.customer_context_visible) {
    return targetCustomerHistoryContext.value !== null;
  }
  return Boolean(trade.customer_context_management_name || trade.customer_context_tier);
}

function getTradeCustomerContextManagementName(trade: MutualTradePreview) {
  return typeof trade.customer_context_management_name === 'string' && trade.customer_context_management_name.trim()
    ? trade.customer_context_management_name
    : null;
}

function getTradeCustomerContextOwnerAccountName(trade: MutualTradePreview) {
  if (trade.customer_context_visible) {
    return null;
  }
  return targetCustomerHistoryContext.value?.ownerAccountName ?? null;
}

function getTradeCustomerContextTier(trade: MutualTradePreview) {
  if (trade.customer_context_tier === 'tier1' || trade.customer_context_tier === 'tier2') {
    return trade.customer_context_tier;
  }
  return targetCustomerHistoryContext.value?.customerTier ?? null;
}

function openTradeCounterpartyProfile(trade: MutualTradePreview) {
  const target = getTradeCounterpartyProfileTarget(trade);
  if (!target) {
    return;
  }

  emit('navigate', 'public_profile', target);
}

function openOwnerCustomerProfile(relation: PublicCustomerRelationSummary) {
  if (!relation.customer_user_id || !relation.customer_account_name) {
    return;
  }

  emit('navigate', 'public_profile', {
    id: relation.customer_user_id,
    account_name: relation.customer_account_name,
  });
}

function openProjectUserProfile(user: ProjectUserDirectoryEntry) {
  emit('navigate', 'public_profile', {
    id: user.id,
    account_name: user.account_name,
  });
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

        <div v-if="resolvedAccountantContext" class="accountant-resolution-banner">
          <div class="accountant-resolution-title">نمایش پروفایل مالک اصلی</div>
          <p class="accountant-resolution-copy">
            این صفحه از مسیر حسابدار باز شده است و اطلاعات مالک اصلی را نشان می‌دهد.
            <span v-if="resolvedAccountantContext.relationDisplayName">
              عنوان این رابطه: «{{ resolvedAccountantContext.relationDisplayName }}»
            </span>
          </p>
        </div>

        <div v-if="customerProfileContext" class="customer-context-banner">
          <div class="customer-context-title">نمای مشتری</div>
          <p class="customer-context-copy">
            این کاربر با عنوان «{{ customerProfileContext.managementName }}»
            <span v-if="customerProfileContext.ownerAccountName">
              زیرمجموعه مالک «{{ customerProfileContext.ownerAccountName }}»
            </span>
            ثبت شده و در {{ getCustomerTierLabel(customerProfileContext.customerTier) }} قرار دارد.
          </p>
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

      <section v-if="showProjectUsersSection" class="profile-section project-users-section">
        <div class="ds-accordion" :class="{ open: openSections.projectUsers }">
          <div class="ds-accordion-header" @click="toggleProjectUsersSection">
            <div class="ds-accordion-header-info">
              <UserIcon :size="18" class="text-amber-600" />
              <h2>کاربران پروژه</h2>
            </div>
            <ChevronLeft :size="20" class="ds-accordion-icon" />
          </div>

          <div v-show="openSections.projectUsers" class="ds-accordion-body">
            <form class="project-users-search" @submit.prevent="submitProjectUsersSearch">
              <input
                v-model="projectUsersQuery"
                type="search"
                class="project-users-search-input"
                placeholder="جستجو با نام کاربری یا شماره تماس"
              />
              <button type="submit" class="project-users-search-submit" :disabled="projectUsersLoading">
                {{ projectUsersLoading ? 'در حال جستجو...' : 'جستجو' }}
              </button>
            </form>

            <p v-if="projectUsersError" class="admin-user-error">{{ projectUsersError }}</p>
            <div v-else-if="projectUsersLoading">
              <LoadingSkeleton :count="3" :height="52" />
            </div>
            <p v-else-if="projectUsers.length === 0" class="empty-text">
              {{ projectUsersQuery.trim() ? 'کاربری با این جستجو پیدا نشد.' : 'کاربر پروژه‌ای برای نمایش وجود ندارد.' }}
            </p>
            <div v-else class="project-users-list">
              <article
                v-for="projectUser in projectUsers"
                :key="projectUser.id"
                class="project-user-card"
              >
                <button
                  type="button"
                  class="profile-link-btn project-user-link-btn"
                  @click.stop="openProjectUserProfile(projectUser)"
                >
                  {{ projectUser.account_name }}
                </button>
                <span class="project-user-mobile">{{ projectUser.mobile_number }}</span>
              </article>
            </div>
          </div>
        </div>
      </section>

      <section v-if="accountantRelations.length > 0" class="profile-section accountant-relations-section">
        <div class="ds-accordion" :class="{ open: openSections.accountants }">
          <div class="ds-accordion-header" @click="openSections.accountants = !openSections.accountants">
            <div class="ds-accordion-header-info">
              <UserIcon :size="18" class="text-amber-600" />
              <h2>حسابداران این مالک</h2>
            </div>
            <ChevronLeft :size="20" class="ds-accordion-icon" />
          </div>

          <div v-show="openSections.accountants" class="ds-accordion-body">
            <div class="public-accountant-list">
              <article
                v-for="relation in accountantRelations"
                :key="`${relation.accountant_user_id || 'relation'}-${relation.relation_display_name}`"
                class="public-accountant-card"
                :class="{ highlighted: isHighlightedAccountant(relation) }"
              >
                <div class="public-accountant-card-head">
                  <div>
                    <h4>{{ relation.relation_display_name }}</h4>
                    <p class="public-accountant-handle">@{{ relation.accountant_account_name || 'unknown' }}</p>
                  </div>
                  <span v-if="isHighlightedAccountant(relation)" class="public-accountant-highlight-badge">مسیر فعلی</span>
                </div>
                <p v-if="relation.duty_description" class="public-accountant-duty">{{ relation.duty_description }}</p>
              </article>
            </div>
          </div>
        </div>
      </section>

      <section v-if="showCustomerListSection" class="profile-section customer-relations-section">
        <div class="ds-accordion" :class="{ open: openSections.customers }">
          <div class="ds-accordion-header" @click="openSections.customers = !openSections.customers">
            <div class="ds-accordion-header-info">
              <UserIcon :size="18" class="text-amber-600" />
              <h2>مشتریان این مالک</h2>
            </div>
            <ChevronLeft :size="20" class="ds-accordion-icon" />
          </div>

          <div v-show="openSections.customers" class="ds-accordion-body">
            <div class="public-customer-list">
              <article
                v-for="relation in customerRelations"
                :key="`${relation.customer_user_id || 'customer'}-${relation.management_name}`"
                class="public-customer-card"
              >
                <div class="public-customer-card-head">
                  <div>
                    <button
                      v-if="relation.customer_user_id && relation.customer_account_name"
                      type="button"
                      class="profile-link-btn public-customer-profile-link customer-profile-link-btn"
                      @click.stop="openOwnerCustomerProfile(relation)"
                    >
                      <span class="public-customer-link-title">{{ relation.management_name }}</span>
                      <span class="public-customer-handle">@{{ relation.customer_account_name }}</span>
                    </button>
                    <template v-else>
                      <h4>{{ relation.management_name }}</h4>
                      <p class="public-customer-handle">@{{ relation.customer_account_name || 'unknown' }}</p>
                    </template>
                  </div>
                  <span class="public-customer-tier-badge">{{ getCustomerTierLabel(relation.customer_tier) }}</span>
                </div>
              </article>
            </div>
          </div>
        </div>
      </section>

      <section v-if="showVisitorSections && visitorActionCards.length > 0" class="profile-section visitor-profile-section">
        <div class="action-grid" :class="{ 'single-column': visitorActionCards.length === 1 }">
          <button
            v-for="action in visitorActionCards"
            :key="action.key"
            class="settings-btn visitor-action-btn"
            @click="handleActionClick(action)"
          >
            <span class="stat-icon">{{ action.icon }}</span>
            <span class="stat-label">{{ action.label }}</span>
          </button>
        </div>
      </section>

      <section v-if="showAdminSections && adminActionCards.length > 0" class="profile-section owner-profile-section">
        <p v-if="adminUserError" class="admin-user-error">{{ adminUserError }}</p>
        <div class="action-grid single-column">
          <button
            v-for="action in adminActionCards"
            :key="action.key"
            class="settings-btn"
            :disabled="adminUserLoading"
            @click="handleActionClick(action)"
          >
            <span class="stat-icon">{{ action.icon }}</span>
            <span class="stat-label">{{ adminUserLoading ? 'در حال بارگذاری...' : action.label }}</span>
          </button>
        </div>
      </section>

      <section class="profile-section">
        <div class="ds-accordion" :class="{ open: openSections.history }">
          <div class="ds-accordion-header" @click="toggleHistory">
            <div class="ds-accordion-header-info">
              <Activity :size="18" class="text-amber-600" />
              <h2>{{ tradeHistoryTitle }}</h2>
            </div>
            <ChevronLeft :size="20" class="ds-accordion-icon" />
          </div>

          <div v-show="openSections.history" class="ds-accordion-body">
            <div v-if="isHistoryLoading">
               <LoadingSkeleton :count="3" :height="60" />
            </div>
            <p v-else-if="mutualTrades.length === 0" class="empty-text">
              {{ tradeHistoryEmptyText }}
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
                      <button
                        v-if="getTradeCounterpartyProfileTarget(trade)"
                        type="button"
                        class="value profile-link-btn"
                        @click.stop="openTradeCounterpartyProfile(trade)"
                      >
                        {{ getTradeCounterpartyLabel(trade) }}
                      </button>
                      <span v-else class="value">{{ getTradeCounterpartyLabel(trade) }}</span>
                    </div>
                    <div v-if="trade.trade_path_summary" class="trade-counterparty">
                      <span class="label">مسیر:</span>
                      <span class="value">{{ trade.trade_path_summary }}</span>
                    </div>
                    <div v-if="showTradeCustomerContext(trade)" class="trade-counterparty">
                      <span class="label">رابطه:</span>
                      <span class="value trade-customer-context-value">
                        <span class="customer-context-badge">مشتری</span>
                        <span v-if="getTradeCustomerContextManagementName(trade)">{{ getTradeCustomerContextManagementName(trade) }}</span>
                        <span v-else-if="getTradeCustomerContextOwnerAccountName(trade)">مالک {{ getTradeCustomerContextOwnerAccountName(trade) }}</span>
                        <span v-if="getTradeCustomerContextTier(trade)">{{ getCustomerTierLabel(getTradeCustomerContextTier(trade)) }}</span>
                      </span>
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

    <OwnerCustomerManagerModal
      v-if="showCustomerManager"
      @close="closeCustomerManager"
    />

    <Teleport to="body">
      <div v-if="showAdminUserManager" class="admin-user-modal-overlay" @click.self="closeAdminUserManager">
        <div class="admin-user-modal">
          <button type="button" class="admin-user-modal-close" @click="closeAdminUserManager" aria-label="بستن">×</button>
          <UserProfile
            v-if="adminUserData"
            :user="adminUserData"
            :isAdminView="true"
            :apiBaseUrl="props.apiBaseUrl"
            :jwtToken="props.jwtToken"
            @navigate="handleAdminUserManagerNavigate"
          />
        </div>
      </div>
    </Teleport>
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

.accountant-resolution-banner {
  width: 100%;
  max-width: min(100%, 520px);
  margin: 0 auto;
  padding: 12px 14px;
  border-radius: 16px;
  border: 1px solid rgba(245, 158, 11, 0.28);
  background:
    linear-gradient(135deg, rgba(255, 251, 235, 0.96), rgba(255, 247, 237, 0.96));
  box-shadow: 0 10px 28px rgba(245, 158, 11, 0.12);
  text-align: right;
}

.accountant-resolution-title {
  margin-bottom: 6px;
  font-size: 0.94rem;
  font-weight: 800;
  color: #b45309;
}

.accountant-resolution-copy {
  margin: 0;
  font-size: 0.86rem;
  line-height: 1.7;
  color: #78350f;
}

.customer-context-banner {
  width: 100%;
  max-width: min(100%, 520px);
  margin: 0 auto;
  padding: 12px 14px;
  border-radius: 16px;
  border: 1px solid rgba(59, 130, 246, 0.22);
  background: linear-gradient(135deg, rgba(239, 246, 255, 0.98), rgba(224, 242, 254, 0.98));
  box-shadow: 0 10px 28px rgba(59, 130, 246, 0.10);
  text-align: right;
}

.customer-context-title {
  margin-bottom: 6px;
  font-size: 0.94rem;
  font-weight: 800;
  color: #1d4ed8;
}

.customer-context-copy {
  margin: 0;
  font-size: 0.86rem;
  line-height: 1.7;
  color: #1e3a8a;
}

.project-users-search {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.project-users-search-input {
  flex: 1 1 220px;
  min-height: 42px;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 184, 0.28);
  background: rgba(255, 255, 255, 0.9);
  padding: 0 14px;
  font-size: 0.92rem;
  color: var(--ds-text-primary);
}

.project-users-search-input:focus {
  outline: none;
  border-color: rgba(245, 158, 11, 0.48);
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.16);
}

.project-users-search-submit {
  border: 0;
  border-radius: 14px;
  min-height: 42px;
  padding: 0 16px;
  background: linear-gradient(135deg, #f59e0b, #f97316);
  color: #fff;
  font-size: 0.88rem;
  font-weight: 700;
  cursor: pointer;
}

.project-users-search-submit:disabled {
  cursor: wait;
  opacity: 0.72;
}

.project-users-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.project-user-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.16);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.96));
}

.project-user-link-btn {
  font-size: 0.94rem;
  font-weight: 800;
}

.project-user-mobile {
  color: var(--ds-text-secondary);
  font-size: 0.85rem;
  direction: ltr;
  text-align: left;
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

.admin-user-error {
  margin: 0 0 10px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(239, 68, 68, 0.08);
  color: var(--ds-danger-600, #dc2626);
  font-size: 0.88rem;
  text-align: center;
}

.admin-user-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 14px;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(10px);
}

.admin-user-modal {
  position: relative;
  width: min(100%, 640px);
  max-height: min(92vh, 860px);
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  border-radius: 22px;
  background: var(--ds-bg-card, #fff);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.24);
}

.admin-user-modal-close {
  position: sticky;
  top: 10px;
  right: calc(100% - 48px);
  z-index: 2;
  width: 38px;
  height: 38px;
  margin: 10px 10px -48px auto;
  border: 0;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.08);
  color: var(--ds-text-primary, #111827);
  font-size: 1.45rem;
  line-height: 1;
  cursor: pointer;
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

.public-accountant-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.public-customer-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.public-accountant-card {
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid rgba(245, 158, 11, 0.16);
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.96), rgba(255, 247, 237, 0.98));
}

.public-customer-card {
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid rgba(59, 130, 246, 0.16);
  background: linear-gradient(180deg, rgba(239, 246, 255, 0.98), rgba(224, 242, 254, 0.98));
}

.public-accountant-card.highlighted {
  border-color: rgba(217, 119, 6, 0.42);
  box-shadow: 0 0 0 1px rgba(217, 119, 6, 0.12), 0 16px 34px rgba(180, 83, 9, 0.16);
}

.public-accountant-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.public-customer-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.public-accountant-card-head h4 {
  margin: 0;
  font-size: 1rem;
  color: #7c2d12;
}

.public-customer-card-head h4 {
  margin: 0;
  font-size: 1rem;
  color: #1d4ed8;
}

.public-customer-profile-link {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  text-align: right;
}

.public-customer-link-title {
  font-size: 1rem;
  font-weight: 800;
  color: #1d4ed8;
}

.public-accountant-handle {
  margin: 6px 0 0;
  font-size: 0.9rem;
  color: #9a3412;
  direction: ltr;
  text-align: right;
}

.public-customer-handle {
  margin: 6px 0 0;
  font-size: 0.9rem;
  color: #2563eb;
  direction: ltr;
  text-align: right;
}

.public-accountant-highlight-badge {
  flex-shrink: 0;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(217, 119, 6, 0.14);
  color: #9a3412;
  font-size: 0.78rem;
  font-weight: 700;
}

.public-customer-tier-badge {
  flex-shrink: 0;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.12);
  color: #1d4ed8;
  font-size: 0.78rem;
  font-weight: 700;
}

.public-accountant-duty {
  margin: 12px 0 0;
  color: #7c2d12;
  line-height: 1.7;
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

.profile-link-btn {
  appearance: none;
  background: none;
  border: 0;
  padding: 0;
  color: #0f766e;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.profile-link-btn:hover,
.profile-link-btn:focus-visible {
  text-decoration: underline;
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


