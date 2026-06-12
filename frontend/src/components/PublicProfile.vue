<script setup lang="ts">
import { computed, ref, onMounted } from 'vue';
import { ChevronLeft, User as UserIcon, Activity, Pencil } from 'lucide-vue-next';
import LoadingSkeleton from './LoadingSkeleton.vue';
import HelpPopover from './HelpPopover.vue';
import OwnerAccountantManagerModal from './OwnerAccountantManagerModal.vue';
import OwnerCustomerManagerModal from './OwnerCustomerManagerModal.vue';
import UserProfile from './UserProfile.vue';
import JalaliDatePicker from './JalaliDatePicker.vue';
import { isAdminRoleValue, readCachedCurrentUserRole, SUPER_ADMIN_ROLE } from '../utils/adminAccess';
import { resolveTradeParticipantProfileTarget } from '../utils/accountantChatIdentity';
import { apiFetch } from '../utils/auth';
import { buildChatFileUrl, getAvatarInitial, uploadAvatarImage } from '../utils/chatFiles';
import { currentUserSummary } from '../utils/currentUser';
import { formatLastSeenStatus, isUserOnline as isPresenceOnline } from '../utils/userPresence';
import { formatIranDate } from '../utils/iranTime';

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
  last_seen_at?: string | null;
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

const PROJECT_USERS_PAGE_SIZE = 25;

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

interface CommodityFilterOption {
  id: number;
  name: string;
  suggestions: string[];
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
  description?: string | null;
  disabled?: boolean;
}

interface PublicBlockStatus {
  can_block: boolean;
  can_block_now: boolean;
  max_blocked: number;
  current_blocked: number;
  remaining: number;
  reason_code?: string | null;
  reason_message?: string | null;
}

const profileData = ref<PublicUser | null>(null);
const mutualTrades = ref<MutualTradePreview[]>([]);
const isLoading = ref(true);
const error = ref('');
const isHistoryLoading = ref(false);
const historyError = ref('');
const historyFromDate = ref('');
const historyToDate = ref('');
const historyCommodityQuery = ref('');
const historyActivePresetMonths = ref<number | null>(null);
const historyLoadedQueryKey = ref('');
const historyExportingFormat = ref<'excel' | 'pdf' | null>(null);
const historyCommodityOptions = ref<CommodityFilterOption[]>([]);
const historyCommodityOptionsLoading = ref(false);
const historyCommodityOptionsLoaded = ref(false);
const historyCounterpartyUserId = ref<number | null>(null);
const historyCounterpartyOptions = ref<ProjectUserDirectoryEntry[]>([]);
const historyCounterpartyOptionsLoading = ref(false);
const historyCounterpartyOptionsLoaded = ref(false);
const historyCounterpartyError = ref('');
const openSections = ref({
  info: false,
  history: false,
  accountants: false,
  customers: false,
  projectUsers: false,
});
const avatarBusy = ref(false);
const avatarInput = ref<HTMLInputElement | null>(null);
const addressEditing = ref(false);
const addressDraft = ref('');
const addressBusy = ref(false);
const addressError = ref('');
const publicBlockBusy = ref(false);
const publicBlockState = ref<boolean | null>(null);
const publicBlockStatus = ref<PublicBlockStatus | null>(null);
const showAccountantManager = ref(false);
const showCustomerManager = ref(false);
const showAdminUserManager = ref(false);
const adminUserData = ref<any>(null);
const adminUserLoading = ref(false);
const adminUserError = ref('');
const projectUsers = ref<ProjectUserDirectoryEntry[]>([]);
const projectUsersLoading = ref(false);
const projectUsersLoadingMore = ref(false);
const projectUsersError = ref('');
const projectUsersQuery = ref('');
const projectUsersLoaded = ref(false);
const lastLoadedProjectUsersQuery = ref('');
const projectUsersOffset = ref(0);
const projectUsersHasMore = ref(false);
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
const viewerIsCustomer = computed(() => {
  return currentUserSummary.value?.is_customer === true || (isOwnProfile.value && customerProfileContext.value !== null);
});
const viewerIsAccountant = computed(() => currentUserSummary.value?.is_accountant === true);
const profileAvatarUrl = computed(() => buildChatFileUrl(profileData.value?.avatar_file_id ?? null, props.apiBaseUrl));
const profilePresenceStatus = computed(() => formatLastSeenStatus(profileData.value?.last_seen_at, { emptyText: null }));
const profileIsOnline = computed(() => isPresenceOnline(profileData.value?.last_seen_at));
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
const historyPresetOptions = [
  { label: '۱ ماه', months: 1 },
  { label: '۳ ماه', months: 3 },
  { label: '۶ ماه', months: 6 },
  { label: '۱۲ ماه', months: 12 },
];
const hasActiveHistoryFilters = computed(() => {
  return Boolean(
    historyFromDate.value
    || historyToDate.value
    || historyCommodityQuery.value.trim()
    || historyCounterpartyUserId.value,
  );
});
const historyCommoditySuggestions = computed(() => {
  const uniqueSuggestions = new Set<string>();
  historyCommodityOptions.value.forEach((option) => {
    option.suggestions.forEach((suggestion) => {
      if (suggestion) uniqueSuggestions.add(suggestion);
    });
  });
  return Array.from(uniqueSuggestions);
});
const historyCommoditySelectOptions = computed(() => {
  const options = new Map<string, CommodityFilterOption>();

  historyCommodityOptions.value.forEach((option) => {
    options.set(option.name, option);
  });
  mutualTrades.value.forEach((trade, index) => {
    const name = typeof trade.commodity_name === 'string' ? trade.commodity_name.trim() : '';
    if (!name || options.has(name)) return;
    options.set(name, {
      id: -1 - index,
      name,
      suggestions: [name],
    });
  });

  return Array.from(options.values());
});
const historyFilterSummary = computed(() => {
  const parts: string[] = [];
  if (historyFromDate.value || historyToDate.value) {
    const fromLabel = formatHistoryDateLabel(historyFromDate.value);
    const toLabel = formatHistoryDateLabel(historyToDate.value);
    if (fromLabel && toLabel) {
      parts.push(`بازه: ${fromLabel} تا ${toLabel}`);
    } else if (fromLabel) {
      parts.push(`از ${fromLabel}`);
    } else if (toLabel) {
      parts.push(`تا ${toLabel}`);
    }
  }
  const commodityLabel = historyCommodityQuery.value.trim();
  if (commodityLabel) {
    parts.push(`کالا: ${commodityLabel}`);
  }
  if (historyCounterpartyLabel.value) {
    parts.push(`طرف دیگر: ${historyCounterpartyLabel.value}`);
  }
  return parts.join(' | ');
});
const historyCounterpartyLabel = computed(() => {
  if (!historyCounterpartyUserId.value) return '';
  const selected = historyCounterpartyOptions.value.find((user) => Number(user.id) === Number(historyCounterpartyUserId.value));
  return selected ? formatProjectUserLabel(selected) : '';
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
  return !isOwnProfile.value && customerProfileContext.value === null && customerRelations.value.length > 0 && viewerIsSuperAdmin.value;
});
const viewerIsDisplayedOwnerAccountant = computed(() => {
  const viewerUserId = Number(props.viewerUserId);
  if (!Number.isInteger(viewerUserId) || viewerUserId <= 0) {
    return false;
  }

  const resolvedFromAccountantId = Number(profileData.value?.resolved_from_accountant_id);
  if (Number.isInteger(resolvedFromAccountantId) && resolvedFromAccountantId === viewerUserId) {
    return true;
  }

  return accountantRelations.value.some(
    (relation) => Number(relation.accountant_user_id) === viewerUserId,
  );
});
const showProjectUsersSection = computed(() => {
  if (!profileData.value) return false;
  if (customerProfileContext.value !== null) return false;
  return isOwnProfile.value || viewerIsDisplayedOwnerAccountant.value;
});
const showPublicBlockAction = computed(() => {
  return showVisitorSections.value
    && !!profileData.value
    && customerProfileContext.value === null
    && !viewerIsCustomer.value
    && !viewerIsAccountant.value;
});
const publicBlockActionDisabled = computed(() => {
  if (publicBlockState.value === true) return false;
  if (!publicBlockStatus.value) return false;
  return !publicBlockStatus.value.can_block_now;
});
const publicBlockActionDescription = computed(() => {
  if (publicBlockState.value === true) {
    return null;
  }
  return publicBlockStatus.value?.reason_message || null;
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
      description: publicBlockActionDescription.value,
      disabled: publicBlockBusy.value || publicBlockActionDisabled.value,
    });
  }

  return actions;
});
const ownerOnlyActions = computed<ProfileActionCard[]>(() => {
  if (!showOwnerSections.value) return [];
  const actions: ProfileActionCard[] = [
    {
      key: 'settings',
      icon: '⚙️',
      label: 'تنظیمات کاربری',
    },
  ];

  if (customerProfileContext.value === null && !viewerIsCustomer.value) {
    actions.push({
      key: 'add_customer',
      icon: '👥',
      label: 'مشتریان',
    }, {
      key: 'add_accountant',
      icon: '💼',
      label: 'حسابداران',
    });
  }

  return actions;
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
    const response = await apiFetch(`/api/users-public/${props.user.id}`);

    if (!response.ok) throw new Error('خطا در دریافت اطلاعات کاربر');
    
    profileData.value = await response.json();
    addressDraft.value = profileData.value?.address || '';
    if (showPublicBlockAction.value) {
      await refreshPublicBlockUiState();
    } else {
      publicBlockState.value = null;
      publicBlockStatus.value = null;
    }
    if (!isOwnProfile.value && highlightedAccountantUserId.value && accountantRelations.value.length > 0) {
      openSections.value.accountants = true;
    }
    if (!isOwnProfile.value && showCustomerListSection.value) {
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

function toDateInputValue(value: Date) {
  const year = value.getFullYear();
  const month = `${value.getMonth() + 1}`.padStart(2, '0');
  const day = `${value.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatHistoryDateLabel(value: string) {
  if (!value) return '';
  return formatIranDate(`${value}T00:00:00Z`, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  }, 'fa-IR-u-ca-persian') || value;
}

function buildHistoryQueryKey() {
  return JSON.stringify({
    from_date: historyFromDate.value || null,
    to_date: historyToDate.value || null,
    commodity_query: historyCommodityQuery.value.trim() || null,
    counterparty_user_id: historyCounterpartyUserId.value || null,
    self: isOwnProfile.value,
    target_id: profileData.value?.id ?? null,
  });
}

function buildHistoryQueryParams(format?: 'excel' | 'pdf') {
  const params = new URLSearchParams();
  if (format) {
    params.set('format', format);
  }
  if (historyFromDate.value) {
    params.set('from_date', historyFromDate.value);
  }
  if (historyToDate.value) {
    params.set('to_date', historyToDate.value);
  }
  const commodityQuery = historyCommodityQuery.value.trim();
  if (commodityQuery) {
    params.set('commodity_query', commodityQuery);
  }
  return params;
}

function buildTradeHistoryEndpoint(isExport = false) {
  if (isOwnProfile.value && historyCounterpartyUserId.value) {
    const counterpartyPath = `/api/trades/with/${historyCounterpartyUserId.value}`;
    return isExport ? `${counterpartyPath}/export` : counterpartyPath;
  }

  const basePath = isOwnProfile.value
    ? '/api/trades/my'
    : `/api/trades/with/${profileData.value?.id}`;
  return isExport ? `${basePath}/export` : basePath;
}

function validateHistoryFilters() {
  if (historyFromDate.value && historyToDate.value && historyFromDate.value > historyToDate.value) {
    return 'بازه زمانی انتخاب‌شده معتبر نیست.';
  }
  return null;
}

function normalizeCommodityOptions(payload: unknown): CommodityFilterOption[] {
  if (!Array.isArray(payload)) return [];
  return payload
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const commodity = item as {
        id?: unknown;
        name?: unknown;
        aliases?: unknown;
      };
      const id = Number(commodity.id);
      const name = typeof commodity.name === 'string' ? commodity.name.trim() : '';
      if (!Number.isInteger(id) || id <= 0 || !name) {
        return null;
      }
      const aliases = Array.isArray(commodity.aliases)
        ? commodity.aliases
            .map((aliasItem) => {
              if (typeof aliasItem === 'string') return aliasItem.trim();
              if (aliasItem && typeof aliasItem === 'object' && 'alias' in aliasItem) {
                const aliasValue = (aliasItem as { alias?: unknown }).alias;
                return typeof aliasValue === 'string' ? aliasValue.trim() : '';
              }
              return '';
            })
            .filter(Boolean)
        : [];
      return {
        id,
        name,
        suggestions: Array.from(new Set([name, ...aliases])),
      } as CommodityFilterOption;
    })
    .filter((item): item is CommodityFilterOption => item !== null);
}

async function loadHistoryCommodityOptions() {
  if (!props.jwtToken || historyCommodityOptionsLoading.value || historyCommodityOptionsLoaded.value) {
    return;
  }

  historyCommodityOptionsLoading.value = true;
  try {
    const response = await apiFetch('/api/commodities/');
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'خطا در دریافت فهرست کالاها'));
    }
    historyCommodityOptions.value = normalizeCommodityOptions(payload);
    historyCommodityOptionsLoaded.value = true;
  } catch (e) {
    console.error('Failed to load commodity suggestions', e);
  } finally {
    historyCommodityOptionsLoading.value = false;
  }
}

function formatProjectUserLabel(user: ProjectUserDirectoryEntry) {
  return user.mobile_number ? `${user.account_name} - ${user.mobile_number}` : user.account_name;
}

async function loadHistoryCounterpartyOptions() {
  const targetProfileUserId = Number(profileData.value?.id);
  if (
    !isOwnProfile.value
    || !Number.isInteger(targetProfileUserId)
    || targetProfileUserId <= 0
    || !props.jwtToken
    || historyCounterpartyOptionsLoading.value
    || historyCounterpartyOptionsLoaded.value
  ) {
    return;
  }

  historyCounterpartyOptionsLoading.value = true;
  historyCounterpartyError.value = '';
  try {
    const params = new URLSearchParams({ limit: '100' });
    const response = await apiFetch(`/api/users-public/${targetProfileUserId}/project-users?${params.toString()}`);
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'خطا در دریافت اعضای پروژه'));
    }

    historyCounterpartyOptions.value = (Array.isArray(payload) ? payload as ProjectUserDirectoryEntry[] : [])
      .filter((user) => Number(user.id) !== targetProfileUserId);
    historyCounterpartyOptionsLoaded.value = true;
  } catch (e: any) {
    historyCounterpartyError.value = e?.message || 'خطا در دریافت اعضای پروژه';
  } finally {
    historyCounterpartyOptionsLoading.value = false;
  }
}

function handleHistoryCounterpartyChange(event: Event) {
  const value = (event.target as HTMLSelectElement | null)?.value || '';
  const parsed = Number(value);
  historyCounterpartyUserId.value = Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function handleHistoryDateInput() {
  historyActivePresetMonths.value = null;
}

async function applyHistoryPreset(months: number) {
  const end = new Date();
  const start = new Date(end);
  start.setMonth(start.getMonth() - months);
  historyActivePresetMonths.value = months;
  historyFromDate.value = toDateInputValue(start);
  historyToDate.value = toDateInputValue(end);
  await loadMutualTrades(true);
}

async function resetHistoryFilters() {
  historyActivePresetMonths.value = null;
  historyFromDate.value = '';
  historyToDate.value = '';
  historyCommodityQuery.value = '';
  historyCounterpartyUserId.value = null;
  historyLoadedQueryKey.value = '';
  historyError.value = '';
  if (openSections.value.history) {
    await loadMutualTrades(true);
  }
}

async function applyHistoryFilters() {
  await loadMutualTrades(true);
}

function resolveDownloadFilename(headerValue: string | null, fallback: string) {
  if (!headerValue) return fallback;
  const utf8Match = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1]);
  }
  const quotedMatch = headerValue.match(/filename="?([^";]+)"?/i);
  return quotedMatch?.[1] || fallback;
}

async function downloadHistoryExport(format: 'excel' | 'pdf') {
  if (!profileData.value || !props.jwtToken || historyExportingFormat.value) return;

  const validationError = validateHistoryFilters();
  if (validationError) {
    historyError.value = validationError;
    return;
  }

  historyExportingFormat.value = format;
  historyError.value = '';
  try {
    const params = buildHistoryQueryParams(format);
    const endpoint = `${buildTradeHistoryEndpoint(true)}?${params.toString()}`;
    const response = await apiFetch(endpoint);
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(parseApiError(payload, 'خطا در دریافت خروجی تاریخچه معاملات'));
    }

    const blob = await response.blob();
    const fallbackName = `trade_history.${format === 'excel' ? 'xlsx' : 'pdf'}`;
    const downloadName = resolveDownloadFilename(response.headers.get('content-disposition'), fallbackName);
    const blobUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = blobUrl;
    anchor.download = downloadName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(blobUrl);
  } catch (e: any) {
    historyError.value = e?.message || 'خطا در دریافت خروجی تاریخچه معاملات';
  } finally {
    historyExportingFormat.value = null;
  }
}

function triggerAvatarPicker() {
  if (avatarBusy.value || !isOwnProfile.value) return
  avatarInput.value?.click()
}

async function updateOwnAvatar(avatarFileId: string | null) {
  if (!props.jwtToken) {
    throw new Error('نشست کاربری معتبر نیست.')
  }

  const response = await apiFetch('/api/auth/me/avatar', {
    method: 'PUT',
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

function startAddressEdit() {
  if (!isOwnProfile.value || !profileData.value) return;
  addressDraft.value = profileData.value.address || '';
  addressError.value = '';
  addressEditing.value = true;
}

function cancelAddressEdit() {
  addressDraft.value = profileData.value?.address || '';
  addressError.value = '';
  addressEditing.value = false;
}

async function saveOwnAddress() {
  if (!props.jwtToken || !profileData.value || addressBusy.value) return;

  const normalizedAddress = addressDraft.value.trim();
  if (normalizedAddress.length < 5) {
    addressError.value = 'آدرس باید حداقل ۵ کاراکتر داشته باشد.';
    return;
  }

  addressBusy.value = true;
  addressError.value = '';
  try {
    const response = await apiFetch('/api/auth/me/address', {
      method: 'PUT',
      body: JSON.stringify({ address: normalizedAddress }),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'ذخیره آدرس ناموفق بود.'));
    }

    const savedAddress = typeof (payload as { address?: unknown } | null)?.address === 'string'
      ? (payload as { address: string }).address
      : normalizedAddress;
    profileData.value.address = savedAddress;
    addressDraft.value = savedAddress;
    addressEditing.value = false;
  } catch (e: any) {
    addressError.value = e?.message || 'ذخیره آدرس ناموفق بود.';
  } finally {
    addressBusy.value = false;
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

async function toggleHistory() {
  openSections.value.history = !openSections.value.history;
  if (!openSections.value.history) return;
  await loadMutualTrades();
  void loadHistoryCommodityOptions();
  void loadHistoryCounterpartyOptions();
}

async function loadMutualTrades(force = false) {
    if (!profileData.value || isHistoryLoading.value) return;

  const validationError = validateHistoryFilters();
  if (validationError) {
    historyError.value = validationError;
    mutualTrades.value = [];
    return;
  }

  const queryKey = buildHistoryQueryKey();
  if (!force && historyLoadedQueryKey.value === queryKey) {
    return;
  }

  isHistoryLoading.value = true;
  historyError.value = '';
  try {
    const params = buildHistoryQueryParams();
    const endpoint = `${buildTradeHistoryEndpoint()}${params.toString() ? `?${params.toString()}` : ''}`;
    const response = await apiFetch(endpoint);
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'خطا در دریافت تاریخچه معاملات'));
    }
    mutualTrades.value = Array.isArray(payload) ? payload as MutualTradePreview[] : [];
    historyLoadedQueryKey.value = queryKey;
  } catch (e: any) {
    console.error("Failed to load history", e);
    historyError.value = e?.message || 'خطا در دریافت تاریخچه معاملات';
    mutualTrades.value = [];
  } finally {
    isHistoryLoading.value = false;
  }
}

async function loadProjectUsersDirectory(force = false) {
  const targetProfileUserId = Number(profileData.value?.id);
  const normalizedQuery = projectUsersQuery.value.trim();
  const isLoadMore = !force && projectUsersOffset.value > 0;
  if (
    !showProjectUsersSection.value
    || !Number.isInteger(targetProfileUserId)
    || targetProfileUserId <= 0
    || !props.jwtToken
    || projectUsersLoading.value
  ) {
    return;
  }

  if (!force && !isLoadMore && projectUsersLoaded.value && lastLoadedProjectUsersQuery.value === normalizedQuery) {
    return;
  }

  if (isLoadMore) {
    projectUsersLoadingMore.value = true;
  } else {
    projectUsersLoading.value = true;
  }
  projectUsersError.value = '';
  try {
    const params = new URLSearchParams();
    params.set('limit', String(PROJECT_USERS_PAGE_SIZE));
    params.set('offset', String(isLoadMore ? projectUsersOffset.value : 0));
    if (normalizedQuery) {
      params.set('q', normalizedQuery);
    }
    const response = await apiFetch(`/api/users-public/${targetProfileUserId}/project-users?${params.toString()}`);
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'خطا در دریافت لیست کاربران پروژه'));
    }

    const rawRows = Array.isArray(payload) ? payload as ProjectUserDirectoryEntry[] : [];
    const nextRows = rawRows.filter((user) => Number(user.id) !== targetProfileUserId);
    if (isLoadMore) {
      const existingIds = new Set(projectUsers.value.map((user) => user.id));
      projectUsers.value = [
        ...projectUsers.value,
        ...nextRows.filter((user) => !existingIds.has(user.id)),
      ];
    } else {
      projectUsers.value = nextRows;
    }
    projectUsersLoaded.value = true;
    lastLoadedProjectUsersQuery.value = normalizedQuery;
    projectUsersHasMore.value = rawRows.length === PROJECT_USERS_PAGE_SIZE;
    projectUsersOffset.value += rawRows.length;
  } catch (e: any) {
    projectUsersError.value = e?.message || 'خطا در دریافت لیست کاربران پروژه';
  } finally {
    projectUsersLoading.value = false;
    projectUsersLoadingMore.value = false;
  }
}

async function toggleProjectUsersSection() {
  openSections.value.projectUsers = !openSections.value.projectUsers;
  if (!openSections.value.projectUsers) return;
  await loadProjectUsersDirectory();
}

async function submitProjectUsersSearch() {
  projectUsers.value = [];
  projectUsersOffset.value = 0;
  projectUsersHasMore.value = false;
  projectUsersLoaded.value = false;
  await loadProjectUsersDirectory(true);
}

async function loadMoreProjectUsers() {
  if (projectUsersLoading.value || projectUsersLoadingMore.value || !projectUsersHasMore.value) {
    return;
  }
  await loadProjectUsersDirectory();
}

async function openAdminUserManager() {
  if (!profileData.value || !props.jwtToken || adminUserLoading.value) return;

  adminUserLoading.value = true;
  adminUserError.value = '';
  try {
    const response = await apiFetch(`/api/users/${profileData.value.id}`);
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

  const response = await apiFetch(`/api/blocks/check/${profileData.value.id}`);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(parseApiError(payload, 'خطا در بررسی وضعیت بلاک کاربر'));
  }

  return Boolean((payload as { is_blocked_by_me?: unknown } | null)?.is_blocked_by_me);
}

function normalizePublicBlockStatus(payload: Partial<PublicBlockStatus> | null | undefined): PublicBlockStatus {
  const canBlock = Boolean(payload?.can_block);
  const maxBlocked = Number(payload?.max_blocked ?? 0);
  const currentBlocked = Math.max(0, Number(payload?.current_blocked ?? 0));
  const remaining = canBlock ? Math.max(0, Number(payload?.remaining ?? Math.max(0, maxBlocked - currentBlocked))) : 0;
  const canBlockNow = typeof payload?.can_block_now === 'boolean'
    ? payload.can_block_now
    : (canBlock && remaining > 0);

  return {
    can_block: canBlock,
    can_block_now: canBlockNow,
    max_blocked: maxBlocked,
    current_blocked: currentBlocked,
    remaining,
    reason_code: typeof payload?.reason_code === 'string' ? payload.reason_code : null,
    reason_message: typeof payload?.reason_message === 'string' ? payload.reason_message : null,
  };
}

function derivePublicBlockStatus(currentStatus: PublicBlockStatus, nextBlockedCount: number): PublicBlockStatus {
  const currentBlocked = Math.max(0, nextBlockedCount);
  const remaining = currentStatus.can_block ? Math.max(0, currentStatus.max_blocked - currentBlocked) : 0;
  const canBlockNow = currentStatus.can_block && remaining > 0;
  let reasonCode: string | null = null;
  let reasonMessage: string | null = null;

  if (!currentStatus.can_block) {
    reasonCode = 'capability_disabled';
    reasonMessage = 'قابلیت بلاک برای شما غیرفعال است.';
  } else if (!canBlockNow) {
    reasonCode = 'limit_reached';
    reasonMessage = `ظرفیت بلاک شما تکمیل است. حداکثر ${currentStatus.max_blocked} کاربر را می‌توانید بلاک کنید.`;
  }

  return {
    ...currentStatus,
    current_blocked: currentBlocked,
    remaining,
    can_block_now: canBlockNow,
    reason_code: reasonCode,
    reason_message: reasonMessage,
  };
}

async function getPublicBlockStatus() {
  if (!props.jwtToken) {
    throw new Error('نشست کاربری معتبر نیست.');
  }

  const response = await apiFetch('/api/blocks/status');
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(parseApiError(payload, 'خطا در دریافت وضعیت بلاک کاربر'));
  }

  return normalizePublicBlockStatus(payload as Partial<PublicBlockStatus> | null);
}

async function refreshPublicBlockUiState() {
  if (!showPublicBlockAction.value) {
    publicBlockStatus.value = null;
    publicBlockState.value = null;
    return;
  }

  const [statusPayload, blockedByMe] = await Promise.all([
    getPublicBlockStatus(),
    getCurrentPublicBlockState(),
  ]);

  publicBlockStatus.value = statusPayload;
  publicBlockState.value = blockedByMe;
}

async function togglePublicProfileBlock() {
  if (!showPublicBlockAction.value || !profileData.value || !props.jwtToken || publicBlockBusy.value) return;

  publicBlockBusy.value = true;
  try {
    if (publicBlockState.value === null || publicBlockStatus.value === null) {
      await refreshPublicBlockUiState();
    }

    const isBlocked = publicBlockState.value === true;
    if (!isBlocked && publicBlockStatus.value && !publicBlockStatus.value.can_block_now) {
      window.alert(publicBlockStatus.value.reason_message || 'امکان بلاک کاربر در حال حاضر وجود ندارد.');
      return;
    }

    const shouldUnblock = isBlocked;
    const confirmed = window.confirm(
      shouldUnblock
        ? `آیا از رفع بلاک کاربر ${profileData.value.account_name} اطمینان دارید؟`
        : `آیا از بلاک کاربر ${profileData.value.account_name} اطمینان دارید؟`
    );
    if (!confirmed) {
      return;
    }

    const response = await apiFetch(`/api/blocks/${profileData.value.id}`, {
      method: shouldUnblock ? 'DELETE' : 'POST',
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parseApiError(payload, shouldUnblock ? 'رفع بلاک کاربر ناموفق بود.' : 'بلاک کاربر ناموفق بود.'));
    }

    publicBlockState.value = !shouldUnblock;
    if (publicBlockStatus.value) {
      const nextBlockedCount = publicBlockStatus.value.current_blocked + (shouldUnblock ? -1 : 1);
      publicBlockStatus.value = derivePublicBlockStatus(publicBlockStatus.value, nextBlockedCount);
    }
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

function getActionButtonClass(action: ProfileActionCard) {
  if (action.key === 'message') return 'message-menu-btn';
  if (action.key === 'block_toggle') {
    return publicBlockState.value ? 'unblock-btn' : 'block-btn';
  }
  if (action.key === 'admin_settings' || action.key === 'settings') return 'settings-btn';
  return 'settings-btn';
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
    <div class="header-row profile-header-row">
      <div class="header-spacer">
        <div v-if="profileData" class="profile-avatar-stack profile-avatar-stack--header">
          <button
            v-if="showOwnerSections"
            type="button"
            class="profile-avatar profile-avatar-button profile-avatar-button--editable"
            data-test="profile-avatar-trigger"
            :disabled="avatarBusy"
            :aria-label="profileAvatarUrl ? 'تغییر آواتار' : 'افزودن آواتار'"
            @click="triggerAvatarPicker"
          >
            <img v-if="profileAvatarUrl" :src="profileAvatarUrl" :alt="profileData.account_name" class="profile-avatar-image" />
            <template v-else>{{ getAvatarInitial(profileData.account_name) }}</template>
            <span class="profile-avatar-edit-indicator" aria-hidden="true">
              <Pencil :size="12" />
            </span>
            <div v-if="avatarBusy" class="profile-avatar-busy">در حال ذخیره...</div>
          </button>
          <div v-else class="profile-avatar profile-avatar--readonly" data-test="profile-avatar-readonly">
            <img v-if="profileAvatarUrl" :src="profileAvatarUrl" :alt="profileData.account_name" class="profile-avatar-image" />
            <template v-else>{{ getAvatarInitial(profileData.account_name) }}</template>
          </div>
          <p
            v-if="profilePresenceStatus"
            class="profile-presence-status"
            :class="[
              showOwnerSections ? 'profile-presence-status--own' : 'profile-presence-status--header',
              { online: profileIsOnline },
            ]"
          >
            {{ profilePresenceStatus }}
          </p>
        </div>
      </div>
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

    <div v-else-if="profileData" class="profile-content" :class="{ 'profile-content--own': showOwnerSections }">
      <section class="profile-section shared-profile-section">
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
          <div class="customer-context-title">پروفایل مشتری</div>
          <p class="customer-context-copy">
            <span>{{ customerProfileContext.managementName }}</span>
            <span v-if="customerProfileContext.ownerAccountName"> | مالک: {{ customerProfileContext.ownerAccountName }}</span>
            <span> | {{ getCustomerTierLabel(customerProfileContext.customerTier) }}</span>
          </p>
        </div>

        <div class="ds-accordion mt-4 card-with-help" :class="{ open: openSections.info }">
          <div class="ds-accordion-header" @click="openSections.info = !openSections.info">
            <div class="ds-accordion-header-info">
              <UserIcon :size="18" class="text-amber-600" />
              <h2>اطلاعات شخصی</h2>
            </div>
            <div class="accordion-header-actions">
              <ChevronLeft :size="20" class="ds-accordion-icon" />
              <HelpPopover
                button-test="public-profile-info-help"
                note-test="public-profile-info-help-note"
                label="راهنمای اطلاعات پروفایل"
                text="در این بخش شماره تماس و آدرس ثبت‌شده نمایش داده می‌شود. در پروفایل خودتان می‌توانید آدرس را مستقیم از همین قسمت ویرایش کنید."
              />
            </div>
          </div>
          
          <div v-show="openSections.info" class="ds-accordion-body">
            <div class="info-section">
              <div class="info-row">
                  <span class="label">📞 موبایل:</span>
                  <span class="value">{{ profileData.mobile_number }}</span>
              </div>
              <div class="info-row address-row">
                  <span class="label">📍 آدرس:</span>
                  <div v-if="!addressEditing" class="address-display-frame" :class="{ editable: isOwnProfile }">
                    <span class="value address-value">{{ profileData.address }}</span>
                    <button
                      v-if="isOwnProfile"
                      type="button"
                      class="address-edit-trigger"
                      aria-label="ویرایش آدرس"
                      title="ویرایش آدرس"
                      @click.stop="startAddressEdit"
                    >
                      <Pencil :size="16" />
                    </button>
                  </div>
                <form v-else-if="isOwnProfile" class="address-edit-form" @submit.prevent="saveOwnAddress">
                  <textarea
                    v-model="addressDraft"
                    rows="3"
                    class="address-edit-textarea"
                    placeholder="آدرس کامل خود را وارد کنید"
                  />
                  <p v-if="addressError" class="error-text address-error-text">{{ addressError }}</p>
                  <div class="address-edit-actions">
                    <button type="submit" class="history-action-btn primary" :disabled="addressBusy">
                      {{ addressBusy ? 'در حال ذخیره...' : 'ذخیره آدرس' }}
                    </button>
                    <button type="button" class="history-action-btn" :disabled="addressBusy" @click.stop="cancelAddressEdit">
                      انصراف
                    </button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-if="showProjectUsersSection" class="profile-section project-users-section">
        <div class="ds-accordion card-with-help" :class="{ open: openSections.projectUsers }">
          <div class="ds-accordion-header" @click="toggleProjectUsersSection">
            <div class="ds-accordion-header-info">
              <UserIcon :size="18" class="text-amber-600" />
              <h2>لیست همکاران</h2>
            </div>
            <div class="accordion-header-actions">
              <ChevronLeft :size="20" class="ds-accordion-icon" />
              <HelpPopover
                button-test="public-profile-project-users-help"
                note-test="public-profile-project-users-help-note"
                label="راهنمای لیست همکاران"
                text="لیست همکاران، اعضای قابل مشاهده پروژه را نشان می‌دهد. با انتخاب نام هر همکار، پروفایل عمومی همان کاربر باز می‌شود."
              />
            </div>
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
              {{ projectUsersQuery.trim() ? 'همکاری با این جستجو پیدا نشد.' : 'همکاری برای نمایش وجود ندارد.' }}
            </p>
            <template v-else>
              <div class="project-users-list">
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
              <div v-if="projectUsersHasMore" class="project-users-footer">
                <button
                  type="button"
                  class="history-action-btn project-users-load-more"
                  :disabled="projectUsersLoadingMore"
                  @click="loadMoreProjectUsers"
                >
                  {{ projectUsersLoadingMore ? 'در حال دریافت...' : 'نمایش بیشتر' }}
                </button>
              </div>
            </template>
          </div>
        </div>
      </section>

      <section v-if="accountantRelations.length > 0" class="profile-section accountant-relations-section">
        <div class="ds-accordion card-with-help" :class="{ open: openSections.accountants }">
          <div class="ds-accordion-header" @click="openSections.accountants = !openSections.accountants">
            <div class="ds-accordion-header-info">
              <UserIcon :size="18" class="text-amber-600" />
              <h2>{{ showOwnerSections ? 'لیست حسابداران' : 'حسابداران این مالک' }}</h2>
            </div>
            <div class="accordion-header-actions">
              <ChevronLeft :size="20" class="ds-accordion-icon" />
              <HelpPopover
                button-test="public-profile-accountants-help"
                note-test="public-profile-accountants-help-note"
                label="راهنمای لیست حسابداران"
                text="این لیست حسابداران فعال مالک را نشان می‌دهد. عنوان هر ردیف همان نام نمایشی رابطه است و توضیح وظیفه، در صورت ثبت، زیر آن می‌آید."
              />
            </div>
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
        <div class="ds-accordion card-with-help" :class="{ open: openSections.customers }">
          <div class="ds-accordion-header" @click="openSections.customers = !openSections.customers">
            <div class="ds-accordion-header-info">
              <UserIcon :size="18" class="text-amber-600" />
              <h2>مشتریان این مالک</h2>
            </div>
            <div class="accordion-header-actions">
              <ChevronLeft :size="20" class="ds-accordion-icon" />
              <HelpPopover
                button-test="public-profile-customers-help"
                note-test="public-profile-customers-help-note"
                label="راهنمای مشتریان این مالک"
                text="این بخش مشتریان ثبت‌شده زیر این مالک را نشان می‌دهد. نمایش آن به حسابداران همان مالک و مدیر ارشد محدود است."
              />
            </div>
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
        <div class="profile-menu-card card-with-help">
          <HelpPopover
            floating
            button-test="public-profile-visitor-menu-help"
            note-test="public-profile-visitor-menu-help-note"
            label="راهنمای منوی پروفایل عمومی"
            text="اقدام‌های عمومی این پروفایل در این بخش قرار گرفته‌اند تا مسیر پیام، بلاک و عملیات مشابه یکپارچه و قابل پیش‌بینی بماند."
          />
          <div class="profile-menu-heading">اقدام‌های عمومی</div>
          <button
            v-for="action in visitorActionCards"
            :key="action.key"
            class="menu-button"
            :class="[getActionButtonClass(action), { 'menu-button--disabled': Boolean(action.disabled) }]"
            :disabled="Boolean(action.disabled)"
            @click="handleActionClick(action)"
          >
            <span class="menu-button-icon">{{ action.icon }}</span>
            <span class="menu-button-copy">
              <span class="menu-button-label">{{ action.label }}</span>
              <span v-if="action.description" class="menu-button-note">{{ action.description }}</span>
            </span>
          </button>
        </div>
      </section>

      <section v-if="showAdminSections && adminActionCards.length > 0" class="profile-section owner-profile-section">
        <p v-if="adminUserError" class="admin-user-error">{{ adminUserError }}</p>
        <div class="profile-menu-card card-with-help">
          <HelpPopover
            floating
            button-test="public-profile-admin-menu-help"
            note-test="public-profile-admin-menu-help-note"
            label="راهنمای منوی مدیریت پروفایل"
            text="تنظیمات مدیریتی کاربر از بخش عمومی جدا شده‌اند تا عملیات روزمره با ابزارهای مدیریتی مخلوط نشود."
          />
          <div class="profile-menu-heading">مدیریت کاربر</div>
          <button
            v-for="action in adminActionCards"
            :key="action.key"
            class="menu-button"
            :class="getActionButtonClass(action)"
            :disabled="adminUserLoading"
            @click="handleActionClick(action)"
          >
            <span class="menu-button-icon">{{ action.icon }}</span>
            <span class="menu-button-copy">
              <span class="menu-button-label">{{ adminUserLoading ? 'در حال بارگذاری...' : action.label }}</span>
            </span>
          </button>
        </div>
      </section>

      <section class="profile-section">
        <div class="ds-accordion card-with-help" :class="{ open: openSections.history }">
          <div class="ds-accordion-header" @click="toggleHistory">
            <div class="ds-accordion-header-info">
              <Activity :size="18" class="text-amber-600" />
              <h2>{{ tradeHistoryTitle }}</h2>
            </div>
            <div class="accordion-header-actions">
              <ChevronLeft :size="20" class="ds-accordion-icon" />
              <HelpPopover
                button-test="public-profile-history-help"
                note-test="public-profile-history-help-note"
                label="راهنمای تاریخچه معاملات"
                text="در تاریخچه خودتان می‌توانید طرف دیگر معامله را از میان همکاران پروژه انتخاب کنید و کالا را از فهرست کالاهای ثبت‌شده محدود کنید. خروجی‌ها همین فیلترها را رعایت می‌کنند."
              />
            </div>
          </div>

          <div v-show="openSections.history" class="ds-accordion-body">
            <div class="history-toolbar">
              <div class="history-presets">
                <button
                  v-for="preset in historyPresetOptions"
                  :key="preset.months"
                  type="button"
                  class="history-chip"
                  :class="{ active: historyActivePresetMonths === preset.months }"
                  @click.stop="applyHistoryPreset(preset.months)"
                >
                  {{ preset.label }}
                </button>
              </div>

              <div class="history-filter-grid">
                <label class="history-filter-field">
                  <span>از تاریخ</span>
                  <JalaliDatePicker
                    v-model="historyFromDate"
                    value-type="gregorian"
                    placeholder="انتخاب تاریخ شروع"
                    trigger-test-id="history-from-date"
                    @change="handleHistoryDateInput"
                  />
                </label>
                <label class="history-filter-field">
                  <span>تا تاریخ</span>
                  <JalaliDatePicker
                    v-model="historyToDate"
                    value-type="gregorian"
                    placeholder="انتخاب تاریخ پایان"
                    trigger-test-id="history-to-date"
                    @change="handleHistoryDateInput"
                  />
                </label>
                <label class="history-filter-field history-filter-field-wide">
                  <span>کالا</span>
                  <select
                    v-model="historyCommodityQuery"
                    :disabled="historyCommodityOptionsLoading"
                    @focus="loadHistoryCommodityOptions"
                  >
                    <option value="">همه کالاها</option>
                    <option v-for="option in historyCommoditySelectOptions" :key="`${option.id}:${option.name}`" :value="option.name">
                      {{ option.name }}
                    </option>
                  </select>
                </label>
                <label v-if="isOwnProfile" class="history-filter-field history-filter-field-wide">
                  <span>طرف دیگر معامله</span>
                  <select
                    :value="historyCounterpartyUserId ?? ''"
                    :disabled="historyCounterpartyOptionsLoading"
                    @focus="loadHistoryCounterpartyOptions"
                    @change="handleHistoryCounterpartyChange"
                  >
                    <option value="">همه همکاران</option>
                    <option v-for="projectUser in historyCounterpartyOptions" :key="projectUser.id" :value="projectUser.id">
                      {{ formatProjectUserLabel(projectUser) }}
                    </option>
                  </select>
                  <span v-if="historyCounterpartyOptionsLoading" class="history-filter-hint">در حال دریافت لیست همکاران...</span>
                  <span v-else-if="historyCounterpartyError" class="history-filter-hint error">{{ historyCounterpartyError }}</span>
                </label>
              </div>

              <div class="history-filter-actions">
                <button type="button" class="history-action-btn primary" @click.stop="applyHistoryFilters">اعمال فیلتر</button>
                <button
                  type="button"
                  class="history-action-btn"
                  :disabled="!hasActiveHistoryFilters && !historyLoadedQueryKey"
                  @click.stop="resetHistoryFilters"
                >
                  حذف فیلتر
                </button>
                <button
                  type="button"
                  class="history-action-btn"
                  :disabled="isHistoryLoading || historyExportingFormat !== null"
                  @click.stop="downloadHistoryExport('excel')"
                >
                  {{ historyExportingFormat === 'excel' ? 'در حال دانلود...' : 'خروجی Excel' }}
                </button>
                <button
                  type="button"
                  class="history-action-btn"
                  :disabled="isHistoryLoading || historyExportingFormat !== null"
                  @click.stop="downloadHistoryExport('pdf')"
                >
                  {{ historyExportingFormat === 'pdf' ? 'در حال دانلود...' : 'خروجی PDF' }}
                </button>
              </div>

              <p v-if="historyFilterSummary" class="history-filter-summary">{{ historyFilterSummary }}</p>
              <p v-if="historyError" class="error-text history-error-text">{{ historyError }}</p>
            </div>

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
        <div class="profile-menu-card card-with-help">
          <HelpPopover
            floating
            button-test="public-profile-owner-menu-help"
            note-test="public-profile-owner-menu-help-note"
            label="راهنمای منوی مالک"
            text="میانبرهای تنظیمات، مشتریان و حسابداران در همین منو جمع شده‌اند تا ظاهر پروفایل شما با پروفایل عمومی بقیه بخش‌ها هم‌راستا بماند."
          />
          <div class="profile-menu-heading">میانبرهای مدیریت پروفایل</div>
          <button
            v-for="action in ownerOnlyActions"
            :key="action.key"
            class="menu-button"
            :class="getActionButtonClass(action)"
            @click="handleActionClick(action)"
          >
            <span class="menu-button-icon">{{ action.icon }}</span>
            <span class="menu-button-copy">
              <span class="menu-button-label">{{ action.label }}</span>
            </span>
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
  gap: 7px;
  align-items: center;
  padding: 4px 0 12rem 0;
}

.profile-content--own {
  gap: 7px;
  padding-top: 4px;
}

.profile-content--own .profile-section {
  width: 100%;
}

.profile-content--own .ds-accordion {
  margin-bottom: 0.35rem;
}

.profile-content--own .ds-accordion-header {
  padding-top: 0.74rem;
  padding-bottom: 0.74rem;
}

.profile-header-row {
  grid-template-columns: 88px 1fr 88px;
  align-items: center;
  padding-bottom: 24px;
}

.profile-avatar-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
}

.profile-avatar-stack--header {
  position: relative;
  width: 88px;
  height: 64px;
  padding-top: 0;
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

.profile-avatar-stack--header .profile-avatar {
  width: 64px;
  height: 64px;
  font-size: 1.35rem;
}

.profile-avatar-button {
  border: 0;
  padding: 0;
  appearance: none;
  cursor: pointer;
}

.profile-avatar-button:disabled {
  cursor: wait;
}

.profile-avatar-button--editable {
  box-shadow: 0 14px 32px rgba(51, 144, 236, 0.18);
}

.profile-avatar--readonly {
  box-shadow: 0 10px 24px rgba(51, 144, 236, 0.14);
}

.profile-avatar-edit-indicator {
  position: absolute;
  left: 50%;
  bottom: 3px;
  width: 18px;
  height: 18px;
  transform: translateX(-50%);
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.86);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.85);
  box-shadow: 0 6px 14px rgba(15, 23, 42, 0.22);
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

.profile-presence-status {
  margin: 6px 0 0;
  font-size: 0.84rem;
  font-weight: 700;
  color: var(--ds-text-secondary);
}

.profile-presence-status--own {
  position: absolute;
  top: 68px;
  left: 0;
  right: 0;
  margin: 0;
  min-height: 1.1rem;
  font-size: 0.76rem;
  line-height: 1.45;
  text-align: center;
}

.profile-presence-status--header {
  margin-top: 0;
  min-height: 1.1rem;
  font-size: 0.76rem;
  line-height: 1.45;
  text-align: center;
}

.profile-presence-status.online {
  color: #f59e0b;
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

.project-users-footer {
  display: flex;
  justify-content: center;
  margin-top: 12px;
}

.project-users-load-more {
  min-width: 160px;
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

.address-value {
  white-space: pre-wrap;
}

.address-display-frame {
  position: relative;
  width: 100%;
  min-height: 44px;
  display: flex;
  align-items: center;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.78);
  padding: 10px 12px;
}

.address-display-frame.editable {
  padding-left: 48px;
}

.address-edit-trigger {
  position: absolute;
  left: 8px;
  top: 50%;
  transform: translateY(-50%);
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(15, 118, 110, 0.18);
  border-radius: 999px;
  background: rgba(240, 253, 250, 0.94);
  color: #0f766e;
  cursor: pointer;
  box-shadow: 0 8px 16px rgba(15, 118, 110, 0.08);
  transition: color 0.18s ease, background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.address-edit-trigger:hover,
.address-edit-trigger:focus-visible {
  color: #fff;
  border-color: rgba(15, 118, 110, 0.2);
  background: #0f766e;
  box-shadow: 0 10px 20px rgba(15, 118, 110, 0.16);
  outline: none;
}

.address-edit-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.address-edit-textarea {
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.28);
  border-radius: 14px;
  padding: 10px 12px;
  resize: vertical;
  min-height: 78px;
  background: rgba(255, 255, 255, 0.96);
  color: var(--ds-text-primary);
  font: inherit;
  line-height: 1.8;
}

.address-edit-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.address-error-text {
  margin: 0;
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

.card-with-help {
  position: relative;
  overflow: visible;
}

.card-with-help > .ds-accordion-header {
  gap: 12px;
}

.accordion-header-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.45rem;
  direction: ltr;
  flex: 0 0 auto;
}

.stats-grid.single-column {
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

.stat-icon {
    font-size: 20px;
}

.stat-value {
  font-weight: 700;
  font-size: 15px;
  color: var(--ds-primary-500);
  direction: ltr; /* Fix number direction */
}

.profile-menu-card {
  position: relative;
  padding: 1rem;
  padding-left: 3.8rem;
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 1.25rem;
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.72), rgba(255, 255, 255, 0.96));
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.07);
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.profile-menu-heading {
  margin-bottom: 0.7rem;
  padding-right: 0.2rem;
  font-size: 0.8rem;
  font-weight: 800;
  color: #92400e;
}

.menu-button {
  width: 100%;
  min-height: 3.4rem;
  padding: 0.78rem 0.9rem;
  font-size: 0.85rem;
  font-weight: 850;
  background: rgba(255, 255, 255, 0.94);
  color: #1f2937;
  border: 1px solid rgba(15, 23, 42, 0.07);
  border-radius: 1rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.72rem;
  transition: all 0.2s;
  text-align: right;
  -webkit-tap-highlight-color: transparent;
}

.menu-button:hover {
  border-color: rgba(245, 158, 11, 0.3);
  background: #fffbeb;
}

.menu-button:active {
  transform: scale(0.98);
}

.menu-button:disabled,
.menu-button--disabled {
  cursor: not-allowed;
  opacity: 0.78;
  transform: none;
}

.menu-button-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 0.8rem;
  background: rgba(245, 158, 11, 0.12);
  color: #92400e;
  font-size: 0.95rem;
  line-height: 1;
  flex: 0 0 auto;
}

.menu-button-copy {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.18rem;
}

.menu-button-label {
  flex: 1;
  min-width: 0;
}

.menu-button-note {
  font-size: 0.72rem;
  line-height: 1.55;
  font-weight: 600;
  color: #6b7280;
}

.message-menu-btn {
  background: linear-gradient(135deg, rgba(239, 246, 255, 0.96), rgba(224, 242, 254, 0.98)) !important;
  color: #075985 !important;
  border-color: rgba(14, 165, 233, 0.22) !important;
}

.message-menu-btn .menu-button-icon {
  background: rgba(14, 165, 233, 0.14);
  color: #0369a1;
}

.settings-btn {
  background: linear-gradient(135deg, #fffbeb, #fef3c7) !important;
  color: #92400e !important;
  border-color: rgba(245, 158, 11, 0.2) !important;
}

.block-btn {
  background: #fef2f2 !important;
  color: #991b1b !important;
  border-color: #fecaca !important;
}

.block-btn .menu-button-icon {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
}

.unblock-btn {
  background: #f0fdf4 !important;
  color: #166534 !important;
  border-color: #bbf7d0 !important;
}

.unblock-btn .menu-button-icon {
  background: rgba(34, 197, 94, 0.14);
  color: #166534;
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

.history-toolbar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  margin-bottom: 14px;
  border-radius: 18px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(248, 250, 252, 0.98));
}

.history-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.history-chip {
  border: 1px solid rgba(217, 119, 6, 0.18);
  background: rgba(255, 251, 235, 0.9);
  color: #9a3412;
  border-radius: 999px;
  padding: 8px 12px;
  font-size: 0.82rem;
  font-weight: 700;
  cursor: pointer;
}

.history-chip.active {
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white;
  border-color: transparent;
}

.history-filter-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.history-filter-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.84rem;
  color: var(--ds-text-secondary);
}

.history-filter-field-wide {
  grid-column: span 2;
}

.history-filter-field input,
.history-filter-field select {
  border: 1px solid rgba(148, 163, 184, 0.28);
  border-radius: 12px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.96);
  color: var(--ds-text-primary);
  font: inherit;
}

.history-filter-field select {
  min-height: 44px;
  cursor: pointer;
}

.history-filter-hint {
  color: var(--ds-text-secondary);
  font-size: 0.78rem;
  font-weight: 700;
}

.history-filter-hint.error {
  color: #b91c1c;
}

.history-filter-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.history-action-btn {
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 12px;
  background: white;
  color: var(--ds-text-primary);
  padding: 10px 12px;
  font-weight: 700;
  cursor: pointer;
}

.history-action-btn.primary {
  background: linear-gradient(135deg, #f59e0b, #d97706);
  border-color: transparent;
  color: white;
}

.history-action-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.history-filter-summary {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: 0.82rem;
}

.history-error-text {
  margin: 0;
}

.public-accountant-list,
.public-customer-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.public-accountant-card,
.public-customer-card {
  position: relative;
  overflow: hidden;
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid rgba(14, 165, 233, 0.18);
  background:
    radial-gradient(circle at top left, rgba(20, 184, 166, 0.12), transparent 42%),
    linear-gradient(135deg, rgba(240, 249, 255, 0.98), rgba(236, 253, 245, 0.96));
  box-shadow: 0 10px 24px rgba(15, 118, 110, 0.08);
}

.public-accountant-card.highlighted {
  border-color: rgba(20, 184, 166, 0.44);
  box-shadow: 0 0 0 1px rgba(20, 184, 166, 0.14), 0 16px 34px rgba(15, 118, 110, 0.16);
}

.public-accountant-card-head,
.public-customer-card-head {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.public-accountant-card-head h4,
.public-customer-card-head h4 {
  margin: 0;
  font-size: 1rem;
  color: #0f766e;
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
  color: #0f766e;
}

.public-accountant-handle,
.public-customer-handle {
  margin: 6px 0 0;
  font-size: 0.9rem;
  color: #0284c7;
  direction: ltr;
  text-align: right;
}

.public-accountant-highlight-badge {
  flex-shrink: 0;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(20, 184, 166, 0.14);
  color: #0f766e;
  font-size: 0.78rem;
  font-weight: 700;
}

.public-customer-tier-badge {
  flex-shrink: 0;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(14, 165, 233, 0.14);
  color: #0369a1;
  font-size: 0.78rem;
  font-weight: 700;
}

.public-accountant-duty {
  position: relative;
  z-index: 1;
  margin: 12px 0 0;
  color: #115e59;
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

@media (max-width: 640px) {
  .history-filter-grid {
    grid-template-columns: 1fr;
  }

  .history-filter-field-wide {
    grid-column: span 1;
  }
}
</style>
