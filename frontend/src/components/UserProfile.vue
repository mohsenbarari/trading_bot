<script setup lang="ts">
import { ref, computed, watchEffect, onUnmounted, watch, toRef } from 'vue';
import moment from 'moment-jalaali';
import {
  AlertTriangle,
  Ban,
  Bell,
  Check,
  ChevronLeft,
  Pencil,
  RotateCcw,
  Settings,
  Trash2,
  Undo2,
} from 'lucide-vue-next';
import { ActionContractError, useActionState } from '../composables/useActionState';
import { useUserProfileTiming } from '../composables/useUserProfileTiming';
import { MIDDLE_MANAGER_ROLE, SUPER_ADMIN_ROLE } from '../utils/adminAccess';
import { currentUserSummary } from '../utils/currentUser';
import { isAppHttpError, normalizeErrorPresentation } from '../utils/httpErrorPolicy';
import { formatIranDateTime } from '../utils/iranTime';
import { routeRequest } from '../utils/routeRequest';
import CustomerNameWithBadge from './CustomerNameWithBadge.vue';
import JalaliDatePicker from './JalaliDatePicker.vue';
import { ProfileIdentityHeader } from './profile';
import {
  AppButton,
  AppConfirmDialog,
  AppFormField,
  AppInput,
  AppInsetGroup,
  AppListItem,
  AppResponsiveDialog,
  AppSelect,
} from './ui';

type UserProfileUser = {
  id: number;
  account_name?: string | null;
  mobile_number?: string | null;
  address?: string | null;
  role: string;
  account_status?: string | null;
  can_block_users?: boolean | null;
  max_blocked_users?: number | null;
  max_sessions?: number | null;
  max_accountants?: number | null;
  max_customers?: number | null;
  trading_restricted_until?: string | null;
  trading_restricted_until_jalali?: string | null;
  max_daily_trades?: number | null;
  max_active_commodities?: number | null;
  max_daily_requests?: number | null;
  limitations_expire_at?: string | null;
  limitations_expire_at_jalali?: string | null;
  trades_count?: number | null;
  commodities_traded_count?: number | null;
  channel_messages_count?: number | null;
  global_lock_grace_expires_at?: string | null;
  global_web_locked_at?: string | null;
  is_accountant?: boolean;
  is_customer?: boolean;
  customer_management_name?: string | null;
  customer_owner_account_name?: string | null;
  customer_tier?: string | null;
  [key: string]: unknown;
};

const props = defineProps<{
  user: UserProfileUser;
  isAdminView?: boolean;
  apiBaseUrl?: string;
  jwtToken?: string | null;
  viewerUserId?: number | null;
  viewerRole?: string | null;
}>();

const emit = defineEmits(['navigate']);

const {
  countdownRestriction,
  countdownLimitation,
  parseJalaliToIranISO,
  toEnglishDigits,
} = useUserProfileTiming(toRef(props, 'user'));

const isEditingRole = ref(false);
const showSettings = ref(false);
const showBlockModal = ref(false);
const showLimitationsModal = ref(false);

type UserRecord = Record<string, unknown>;
type UserActionContext = {
  userId: number;
  action: string;
};
type ActionFeedback = {
  tone: 'success' | 'error';
  message: string;
};
type ConfirmationKind =
  | 'account-status'
  | 'unblock'
  | 'remove-limitations'
  | 'terminate-sessions'
  | 'delete-user';
type PendingConfirmation = {
  kind: ConfirmationKind;
  key: string;
  title: string;
  message: string;
  confirmLabel: string;
  tone: 'warning' | 'danger';
  targetStatus?: 'active' | 'inactive';
};

const userActions = useActionState<UserActionContext, unknown>();
const actionFeedback = ref<ActionFeedback | null>(null);
const quotaFeedback = ref<ActionFeedback | null>(null);
const roleError = ref('');
const blockError = ref('');
const limitationsError = ref('');
const pendingConfirmation = ref<PendingConfirmation | null>(null);
const confirmationError = ref('');
const activeUserMutationKey = ref<string | null>(null);
const isUserMutationBusy = computed(() => activeUserMutationKey.value !== null);
const confirmationBusy = computed(() => {
  const key = pendingConfirmation.value?.key;
  return key ? userActions.states.value[key]?.status === 'busy' : false;
});

function normalizedPositiveId(value: unknown): number | null {
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

const effectiveViewerUserId = computed(() => (
  normalizedPositiveId(props.viewerUserId) ?? normalizedPositiveId(currentUserSummary.value?.id)
));
const effectiveViewerRole = computed(() => {
  const propRole = typeof props.viewerRole === 'string' && props.viewerRole.trim()
    ? props.viewerRole
    : null;
  return propRole || currentUserSummary.value?.role || null;
});
const isAdminSelfTarget = computed(() => (
  effectiveViewerUserId.value !== null
  && effectiveViewerUserId.value === normalizedPositiveId(props.user?.id)
));
const isSuperAdminPeerTarget = computed(() => (
  effectiveViewerRole.value === SUPER_ADMIN_ROLE
  && props.user?.role === SUPER_ADMIN_ROLE
));
const isSensitiveAdminTargetReadOnly = computed(() => (
  Boolean(props.isAdminView) && (isAdminSelfTarget.value || isSuperAdminPeerTarget.value)
));
const adminReadOnlyMessage = computed(() => {
  if (isAdminSelfTarget.value) {
    return 'تنظیمات حساس حساب خودتان فقط برای مشاهده است؛ تغییر آن از این مسیر مجاز نیست.';
  }
  return 'تنظیمات حساس مدیر ارشد هم‌سطح فقط برای مشاهده است؛ تغییر آن از این مسیر مجاز نیست.';
});
const canPerformSensitiveAdminActions = computed(() => (
  Boolean(props.isAdminView) && !isSensitiveAdminTargetReadOnly.value
));

function actionKey(action: string) {
  return `user:${props.user?.id ?? 'unknown'}:${action}`;
}

function isActionBusy(action: string) {
  const key = actionKey(action);
  return activeUserMutationKey.value === key || userActions.states.value[key]?.status === 'busy';
}

function errorHttpStatus(error: unknown): number | null {
  if (isAppHttpError(error)) return error.status;
  if (error instanceof ActionContractError) return error.response.status;

  // Test/module boundaries can duplicate error constructors. Trust only the
  // structured status, never the server detail, for this recovery wording.
  if (!error || typeof error !== 'object') return null;
  const directStatus = Number((error as { status?: unknown }).status);
  if (Number.isInteger(directStatus) && directStatus >= 100 && directStatus <= 599) {
    return directStatus;
  }
  const responseStatus = Number((error as { response?: { status?: unknown } }).response?.status);
  return Number.isInteger(responseStatus) && responseStatus >= 100 && responseStatus <= 599
    ? responseStatus
    : null;
}

function errorMessage(error: unknown, fallback: string) {
  if (errorHttpStatus(error) === 403) {
    return 'اجازه تغییر این تنظیم حساس را ندارید. اطلاعات بدون تغییر باقی ماند.';
  }
  if (error instanceof ActionContractError) return fallback;
  return normalizeErrorPresentation(error, {
    surface: 'admin',
    scope: 'action',
    operation: 'update',
    userInitiated: true,
    fallbackMessage: fallback,
  }).message || fallback;
}

const confirmationSafeCopy: Record<ConfirmationKind, string> = {
  'account-status': 'تغییر وضعیت حساب تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.',
  unblock: 'رفع مسدودیت تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.',
  'remove-limitations': 'رفع محدودیت‌ها تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.',
  'terminate-sessions': 'پایان نشست‌ها تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.',
  'delete-user': 'حذف کاربر تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.',
};
const ZERO_MUTATION_HINT = ' لغو یا Escape هیچ تغییری ایجاد نمی‌کند.';

function getSafeConfirmationError(kind: ConfirmationKind, error: unknown): string {
  const status = errorHttpStatus(error);
  if (status === 403) {
    return 'اجازه این اقدام را ندارید. اطلاعات نمایش‌داده‌شده تغییری نکرده است.';
  }
  if (status === 404) {
    return 'این کاربر دیگر در دسترس نیست. اطلاعات نمایش‌داده‌شده تغییری نکرده است.';
  }
  return confirmationSafeCopy[kind];
}

function isRecord(value: unknown): value is UserRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

const validUserRoles = new Set(['تماشا', 'عادی', 'پلیس', 'مدیر میانی', 'مدیر ارشد']);
const validAccountStatuses = new Set(['active', 'inactive']);

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim());
}

function dateTimeEpoch(value: unknown): number | null {
  if (!isNonEmptyString(value)) return null;
  const trimmed = value.trim();
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?$/i.test(trimmed)) {
    return null;
  }
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(trimmed) ? trimmed : `${trimmed}Z`;
  const epoch = Date.parse(normalized);
  return Number.isFinite(epoch) ? epoch : null;
}

function isDateTimeString(value: unknown): value is string {
  return dateTimeEpoch(value) !== null;
}

function isSameDateTime(value: unknown, expected: string) {
  const actualEpoch = dateTimeEpoch(value);
  const expectedEpoch = dateTimeEpoch(expected);
  return actualEpoch !== null && expectedEpoch !== null && actualEpoch === expectedEpoch;
}

function isMinimalUserRead(receipt: unknown, targetUserId: number): receipt is UserRecord {
  if (!isRecord(receipt)) return false;
  return Number.isInteger(receipt.id)
    && receipt.id === targetUserId
    && typeof receipt.full_name === 'string'
    && typeof receipt.account_name === 'string'
    && typeof receipt.mobile_number === 'string'
    && typeof receipt.role === 'string'
    && validUserRoles.has(receipt.role)
    && typeof receipt.account_status === 'string'
    && validAccountStatuses.has(receipt.account_status)
    && typeof receipt.has_bot_access === 'boolean'
    && isDateTimeString(receipt.created_at);
}

function isExpectedUserReceipt(
  receipt: unknown,
  expected: Record<string, unknown | ((value: unknown) => boolean)>,
): receipt is UserRecord {
  if (!isMinimalUserRead(receipt, props.user.id)) return false;
  return Object.entries(expected).every(([field, expectedValue]) => {
    if (!(field in receipt)) return false;
    return typeof expectedValue === 'function'
      ? expectedValue(receipt[field])
      : Object.is(receipt[field], expectedValue);
  });
}

async function parseReceipt(response: Response) {
  try {
    return await response.json();
  } catch {
    throw new Error('پاسخ سرور قابل تأیید نبود. دوباره تلاش کنید.');
  }
}

async function runJsonAction(options: {
  action: string;
  url: string;
  method: 'PUT' | 'POST' | 'DELETE';
  body?: Record<string, unknown>;
  validate: (receipt: unknown) => boolean;
  fallbackError: string;
}) {
  const key = actionKey(options.action);
  const context = { userId: props.user.id, action: options.action };
  if (props.isAdminView && !canPerformSensitiveAdminActions.value) {
    const error = new Error(adminReadOnlyMessage.value);
    actionFeedback.value = { tone: 'error', message: adminReadOnlyMessage.value };
    return { outcome: 'error' as const, key, context, error, response: null };
  }
  if (activeUserMutationKey.value !== null) {
    return { outcome: 'duplicate' as const, key, context };
  }

  activeUserMutationKey.value = key;
  actionFeedback.value = null;
  try {
    return await userActions.run({
      key,
      context,
      action: async () => {
        const response = await routeRequest(options.url, {
          method: options.method,
          ...(options.body ? { body: JSON.stringify(options.body) } : {}),
          errorContext: {
            surface: 'admin',
            scope: 'action',
            operation: options.method === 'DELETE' ? 'delete' : 'update',
            userInitiated: true,
            fallbackMessage: options.fallbackError,
          },
        });
        return { response, receipt: await parseReceipt(response) };
      },
      validateReceipt: (receipt, _context, response) => (
        response.status === 200 && options.validate(receipt)
      ),
    });
  } finally {
    if (activeUserMutationKey.value === key) activeUserMutationKey.value = null;
  }
}

async function runUserUpdate(options: {
  action: string;
  body: Record<string, unknown>;
  expected: Record<string, unknown | ((value: unknown) => boolean)>;
  fallbackError: string;
}) {
  const result = await runJsonAction({
    action: options.action,
    url: `/api/users/${props.user.id}`,
    method: 'PUT',
    body: options.body,
    validate: (receipt) => isExpectedUserReceipt(receipt, options.expected),
    fallbackError: options.fallbackError,
  });

  if (result.outcome === 'success') {
    Object.assign(props.user, result.receipt as UserRecord);
  }
  return result;
}

function setActionSuccess(message: string) {
  actionFeedback.value = { tone: 'success', message };
}

function setQuotaError(error: unknown, fallback: string) {
  quotaFeedback.value = { tone: 'error', message: errorMessage(error, fallback) };
}

function setQuotaSuccess(message: string) {
  quotaFeedback.value = { tone: 'success', message };
}

// Limitation Refs
const limitMaxTrades = ref<number | null>(null);
const limitMaxCommodities = ref<number | null>(null);
const limitMaxRequests = ref<number | null>(null);
const limitDurationMinutes = ref(0); // 0 = Unlimited
const showLimitDateModal = ref(false);
const customLimitDate = ref('');
const selectedRole = ref(props.user?.role || 'تماشا');
const editMaxSessions = ref(props.user?.is_accountant ? 1 : (props.user?.max_sessions ?? 1));
const editMaxAccountants = ref(props.user?.max_accountants ?? 3);
const editMaxCustomers = ref(props.user?.max_customers ?? 5);
const canBlockUsers = ref(props.user?.can_block_users ?? true);
const editMaxBlockedUsers = ref(props.user?.max_blocked_users ?? 10);
const accountStatus = ref(props.user?.account_status ?? 'active');
const isTerminatingSessions = computed(() => isActionBusy('terminate-sessions'));
const showCustomerContext = computed(() => Boolean(
  props.user?.is_customer
  || props.user?.customer_management_name
  || props.user?.customer_owner_account_name
  || props.user?.customer_tier,
));
const userDisplayName = computed(() => {
  const customerName = typeof props.user?.customer_management_name === 'string'
    ? props.user.customer_management_name.trim()
    : '';
  return customerName || props.user?.account_name || '---';
});
const canEditRole = computed(() => (
  effectiveViewerRole.value !== MIDDLE_MANAGER_ROLE
  && canPerformSensitiveAdminActions.value
));
const hasFixedSingleSessionLimit = computed(() => (
  props.user?.is_accountant === true
  || props.user?.role === 'مدیر ارشد'
  || props.user?.role === 'مدیر میانی'
));

function getCustomerTierLabel(value: string | null | undefined) {
  if (value === 'tier2') return 'سطح 2';
  if (value === 'tier1') return 'سطح 1';
  return '---';
}

// --- Date Picker Logic ---
const showCustomDateInput = ref(false);
const showBlockDateModal = ref(false);
const customDate = ref('');

const pickerStep = ref(1);
const tempDatePart = ref('');
const tempTimePart = ref('');
const blockTimePickerRef = ref<{ modelValue?: unknown } | null>(null);
const limitTimePickerRef = ref<{ modelValue?: unknown } | null>(null);

// Watch tempTimePart for debugging
watch(tempTimePart, (newVal, oldVal) => {
    console.log('tempTimePart changed via watch:', oldVal, '->', newVal);
});

watch(
  () => [props.user?.max_sessions, props.user?.is_accountant] as const,
  ([value, isAccountant]) => {
    editMaxSessions.value = isAccountant ? 1 : (value ?? 1);
  }
);

watch(
  () => props.user?.max_accountants,
  (value) => {
    editMaxAccountants.value = value ?? 3;
  }
);

watch(
  () => props.user?.max_customers,
  (value) => {
    editMaxCustomers.value = value ?? 5;
  }
);

watch(
  () => props.user?.can_block_users,
  (value) => {
    canBlockUsers.value = value ?? true;
  }
);

watch(
  () => props.user?.max_blocked_users,
  (value) => {
    editMaxBlockedUsers.value = value ?? 10;
  }
);

watch(
  () => props.user?.account_status,
  (value) => {
    accountStatus.value = value ?? 'active';
  }
);

watch(isSensitiveAdminTargetReadOnly, (readOnly) => {
  if (!readOnly) return;
  showSettings.value = false;
  isEditingRole.value = false;
  showBlockModal.value = false;
  showLimitationsModal.value = false;
  showBlockDateModal.value = false;
  showLimitDateModal.value = false;
  pendingConfirmation.value = null;
  confirmationError.value = '';
});

function initDatePicker(currentValue: string) {
    pickerStep.value = 1;
    if (currentValue) {
        // Try to parse existing value
        const m = moment(currentValue, 'jYYYY/jMM/jDD HH:mm');
        if (m.isValid()) {
            tempDatePart.value = m.format('jYYYY/jMM/jDD');
            tempTimePart.value = m.format('HH:mm');
        } else {
            tempDatePart.value = moment().format('jYYYY/jMM/jDD');
            tempTimePart.value = moment().format('HH:mm');
        }
    } else {
        tempDatePart.value = moment().format('jYYYY/jMM/jDD');
        tempTimePart.value = moment().format('HH:mm');
    }
}

// Final submission handler
function handleNextStep() {
    if (!tempDatePart.value) return;
    pickerStep.value = 2;
}

function handleFinalSubmit() {
    // Log current state for debugging
    console.log('tempDatePart before submit:', tempDatePart.value);
    console.log('tempTimePart before submit:', tempTimePart.value);

    if (!tempDatePart.value) {
        if (showBlockDateModal.value) blockError.value = 'لطفاً تاریخ را انتخاب کنید.';
        if (showLimitDateModal.value) limitationsError.value = 'لطفاً تاریخ را انتخاب کنید.';
        return;
    }

    // Try to get time from picker ref if tempTimePart is empty
    let timePart = tempTimePart.value;

    if (!timePart || timePart === '') {
        // Try to read from picker component directly
        const pickerRef = showBlockDateModal.value ? blockTimePickerRef.value : limitTimePickerRef.value;
        if (pickerRef && pickerRef.modelValue) {
            const pickerVal = pickerRef.modelValue;
            console.log('Reading from picker ref:', pickerVal);
            if (typeof pickerVal === 'string') {
                timePart = pickerVal;
            } else if (pickerVal instanceof Date) {
                timePart = moment(pickerVal).format('HH:mm');
            }
        }
    }

    // Final fallback to current time
    if (!timePart || timePart === '') {
        timePart = moment().format('HH:mm');
        console.log('Using fallback current time:', timePart);
    }

    const finalVal = `${tempDatePart.value} ${timePart}`;
    console.log('Final Submit (Jalali):', finalVal);

    if (showBlockDateModal.value) {
        customDate.value = finalVal;
        showBlockDateModal.value = false;
    } else if (showLimitDateModal.value) {
        customLimitDate.value = finalVal;
        showLimitDateModal.value = false;
    }
}

// Legacy handler - can be removed or kept as alias
// Explicit update handlers to ensure v-model sync works for custom integration
function updateDatePart(val: unknown) {
    console.log('updateDatePart received:', val, 'type:', typeof val);
    // Do NOT modify tempDatePart here - v-model handles it.
    // This handler is only for logging/debugging.
}

const roles = [
  { value: 'تماشا', label: 'تماشا' },
  { value: 'عادی', label: 'عادی' },
  { value: 'پلیس', label: 'پلیس' },
  { value: 'مدیر میانی', label: 'مدیر میانی' },
  { value: 'مدیر ارشد', label: 'مدیر ارشد' }
];

const blockDurations = [
  { label: '1 ساعت', minutes: 60 },
  { label: '6 ساعت', minutes: 360 },
  { label: '12 ساعت', minutes: 720 },
  { label: '1 روز', minutes: 1440 },
  { label: '3 روز', minutes: 4320 },
  { label: '1 هفته', minutes: 10080 },
  { label: 'نامحدود', minutes: 0 },
  { label: 'انتخاب زمان دلخواه', minutes: -1 } // -1 برای حالت کاستوم
];
const maxSessionOptions = [
  { value: '1', label: '۱' },
  { value: '2', label: '۲' },
  { value: '3', label: '۳' },
];
const maxSessionsSelectValue = computed(() => String(editMaxSessions.value ?? 1));
const limitDurationOptions = computed(() => blockDurations.map((duration) => ({
  value: String(duration.minutes),
  label: duration.label,
})));
const limitDurationSelectValue = computed(() => String(limitDurationMinutes.value));

function setLimitDurationValue(value: string) {
  const parsed = Number(value);
  limitDurationMinutes.value = Number.isFinite(parsed) ? parsed : 0;
}



// Lock body scroll when any modal is open
watchEffect(() => {
    const anyModalOpen = showBlockModal.value
      || showLimitationsModal.value
      || showBlockDateModal.value
      || showLimitDateModal.value
      || Boolean(pendingConfirmation.value);
    document.body.style.overflow = anyModalOpen ? 'hidden' : '';
});

// Cleanup on unmount
onUnmounted(() => {
    document.body.style.overflow = '';
});

const isRestricted = computed(() => {
  if (!props.user.trading_restricted_until) return false;
  // Use moment to handle both naive (which assumes local) and aware strings safely
  // Since backend typically sends UTC ISO, we parse as UTC
  const restrictionTime = moment.utc(props.user.trading_restricted_until);
  return restrictionTime.isValid() && restrictionTime.isAfter(moment.utc());
});

const restrictionText = computed(() => {
  if (!isRestricted.value) return 'آزاد';
  const restrictionTime = moment.utc(props.user.trading_restricted_until);
  if (restrictionTime.year() > 2100) return 'مسدود دائم';
  return `تا ${props.user.trading_restricted_until_jalali}`;
});

const isAccountInactive = computed(() => (props.user?.account_status ?? accountStatus.value) === 'inactive');

function formatAccountStatusDate(value: string | null | undefined) {
  if (!value) return null;
  return formatIranDateTime(value, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }) || null;
}

const globalLockGraceExpiresAtText = computed(() => formatAccountStatusDate(props.user?.global_lock_grace_expires_at));
const globalWebLockedAtText = computed(() => formatAccountStatusDate(props.user?.global_web_locked_at));

const accountStatusText = computed(() => {
  if (!isAccountInactive.value) return 'فعال';
  if (globalWebLockedAtText.value) return 'غیرفعال (قفل کامل)';
  if (globalLockGraceExpiresAtText.value) return 'غیرفعال (در مهلت فعال‌سازی)';
  return 'غیرفعال';
});

const accountStatusDetailText = computed(() => {
  if (!isAccountInactive.value) return null;
  if (globalWebLockedAtText.value) {
    return `قفل سراسری وب/پیام‌رسان از ${globalWebLockedAtText.value}`;
  }
  if (globalLockGraceExpiresAtText.value) {
    return `پایان مهلت فعال‌سازی: ${globalLockGraceExpiresAtText.value}`;
  }
  return 'این حساب از بازار خارج شده و تا فعال‌سازی مجدد، دسترسی معاملاتی ندارد.';
});

async function saveRole() {
  if (!canEditRole.value) return;
  if (!props.jwtToken || isUserMutationBusy.value) return;
  roleError.value = '';
  const result = await runUserUpdate({
    action: 'role',
    body: { role: selectedRole.value },
    expected: { role: selectedRole.value },
    fallbackError: 'ذخیره نقش کاربر ناموفق بود.',
  });
  if (result.outcome === 'success') {
    isEditingRole.value = false;
    setActionSuccess('نقش کاربر تغییر کرد.');
  } else if (result.outcome === 'error') {
    roleError.value = errorMessage(result.error, 'ذخیره نقش کاربر ناموفق بود.');
  }
}

function openConfirmation(confirmation: Omit<PendingConfirmation, 'key'> & { action: string }) {
  if (!canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
  const { action, ...pending } = confirmation;
  pendingConfirmation.value = { ...pending, key: actionKey(action) };
  confirmationError.value = '';
}

function closeConfirmation() {
  if (confirmationBusy.value) return;
  pendingConfirmation.value = null;
  confirmationError.value = '';
}

function toggleAccountStatus() {
  if (!props.jwtToken || !canPerformSensitiveAdminActions.value) return;
  const targetStatus = isAccountInactive.value ? 'active' : 'inactive';
  const verb = targetStatus === 'active' ? 'فعال' : 'غیرفعال';
  const message = targetStatus === 'inactive'
    ? `فوری: بازار بسته می‌شود. خروج کاربر از کانال تلگرام مورد انتظار است؛ نتیجه آن در این صفحه قابل‌تأیید نیست. اگر حساب تا پایان مهلت دو روزه دوباره فعال نشود، دسترسی وب و پیام‌رسان قفل و نشست‌ها لغو می‌شوند.${ZERO_MUTATION_HINT}`
    : `این حساب دوباره فعال شود؟ دسترسی‌های مجاز فقط پس از پاسخ معتبر سرور بازمی‌گردند.${ZERO_MUTATION_HINT}`;
  openConfirmation({
    kind: 'account-status',
    action: 'account-status',
    targetStatus,
    title: `تأیید ${verb}‌سازی حساب`,
    message,
    confirmLabel: `${verb} کردن`,
    tone: targetStatus === 'inactive' ? 'danger' : 'warning',
  });
}

async function blockUser(minutes: number) {
  if (!props.jwtToken || !canPerformSensitiveAdminActions.value) return;

  if (minutes === -1) {
      customDate.value = ''; // Reset custom date
      showCustomDateInput.value = true;
      return;
  }

  if (isUserMutationBusy.value) return;
  let restrictedUntil: string;
  if (minutes === 0) {
    // نامحدود (100 سال)
    const date = new Date();
    date.setFullYear(date.getFullYear() + 100);
    restrictedUntil = date.toISOString();
  } else {
    const date = new Date();
    date.setMinutes(date.getMinutes() + minutes);
    restrictedUntil = date.toISOString();
  }
  await sendBlockRequest(restrictedUntil);
}

async function blockUserCustom() {
  if (!canPerformSensitiveAdminActions.value) return;
  if (!customDate.value) {
    blockError.value = 'لطفاً یک تاریخ معتبر انتخاب کنید.';
    return;
  }
  if (isUserMutationBusy.value) return;

  const normalizedDate = toEnglishDigits(customDate.value);
  const isoDate = parseJalaliToIranISO(normalizedDate);
  if (!isoDate) {
    blockError.value = 'تاریخ نامعتبر است.';
    return;
  }
  await sendBlockRequest(isoDate);
}

async function sendBlockRequest(restrictedUntil: string) {
  blockError.value = '';
  const result = await runUserUpdate({
    action: 'block',
    body: { trading_restricted_until: restrictedUntil },
    expected: {
      trading_restricted_until: (value) => isSameDateTime(value, restrictedUntil),
    },
    fallbackError: 'اعمال مسدودیت ناموفق بود.',
  });
  if (result.outcome === 'success') {
    showBlockModal.value = false;
    showCustomDateInput.value = false;
    setActionSuccess('کاربر مسدود شد.');
  } else if (result.outcome === 'error') {
    blockError.value = errorMessage(result.error, 'اعمال مسدودیت ناموفق بود.');
  }
}

async function saveLimitations() {
  if (!props.jwtToken || !canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
  limitationsError.value = '';
  let expireAt: string | null = null;
  if (limitDurationMinutes.value === -1) {
    if (!customLimitDate.value) {
      limitationsError.value = 'لطفاً تاریخ پایان محدودیت را انتخاب کنید.';
      return;
    }
    expireAt = parseJalaliToIranISO(customLimitDate.value);
    if (!expireAt) {
      limitationsError.value = 'تاریخ نامعتبر است.';
      return;
    }
  } else if (limitDurationMinutes.value > 0) {
    const date = new Date();
    date.setMinutes(date.getMinutes() + limitDurationMinutes.value);
    expireAt = date.toISOString();
  }

  const body = {
    max_daily_trades: limitMaxTrades.value,
    max_active_commodities: limitMaxCommodities.value,
    max_daily_requests: limitMaxRequests.value,
    limitations_expire_at: expireAt,
  };
  const result = await runUserUpdate({
    action: 'limitations',
    body,
    expected: {
      max_daily_trades: body.max_daily_trades,
      max_active_commodities: body.max_active_commodities,
      max_daily_requests: body.max_daily_requests,
      limitations_expire_at: expireAt === null
        ? null
        : (value) => isSameDateTime(value, expireAt),
    },
    fallbackError: 'ذخیره محدودیت‌ها ناموفق بود.',
  });
  if (result.outcome === 'success') {
    showLimitationsModal.value = false;
    setActionSuccess('محدودیت‌ها اعمال شد.');
  } else if (result.outcome === 'error') {
    limitationsError.value = errorMessage(result.error, 'ذخیره محدودیت‌ها ناموفق بود.');
  }
}

function openLimitationsModal() {
    if (!canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
    limitMaxTrades.value = props.user.max_daily_trades;
    limitMaxCommodities.value = props.user.max_active_commodities;
    limitMaxRequests.value = props.user.max_daily_requests;
    // We don't easily know the duration from expire_at, so reset duration to default
    limitDurationMinutes.value = 0;
    customLimitDate.value = ''; // Reset custom date
    limitationsError.value = '';
    showLimitationsModal.value = true;
}

function closeLimitationsModal() {
  if (isUserMutationBusy.value) return;
  showLimitationsModal.value = false;
}

function openBlockModal() {
  if (!canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
  blockError.value = '';
  showBlockModal.value = true;
}

function closeBlockModal() {
  if (isUserMutationBusy.value) return;
  showBlockModal.value = false;
}



function unblockUser() {
  if (!props.jwtToken || !canPerformSensitiveAdminActions.value) return;
  openConfirmation({
    kind: 'unblock',
    action: 'unblock',
    title: 'تأیید رفع مسدودیت',
    message: `مسدودیت معاملاتی این کاربر برداشته شود؟${ZERO_MUTATION_HINT}`,
    confirmLabel: 'رفع مسدودیت',
    tone: 'warning',
  });
}

// Check if user has active limitations
const hasLimitations = computed(() => {
    return props.user.max_daily_trades != null ||
           props.user.max_active_commodities != null ||
           props.user.max_daily_requests != null;
});

function removeLimitations() {
  if (!props.jwtToken || !canPerformSensitiveAdminActions.value) return;
  openConfirmation({
    kind: 'remove-limitations',
    action: 'remove-limitations',
    title: 'تأیید رفع محدودیت‌ها',
    message: `همه محدودیت‌های ثبت‌شده برای این کاربر برداشته شود؟${ZERO_MUTATION_HINT}`,
    confirmLabel: 'رفع محدودیت‌ها',
    tone: 'warning',
  });
}

async function saveMaxSessions() {
  if (!canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
  quotaFeedback.value = null;
  const requestedValue = hasFixedSingleSessionLimit.value ? 1 : editMaxSessions.value;
  editMaxSessions.value = requestedValue;
  const result = await runUserUpdate({
    action: 'max-sessions',
    body: { max_sessions: requestedValue },
    expected: { max_sessions: requestedValue },
    fallbackError: 'ذخیره تنظیمات نشست ناموفق بود.',
  });
  if (result.outcome === 'success') {
    setQuotaSuccess('حداکثر نشست همزمان ذخیره شد.');
  } else if (result.outcome === 'error') {
    setQuotaError(result.error, 'ذخیره تنظیمات نشست ناموفق بود.');
  }
}

function handleMaxSessionsSelect(value: string) {
  const parsed = Number(value);
  editMaxSessions.value = Number.isFinite(parsed) ? parsed : 1;
  void saveMaxSessions();
}

async function saveMaxAccountants() {
  if (!canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
  const normalizedValue = Number.isFinite(editMaxAccountants.value)
    ? Math.max(0, Math.trunc(editMaxAccountants.value))
    : 0;
  editMaxAccountants.value = normalizedValue;
  quotaFeedback.value = null;
  const result = await runUserUpdate({
    action: 'max-accountants',
    body: { max_accountants: normalizedValue },
    expected: { max_accountants: normalizedValue },
    fallbackError: 'ذخیره سقف حسابداران ناموفق بود.',
  });
  if (result.outcome === 'success') {
    setQuotaSuccess('سقف حسابداران ذخیره شد.');
  } else if (result.outcome === 'error') {
    setQuotaError(result.error, 'ذخیره سقف حسابداران ناموفق بود.');
  }
}

async function saveMaxCustomers() {
  if (!canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
  const normalizedValue = Number.isFinite(editMaxCustomers.value)
    ? Math.max(0, Math.trunc(editMaxCustomers.value))
    : 0;
  editMaxCustomers.value = normalizedValue;
  quotaFeedback.value = null;
  const result = await runUserUpdate({
    action: 'max-customers',
    body: { max_customers: normalizedValue },
    expected: { max_customers: normalizedValue },
    fallbackError: 'ذخیره سقف مشتریان ناموفق بود.',
  });
  if (result.outcome === 'success') {
    setQuotaSuccess('سقف مشتریان ذخیره شد.');
  } else if (result.outcome === 'error') {
    setQuotaError(result.error, 'ذخیره سقف مشتریان ناموفق بود.');
  }
}

async function toggleBlockCapability() {
  if (!canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
  const nextValue = !canBlockUsers.value;
  quotaFeedback.value = null;
  const result = await runUserUpdate({
    action: 'block-capability',
    body: { can_block_users: nextValue },
    expected: { can_block_users: nextValue },
    fallbackError: 'ذخیره مجوز بلاک ناموفق بود.',
  });
  if (result.outcome === 'success') {
    canBlockUsers.value = nextValue;
    setQuotaSuccess(`حق بلاک‌کردن دیگران ${nextValue ? 'فعال' : 'غیرفعال'} شد.`);
  } else if (result.outcome === 'error') {
    setQuotaError(result.error, 'ذخیره مجوز بلاک ناموفق بود.');
  }
}

async function saveMaxBlockedUsers() {
  if (!canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
  const normalizedValue = Number.isFinite(editMaxBlockedUsers.value)
    ? Math.min(100, Math.max(1, Math.trunc(editMaxBlockedUsers.value)))
    : 10;
  editMaxBlockedUsers.value = normalizedValue;
  quotaFeedback.value = null;
  const result = await runUserUpdate({
    action: 'max-blocked-users',
    body: { max_blocked_users: normalizedValue },
    expected: { max_blocked_users: normalizedValue },
    fallbackError: 'ذخیره سقف بلاک ناموفق بود.',
  });
  if (result.outcome === 'success') {
    setQuotaSuccess('سقف کاربران قابل بلاک ذخیره شد.');
  } else if (result.outcome === 'error') {
    setQuotaError(result.error, 'ذخیره سقف بلاک ناموفق بود.');
  }
}

function terminateAllSessions() {
  if (!canPerformSensitiveAdminActions.value) return;
  openConfirmation({
    kind: 'terminate-sessions',
    action: 'terminate-sessions',
    title: 'پایان همه نشست‌ها',
    message: 'همه نشست‌های فعال این کاربر فوراً پایان یابد؟',
    confirmLabel: 'پایان نشست‌ها',
    tone: 'danger',
  });
}

function handleAdminSessionClick() {
  if (!canPerformSensitiveAdminActions.value) return;
  if (hasFixedSingleSessionLimit.value) {
    quotaFeedback.value = {
      tone: 'error',
      message: 'به دلایل امنیتی، تعداد نشست‌های مجاز برای مدیران سایت نمی‌تواند بیش از ۱ باشد.',
    };
  }
}

function deleteUser() {
  if (!props.jwtToken || !canPerformSensitiveAdminActions.value) return;
  openConfirmation({
    kind: 'delete-user',
    action: 'delete-user',
    title: 'حذف کاربر',
    message: 'حذف این حساب دسترسی وب‌اپ و بات را غیرفعال می‌کند، همه نشست‌ها را پایان می‌دهد، آفرهای فعال را منقضی و دعوت‌های در انتظار را لغو می‌کند و روابط مشتری/حسابدار متعلق یا لینک‌شده را به‌صورت بازگشتی می‌بندد. این اقدام بازگشت‌پذیر نیست.',
    confirmLabel: 'حذف کاربر',
    tone: 'danger',
  });
}

async function confirmPendingAction() {
  const confirmation = pendingConfirmation.value;
  if (!confirmation || !canPerformSensitiveAdminActions.value || isUserMutationBusy.value) return;
  confirmationError.value = '';

  if (confirmation.kind === 'account-status' && confirmation.targetStatus) {
    const expected = confirmation.targetStatus === 'inactive'
      ? {
          account_status: 'inactive',
          global_lock_grace_expires_at: isDateTimeString,
          global_web_locked_at: null,
        }
      : {
          account_status: 'active',
          global_lock_grace_expires_at: null,
          global_web_locked_at: null,
        };
    const result = await runUserUpdate({
      action: 'account-status',
      body: { account_status: confirmation.targetStatus },
      expected,
      fallbackError: 'تغییر وضعیت حساب ناموفق بود.',
    });
    if (result.outcome === 'success') {
      accountStatus.value = confirmation.targetStatus;
      if (confirmation.targetStatus === 'inactive') {
        const deadline = formatAccountStatusDate(props.user.global_lock_grace_expires_at);
        setActionSuccess(deadline
          ? `حساب غیرفعال شد. مهلت فعال‌سازی تا ${deadline} است.`
          : 'وضعیت حساب غیرفعال شد.');
      } else {
        setActionSuccess('وضعیت حساب فعال شد.');
      }
      closeConfirmation();
    } else if (result.outcome === 'error') {
      confirmationError.value = getSafeConfirmationError(confirmation.kind, result.error);
    }
    return;
  }

  if (confirmation.kind === 'unblock') {
    const result = await runUserUpdate({
      action: 'unblock',
      body: { trading_restricted_until: null },
      expected: { trading_restricted_until: null },
      fallbackError: 'رفع مسدودیت ناموفق بود.',
    });
    if (result.outcome === 'success') {
      customDate.value = '';
      setActionSuccess('رفع مسدودیت انجام شد.');
      closeConfirmation();
    } else if (result.outcome === 'error') {
      confirmationError.value = getSafeConfirmationError(confirmation.kind, result.error);
    }
    return;
  }

  if (confirmation.kind === 'remove-limitations') {
    const clearedLimits = {
      max_daily_trades: null,
      max_active_commodities: null,
      max_daily_requests: null,
      limitations_expire_at: null,
    };
    const result = await runUserUpdate({
      action: 'remove-limitations',
      body: clearedLimits,
      expected: clearedLimits,
      fallbackError: 'رفع محدودیت‌ها ناموفق بود.',
    });
    if (result.outcome === 'success') {
      setActionSuccess('محدودیت‌ها برداشته شد.');
      closeConfirmation();
    } else if (result.outcome === 'error') {
      confirmationError.value = getSafeConfirmationError(confirmation.kind, result.error);
    }
    return;
  }

  if (confirmation.kind === 'terminate-sessions') {
    const result = await runJsonAction({
      action: 'terminate-sessions',
      url: `/api/users/${props.user.id}/sessions/terminate-all`,
      method: 'POST',
      validate: (receipt) => isRecord(receipt)
        && Number.isInteger(receipt.terminated_sessions)
        && receipt.terminated_sessions >= 0,
      fallbackError: 'پایان دادن به نشست‌های فعال ناموفق بود.',
    });
    if (result.outcome === 'success') {
      const terminatedSessions = (result.receipt as UserRecord).terminated_sessions as number;
      setActionSuccess(terminatedSessions > 0
        ? `${terminatedSessions} نشست پایان یافت.`
        : 'نشست فعالی برای پایان دادن وجود نداشت.');
      closeConfirmation();
    } else if (result.outcome === 'error') {
      confirmationError.value = getSafeConfirmationError(confirmation.kind, result.error);
    }
    return;
  }

  if (confirmation.kind === 'delete-user') {
    const result = await runJsonAction({
      action: 'delete-user',
      url: `/api/users/${props.user.id}`,
      method: 'DELETE',
      validate: (receipt) => isRecord(receipt) && receipt.message === 'User deleted successfully',
      fallbackError: 'حذف کاربر ناموفق بود.',
    });
    if (result.outcome === 'success') {
      closeConfirmation();
      emit('navigate', 'manage_users');
    } else if (result.outcome === 'error') {
      confirmationError.value = getSafeConfirmationError(confirmation.kind, result.error);
    }
  }
}
</script>

<template>
  <div class="admin-user-profile">
    <ProfileIdentityHeader
      :display-name="userDisplayName"
      :avatar-initial="userDisplayName.slice(0, 1)"
      :hide-back-button="!isAdminView"
      back-label="بازگشت به لیست کاربران"
      @back="$emit('navigate', 'manage_users')"
    >
      <template #title>
        <CustomerNameWithBadge
          v-if="showCustomerContext && user.customer_management_name"
          :name="userDisplayName"
        />
        <template v-else>{{ userDisplayName }}</template>
      </template>
    </ProfileIdentityHeader>

    <AppInsetGroup class="profile-details">
      <div class="detail-item">
        <span class="label">نام کاربری</span>
        <span class="value">
          <CustomerNameWithBadge
            v-if="showCustomerContext && user.customer_management_name"
            :name="userDisplayName"
            compact
          />
          <template v-else>{{ userDisplayName }}</template>
        </span>
      </div>
      <div class="detail-item">
        <span class="label">موبایل</span>
        <span class="value">{{ user.mobile_number }}</span>
      </div>
      <div class="detail-item">
        <span class="label">آدرس</span>
        <span class="value">{{ user.address || 'ثبت نشده' }}</span>
      </div>
      <div class="detail-item">
          <span class="label">نقش</span>
          <span class="value">{{ user.role }}</span>
      </div>
      <div class="detail-item">
          <span class="label">وضعیت حساب</span>
          <span class="value">{{ accountStatusText }}</span>
      </div>
        <div v-if="accountStatusDetailText" class="detail-item account-status-detail-row">
          <span class="label">جزئیات وضعیت</span>
          <span class="value">{{ accountStatusDetailText }}</span>
        </div>
      <div class="detail-item">
          <span class="label">وضعیت معاملات</span>
          <span class="value" :class="{ 'text-red': isRestricted }">{{ restrictionText }}</span>
      </div>

      <AppListItem
        v-if="isRestricted && countdownRestriction"
        class="restriction-countdown"
        title="زمان باقی‌مانده مسدودیت"
        :meta="countdownRestriction"
      />
      <AppListItem
        v-if="user.max_daily_trades"
        title="مجموع معاملات"
        :meta="`${user.trades_count ?? 0} / ${user.max_daily_trades}`"
      />
      <AppListItem
        v-if="user.max_active_commodities"
        title="مجموع کالا"
        :meta="`${user.commodities_traded_count ?? 0} / ${user.max_active_commodities}`"
      />
      <AppListItem
        v-if="user.max_daily_requests"
        title="مجموع لفظ"
        :meta="`${user.channel_messages_count ?? 0} / ${user.max_daily_requests}`"
      />
      <AppListItem
        v-if="user.limitations_expire_at"
        title="انقضای محدودیت"
        :meta="user.limitations_expire_at_jalali"
      />
      <AppListItem
        v-if="countdownLimitation"
        title="باقی‌مانده محدودیت"
        :meta="countdownLimitation"
      />
      <p v-if="showCustomerContext" class="profile-details-heading">اطلاعات مشتری</p>
      <AppListItem
        v-if="showCustomerContext"
        title="نام مدیریتی"
      >
        <template #trailing>
          <CustomerNameWithBadge
            v-if="user.customer_management_name"
            :name="user.customer_management_name"
            compact
          />
          <template v-else>---</template>
        </template>
      </AppListItem>
      <AppListItem
        v-if="showCustomerContext"
        title="مالک"
        :meta="user.customer_owner_account_name || '---'"
      />
      <AppListItem
        v-if="showCustomerContext"
        title="سطح مشتری"
        :meta="getCustomerTierLabel(user.customer_tier)"
      />

    </AppInsetGroup>

      <p
        v-if="isAdminView && isSensitiveAdminTargetReadOnly"
        class="admin-sensitive-readonly"
        role="status"
        aria-live="polite"
      >
        {{ adminReadOnlyMessage }}
      </p>

      <p
        v-if="actionFeedback"
        class="user-action-feedback"
        :class="`user-action-feedback--${actionFeedback.tone}`"
        :role="actionFeedback.tone === 'error' ? 'alert' : 'status'"
        aria-live="polite"
      >
        {{ actionFeedback.message }}
      </p>

      <!-- تنظیمات نشست -->
      <div v-if="isAdminView && canPerformSensitiveAdminActions" class="sessions-config-box">
        <div class="detail-item">
          <span class="label">حداکثر نشست همزمان</span>
          <div class="inline-edit" @click="handleAdminSessionClick">
            <AppSelect
              :model-value="maxSessionsSelectValue"
              class="form-select-sm"
              :options="maxSessionOptions"
              :disabled="hasFixedSingleSessionLimit || isUserMutationBusy"
              :style="{ pointerEvents: hasFixedSingleSessionLimit ? 'none' : 'auto' }"
              @update:modelValue="handleMaxSessionsSelect"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">حداکثر حسابداران مجاز</span>
          <div class="inline-edit">
            <AppInput
              v-model.number="editMaxAccountants"
              type="number"
              min="0"
              step="1"
              class="form-input-sm max-accountants-input"
              aria-label="حداکثر حسابداران مجاز"
              :disabled="isUserMutationBusy"
              @change="saveMaxAccountants"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">حداکثر مشتریان مجاز</span>
          <div class="inline-edit">
            <AppInput
              v-model.number="editMaxCustomers"
              type="number"
              min="0"
              step="1"
              class="form-input-sm max-customers-input"
              aria-label="حداکثر مشتریان مجاز"
              :disabled="isUserMutationBusy"
              @change="saveMaxCustomers"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">حق بلاک‌کردن دیگران</span>
          <div class="inline-edit">
            <AppButton
              type="button"
              class="inline-control toggle-block-capability-btn"
              :class="{ 'is-disabled': !canBlockUsers }"
              variant="secondary"
              size="sm"
              :disabled="isUserMutationBusy"
              @click="toggleBlockCapability"
            >
              {{ canBlockUsers ? 'فعال' : 'غیرفعال' }}
            </AppButton>
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">سقف کاربران قابل بلاک</span>
          <div class="inline-edit">
            <AppInput
              v-model.number="editMaxBlockedUsers"
              type="number"
              min="1"
              max="100"
              step="1"
              class="form-input-sm max-blocked-users-input"
              aria-label="سقف کاربران قابل بلاک"
              :disabled="isUserMutationBusy"
              @change="saveMaxBlockedUsers"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row terminate-sessions-row">
          <span class="label">نشست‌های فعال کاربر</span>
          <div class="inline-edit">
            <AppButton
              type="button"
              class="inline-control danger-inline-btn terminate-sessions-btn"
              variant="danger"
              size="sm"
              :disabled="isUserMutationBusy"
              @click="terminateAllSessions"
            >
              {{ isTerminatingSessions ? 'در حال قطع...' : 'پایان فوری همه نشست‌ها' }}
            </AppButton>
          </div>
        </div>
        <p
          v-if="quotaFeedback"
          class="user-action-feedback quota-feedback"
          :class="`user-action-feedback--${quotaFeedback.tone}`"
          :role="quotaFeedback.tone === 'error' ? 'alert' : 'status'"
          aria-live="polite"
        >
          {{ quotaFeedback.message }}
        </p>
      </div>

      <!-- ویرایش نقش (مودال داخلی) -->
      <div v-if="isEditingRole && canEditRole" class="edit-section">
        <AppFormField v-slot="{ id }" label="انتخاب نقش جدید:">
          <AppSelect :id="id" v-model="selectedRole" class="form-select" :options="roles" :disabled="isUserMutationBusy" />
        </AppFormField>
        <p v-if="roleError" class="user-action-feedback user-action-feedback--error" role="alert">{{ roleError }}</p>
        <div class="action-buttons ds-native-actions ds-native-actions--split">
            <AppButton class="cancel-btn" variant="secondary" :disabled="isUserMutationBusy" @click="isEditingRole = false">انصراف</AppButton>
            <AppButton class="save-btn" variant="primary" :disabled="isUserMutationBusy" :loading="isActionBusy('role')" @click="saveRole">
              ذخیره
            </AppButton>
        </div>
      </div>

      <!-- منوی مدیریت (فقط ادمین) -->
      <template v-if="isAdminView">
        <AppInsetGroup v-if="!showSettings || !canPerformSensitiveAdminActions" class="main-actions profile-menu-card" title="مدیریت کاربر">
            <template v-if="canPerformSensitiveAdminActions">
              <AppListItem
                class="profile-control settings-btn"
                title="تنظیمات کاربر"
                interactive
                @select="showSettings = true"
              >
                <template #leading><Settings :size="20" /></template>
                <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
              </AppListItem>
              <AppListItem
                class="profile-control delete-btn"
                title="حذف کاربر"
                interactive
                :disabled="isUserMutationBusy"
                @select="deleteUser"
              >
                <template #leading><Trash2 :size="20" /></template>
                <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
              </AppListItem>
            </template>
            <AppListItem
              class="profile-control back-btn"
              title="بازگشت به لیست"
              interactive
              @select="$emit('navigate', 'manage_users')"
            >
              <template #leading><ChevronLeft :size="20" /></template>
            </AppListItem>
        </AppInsetGroup>

        <AppInsetGroup v-else-if="canPerformSensitiveAdminActions" class="settings-menu profile-menu-card" title="تنظیمات کاربر">
          <AppListItem
            class="profile-control"
            :title="`تغییر وضعیت حساب (${isAccountInactive ? 'غیرفعال' : 'فعال'})`"
            interactive
            :disabled="isUserMutationBusy"
            @select="toggleAccountStatus"
          >
            <template #leading><RotateCcw :size="20" /></template>
            <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
          </AppListItem>
            <AppListItem
              v-if="canEditRole"
              class="profile-control"
              title="ویرایش نقش"
              interactive
              :disabled="isUserMutationBusy"
              @select="roleError = ''; isEditingRole = true"
            >
              <template #leading><Pencil :size="20" /></template>
              <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
            </AppListItem>

            <AppListItem
              v-if="!hasLimitations"
              class="profile-control"
              title="اعمال محدودیت"
              interactive
              :disabled="isUserMutationBusy"
              @select="openLimitationsModal"
            >
              <template #leading><AlertTriangle :size="20" /></template>
              <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
            </AppListItem>
            <AppListItem
              v-else
              class="profile-control unlimit-btn"
              title="رفع محدودیت"
              interactive
              :disabled="isUserMutationBusy"
              @select="removeLimitations"
            >
              <template #leading><Check :size="20" /></template>
              <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
            </AppListItem>

            <AppListItem
              v-if="!isRestricted"
              class="profile-control block-btn"
              title="مسدود کردن"
              interactive
              :disabled="isUserMutationBusy"
              @select="openBlockModal"
            >
              <template #leading><Ban :size="20" /></template>
              <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
            </AppListItem>
            <AppListItem
              v-else
              class="profile-control unblock-btn"
              title="رفع مسدودیت"
              interactive
              :disabled="isUserMutationBusy"
              @select="unblockUser"
            >
              <template #leading><Undo2 :size="20" /></template>
              <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
            </AppListItem>

            <AppListItem
              class="profile-control back-btn"
              title="بازگشت"
              interactive
              :disabled="isUserMutationBusy"
              @select="showSettings = false"
            >
              <template #leading><ChevronLeft :size="20" /></template>
            </AppListItem>
        </AppInsetGroup>
      </template>

    <!-- مودال انتخاب مدت زمان مسدودیت -->
    <AppResponsiveDialog
      :open="showBlockModal && canPerformSensitiveAdminActions"
      title="مدت زمان مسدودیت"
      backdrop-class="modal-overlay"
      panel-class="modal-content"
      @close="closeBlockModal"
    >
      <div v-if="!showCustomDateInput">
        <div class="duration-list">
          <AppButton
            v-for="duration in blockDurations"
            :key="duration.minutes"
            class="duration-btn"
            variant="secondary"
            size="sm"
            :disabled="isUserMutationBusy"
            @click="blockUser(duration.minutes)"
          >
            {{ duration.label }}
          </AppButton>
        </div>
      </div>

      <div v-else class="custom-date-section">
        <span class="custom-date-label">تاریخ و زمان پایان مسدودیت:</span>
        <AppButton
          type="button"
          class="custom-date-trigger"
          variant="secondary"
          :disabled="isUserMutationBusy"
          @click="initDatePicker(customDate); showBlockDateModal = true"
        >
          {{ customDate || 'انتخاب تاریخ...' }}
        </AppButton>
      </div>

      <p v-if="blockError" class="user-action-feedback user-action-feedback--error" role="alert">{{ blockError }}</p>
      <template #actions>
        <template v-if="showCustomDateInput">
          <AppButton class="cancel-btn" variant="secondary" :disabled="isUserMutationBusy" @click="showCustomDateInput = false">بازگشت</AppButton>
          <AppButton class="save-btn" variant="primary" :disabled="isUserMutationBusy" :loading="isActionBusy('block')" @click="blockUserCustom">
            تایید نهایی
          </AppButton>
        </template>
        <AppButton
          v-else
          class="cancel-btn full-width"
          variant="secondary"
          :disabled="isUserMutationBusy"
          @click="closeBlockModal"
        >
          انصراف
        </AppButton>
      </template>
    </AppResponsiveDialog>

    <!-- مودال اعمال محدودیت -->
    <AppResponsiveDialog
      :open="showLimitationsModal && canPerformSensitiveAdminActions"
      title="اعمال محدودیت"
      backdrop-class="modal-overlay"
      panel-class="modal-content"
      @close="closeLimitationsModal"
    >
      <div class="limitations-form">
      <AppFormField v-slot="{ id }" label="مجموع تعداد معاملات:">
        <AppInput :id="id" v-model.number="limitMaxTrades" class="form-input" type="number" min="0" placeholder="نامحدود (خالی)" :disabled="isUserMutationBusy" />
      </AppFormField>
      <AppFormField v-slot="{ id }" label="مجموع تعداد کالای معامله شده:">
        <AppInput :id="id" v-model.number="limitMaxCommodities" class="form-input" type="number" min="0" placeholder="نامحدود (خالی)" :disabled="isUserMutationBusy" />
      </AppFormField>
      <AppFormField v-slot="{ id }" label="مجموع ارسال لفظ در کانال:">
        <AppInput :id="id" v-model.number="limitMaxRequests" class="form-input" type="number" min="0" placeholder="نامحدود (خالی)" :disabled="isUserMutationBusy" />
      </AppFormField>

      <AppFormField v-slot="{ id }" label="مدت زمان محدودیت:">
        <AppSelect
          :id="id"
          :model-value="limitDurationSelectValue"
          class="form-select"
          :options="limitDurationOptions"
          :disabled="isUserMutationBusy"
          @update:modelValue="setLimitDurationValue"
        />
      </AppFormField>
      </div>

      <div v-if="limitDurationMinutes === -1" class="custom-date-section">
        <span class="custom-date-label">تاریخ پایان:</span>
        <AppButton
          type="button"
          class="custom-date-trigger"
          variant="secondary"
          :disabled="isUserMutationBusy"
          @click="initDatePicker(customLimitDate); showLimitDateModal = true"
        >
          {{ customLimitDate || 'انتخاب تاریخ...' }}
        </AppButton>
      </div>

      <p v-if="limitationsError" class="user-action-feedback user-action-feedback--error" role="alert">{{ limitationsError }}</p>
      <template #actions>
        <AppButton class="cancel-btn" variant="secondary" :disabled="isUserMutationBusy" @click="closeLimitationsModal">انصراف</AppButton>
        <AppButton class="save-btn" variant="primary" :disabled="isUserMutationBusy" :loading="isActionBusy('limitations')" @click="saveLimitations">
          ذخیره
        </AppButton>
      </template>
    </AppResponsiveDialog>

    <template v-if="!isAdminView">
      <AppInsetGroup class="profile-user-actions profile-menu-card" title="اقدام‌ها">
        <AppListItem
          class="profile-control notification-btn"
          title="صندوق پیام‌ها"
          interactive
          @select="emit('navigate', 'notifications')"
        >
          <template #leading><Bell :size="20" /></template>
          <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
        </AppListItem>
        <AppListItem
          v-if="user.role !== 'عادی'"
          class="profile-control settings-btn"
          title="تنظیمات"
          interactive
          @select="emit('navigate', 'user_settings')"
        >
          <template #leading><Settings :size="20" /></template>
          <template #trailing><ChevronLeft :size="18" aria-hidden="true" /></template>
        </AppListItem>
      </AppInsetGroup>
    </template>

    <!-- Moved Block Date Modal -->
    <AppResponsiveDialog
      :open="showBlockDateModal && canPerformSensitiveAdminActions"
      :title="pickerStep === 1 ? 'انتخاب تاریخ' : 'انتخاب ساعت'"
      backdrop-class="modal-overlay date-modal-overlay"
      panel-class="modal-content date-modal-content"
      body-class="date-modal-body"
      :show-close="false"
      :close-on-backdrop="false"
      :close-on-escape="false"
      @close="showBlockDateModal = false"
    >
      <div class="date-picker-wrapper">
        <!-- Step 1: Date -->
        <JalaliDatePicker
          v-if="pickerStep === 1"
          v-model="tempDatePart"
          inline
          value-type="jalali"
          arrow-key-navigation
          :clearable="false"
          :auto-close="false"
          @change="updateDatePart"
        />
        <!-- Step 2: Time - Using native HTML5 input for reliability -->
        <div v-if="pickerStep === 2" class="native-time-picker">
          <label>ساعت مورد نظر:</label>
          <AppInput
            type="time"
            v-model="tempTimePart"
            class="time-input"
            aria-label="ساعت مورد نظر"
          />
        </div>
      </div>
      <!-- Footer moved outside wrapper to ensure visibility -->
      <div class="integrated-footer ds-native-actions ds-native-actions--split">
        <AppButton class="integrated-cancel-btn cancel-btn" variant="secondary" @click="showBlockDateModal = false">انصراف</AppButton>
        <AppButton v-if="pickerStep === 1" class="integrated-save-btn save-btn" variant="primary" @click="handleNextStep">ادامه</AppButton>
        <AppButton v-if="pickerStep === 2" class="integrated-save-btn save-btn" variant="primary" @click="handleFinalSubmit">تایید نهایی</AppButton>
      </div>
    </AppResponsiveDialog>

    <!-- Moved Limit Date Modal -->
    <AppResponsiveDialog
      :open="showLimitDateModal && canPerformSensitiveAdminActions"
      :title="pickerStep === 1 ? 'انتخاب تاریخ' : 'انتخاب ساعت'"
      backdrop-class="modal-overlay date-modal-overlay"
      panel-class="modal-content date-modal-content"
      body-class="date-modal-body"
      :show-close="false"
      :close-on-backdrop="false"
      :close-on-escape="false"
      @close="showLimitDateModal = false"
    >
      <div class="date-picker-wrapper">
        <!-- Step 1: Date -->
        <JalaliDatePicker
          v-if="pickerStep === 1"
          v-model="tempDatePart"
          inline
          value-type="jalali"
          arrow-key-navigation
          :clearable="false"
          :auto-close="false"
          @change="updateDatePart"
        />
        <!-- Step 2: Time - Using native HTML5 input for reliability -->
        <div v-if="pickerStep === 2" class="native-time-picker">
          <label>ساعت مورد نظر:</label>
          <AppInput
            type="time"
            v-model="tempTimePart"
            class="time-input"
            aria-label="ساعت مورد نظر"
          />
        </div>
      </div>
      <!-- Footer moved outside wrapper to ensure visibility -->
      <div class="integrated-footer ds-native-actions ds-native-actions--split">
        <AppButton class="integrated-cancel-btn cancel-btn" variant="secondary" @click="showLimitDateModal = false">انصراف</AppButton>
        <AppButton v-if="pickerStep === 1" class="integrated-save-btn save-btn" variant="primary" @click="handleNextStep">ادامه</AppButton>
        <AppButton v-if="pickerStep === 2" class="integrated-save-btn save-btn" variant="primary" @click="handleFinalSubmit">تایید نهایی</AppButton>
      </div>
    </AppResponsiveDialog>

    <AppConfirmDialog
      v-if="pendingConfirmation && canPerformSensitiveAdminActions"
      :open="Boolean(pendingConfirmation && canPerformSensitiveAdminActions)"
      :title="pendingConfirmation.title"
      :message="pendingConfirmation.message"
      :confirm-label="pendingConfirmation.confirmLabel"
      :tone="pendingConfirmation.tone"
      :busy="confirmationBusy"
      :error="confirmationError"
      :confirm-disabled="confirmationBusy"
      @cancel="closeConfirmation"
      @confirm="confirmPendingAction"
    />
  </div>
</template>

<style>
/* Integrated Footer Styles */
.integrated-footer {
    width: 100%;
    max-width: 320px;
    padding: 0.75rem;
    border-top: 1px solid var(--ds-border-light);
    background: var(--ds-bg-card);
    text-align: center;
    border-bottom-left-radius: var(--ds-radius-md);
    border-bottom-right-radius: var(--ds-radius-md);
    margin-top: -5px;
    z-index: 10;
    display: flex;
    justify-content: space-between;
    gap: 0.625rem;
}

.integrated-save-btn,
.integrated-cancel-btn {
    flex: 1;
}

/* Native time picker styles */
.native-time-picker {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 1.25rem;
    background: var(--ds-bg-inset);
    border-radius: var(--ds-radius-md);
    margin: 0.625rem 0;
}

.native-time-picker label {
    font-size: var(--ds-font-lg);
    font-weight: 800;
    color: var(--ds-text-primary);
    margin-bottom: 0.875rem;
}

.native-time-picker .time-input {
    font-size: 1.5rem;
    min-height: 3.5rem;
    padding: 0.875rem 1.25rem;
    border: 1.5px solid var(--ds-control-border, var(--ds-border-medium));
    border-radius: var(--ds-control-radius, 12px);
    text-align: center;
    background: var(--ds-control-bg, var(--ds-bg-subtle));
    color: var(--ds-text-primary);
    width: auto;
    min-width: 10rem;
}

.native-time-picker .time-input:focus-visible {
    outline: none;
    border-color: var(--ds-primary-500);
    box-shadow: var(--ds-focus-ring);
}


/* Hide original actions just in case they appear */
.vpd-actions {
    display: none !important;
}

.vpd-main {
    height: auto !important;
    overflow: visible !important;
    flex: 1 !important;
}

.vpd-days {
    height: auto !important;
    overflow: visible !important;
    padding-bottom: 10px !important;
}

/* Hide header submit/check buttons to prevent reset bug */
.vpd-header button[class*="vpd-"][class$="-btn"] svg {
    display: block;
}

/* Specific fix for the top-left button in Time View (which resets date) */
/* In RTL, :last-child is usually the left-most element in a flex row */
.vpd-content.vpd-is-time .vpd-header button[class*="vpd-"][class$="-btn"]:last-child,
.vpd-time .vpd-header button[class*="vpd-"][class$="-btn"]:last-child {
    display: none !important;
}

/* Hide any button explicitly named 'submit' or having check icon class in header */
.vpd-header button[class*="vpd-"][class$="-btn"].vpd-check,
.vpd-header button[class*="vpd-"][class$="-btn"].vpd-tick {
    display: none !important;
}

/* Hide library footer to use custom buttons */
.vpd-actions {
    display: none !important;
}

/* More specific width control to prevent full-width ugly look */
.date-picker-wrapper {
    width: 100%;
    max-width: 320px; /* Force standard calendar width */
    margin: 0 auto; /* Center it */
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}

.date-modal-content {
    width: auto !important;
    min-width: 300px;
    max-width: 360px !important;
    padding: 0 !important; /* Remove padding to let picker fill nicely */
    background: transparent !important; /* Let picker bg show */
    box-shadow: none !important; /* Remove double shadow */
    display: flex;
    flex-direction: column;
    align-items: center;
}

.date-modal-overlay {
    z-index: 2010;
}

.date-modal-body {
    width: 100%;
    padding: 0 !important;
    overflow: visible !important;
    display: flex;
    flex-direction: column;
    align-items: center;
}

/* Hide redundant input field (white box with date) in inline mode */
.date-picker-wrapper .vpd-input-group {
    display: none !important;
}

.date-modal-content .ui-responsive-dialog__header {
    background: #fff;
    width: 100%;
    text-align: center;
    padding: 10px;
    max-width: 320px;
    border: 0;
    border-radius: 8px 8px 0 0;
    justify-content: center;
}

.date-modal-content .ui-responsive-dialog__header h2 {
    font-size: 1rem;
    color: #333;
}

/* Stronger layout fix for width */
.vpd-container, .vpd-wrapper {
    width: 100% !important;
    max-width: 320px !important;
    margin: 0 auto !important;
}

/* Fix for top-left header button in Time View */
/* Hide ALL icon buttons in header in time view to prevent accidental resets */
.vpd-content.vpd-is-time .vpd-header button[class*="vpd-"][class$="-btn"],
.vpd-time .vpd-header button[class*="vpd-"][class$="-btn"] {
    display: none !important;
}

/* Just in case, explicit hide for check/tick class if present */
.vpd-header button[class*="vpd-"][class$="-btn"].vpd-check,
.vpd-header button[class*="vpd-"][class$="-btn"].vpd-tick {
    display: none !important;
}

/* Neutral trigger that looks like an input but acts like a div */
.custom-date-trigger {
    display: block;
    width: 100%;
    min-height: var(--ds-touch-target);
    transition: all 0.2s;
    text-align: center;
}

.custom-date-trigger:active {
    background-color: var(--ds-bg-inset);
    border-color: var(--ds-primary-400);
}

.date-picker-wrapper {
    display: flex;
    justify-content: center;
    margin-bottom: 20px;
}
</style>

<style scoped>
.admin-user-profile {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
  font-family: Vazirmatn, Tahoma, Arial, sans-serif;
  font-synthesis: none;
}
.admin-user-profile > .header-row {
  padding: 0 0 0.25rem;
  background: transparent;
  max-width: none;
}
.profile-nav-back {
  justify-self: start;
}
h2 {
  margin: 0;
  font-size: var(--ds-font-lg);
  font-weight: 800;
  color: var(--ds-text-primary);
}
.profile-details {
  display: grid;
  gap: 0;
  min-width: 0;
  overflow: hidden;
  border-radius: 12px;
  background: var(--ds-bg-card);
}
.detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
  min-height: var(--ds-native-row-min-height, 48px);
  padding: 0.75rem 0.875rem;
  background: var(--ds-bg-card);
  border: 0;
  border-block-end: 1px solid var(--ds-native-hairline);
  border-radius: 0;
}
.detail-item:last-child {
  border-block-end: 0;
}
.label {
  font-weight: 700;
  color: var(--ds-text-placeholder);
  font-size: var(--ds-font-sm);
}
.value {
  font-weight: 700;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-base);
  min-width: 0;
  text-align: left;
}
.value.code {
  direction: ltr;
  font-family: var(--ds-font-mono);
}

.edit-section {
  background: var(--ds-primary-50);
  padding: 1rem;
  border-radius: var(--ds-radius-lg);
  border: 1px solid rgba(245, 158, 11, 0.18);
  margin-top: 0.25rem;
  display: grid;
  gap: 0.75rem;
}
.form-select,
.form-input {
  width: 100%;
  min-height: var(--ds-control-min-height, 48px);
  box-sizing: border-box;
  transition: all 0.2s;
}
input[type="number"].form-select,
input[type="number"].form-input {
  -moz-appearance: textfield;
  appearance: textfield;
  cursor: text;
  user-select: text;
}
input[type="number"].form-select::-webkit-outer-spin-button,
input[type="number"].form-select::-webkit-inner-spin-button,
input[type="number"].form-input::-webkit-outer-spin-button,
input[type="number"].form-input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.action-buttons {
  margin-top: 0.75rem;
}

.profile-menu-card {
  position: relative;
  margin-top: 0.25rem;
  padding: 0;
  border: 0;
  border-radius: 12px;
  background: var(--ds-bg-card);
  box-shadow: none;
  display: flex;
  flex-direction: column;
  gap: 0;
  overflow: hidden;
  min-width: 0;
}

.profile-user-actions {
  margin-top: 0.25rem;
}

.profile-control {
  width: 100%;
  min-height: var(--ds-native-row-min-height, 48px);
  border: 0;
  border-radius: 0;
  background: var(--ds-bg-card);
  box-shadow: inset 0 -1px 0 var(--ds-native-hairline);
  transition: all 0.2s;
}

.text-red { color: var(--ds-danger-600); font-weight: 800; }

.modal-content {
  text-align: right;
}
.duration-list {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0.5rem;
  margin-bottom: 0.25rem;
}
.duration-btn {
  width: 100%;
  white-space: normal;
  transition: all 0.2s;
}
.duration-list .duration-btn:last-child {
  grid-column: 1 / -1;
}
.full-width { width: 100%; }
.custom-date-section {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-bottom: 0.25rem;
  text-align: right;
}
.custom-date-label {
  font-size: var(--ds-font-sm);
  font-weight: 700;
  color: var(--ds-text-secondary);
}
.limitations-form {
  display: grid;
  gap: 0.75rem;
}

.limitations-box {
  background: transparent;
  border: 0;
  border-block-start: 1px solid var(--ds-native-hairline);
  border-radius: 0;
  padding: 0.75rem 0 0;
}
.limitations-box h4 {
  margin: 0 0 0.5rem 0;
  font-size: var(--ds-font-base);
  font-weight: 800;
  color: var(--ds-text-primary);
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
.limit-item {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  font-size: var(--ds-font-sm);
  margin-bottom: 0.25rem;
  color: var(--ds-text-primary);
}
.limit-expiry {
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--ds-native-hairline);
  font-size: var(--ds-font-xs);
  color: var(--ds-text-secondary);
  display: flex;
  justify-content: space-between;
}
.usage-ratio {
  font-family: 'Vazirmatn', tahoma, sans-serif;
  font-weight: 700;
  color: var(--ds-text-primary);
  direction: ltr;
}

.countdown-box {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 0;
  border-radius: 0;
  animation: none;
}
.restriction-countdown {
  background: transparent;
  border: 0;
  border-block-start: 1px solid var(--ds-native-hairline);
}
.countdown-icon { font-size: 1rem; }
.countdown-label { font-size: var(--ds-font-sm); color: var(--ds-danger-800); }
.countdown-value {
  font-family: 'Vazirmatn', var(--ds-font-mono);
  font-size: var(--ds-font-base);
  font-weight: 700;
  color: var(--ds-danger-600);
  background: var(--ds-bg-card);
  padding: 0.25rem 0.625rem;
  border-radius: var(--ds-radius-sm);
  margin-right: auto;
  direction: ltr;
}
.countdown-inline {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 0.625rem;
  padding-top: 0.625rem;
  border-top: 1px dashed var(--ds-primary-300);
}
.countdown-inline .countdown-icon { font-size: 0.8rem; }
.countdown-inline .countdown-label { font-size: var(--ds-font-xs); color: var(--ds-primary-800); }
.countdown-inline .countdown-value {
  font-size: var(--ds-font-sm);
  font-weight: 700;
  color: var(--ds-primary-600);
  background: var(--ds-primary-100);
  padding: 0.15rem 0.5rem;
  border-radius: var(--ds-radius-sm);
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.85; }
}

.sessions-config-box {
  margin: 0.25rem 0;
  padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  display: grid;
  gap: 0;
}
.admin-sensitive-readonly {
  margin: 0.25rem 0;
  padding: 0.7rem 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-base);
  line-height: 1.75;
}
.user-action-feedback {
  margin: 0.25rem 0;
  padding: 0.65rem 0.75rem;
  border: 1px solid;
  border-radius: var(--ds-radius-md);
  font-size: var(--ds-font-sm);
  line-height: 1.7;
  text-align: right;
}
.user-action-feedback--success {
  color: var(--ds-success-800);
  background: var(--ds-success-50);
  border-color: rgba(16, 185, 129, 0.28);
}
.user-action-feedback--error {
  color: var(--ds-danger-800);
  background: var(--ds-danger-50);
  border-color: var(--ds-danger-200);
}
.quota-feedback {
  margin-bottom: 0;
}
.sessions-config-box .detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--ds-bg-card);
}
.owner-limit-row {
  margin-top: 0;
}
.inline-edit {
  display: flex;
  align-items: center;
  min-width: 0;
}
.form-select-sm {
  min-width: 4.5rem;
}
.form-input-sm {
  width: 5.5rem;
  min-height: var(--ds-control-min-height, 48px);
  text-align: center;
}
.inline-control.is-disabled {
  color: var(--ds-danger-800);
  background: var(--ds-danger-50);
  border-color: var(--ds-danger-200);
}
.terminate-sessions-row {
  align-items: flex-start;
}
.admin-lock-note {
  font-size: var(--ds-font-sm);
  color: var(--ds-text-muted);
  margin-right: 0.5rem;
}

@media (prefers-reduced-motion: reduce) {
  .form-select,
  .form-input,
  .save-btn,
  .cancel-btn,
  .profile-control,
  .duration-btn,
  .custom-date-trigger {
    transition: none;
  }
  .countdown-box {
    animation: none;
  }
}
</style>
