<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from 'vue';
import {
  BriefcaseBusiness,
  MessageCircle,
  Settings,
  ShieldBan,
  ShieldCheck,
  Users,
  Pencil,
  Wrench,
} from 'lucide-vue-next';
import LoadingSkeleton from './LoadingSkeleton.vue';
import HelpPopover from './HelpPopover.vue';
import CustomerNameWithBadge from './CustomerNameWithBadge.vue';
import UserProfile from './UserProfile.vue';
import JalaliDatePicker from './JalaliDatePicker.vue';
import {
  AppButton,
  AppConfirmDialog,
  AppEmptyState,
  AppErrorState,
  AppFilterChips,
  AppFormField,
  AppIconButton,
  AppInput,
  AppListItem,
  AppResponsiveDialog,
  AppSectionCard,
  AppSelect,
  AppStatusBadge,
  AppTextarea,
} from './ui';
import {
  ProfileActions,
  ProfileAdminControls,
  ProfileIdentityHeader,
  ProfileRelationshipSection,
  ProfileSummary,
  ProfileTradeHistory,
  type ProfileActionItem,
} from './profile';
import { isAdminRoleValue, readCachedCurrentUserRole } from '../utils/adminAccess';
import { resolveTradeParticipantProfileTarget } from '../utils/accountantChatIdentity';
import { apiFetch } from '../utils/auth';
import { buildChatFileUrl, getAvatarInitial, uploadAvatarImage } from '../utils/chatFiles';
import { currentUserSummary } from '../utils/currentUser';
import { routeRequestJson } from '../utils/routeRequest';
import { formatLastSeenStatus, isUserOnline as isPresenceOnline } from '../utils/userPresence';
import { formatIranDate } from '../utils/iranTime';
import { tradeSettlementLabel, type SettlementType } from '../utils/settlementType';
import { useWebSocket } from '../composables/useWebSocket';
import { WS_NOTIFICATION_EVENTS } from '../types/notifications';

const props = defineProps<{
  user: { id: number; account_name: string } | null;
  viewerUserId?: number | null;
  apiBaseUrl: string;
  jwtToken: string | null;
  initialOwnerWorkspace?: 'customers' | 'accountants' | null;
  hideBackButton?: boolean;
}>();

const emit = defineEmits(['navigate']);
const { on: wsOn, off: wsOff } = useWebSocket();

interface PublicUser {
  id: number;
  account_name: string;
  avatar_file_id?: string | null;
  mobile_number?: string | null;
  address?: string | null;
  last_seen_at?: string | null;
  created_at_jalali?: string | null;
  trades_count?: number | null;
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
  mobile_number?: string | null;
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
  responder_user_id: number;
  responder_user_name?: string;
  responder_user_profile_user_id?: number | null;
  responder_user_profile_account_name?: string | null;
  counterparty_user_id?: number | null;
  counterparty_name?: string | null;
  counterparty_profile_user_id?: number | null;
  counterparty_profile_account_name?: string | null;
  customer_context_visible?: boolean;
  customer_context_user_id?: number | null;
  customer_context_management_name?: string | null;
  customer_context_tier?: 'tier1' | 'tier2' | null;
  quantity: number;
  commodity_name: string;
  price: number;
  trade_type?: string;
  settlement_type?: SettlementType;
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
  label: string;
  value: string;
}

interface ProfileActionCard {
  key: 'message' | 'block_toggle' | 'settings' | 'admin_settings' | 'add_customer' | 'add_accountant';
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

type PublicBlockActionKind = 'block' | 'unblock';

interface PendingPublicBlockAction {
  kind: PublicBlockActionKind;
  targetUserId: number;
}

interface PublicBlockFeedback {
  tone: 'success' | 'error';
  message: string;
}

const profileData = ref<PublicUser | null>(null);
const mutualTrades = ref<MutualTradePreview[]>([]);
const isLoading = ref(true);
const error = ref('');
const isHistoryLoading = ref(false);
const isHistoryLoadingMore = ref(false);
const historyError = ref('');
const historyPaginationError = ref('');
const historyNextCursor = ref<string | null>(null);
const historyHasMore = ref(false);
const historyFromDate = ref('');
const historyToDate = ref('');
const historyCommodityQuery = ref('');
const historyTradeType = ref('');
const historySettlementType = ref('');
const historyActivePresetMonths = ref<number | null>(null);
const historyLoadedQueryKey = ref('');
const historyExportingFormat = ref<'excel' | 'pdf' | null>(null);
const historyCommodityOptions = ref<CommodityFilterOption[]>([]);
const historyCommodityOptionsLoading = ref(false);
const historyCommodityOptionsLoaded = ref(false);
const historyCounterpartyUserId = ref<number | null>(null);
const historyCounterpartyOptions = ref<ProjectUserDirectoryEntry[]>([]);
const historyCounterpartyOptionsLoading = ref(false);
let historyRequestRevision = 0;
let profileRequestRevision = 0;
let projectUsersRequestRevision = 0;
let realtimeTradeRefreshTimer: ReturnType<typeof setTimeout> | null = null;
const historyCounterpartyOptionsLoaded = ref(false);
const historyCounterpartyError = ref('');
const avatarBusy = ref(false);
const avatarInput = ref<HTMLInputElement | null>(null);
const addressEditing = ref(false);
const addressDraft = ref('');
const addressBusy = ref(false);
const addressError = ref('');
const publicBlockBusy = ref(false);
const publicBlockState = ref<boolean | null>(null);
const publicBlockStatus = ref<PublicBlockStatus | null>(null);
const pendingPublicBlockAction = ref<PendingPublicBlockAction | null>(null);
const publicBlockDialogError = ref('');
const publicBlockFeedback = ref<PublicBlockFeedback | null>(null);
const showAdminUserManager = ref(false);
const adminUserData = ref<any>(null);
const adminUserLoading = ref(false);
const adminUserError = ref('');
const appliedInitialOwnerWorkspace = ref<'customers' | 'accountants' | null>(null);
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
const requestedProfileUserId = computed(() => {
  const profileUserId = Number(props.user?.id);
  return Number.isInteger(profileUserId) && profileUserId > 0 ? profileUserId : null;
});
const isRequestedProfileOwnedByViewer = computed(() => {
  const profileUserId = requestedProfileUserId.value;
  const viewerUserId = Number(props.viewerUserId);
  return profileUserId !== null
    && Number.isInteger(viewerUserId)
    && viewerUserId > 0
    && profileUserId === viewerUserId;
});

function asTrimmedString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function asSafeMaskedMobile(value: unknown): string | null {
  const mobile = asTrimmedString(value);
  if (!mobile) return null;

  // Never accept a fully readable number as a "masked" value. The server may
  // use ASCII or Persian digits, but a valid public projection must contain
  // enough masking characters to make the disclosure non-reversible here.
  const isAllowedShape = /^[0-9۰-۹٠-٩+()\-\s*＊•●xX]+$/.test(mobile);
  const maskingCharacters = mobile.match(/[＊*•●xX]/g)?.length || 0;
  const digitCharacters = mobile.match(/[0-9۰-۹٠-٩]/g)?.length || 0;
  return isAllowedShape && maskingCharacters >= 2 && digitCharacters >= 2
    ? mobile
    : null;
}

function normalizePublicProfile(payload: PublicUser, isRequestedSelfProfile: boolean): PublicUser {
  const id = Number(payload.id);
  const commonProfile: PublicUser = {
    id,
    account_name: asTrimmedString(payload.account_name) || '',
    avatar_file_id: asTrimmedString(payload.avatar_file_id),
  };

  // Contact fields are product-visible on every authorized profile. Other
  // owner-only fields still fail closed if an older response is over-broad.
  if (!isRequestedSelfProfile) {
    return {
      ...commonProfile,
      mobile_number: asTrimmedString(payload.mobile_number),
      address: asTrimmedString(payload.address),
    };
  }

  return {
    ...payload,
    ...commonProfile,
    mobile_number: asTrimmedString(payload.mobile_number),
    address: asTrimmedString(payload.address),
    last_seen_at: asTrimmedString(payload.last_seen_at),
    created_at_jalali: asTrimmedString(payload.created_at_jalali),
    trades_count: Number.isFinite(Number(payload.trades_count)) ? Number(payload.trades_count) : null,
  };
}

const isOwnProfile = computed(() => {
  return profileData.value !== null && isRequestedProfileOwnedByViewer.value;
});
const viewerIsAdmin = computed(() => isAdminRoleValue(viewerRole.value));
const showVisitorSections = computed(() => !isOwnProfile.value);
const showOwnerSections = computed(() => isOwnProfile.value);
const showAdminSections = computed(() => !isOwnProfile.value && viewerIsAdmin.value);
const viewerIsCustomer = computed(() => {
  return currentUserSummary.value?.is_customer === true || (isOwnProfile.value && customerProfileContext.value !== null);
});
const viewerIsAccountant = computed(() => currentUserSummary.value?.is_accountant === true);
const profileAvatarUrl = computed(() => buildChatFileUrl(profileData.value?.avatar_file_id ?? null, props.apiBaseUrl));
const profileDisplayName = computed(() => (
  (isOwnProfile.value && asTrimmedString(profileData.value?.customer_management_name))
  || profileData.value?.account_name
  || ''
));
const profilePresenceStatus = computed(() => (
  isOwnProfile.value
    ? formatLastSeenStatus(profileData.value?.last_seen_at, { emptyText: null })
    : null
));
const profileIsOnline = computed(() => (
  isOwnProfile.value && isPresenceOnline(profileData.value?.last_seen_at)
));
const profileMobileNumber = computed(() => {
  return asTrimmedString(profileData.value?.mobile_number) || 'ثبت نشده';
});
function projectUserMobileLabel(user: ProjectUserDirectoryEntry) {
  return asSafeMaskedMobile(user.mobile_number) || '••••••••';
}
const accountantRelations = computed<PublicAccountantRelationSummary[]>(() => {
  return isOwnProfile.value && Array.isArray(profileData.value?.accountant_relations)
    ? profileData.value!.accountant_relations!
    : [];
});
const customerRelations = computed<PublicCustomerRelationSummary[]>(() => {
  return isOwnProfile.value && Array.isArray(profileData.value?.customer_relations)
    ? profileData.value!.customer_relations!
    : [];
});
const customerProfileContext = computed(() => {
  if (!isOwnProfile.value || !profileData.value?.customer_management_name || !profileData.value?.customer_tier) {
    return null;
  }

  return {
    ownerAccountName: profileData.value.customer_owner_account_name || null,
    managementName: profileData.value.customer_management_name,
    customerTier: profileData.value.customer_tier,
  };
});
const showCustomerTierInProfileBanner = computed(() => {
  return false;
});
const shouldHideCustomerTradeRelationshipDetails = computed(() => {
  return viewerIsCustomer.value || (isOwnProfile.value && customerProfileContext.value !== null);
});
const tradeHistoryHelpText = computed(() => {
  if (shouldHideCustomerTradeRelationshipDetails.value) {
    return 'در تاریخچه خودتان می‌توانید بازه زمانی و کالا را از فهرست کالاهای ثبت‌شده محدود کنید. خروجی‌ها همین فیلترها را رعایت می‌کنند.';
  }
  return 'در تاریخچه خودتان می‌توانید طرف دیگر معامله را از میان همکاران پروژه انتخاب کنید و کالا را از فهرست کالاهای ثبت‌شده محدود کنید. خروجی‌ها همین فیلترها را رعایت می‌کنند.';
});
const showTargetTradeHistory = computed(() => {
  return isOwnProfile.value;
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
const historyPresetChipOptions = computed(() => historyPresetOptions.map((preset) => ({
  key: String(preset.months),
  label: preset.label,
})));
const historyTradeTypeOptions = [
  { value: '', label: 'همه جهت‌ها' },
  { value: 'buy', label: 'خرید' },
  { value: 'sell', label: 'فروش' },
];
const historySettlementTypeOptions = [
  { value: '', label: 'همه تسویه‌ها' },
  { value: 'cash', label: 'نقد حاضر' },
  { value: 'tomorrow', label: 'فردایی' },
];
const historyPresetChipValue = computed(() => historyActivePresetMonths.value ? String(historyActivePresetMonths.value) : '');
function getNormalizedHistoryCommodityQuery() {
  return typeof historyCommodityQuery.value === 'string' ? historyCommodityQuery.value.trim() : '';
}
const hasLoadedHistoryOnce = computed(() => Boolean(historyLoadedQueryKey.value));
const hasActiveHistoryFilters = computed(() => {
  return Boolean(
    historyFromDate.value
    || historyToDate.value
    || getNormalizedHistoryCommodityQuery()
    || historyCounterpartyUserId.value
    || historyTradeType.value
    || historySettlementType.value,
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
const historyCommodityAppSelectOptions = computed(() => [
  { value: '', label: 'همه کالاها' },
  ...historyCommoditySelectOptions.value.map((option) => ({
    value: option.name,
    label: option.name,
  })),
]);
const historyCounterpartySelectValue = computed(() => (
  historyCounterpartyUserId.value ? String(historyCounterpartyUserId.value) : ''
));
const historyCounterpartyAppSelectOptions = computed(() => [
  { value: '', label: 'همه همکاران' },
  ...historyCounterpartyOptions.value.map((projectUser) => ({
    value: String(projectUser.id),
    label: formatProjectUserLabel(projectUser),
  })),
]);
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
  const commodityLabel = getNormalizedHistoryCommodityQuery();
  if (commodityLabel) {
    parts.push(`کالا: ${commodityLabel}`);
  }
  if (historyTradeType.value) {
    parts.push(`جهت: ${historyTradeType.value === 'buy' ? 'خرید' : 'فروش'}`);
  }
  if (historySettlementType.value) {
    parts.push(`تسویه: ${historySettlementType.value === 'cash' ? 'نقد حاضر' : 'فردایی'}`);
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
  return false;
});
const showProjectUsersSection = computed(() => {
  if (!profileData.value) return false;
  if (customerProfileContext.value !== null) return false;
  return isOwnProfile.value;
});
const hasLoadedProjectUsersOnce = computed(() => projectUsersLoaded.value || projectUsersLoading.value || projectUsersLoadingMore.value || Boolean(projectUsersError.value));
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
  if (!publicBlockStatus.value) return null;
  if (!publicBlockStatus.value.can_block) {
    return 'قابلیت بلاک برای نقش فعلی شما فعال نیست.';
  }
  if (!publicBlockStatus.value.can_block_now) {
    return 'ظرفیت بلاک شما تکمیل است.';
  }
  return null;
});
const publicBlockActionLabel = computed(() => {
  if (publicBlockBusy.value) {
    return 'در حال ثبت...';
  }
  if (pendingPublicBlockAction.value) {
    return 'در انتظار تأیید...';
  }
  if (publicBlockState.value === true) {
    return 'رفع بلاک';
  }
  if (publicBlockState.value === false) {
    return 'بلاک کاربر';
  }
  return 'بلاک / رفع بلاک';
});
const pendingPublicBlockConfirmation = computed(() => {
  const pendingAction = pendingPublicBlockAction.value;
  if (!pendingAction) return null;

  const isUnblock = pendingAction.kind === 'unblock';
  return {
    title: isUnblock
      ? 'رفع بلاک کاربر؟'
      : 'بلاک کاربر؟',
    message: isUnblock
      ? 'پس از تأیید، وضعیت فقط با پاسخ معتبر سرور به‌روزرسانی می‌شود.'
      : 'با تأیید شما، درخواست بلاک ارسال می‌شود و وضعیت فقط با پاسخ معتبر سرور به‌روزرسانی می‌شود.',
    confirmLabel: isUnblock ? 'تأیید رفع بلاک' : 'تأیید بلاک',
    tone: isUnblock ? 'warning' as const : 'danger' as const,
  };
});
const sharedStatCards = computed<ProfileStatCard[]>(() => {
  if (!isOwnProfile.value || !profileData.value) return [];

  const cards: ProfileStatCard[] = [];
  const membershipDate = asTrimmedString(profileData.value.created_at_jalali);
  if (membershipDate) {
    cards.push({ key: 'member-since', label: 'عضویت', value: membershipDate });
  }
  if (Number.isFinite(Number(profileData.value.trades_count))) {
    cards.push({
      key: 'trade-count',
      label: 'تعداد معاملات',
      value: Number(profileData.value.trades_count).toLocaleString('fa-IR'),
    });
  }
  return cards;
});
const visitorActionCards = computed<ProfileActionCard[]>(() => {
  if (!showVisitorSections.value) return [];

  const actions: ProfileActionCard[] = [
    {
      key: 'message',
      label: 'ارسال پیام',
    },
  ];

  if (showPublicBlockAction.value) {
    actions.push({
      key: 'block_toggle',
      label: publicBlockActionLabel.value,
      description: publicBlockActionDescription.value,
      disabled: publicBlockBusy.value || pendingPublicBlockAction.value !== null || publicBlockActionDisabled.value,
    });
  }

  return actions;
});
const ownerOnlyActions = computed<ProfileActionCard[]>(() => {
  if (!showOwnerSections.value) return [];
  const actions: ProfileActionCard[] = [
    {
      key: 'settings',
      label: 'تنظیمات کاربری',
    },
  ];

  if (customerProfileContext.value === null && !viewerIsCustomer.value) {
    actions.push({
      key: 'add_customer',
      label: 'مشتریان',
    }, {
      key: 'add_accountant',
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
      label: 'تنظیمات کاربر',
    },
  ];
});

function getActionIconComponent(action: ProfileActionCard) {
  if (action.key === 'message') return MessageCircle;
  if (action.key === 'settings') return Settings;
  if (action.key === 'add_customer') return Users;
  if (action.key === 'add_accountant') return BriefcaseBusiness;
  if (action.key === 'admin_settings') return Wrench;
  if (action.key === 'block_toggle') {
    return publicBlockState.value ? ShieldCheck : ShieldBan;
  }
  return Settings;
}

function decorateProfileAction(
  action: ProfileActionCard,
  tone: ProfileActionItem['tone'],
): ProfileActionItem {
  return {
    key: action.key,
    label: action.label,
    description: action.description,
    disabled: action.disabled,
    tone,
    className: getActionButtonClass(action),
    icon: getActionIconComponent(action),
  };
}

const visitorActionItems = computed<ProfileActionItem[]>(() => (
  visitorActionCards.value.map((action) => decorateProfileAction(
    action,
    action.key === 'block_toggle'
      ? (publicBlockState.value ? 'success' : 'danger')
      : action.key === 'message' ? 'info' : 'warning',
  ))
));

const adminActionItems = computed<ProfileActionItem[]>(() => (
  adminActionCards.value.map((action) => decorateProfileAction(action, 'warning'))
));

const ownerActionItems = computed<ProfileActionItem[]>(() => (
  ownerOnlyActions.value.map((action) => decorateProfileAction(action, 'warning'))
));

async function loadProfile() {
  const requestRevision = ++profileRequestRevision;
  isLoading.value = true;
  error.value = '';
  resetPublicBlockActionUi();
  if (!props.user?.id || !props.jwtToken) {
    error.value = 'اطلاعات کاربر نامعتبر است.';
    isLoading.value = false;
    return;
  }

  resetProjectUsersDirectoryState();

  try {
    const payload = await routeRequestJson<PublicUser>(`/api/users-public/${props.user.id}`, {
      errorContext: {
        surface: 'public-profile',
        scope: 'page',
        operation: 'initial-load',
        resourceLabel: 'پروفایل',
        fallbackMessage: 'خطا در دریافت اطلاعات کاربر',
      },
    });
    if (requestRevision !== profileRequestRevision) return;
    if (!payload || typeof payload !== 'object' || Array.isArray(payload) || !Number.isInteger(Number(payload.id))) {
      throw new Error('پاسخ پروفایل معتبر نیست.');
    }
    // The requested route target, rather than a potentially resolved owner id
    // in the payload, defines whether this is the viewer's own profile.
    profileData.value = normalizePublicProfile(payload, isRequestedProfileOwnedByViewer.value);
    addressDraft.value = isOwnProfile.value ? profileData.value.address || '' : '';
    if (showPublicBlockAction.value) {
      await refreshPublicBlockUiState();
    } else {
      publicBlockState.value = null;
      publicBlockStatus.value = null;
    }
    applyInitialOwnerWorkspace();
  } catch (e: any) {
    if (requestRevision !== profileRequestRevision) return;
    error.value = e.message || 'خطا در برقراری ارتباط';
  } finally {
    if (requestRevision === profileRequestRevision) {
      isLoading.value = false;
    }
  }
}

onMounted(loadProfile);

function applyInitialOwnerWorkspace() {
  if (!showOwnerSections.value || customerProfileContext.value !== null || viewerIsCustomer.value) {
    return;
  }
  if (!props.initialOwnerWorkspace || appliedInitialOwnerWorkspace.value === props.initialOwnerWorkspace) {
    return;
  }

  if (props.initialOwnerWorkspace === 'customers') {
    appliedInitialOwnerWorkspace.value = 'customers';
    emit('navigate', 'operations_customers');
  } else if (props.initialOwnerWorkspace === 'accountants') {
    appliedInitialOwnerWorkspace.value = 'accountants';
    emit('navigate', 'operations_accountants');
  }
}

watch(
  () => props.initialOwnerWorkspace,
  () => applyInitialOwnerWorkspace()
);

watch(
  () => profileData.value?.id,
  () => applyInitialOwnerWorkspace()
);

watch(
  () => props.viewerUserId,
  (nextViewerUserId, previousViewerUserId) => {
    if (Number(nextViewerUserId) !== Number(previousViewerUserId)) {
      void loadProfile();
    }
  },
);

watch(
  () => props.user?.id,
  (nextUserId, previousUserId) => {
    if (previousUserId == null) return;
    if (Number(nextUserId) !== Number(previousUserId)) {
      void loadProfile();
    }
  },
);

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
    commodity_query: getNormalizedHistoryCommodityQuery() || null,
    trade_type: historyTradeType.value || null,
    settlement_type: historySettlementType.value || null,
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
  const commodityQuery = getNormalizedHistoryCommodityQuery();
  if (commodityQuery) {
    params.set('commodity_query', commodityQuery);
  }
  if (historyTradeType.value) {
    params.set('trade_type', historyTradeType.value);
  }
  if (historySettlementType.value) {
    params.set('settlement_type', historySettlementType.value);
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

function buildTradeHistoryPageEndpoint() {
  return `${buildTradeHistoryEndpoint()}/page`;
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
  if (!isOwnProfile.value || !props.jwtToken || historyCommodityOptionsLoading.value || historyCommodityOptionsLoaded.value) {
    return;
  }

  historyCommodityOptionsLoading.value = true;
  try {
    const payload = await routeRequestJson<unknown>('/api/commodities/', {
      errorContext: {
        surface: 'public-profile',
        scope: 'section',
        operation: 'initial-load',
        resourceLabel: 'فهرست کالاها',
        fallbackMessage: 'خطا در دریافت فهرست کالاها',
      },
    });
    historyCommodityOptions.value = normalizeCommodityOptions(payload);
    historyCommodityOptionsLoaded.value = true;
  } catch (e) {
    console.error('Failed to load commodity suggestions', e);
  } finally {
    historyCommodityOptionsLoading.value = false;
  }
}

function formatProjectUserLabel(user: ProjectUserDirectoryEntry) {
  return user.account_name;
}

function normalizeProjectUserDirectoryEntry(value: unknown): ProjectUserDirectoryEntry | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  const id = Number(candidate.id);
  const accountName = asTrimmedString(candidate.account_name);
  if (!Number.isInteger(id) || id <= 0 || !accountName) return null;
  return {
    id,
    account_name: accountName,
    mobile_number: asSafeMaskedMobile(candidate.mobile_number),
  };
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
    const payload = await routeRequestJson<unknown>(
      `/api/users-public/${targetProfileUserId}/project-users?${params.toString()}`,
      {
        errorContext: {
          surface: 'public-profile',
          scope: 'section',
          operation: 'initial-load',
          resourceLabel: 'اعضای پروژه',
          fallbackMessage: 'خطا در دریافت اعضای پروژه',
        },
      },
    );

    if (!Array.isArray(payload)) {
      throw new Error('پاسخ اعضای پروژه معتبر نیست.');
    }

    historyCounterpartyOptions.value = payload
      .map(normalizeProjectUserDirectoryEntry)
      .filter((user): user is ProjectUserDirectoryEntry => user !== null && Number(user.id) !== targetProfileUserId);
    historyCounterpartyOptionsLoaded.value = true;
  } catch (e: any) {
    historyCounterpartyError.value = e?.message || 'خطا در دریافت اعضای پروژه';
  } finally {
    historyCounterpartyOptionsLoading.value = false;
  }
}

function setHistoryCounterpartyValue(value: string) {
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
  const hadLoadedHistory = hasLoadedHistoryOnce.value;
  historyActivePresetMonths.value = null;
  historyFromDate.value = '';
  historyToDate.value = '';
  historyCommodityQuery.value = '';
  historyTradeType.value = '';
  historySettlementType.value = '';
  historyCounterpartyUserId.value = null;
  historyError.value = '';
  historyPaginationError.value = '';
  if (hadLoadedHistory) {
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
  if (!isOwnProfile.value || !profileData.value || !props.jwtToken || historyExportingFormat.value) return;

  const validationError = validateHistoryFilters();
  if (validationError) {
    historyError.value = validationError;
    return;
  }

  historyExportingFormat.value = format;
  historyError.value = '';
  try {
    const params = buildHistoryQueryParams(format);
    params.set('_download_ts', Date.now().toString());
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
  if (!isOwnProfile.value || !props.jwtToken || !profileData.value || addressBusy.value) return;

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

function tradeHistoryIdentity(trade: MutualTradePreview) {
  const tradeNumber = Number(trade?.trade_number);
  return Number.isInteger(tradeNumber) && tradeNumber > 0
    ? `number:${tradeNumber}`
    : `id:${String(trade?.id ?? '')}`;
}

function mergeTradeHistoryRows(current: MutualTradePreview[], incoming: MutualTradePreview[]) {
  const seen = new Set<string>();
  return [...current, ...incoming].filter((trade) => {
    const identity = tradeHistoryIdentity(trade);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

async function loadMutualTrades(force = false, append = false) {
  if (!isOwnProfile.value || !profileData.value) return;
  if (append && (isHistoryLoading.value || isHistoryLoadingMore.value)) return;
  if (!append && !force && (isHistoryLoading.value || isHistoryLoadingMore.value)) return;

  const requestRevision = append
    ? historyRequestRevision
    : ++historyRequestRevision;

  const validationError = validateHistoryFilters();
  if (validationError) {
    historyError.value = validationError;
    if (!append) {
      isHistoryLoading.value = false;
      isHistoryLoadingMore.value = false;
    }
    return;
  }

  const queryKey = buildHistoryQueryKey();
  const requestEndpoint = buildTradeHistoryPageEndpoint();
  if (append && historyLoadedQueryKey.value !== queryKey) {
    await loadMutualTrades(true, false);
    return;
  }
  if (append && (!historyHasMore.value || !historyNextCursor.value)) return;
  if (!append && !force && historyLoadedQueryKey.value === queryKey) {
    return;
  }

  if (append) {
    isHistoryLoadingMore.value = true;
    historyPaginationError.value = '';
  } else {
    isHistoryLoading.value = true;
    isHistoryLoadingMore.value = false;
    historyError.value = '';
    historyPaginationError.value = '';
  }
  try {
    const params = buildHistoryQueryParams();
    params.set('limit', '50');
    if (append && historyNextCursor.value) {
      params.set('cursor', historyNextCursor.value);
    }
    const endpoint = `${requestEndpoint}?${params.toString()}`;
    const payload = await routeRequestJson<any>(endpoint, {
      errorContext: {
        surface: 'public-profile',
        scope: 'section',
        operation: append ? 'load-more' : 'refresh',
        resourceLabel: 'تاریخچه معاملات',
        fallbackMessage: 'خطا در دریافت تاریخچه معاملات',
      },
    });
    const requestIsStale = requestRevision !== historyRequestRevision;
    if (requestIsStale) return;
    const items = Array.isArray(payload)
      ? payload as MutualTradePreview[]
      : Array.isArray(payload?.items)
        ? payload.items as MutualTradePreview[]
        : null;
    if (items === null) {
      throw new Error('پاسخ تاریخچه معاملات معتبر نیست.');
    }
    mutualTrades.value = append
      ? mergeTradeHistoryRows(mutualTrades.value, items)
      : mergeTradeHistoryRows([], items);
    historyNextCursor.value = !Array.isArray(payload) && typeof payload?.next_cursor === 'string'
      ? payload.next_cursor
      : null;
    historyHasMore.value = !Array.isArray(payload) && payload?.has_more === true;
    historyLoadedQueryKey.value = queryKey;
  } catch (e: any) {
    const requestIsStale = requestRevision !== historyRequestRevision;
    if (requestIsStale) return;
    console.error("Failed to load history", e);
    if (append) {
      historyPaginationError.value = e?.message === 'NetworkError' || e?.errorCode === 'NETWORK_ERROR'
        ? 'خطا در دریافت ادامه تاریخچه معاملات'
        : e?.message || 'خطا در دریافت ادامه تاریخچه معاملات';
    } else {
      historyError.value = e?.message === 'NetworkError' || e?.errorCode === 'NETWORK_ERROR'
        ? 'خطا در دریافت تاریخچه معاملات'
        : e?.message || 'خطا در دریافت تاریخچه معاملات';
    }
  } finally {
    if (append && requestRevision === historyRequestRevision) {
      isHistoryLoadingMore.value = false;
    } else if (!append && requestRevision === historyRequestRevision) {
      isHistoryLoading.value = false;
    }
  }
}

async function loadMoreMutualTrades() {
  await loadMutualTrades(false, true);
}

function scheduleLoadedTradeHistoryRefresh() {
  if (!isOwnProfile.value || !profileData.value || !hasLoadedHistoryOnce.value) return;
  if (realtimeTradeRefreshTimer !== null) clearTimeout(realtimeTradeRefreshTimer);
  realtimeTradeRefreshTimer = setTimeout(() => {
    realtimeTradeRefreshTimer = null;
    void loadMutualTrades(true, false);
  }, 80);
}

function handleRealtimeTradeCreated() {
  // The server publishes this event on recipient-private channels only. Keep
  // active filters authoritative by refetching instead of injecting a row that
  // might not belong to the selected date/commodity/counterparty projection.
  scheduleLoadedTradeHistoryRefresh();
}

function handleAppNotification(payload: unknown) {
  if (!payload || typeof payload !== 'object') return;
  const notification = payload as Record<string, unknown>;
  const embedded = notification.extra_payload;
  const embeddedCategory = embedded && typeof embedded === 'object'
    ? (embedded as Record<string, unknown>).category
    : null;
  const category = String(notification.category ?? embeddedCategory ?? '').trim().toLowerCase();
  if (category === 'trade') scheduleLoadedTradeHistoryRefresh();
}

function handleRealtimeReconnect() {
  scheduleLoadedTradeHistoryRefresh();
}

onMounted(() => {
  wsOn(WS_NOTIFICATION_EVENTS.tradeCreated, handleRealtimeTradeCreated);
  wsOn(WS_NOTIFICATION_EVENTS.appMessage, handleAppNotification);
  wsOn(WS_NOTIFICATION_EVENTS.wsReconnect, handleRealtimeReconnect);
});

onUnmounted(() => {
  if (realtimeTradeRefreshTimer !== null) clearTimeout(realtimeTradeRefreshTimer);
  wsOff(WS_NOTIFICATION_EVENTS.tradeCreated, handleRealtimeTradeCreated);
  wsOff(WS_NOTIFICATION_EVENTS.appMessage, handleAppNotification);
  wsOff(WS_NOTIFICATION_EVENTS.wsReconnect, handleRealtimeReconnect);
});

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

  const requestRevision = force
    ? ++projectUsersRequestRevision
    : projectUsersRequestRevision;
  const requestOffset = isLoadMore ? projectUsersOffset.value : 0;

  if (isLoadMore) {
    projectUsersLoadingMore.value = true;
  } else {
    projectUsersLoading.value = true;
  }
  projectUsersError.value = '';
  try {
    const params = new URLSearchParams();
    params.set('limit', String(PROJECT_USERS_PAGE_SIZE));
    params.set('offset', String(requestOffset));
    if (normalizedQuery) {
      params.set('q', normalizedQuery);
    }
    const payload = await routeRequestJson<unknown>(
      `/api/users-public/${targetProfileUserId}/project-users?${params.toString()}`,
      {
        errorContext: {
          surface: 'public-profile',
          scope: 'section',
          operation: isLoadMore ? 'load-more' : 'refresh',
          resourceLabel: 'فهرست کاربران پروژه',
          fallbackMessage: 'خطا در دریافت لیست کاربران پروژه',
        },
      },
    );
    if (requestRevision !== projectUsersRequestRevision) return;

    if (!Array.isArray(payload)) {
      throw new Error('پاسخ فهرست کاربران پروژه معتبر نیست.');
    }

    const rawRows = payload as unknown[];
    const normalizedRows = rawRows
      .map(normalizeProjectUserDirectoryEntry)
      .filter((user): user is ProjectUserDirectoryEntry => user !== null);
    const nextRows = normalizedRows.filter((user) => Number(user.id) !== targetProfileUserId);
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
    projectUsersHasMore.value = normalizedRows.length === PROJECT_USERS_PAGE_SIZE;
    projectUsersOffset.value = requestOffset + normalizedRows.length;
  } catch (e: any) {
    if (requestRevision !== projectUsersRequestRevision) return;
    projectUsersError.value = e?.message || 'خطا در دریافت لیست کاربران پروژه';
  } finally {
    if (requestRevision === projectUsersRequestRevision) {
      projectUsersLoading.value = false;
      projectUsersLoadingMore.value = false;
    }
  }
}

function resetProjectUsersDirectoryState() {
  projectUsers.value = [];
  projectUsersLoading.value = false;
  projectUsersLoadingMore.value = false;
  projectUsersError.value = '';
  projectUsersLoaded.value = false;
  lastLoadedProjectUsersQuery.value = '';
  projectUsersOffset.value = 0;
  projectUsersHasMore.value = false;
}

async function submitProjectUsersSearch() {
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
  adminUserData.value = null;
  showAdminUserManager.value = false;
  try {
    const response = await apiFetch(`/api/users/${profileData.value.id}`);
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 403) {
        adminUserError.value = 'دسترسی به تنظیمات حساس این کاربر برای نقش فعلی شما مجاز نیست. پروفایل عمومی بدون تغییر باقی ماند.';
        return;
      }
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

async function getCurrentPublicBlockState() {
  if (!profileData.value || !props.jwtToken) {
    throw new Error('نشست کاربری معتبر نیست.');
  }

  const response = await apiFetch(`/api/blocks/check/${profileData.value.id}`);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(getSafePublicBlockReadError(response.status, 'خطا در بررسی وضعیت بلاک کاربر'));
  }

  if (
    !payload
    || typeof payload !== 'object'
    || Array.isArray(payload)
    || typeof (payload as { is_blocked_by_me?: unknown }).is_blocked_by_me !== 'boolean'
  ) {
    throw new Error('پاسخ وضعیت بلاک کاربر معتبر نیست.');
  }

  return (payload as { is_blocked_by_me: boolean }).is_blocked_by_me;
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
    throw new Error(getSafePublicBlockReadError(response.status, 'خطا در دریافت وضعیت بلاک کاربر'));
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('پاسخ وضعیت بلاک کاربر معتبر نیست.');
  }

  return normalizePublicBlockStatus(payload as Partial<PublicBlockStatus>);
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

function resetPublicBlockActionUi() {
  pendingPublicBlockAction.value = null;
  publicBlockDialogError.value = '';
  publicBlockFeedback.value = null;
}

function setPublicBlockFeedback(tone: PublicBlockFeedback['tone'], message: string) {
  publicBlockFeedback.value = { tone, message };
}

function getSafePublicBlockReadError(status: number, fallback: string) {
  if (status === 403) return 'دسترسی به وضعیت بلاک این کاربر برای نقش فعلی شما مجاز نیست.';
  if (status === 404) return 'اطلاعات بلاک این کاربر دیگر در دسترس نیست.';
  return fallback;
}

function getSafePublicBlockMutationError(status: number, action: PublicBlockActionKind) {
  if (status === 403) {
    return 'دسترسی شما برای تغییر وضعیت بلاک این کاربر مجاز نیست. وضعیت بلاک تغییر نکرد.';
  }
  if (status === 404) {
    return 'این کاربر دیگر در دسترس نیست. وضعیت بلاک تغییر نکرد.';
  }
  return action === 'unblock'
    ? 'رفع بلاک کاربر انجام نشد. وضعیت بلاک تغییر نکرد.'
    : 'بلاک کاربر انجام نشد. وضعیت بلاک تغییر نکرد.';
}

function isValidatedPublicBlockMutation(payload: unknown): payload is { success: true } {
  return Boolean(
    payload
    && typeof payload === 'object'
    && !Array.isArray(payload)
    && (payload as { success?: unknown }).success === true,
  );
}

function reportPublicBlockActionError(message: string) {
  publicBlockDialogError.value = message;
  setPublicBlockFeedback('error', message);
}

function requestPublicProfileBlockToggle() {
  if (
    !showPublicBlockAction.value
    || !profileData.value
    || !props.jwtToken
    || publicBlockBusy.value
    || pendingPublicBlockAction.value
  ) {
    return;
  }

  publicBlockDialogError.value = '';
  publicBlockFeedback.value = null;

  if (publicBlockState.value === null || publicBlockStatus.value === null) {
    setPublicBlockFeedback('error', 'وضعیت بلاک هنوز آماده نیست. لطفاً چند لحظه دیگر دوباره تلاش کنید.');
    return;
  }

  if (!publicBlockState.value && !publicBlockStatus.value.can_block_now) {
    setPublicBlockFeedback(
      'error',
      'امکان بلاک کاربر در حال حاضر وجود ندارد.',
    );
    return;
  }

  pendingPublicBlockAction.value = {
    kind: publicBlockState.value ? 'unblock' : 'block',
    targetUserId: profileData.value.id,
  };
}

function cancelPendingPublicBlockAction() {
  if (publicBlockBusy.value) return;
  pendingPublicBlockAction.value = null;
  publicBlockDialogError.value = '';
}

async function confirmPublicProfileBlockAction() {
  const pendingAction = pendingPublicBlockAction.value;
  if (!pendingAction || publicBlockBusy.value) return;

  if (
    !showPublicBlockAction.value
    || !profileData.value
    || !props.jwtToken
    || profileData.value.id !== pendingAction.targetUserId
  ) {
    reportPublicBlockActionError('این پروفایل تغییر کرده است. وضعیت بلاک بدون تغییر باقی ماند.');
    return;
  }

  publicBlockBusy.value = true;
  publicBlockDialogError.value = '';
  publicBlockFeedback.value = null;
  try {
    const response = await apiFetch(`/api/blocks/${pendingAction.targetUserId}`, {
      method: pendingAction.kind === 'unblock' ? 'DELETE' : 'POST',
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      reportPublicBlockActionError(getSafePublicBlockMutationError(response.status, pendingAction.kind));
      return;
    }
    if (!isValidatedPublicBlockMutation(payload)) {
      reportPublicBlockActionError('پاسخ معتبر از سرور دریافت نشد. وضعیت بلاک تغییر نکرد.');
      return;
    }
    if (profileData.value?.id !== pendingAction.targetUserId) {
      pendingPublicBlockAction.value = null;
      setPublicBlockFeedback('success', 'تغییر وضعیت ثبت شد. برای بررسی وضعیت جدید، پروفایل کاربر را دوباره باز کنید.');
      return;
    }

    const isNowBlocked = pendingAction.kind === 'block';
    publicBlockState.value = isNowBlocked;
    if (publicBlockStatus.value) {
      const nextBlockedCount = publicBlockStatus.value.current_blocked + (isNowBlocked ? 1 : -1);
      publicBlockStatus.value = derivePublicBlockStatus(publicBlockStatus.value, nextBlockedCount);
    }
    pendingPublicBlockAction.value = null;
    setPublicBlockFeedback(
      'success',
      isNowBlocked ? 'کاربر با موفقیت بلاک شد.' : 'رفع بلاک کاربر انجام شد.',
    );
  } catch {
    reportPublicBlockActionError('ارتباط با سرور برای تغییر وضعیت بلاک برقرار نشد. وضعیت بلاک تغییر نکرد.');
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

function handleActionClick(action: { key: string }) {
  if (!profileData.value) return;
  
  if (action.key === 'message') {
    emit('navigate', 'chat', { userId: profileData.value.id, userName: profileData.value.account_name });
  } else if (action.key === 'block_toggle') {
    requestPublicProfileBlockToggle();
  } else if (action.key === 'settings') {
    emit('navigate', 'settings');
  } else if (action.key === 'admin_settings') {
    void openAdminUserManager();
  } else if (action.key === 'add_customer') {
    emit('navigate', 'operations_customers');
  } else if (action.key === 'add_accountant') {
    emit('navigate', 'operations_accountants');
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
    return type === 'BUY' ? 'خرید' : 'فروش';
  } else {
    return type === 'BUY' ? 'فروش' : 'خرید';
  }
}

function getTradeSettlementLabel(trade: MutualTradePreview) {
  return tradeSettlementLabel(trade.settlement_type);
}

function getTradeCounterpartyLabel(trade: MutualTradePreview) {
  if (typeof trade.counterparty_name === 'string' && trade.counterparty_name.trim()) {
    return trade.counterparty_name;
  }
  return Number(trade.responder_user_id) === Number(profileData.value?.id)
    ? trade.offer_user_name
    : trade.responder_user_name;
}

function formatTradePrice(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toLocaleString();
  }
  const numericValue = Number(value);
  if (Number.isFinite(numericValue)) {
    return numericValue.toLocaleString();
  }
  return 'نامشخص';
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
    };
  }

  const resolvedTarget = resolveTradeParticipantProfileTarget(
    trade,
    Number(trade.responder_user_id) === Number(profileData.value.id) ? 'offer_user' : 'responder_user',
  );
  return resolvedTarget ? { id: resolvedTarget.id } : null;
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
  });
}

function openProjectUserProfile(user: ProjectUserDirectoryEntry) {
  emit('navigate', 'public_profile', {
    id: user.id,
  });
}

function handleHistoryPresetChipChange(value: string) {
  const months = Number(value);
  if (!Number.isInteger(months) || months <= 0) {
    historyActivePresetMonths.value = null;
    return;
  }
  void applyHistoryPreset(months);
}
</script>

<template>
  <div class="card public-profile-typography">
    <input ref="avatarInput" type="file" accept="image/*" class="hidden-avatar-input" @change="handleAvatarSelected" />
    <ProfileIdentityHeader
      :display-name="profileData ? profileDisplayName : ''"
      :avatar-url="profileAvatarUrl"
      :avatar-initial="getAvatarInitial(profileDisplayName)"
      :editable="showOwnerSections"
      :avatar-busy="avatarBusy"
      :show-presence="showOwnerSections"
      :presence-status="profilePresenceStatus"
      :online="profileIsOnline"
      :hide-back-button="hideBackButton"
      :loading="isLoading && !profileData"
      @back="$emit('navigate', 'home')"
      @pick-avatar="triggerAvatarPicker"
    >
      <template #title>
        <CustomerNameWithBadge
          v-if="customerProfileContext"
          :name="profileDisplayName"
        />
        <template v-else>{{ profileDisplayName }}</template>
      </template>
    </ProfileIdentityHeader>

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

    <AppErrorState v-else-if="error" title="دریافت پروفایل انجام نشد" :message="error" class="error-state">
      <template #actions>
        <AppButton class="retry-btn" type="button" variant="secondary" :disabled="isLoading" @click="loadProfile">تلاش دوباره</AppButton>
      </template>
    </AppErrorState>

    <div v-else-if="profileData" class="profile-content" :class="{ 'profile-content--own': showOwnerSections }">
      <ProfileSummary
        :stats="sharedStatCards"
        :customer-context="showOwnerSections ? customerProfileContext : null"
        :customer-tier-label="customerProfileContext ? getCustomerTierLabel(customerProfileContext.customerTier) : ''"
      >
        <template #customer-name>
          <CustomerNameWithBadge
            v-if="customerProfileContext"
            :name="customerProfileContext.managementName"
            compact
          />
        </template>

        <AppSectionCard
          class="profile-section-card mt-4"
          title="اطلاعات شخصی"
        >
          <div class="profile-section-card__body">
            <div class="info-section">
              <div class="info-row">
                <span class="label">شماره تماس</span>
                <span class="value" dir="ltr">{{ profileMobileNumber }}</span>
              </div>
              <div class="info-row address-row">
                  <span class="label">آدرس</span>
                  <div v-if="!addressEditing" class="address-display-frame" :class="{ editable: isOwnProfile }">
                    <span class="value address-value">{{ profileData.address || 'ثبت نشده' }}</span>
                    <AppIconButton
                      v-if="isOwnProfile"
                      class="address-edit-trigger"
                      label="ویرایش آدرس"
                      title="ویرایش آدرس"
                      size="sm"
                      @click.stop="startAddressEdit"
                    >
                      <Pencil :size="16" />
                    </AppIconButton>
                  </div>
                <form v-else class="address-edit-form" @submit.prevent="saveOwnAddress">
                  <AppFormField label="آدرس" :error="addressError || undefined">
                    <template #default="{ id, describedby, invalid }">
                      <AppTextarea
                        :id="id"
                        v-model="addressDraft"
                        rows="3"
                        class="address-edit-textarea"
                        :aria-describedby="describedby"
                        :invalid="invalid"
                        placeholder="آدرس کامل خود را وارد کنید"
                      />
                    </template>
                  </AppFormField>
                  <p v-if="addressError" class="error-text address-error-text">{{ addressError }}</p>
                  <div class="address-edit-actions">
                    <AppButton type="submit" size="sm" :loading="addressBusy">ذخیره آدرس</AppButton>
                    <AppButton type="button" variant="secondary" size="sm" :disabled="addressBusy" @click.stop="cancelAddressEdit">انصراف</AppButton>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </AppSectionCard>
      </ProfileSummary>

      <ProfileRelationshipSection
        v-if="showProjectUsersSection"
        section-class="project-users-section"
        title="لیست همکاران"
        description="اعضای قابل مشاهده پروژه را جستجو و از همین بخش باز کنید."
      >
        <template #actions>
            <HelpPopover
              comfortable-target
              button-test="public-profile-project-users-help"
              note-test="public-profile-project-users-help-note"
              label="راهنمای لیست همکاران"
              text="لیست همکاران، اعضای قابل مشاهده پروژه را نشان می‌دهد. با انتخاب نام هر همکار، پروفایل عمومی همان کاربر باز می‌شود."
            />
        </template>
            <form class="project-users-search" @submit.prevent="submitProjectUsersSearch">
              <label class="sr-only" for="project-users-directory-search">جستجوی همکاران پروژه</label>
              <AppInput
                id="project-users-directory-search"
                v-model="projectUsersQuery"
                class="project-users-search-input"
                type="search"
                placeholder="جستجو با نام کاربری یا شماره تماس"
              />
              <AppButton type="submit" size="sm" :loading="projectUsersLoading">جستجو</AppButton>
            </form>

            <p v-if="projectUsersError" class="admin-user-error" role="alert">
              {{ projectUsersError }}
              <span v-if="projectUsers.length > 0"> فهرست قبلی حفظ شده است.</span>
            </p>
            <LoadingSkeleton v-if="projectUsersLoading && projectUsers.length === 0" :count="3" :height="52" />
            <AppEmptyState
              v-else-if="!projectUsersError && !hasLoadedProjectUsersOnce"
              title="فهرست همکاران آماده نمایش نیست"
              message="در صورت بروز مشکل می‌توانید دوباره جستجو یا بارگذاری را تکرار کنید."
              role="status"
            />
            <AppEmptyState
              v-else-if="!projectUsersError && !projectUsersLoading && projectUsers.length === 0"
              title="همکاری برای نمایش پیدا نشد"
              :message="projectUsersQuery.trim() ? 'همکاری با این جستجو پیدا نشد.' : 'همکاری برای نمایش وجود ندارد.'"
              role="status"
            />
            <template v-if="projectUsers.length > 0">
              <div class="project-users-list">
                <AppListItem
                  v-for="projectUser in projectUsers"
                  :key="projectUser.id"
                  :title="projectUser.account_name"
                  :description="projectUserMobileLabel(projectUser)"
                  interactive
                  class="project-user-card"
                  @select="openProjectUserProfile(projectUser)"
                >
                  <template #trailing>
                    <span class="project-user-mobile" dir="ltr">{{ projectUserMobileLabel(projectUser) }}</span>
                  </template>
                </AppListItem>
              </div>
              <div v-if="projectUsersHasMore" class="project-users-footer">
                <AppButton
                  type="button"
                  variant="secondary"
                  size="sm"
                  class="project-users-load-more"
                  :loading="projectUsersLoadingMore"
                  @click="loadMoreProjectUsers"
                >نمایش بیشتر</AppButton>
              </div>
            </template>
      </ProfileRelationshipSection>

      <ProfileRelationshipSection
        v-if="showOwnerSections && accountantRelations.length > 0"
        section-class="accountant-relations-section"
        title="لیست حسابداران"
        description="عنوان هر ردیف همان نام نمایشی رابطه است و توضیح وظیفه، در صورت ثبت، زیر آن می‌آید."
      >
        <template #actions>
            <HelpPopover
              comfortable-target
              button-test="public-profile-accountants-help"
              note-test="public-profile-accountants-help-note"
              label="راهنمای لیست حسابداران"
              text="این لیست حسابداران فعال مالک را نشان می‌دهد. عنوان هر ردیف همان نام نمایشی رابطه است و توضیح وظیفه، در صورت ثبت، زیر آن می‌آید."
            />
        </template>
            <div class="public-accountant-list">
              <article
                v-for="relation in accountantRelations"
                :key="`${relation.accountant_user_id || 'relation'}-${relation.relation_display_name}`"
                class="public-accountant-card profile-relation-card profile-relation-card--accountant"
              >
                <div class="public-accountant-card-head">
                  <div>
                    <h4>{{ relation.relation_display_name }}</h4>
                    <p class="public-accountant-handle">@{{ relation.accountant_account_name || 'unknown' }}</p>
                  </div>
                </div>
                <p v-if="relation.duty_description" class="public-accountant-duty">{{ relation.duty_description }}</p>
              </article>
            </div>
      </ProfileRelationshipSection>

      <ProfileRelationshipSection
        v-if="showCustomerListSection"
        section-class="customer-relations-section"
        title="مشتریان این مالک"
        description="نمایش این بخش به حسابداران همان مالک و مدیر ارشد محدود است."
      >
        <template #actions>
            <HelpPopover
              comfortable-target
              button-test="public-profile-customers-help"
              note-test="public-profile-customers-help-note"
              label="راهنمای مشتریان این مالک"
              text="این بخش مشتریان ثبت‌شده زیر این مالک را نشان می‌دهد. نمایش آن به حسابداران همان مالک و مدیر ارشد محدود است."
            />
        </template>
            <div class="public-customer-list">
              <article
                v-for="relation in customerRelations"
                :key="`${relation.customer_user_id || 'customer'}-${relation.management_name}`"
                class="public-customer-card profile-relation-card profile-relation-card--customer"
              >
                <div class="public-customer-card-head">
                  <div>
                    <button
                      v-if="relation.customer_user_id && relation.customer_account_name"
                      type="button"
                      class="profile-link-btn public-customer-profile-link customer-profile-link-btn"
                      @click.stop="openOwnerCustomerProfile(relation)"
                    >
                      <CustomerNameWithBadge class="public-customer-link-title" :name="relation.management_name" compact />
                      <span class="public-customer-handle">@{{ relation.customer_account_name }}</span>
                    </button>
                    <template v-else>
                      <h4><CustomerNameWithBadge :name="relation.management_name" compact /></h4>
                      <span v-if="relation.customer_account_name" class="public-customer-handle">@{{ relation.customer_account_name }}</span>
                    </template>
                  </div>
                  <AppStatusBadge tone="info">{{ getCustomerTierLabel(relation.customer_tier) }}</AppStatusBadge>
                </div>
              </article>
            </div>
      </ProfileRelationshipSection>

      <ProfileActions
        v-if="showVisitorSections"
        section-class="visitor-profile-section"
        title="اقدام‌های عمومی"
        description="ارسال پیام و مدیریت دسترسی عمومی این کاربر از این بخش انجام می‌شود."
        :actions="visitorActionItems"
        @select="handleActionClick"
      >
        <template #actions>
            <HelpPopover
              floating
              comfortable-target
              button-test="public-profile-visitor-menu-help"
              note-test="public-profile-visitor-menu-help-note"
              label="راهنمای منوی پروفایل عمومی"
              text="اقدام‌های عمومی این پروفایل در این بخش قرار گرفته‌اند تا مسیر پیام، بلاک و عملیات مشابه یکپارچه و قابل پیش‌بینی بماند."
            />
        </template>
          <p
            v-if="publicBlockFeedback"
            class="public-block-feedback"
            :class="`public-block-feedback--${publicBlockFeedback.tone}`"
            :role="publicBlockFeedback.tone === 'error' ? 'alert' : 'status'"
            data-test="public-block-feedback"
          >
            {{ publicBlockFeedback.message }}
          </p>
      </ProfileActions>

      <ProfileAdminControls
        v-if="showAdminSections && adminActionItems.length > 0"
        :error="adminUserError"
      >
        <ProfileActions
          title="مدیریت کاربر"
          description="ابزارهای مدیریتی این پروفایل از اقدام‌های عمومی جدا شده‌اند."
          :actions="adminActionItems"
          :loading="adminUserLoading"
          @select="handleActionClick"
        >
          <template #actions>
            <HelpPopover
              floating
              comfortable-target
              button-test="public-profile-admin-menu-help"
              note-test="public-profile-admin-menu-help-note"
              label="راهنمای منوی مدیریت پروفایل"
              text="تنظیمات مدیریتی کاربر از بخش عمومی جدا شده‌اند تا عملیات روزمره با ابزارهای مدیریتی مخلوط نشود."
            />
          </template>
        </ProfileActions>
      </ProfileAdminControls>

      <ProfileTradeHistory
        v-if="showOwnerSections"
        :title="tradeHistoryTitle"
        description="فیلترها و خروجی‌ها دقیقاً روی همین بازه و کالا اعمال می‌شوند."
      >
          <template #actions>
            <HelpPopover
              comfortable-target
              button-test="public-profile-history-help"
              note-test="public-profile-history-help-note"
              label="راهنمای تاریخچه معاملات"
              :text="tradeHistoryHelpText"
            />
          </template>

          <div class="profile-section-card__body">
            <div class="history-toolbar">
              <AppFilterChips
                :model-value="historyPresetChipValue"
                :options="historyPresetChipOptions"
                label="بازه‌های آماده تاریخچه معاملات"
                focus-selection-on-keyboard
                @update:model-value="handleHistoryPresetChipChange"
              />

              <div class="history-filter-grid">
                <AppFormField label="از تاریخ" class="history-filter-field">
                  <JalaliDatePicker
                    v-model="historyFromDate"
                    value-type="gregorian"
                    arrow-key-navigation
                    placeholder="انتخاب تاریخ شروع"
                    trigger-test-id="history-from-date"
                    @change="handleHistoryDateInput"
                  />
                </AppFormField>
                <AppFormField label="تا تاریخ" class="history-filter-field">
                  <JalaliDatePicker
                    v-model="historyToDate"
                    value-type="gregorian"
                    arrow-key-navigation
                    placeholder="انتخاب تاریخ پایان"
                    trigger-test-id="history-to-date"
                    @change="handleHistoryDateInput"
                  />
                </AppFormField>
                <AppFormField label="کالا" class="history-filter-field history-filter-field-wide">
                  <AppSelect
                    v-model="historyCommodityQuery"
                    :disabled="historyCommodityOptionsLoading"
                    :options="historyCommodityAppSelectOptions"
                    @focus="loadHistoryCommodityOptions"
                  />
                </AppFormField>
                <AppFormField label="جهت معامله" class="history-filter-field">
                  <AppSelect
                    v-model="historyTradeType"
                    :options="historyTradeTypeOptions"
                  />
                </AppFormField>
                <AppFormField label="نوع تسویه" class="history-filter-field">
                  <AppSelect
                    v-model="historySettlementType"
                    :options="historySettlementTypeOptions"
                  />
                </AppFormField>
                <AppFormField v-if="isOwnProfile && !shouldHideCustomerTradeRelationshipDetails" label="طرف دیگر معامله" class="history-filter-field history-filter-field-wide">
                  <AppSelect
                    :model-value="historyCounterpartySelectValue"
                    :disabled="historyCounterpartyOptionsLoading"
                    :options="historyCounterpartyAppSelectOptions"
                    @focus="loadHistoryCounterpartyOptions"
                    @update:modelValue="setHistoryCounterpartyValue"
                  />
                  <span v-if="historyCounterpartyOptionsLoading" class="history-filter-hint">در حال دریافت لیست همکاران...</span>
                  <span v-else-if="historyCounterpartyError" class="history-filter-hint error">{{ historyCounterpartyError }}</span>
                </AppFormField>
              </div>

              <div class="history-filter-actions">
                <AppButton type="button" size="sm" @click.stop="applyHistoryFilters">اعمال فیلتر</AppButton>
                <AppButton
                  type="button"
                  size="sm"
                  variant="secondary"
                  :disabled="!hasActiveHistoryFilters && !historyLoadedQueryKey"
                  @click.stop="resetHistoryFilters"
                >حذف فیلتر</AppButton>
                <AppButton
                  type="button"
                  size="sm"
                  variant="secondary"
                  :disabled="isHistoryLoading || historyExportingFormat !== null"
                  @click.stop="downloadHistoryExport('excel')"
                >{{ historyExportingFormat === 'excel' ? 'در حال دانلود...' : 'خروجی Excel' }}</AppButton>
                <AppButton
                  type="button"
                  size="sm"
                  variant="secondary"
                  :disabled="isHistoryLoading || historyExportingFormat !== null"
                  @click.stop="downloadHistoryExport('pdf')"
                >{{ historyExportingFormat === 'pdf' ? 'در حال دانلود...' : 'خروجی PDF' }}</AppButton>
              </div>

              <p v-if="historyFilterSummary" class="history-filter-summary">{{ historyFilterSummary }}</p>
              <p v-if="historyError" class="error-text history-error-text">{{ historyError }}</p>
            </div>

            <LoadingSkeleton v-if="isHistoryLoading && mutualTrades.length === 0" :count="3" :height="60" />
            <AppEmptyState
              v-else-if="!hasLoadedHistoryOnce"
              title="تاریخچه هنوز بارگذاری نشده است"
              message="بازه دلخواه را انتخاب کنید و فیلترها را اعمال کنید."
              role="status"
            />
            <AppEmptyState
              v-else-if="mutualTrades.length === 0"
              title="نتیجه‌ای پیدا نشد"
              :message="tradeHistoryEmptyText"
              role="status"
            />
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
                        <span class="trade-settlement">{{ getTradeSettlementLabel(trade) }}</span>
                        <span class="trade-price">{{ formatTradePrice(trade.price) }} ریال</span>
                    </div>
                    <div v-if="!shouldHideCustomerTradeRelationshipDetails" class="trade-counterparty">
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
                    <div v-if="trade.trade_path_summary && !shouldHideCustomerTradeRelationshipDetails" class="trade-counterparty">
                      <span class="label">مسیر:</span>
                      <span class="value">{{ trade.trade_path_summary }}</span>
                    </div>
                    <div v-if="showTradeCustomerContext(trade) && !shouldHideCustomerTradeRelationshipDetails" class="trade-counterparty">
                      <span class="label">رابطه:</span>
                      <span class="value trade-customer-context-value">
                        <CustomerNameWithBadge
                          v-if="getTradeCustomerContextManagementName(trade)"
                          :name="getTradeCustomerContextManagementName(trade)"
                          compact
                        />
                        <template v-else>
                          <span class="customer-context-badge">مشتری</span>
                          <span v-if="getTradeCustomerContextOwnerAccountName(trade)">سرگروه {{ getTradeCustomerContextOwnerAccountName(trade) }}</span>
                        </template>
                        <span v-if="getTradeCustomerContextTier(trade)">{{ getCustomerTierLabel(getTradeCustomerContextTier(trade)) }}</span>
                      </span>
                    </div>
                </div>
                <div v-if="historyPaginationError" class="history-pagination-error" role="alert">
                  <span>{{ historyPaginationError }}</span>
                  <AppButton type="button" size="sm" variant="secondary" @click.stop="loadMoreMutualTrades">
                    تلاش دوباره
                  </AppButton>
                </div>
                <div v-else-if="historyHasMore || isHistoryLoadingMore" class="history-load-more-row">
                  <AppButton
                    type="button"
                    size="sm"
                    variant="secondary"
                    :disabled="isHistoryLoadingMore"
                    @click.stop="loadMoreMutualTrades"
                  >{{ isHistoryLoadingMore ? 'در حال دریافت...' : 'نمایش معاملات بیشتر' }}</AppButton>
                </div>
            </div>
          </div>
      </ProfileTradeHistory>

      <ProfileActions
        v-if="showOwnerSections"
        section-class="owner-profile-section"
        title="میانبرهای مدیریت پروفایل"
        description="تنظیمات، مشتریان و حسابداران از همین بخش در دسترس هستند."
        :actions="ownerActionItems"
        @select="handleActionClick"
      >
        <template #actions>
            <HelpPopover
              floating
              comfortable-target
              button-test="public-profile-owner-menu-help"
              note-test="public-profile-owner-menu-help-note"
              label="راهنمای منوی مالک"
              text="میانبرهای تنظیمات، مشتریان و حسابداران در همین منو جمع شده‌اند تا ظاهر پروفایل شما با پروفایل عمومی بقیه بخش‌ها هم‌راستا بماند."
            />
        </template>
      </ProfileActions>
    </div>

    <AppResponsiveDialog
      :open="showAdminUserManager"
      title="مدیریت کاربر"
      backdrop-class="admin-user-modal-overlay"
      panel-class="admin-user-modal"
      body-class="admin-user-modal-body"
      :close-on-escape="false"
      @close="closeAdminUserManager"
    >
      <UserProfile
        v-if="adminUserData"
        :user="adminUserData"
        :isAdminView="true"
        :apiBaseUrl="props.apiBaseUrl"
        :jwtToken="props.jwtToken"
        :viewerUserId="props.viewerUserId"
        :viewerRole="viewerRole"
        @navigate="handleAdminUserManagerNavigate"
      />
    </AppResponsiveDialog>

    <AppConfirmDialog
      :open="Boolean(pendingPublicBlockConfirmation)"
      :title="pendingPublicBlockConfirmation?.title || 'تأیید عملیات'"
      :message="pendingPublicBlockConfirmation?.message"
      :confirm-label="pendingPublicBlockConfirmation?.confirmLabel || 'تأیید'"
      cancel-label="انصراف"
      :tone="pendingPublicBlockConfirmation?.tone || 'warning'"
      :busy="publicBlockBusy"
      :error="publicBlockDialogError || undefined"
      :confirm-disabled="!pendingPublicBlockConfirmation"
      @cancel="cancelPendingPublicBlockAction"
      @confirm="confirmPublicProfileBlockAction"
    />
  </div>
</template>

<style scoped>
.public-profile-typography {
  font-family: Vazirmatn, Tahoma, Arial, sans-serif;
  font-synthesis: none;
}

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

.profile-stats-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
  width: 100%;
  max-width: var(--ds-page-max-width);
}

.profile-header-row {
  /*
   * Keep the title track explicitly shrinkable. A bare `1fr` has an automatic
   * minimum, so a long account name could widen the whole route at 360px and
   * push the back control outside the viewport.
   */
  grid-template-columns: minmax(4rem, 5.5rem) minmax(0, 1fr) minmax(2.75rem, 5.5rem);
  align-items: center;
  min-width: 0;
  padding-bottom: 24px;
}

.profile-header-row > * {
  min-width: 0;
}

.profile-nav-back {
  justify-self: end;
  box-sizing: border-box;
  inline-size: 2.75rem;
  block-size: 2.75rem;
  min-inline-size: 2.75rem;
  min-block-size: 2.75rem;
  border: 1px solid var(--ds-border-medium);
  border-radius: var(--ds-radius-full);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  box-shadow: var(--ds-shadow-sm);
  cursor: pointer;
  transition: background 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}

.profile-nav-back:hover {
  background: var(--ds-bg-hover);
  border-color: var(--ds-primary-200);
  box-shadow: var(--ds-shadow-md);
}

.profile-nav-back:active {
  transform: translateY(1px);
}

.profile-nav-back:focus-visible {
  outline: 3px solid rgba(51, 144, 236, 0.22);
  outline-offset: 2px;
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
  background: linear-gradient(135deg, var(--ds-telegram-500), var(--ds-info-500) 58%, var(--ds-primary-500) 100%);
  color: var(--ds-bg-card);
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
  box-shadow: var(--ds-shadow-lg);
}

.profile-avatar--readonly {
  box-shadow: var(--ds-shadow-md);
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
  color: var(--ds-bg-card);
  border: 1px solid rgba(255, 255, 255, 0.85);
  box-shadow: var(--ds-shadow-md);
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
  color: var(--ds-bg-card);
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
  align-items: stretch;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 0.75rem;
}

.project-users-search-input {
  flex: 1 1 220px;
  min-width: 0;
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

.public-block-feedback {
  margin: 0.9rem 0 0;
  padding: 0.72rem 0.84rem;
  border-radius: var(--ds-radius-md);
  font-size: var(--ds-font-sm);
  font-weight: 700;
  line-height: 1.7;
  text-align: right;
}

.public-block-feedback--success {
  border: 1px solid var(--ds-success-100);
  background: var(--ds-success-50);
  color: var(--ds-success-700);
}

.public-block-feedback--error {
  border: 1px solid var(--ds-danger-200);
  background: var(--ds-danger-50);
  color: var(--ds-danger-700);
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
  overflow: hidden;
  -webkit-overflow-scrolling: touch;
  border-radius: 22px;
  background: var(--ds-bg-card, #fff);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.24);
}

.admin-user-modal-body {
  padding: 0 !important;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
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
  min-width: 32px;
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--ds-success-100);
  border-radius: 999px;
  background: var(--ds-success-50);
  color: var(--ds-success-700);
  cursor: pointer;
  box-shadow: var(--ds-shadow-sm);
  transition: color 0.18s ease, background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.address-edit-trigger:hover,
.address-edit-trigger:focus-visible {
  color: var(--ds-bg-card);
  border-color: var(--ds-success-100);
  background: var(--ds-success-600);
  box-shadow: var(--ds-shadow-md);
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

.profile-action-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.75rem;
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

.profile-action-card {
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

.profile-action-card:hover {
  border-color: color-mix(in srgb, var(--ds-primary-500) 30%, transparent);
  background: var(--ds-primary-50);
}

.profile-action-card:active {
  transform: scale(0.98);
}

@media (prefers-reduced-motion: reduce) {
  .profile-nav-back,
  .address-edit-trigger,
  .profile-action-card {
    transition: none;
  }
}

.profile-action-card:disabled,
.profile-action-card--disabled {
  cursor: not-allowed;
  opacity: 0.78;
  transform: none;
}

.profile-action-card__icon {
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

.profile-action-card__copy {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.18rem;
}

.profile-action-card__label {
  flex: 1;
  min-width: 0;
}

.profile-action-card__note {
  font-size: 0.72rem;
  line-height: 1.55;
  font-weight: 600;
  color: #6b7280;
}

.message-menu-btn {
  background: linear-gradient(135deg, var(--ds-info-50), color-mix(in srgb, var(--ds-info-50) 98%, var(--ds-info-500))) !important;
  color: var(--ds-info-700) !important;
  border-color: color-mix(in srgb, var(--ds-info-500) 22%, transparent) !important;
}

.message-menu-btn .profile-action-card__icon {
  background: color-mix(in srgb, var(--ds-info-500) 14%, transparent);
  color: var(--ds-info-700);
}

.settings-btn {
  background: linear-gradient(135deg, var(--ds-primary-50), var(--ds-primary-100)) !important;
  color: var(--ds-primary-800) !important;
  border-color: color-mix(in srgb, var(--ds-primary-500) 20%, transparent) !important;
}

.block-btn {
  background: var(--ds-danger-50) !important;
  color: var(--ds-danger-800) !important;
  border-color: var(--ds-danger-200) !important;
}

.block-btn .profile-action-card__icon {
  background: color-mix(in srgb, var(--ds-danger-500) 12%, transparent);
  color: var(--ds-danger-700);
}

.unblock-btn {
  background: var(--ds-success-50) !important;
  color: var(--ds-success-800) !important;
  border-color: var(--ds-success-100) !important;
}

.unblock-btn .profile-action-card__icon {
  background: color-mix(in srgb, var(--ds-success-500) 14%, transparent);
  color: var(--ds-success-800);
}

:deep(.profile-action-card) {
  width: 100%;
}

:deep(.message-menu-btn) {
  background: linear-gradient(135deg, var(--ds-info-50), color-mix(in srgb, var(--ds-info-50) 98%, var(--ds-info-500))) !important;
  color: var(--ds-info-700) !important;
  border-color: color-mix(in srgb, var(--ds-info-500) 22%, transparent) !important;
}

:deep(.settings-btn) {
  background: linear-gradient(135deg, var(--ds-primary-50), var(--ds-primary-100)) !important;
  color: var(--ds-primary-800) !important;
  border-color: color-mix(in srgb, var(--ds-primary-500) 20%, transparent) !important;
}

:deep(.block-btn) {
  background: var(--ds-danger-50) !important;
  color: var(--ds-danger-800) !important;
  border-color: var(--ds-danger-200) !important;
}

:deep(.unblock-btn) {
  background: var(--ds-success-50) !important;
  color: var(--ds-success-800) !important;
  border-color: var(--ds-success-100) !important;
}

.retry-btn {
  min-height: 2.75rem;
}



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
  font-size: var(--ds-font-helper);
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
  font-size: var(--ds-font-helper);
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
  font-size: var(--ds-font-helper);
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

.history-control {
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 12px;
  background: white;
  color: var(--ds-text-primary);
  padding: 10px 12px;
  font-weight: 700;
  cursor: pointer;
}

.history-control.primary {
  background: linear-gradient(135deg, #f59e0b, #d97706);
  border-color: transparent;
  color: white;
}

.history-control:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.history-filter-summary {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-meta);
}

.history-error-text {
  margin: 0;
}

.history-load-more-row,
.history-pagination-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 44px;
}

.history-pagination-error {
  justify-content: space-between;
  padding: 10px 12px;
  color: var(--ds-danger-700);
  font-size: var(--ds-font-helper);
  font-weight: 700;
  border: 1px solid var(--ds-danger-200);
  border-radius: var(--ds-radius-md);
  background: var(--ds-danger-50);
}

.public-accountant-list,
.public-customer-list {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
}

.profile-relation-card,
.public-accountant-card,
.public-customer-card {
  position: relative;
  overflow: hidden;
  padding: 0.85rem;
  border-radius: var(--ds-radius-md);
  border: 1px solid var(--ds-border-accent);
  background: var(--ds-bg-card);
  box-shadow: var(--ds-shadow-sm);
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
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 850;
  line-height: 1.5;
}

.public-customer-profile-link {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  text-align: right;
}

.public-customer-link-title {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 800;
  line-height: 1.5;
}

.public-accountant-handle,
.public-customer-handle {
  display: block;
  margin: 6px 0 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  direction: ltr;
  text-align: right;
}

.public-customer-tier-badge {
  flex-shrink: 0;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
  font-size: var(--ds-font-badge);
  font-weight: 700;
}

.public-accountant-duty {
  position: relative;
  z-index: 1;
  margin: 12px 0 0;
  color: var(--ds-text-secondary);
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

@media (prefers-reduced-motion: reduce) {
  .mini-trade-card {
    transition: none;
  }
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
    flex-wrap: wrap;
    gap: 0.45rem;
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

.trade-settlement {
  font-size: var(--ds-font-sm);
  color: var(--ds-text-secondary);
  font-weight: 800;
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
  font-size: var(--ds-font-meta);
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
  color: var(--ds-success-700);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.trade-counterparty .profile-link-btn {
  color: var(--ds-success-700);
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
  .profile-stats-grid {
    grid-template-columns: 1fr;
  }

  .history-filter-grid {
    grid-template-columns: 1fr;
  }

  .history-filter-field-wide {
    grid-column: span 1;
  }
}
</style>
