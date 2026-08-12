<script setup lang="ts">
import { ref, computed, watchEffect, onUnmounted, watch, toRef } from 'vue';
import moment from 'moment-jalaali';
import {
  AlertTriangle,
  Ban,
  Bell,
  Check,
  ChevronLeft,
  Clock,
  Pencil,
  RotateCcw,
  Settings,
  Trash2,
  Undo2,
  Users,
} from 'lucide-vue-next';
import { ActionContractError, useActionState } from '../composables/useActionState';
import { useUserProfileTiming } from '../composables/useUserProfileTiming';
import { MIDDLE_MANAGER_ROLE, SUPER_ADMIN_ROLE } from '../utils/adminAccess';
import { currentUserSummary } from '../utils/currentUser';
import { isAppHttpError, normalizeErrorPresentation } from '../utils/httpErrorPolicy';
import { formatIranDateTime } from '../utils/iranTime';
import { routeRequest } from '../utils/routeRequest';
import CustomerNameWithBadge from './CustomerNameWithBadge.vue';
import HelpPopover from './HelpPopover.vue';
import JalaliDatePicker from './JalaliDatePicker.vue';
import { AppConfirmDialog, AppResponsiveDialog, AppSelect } from './ui';

type UserProfileUser = {
  id: number;
  account_name?: string | null;
  mobile_number?: string | null;
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
  <div class="card">
    <div class="header-row">
        <div class="header-spacer"></div>
        <div class="header-title">
            <h2>{{ isAdminView ? 'مدیریت کاربر' : 'پروفایل کاربری' }}</h2>
        </div>
        <button v-if="isAdminView" class="profile-nav-back" @click="$emit('navigate', 'manage_users')"><ChevronLeft :size="24" /></button>
    </div>

    <div class="profile-details">
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

      <!-- تایمر شمارش معکوس مسدودیت -->
      <div v-if="isRestricted && countdownRestriction" class="countdown-box restriction-countdown">
          <span class="countdown-icon" aria-hidden="true"><Clock :size="15" /></span>
          <span class="countdown-label">زمان باقی‌مانده مسدودیت:</span>
          <span class="countdown-value">{{ countdownRestriction }}</span>
      </div>

      <!-- نمایش محدودیت‌ها -->
      <div v-if="user.max_daily_trades || user.max_active_commodities || user.max_daily_requests" class="limitations-box">
          <h4><AlertTriangle :size="17" aria-hidden="true" /> محدودیت‌های فعال:</h4>
          <div v-if="user.max_daily_trades" class="limit-item">
              <span>مجموع معاملات:</span> <span class="usage-ratio">{{ user.trades_count ?? 0 }} / {{ user.max_daily_trades }}</span>
          </div>
          <div v-if="user.max_active_commodities" class="limit-item">
              <span>مجموع کالا:</span> <span class="usage-ratio">{{ user.commodities_traded_count ?? 0 }} / {{ user.max_active_commodities }}</span>
          </div>
          <div v-if="user.max_daily_requests" class="limit-item">
              <span>مجموع لفظ:</span> <span class="usage-ratio">{{ user.channel_messages_count ?? 0 }} / {{ user.max_daily_requests }}</span>
          </div>
          <div v-if="user.limitations_expire_at" class="limit-expiry">
              <span>انقضا:</span> <span>{{ user.limitations_expire_at_jalali }}</span>
          </div>
          <!-- تایمر شمارش معکوس محدودیت -->
          <div v-if="countdownLimitation" class="countdown-inline">
              <span class="countdown-icon" aria-hidden="true"><Clock :size="15" /></span>
              <span class="countdown-label">باقی‌مانده:</span>
              <span class="countdown-value">{{ countdownLimitation }}</span>
          </div>
      </div>

          <div v-if="showCustomerContext" class="limitations-box customer-context-box">
            <h4><Users :size="17" aria-hidden="true" /> اطلاعات مشتری</h4>
            <div class="limit-item">
              <span>نام مدیریتی:</span>
              <span>
                <CustomerNameWithBadge
                  v-if="user.customer_management_name"
                  :name="user.customer_management_name"
                  compact
                />
                <template v-else>---</template>
              </span>
            </div>
            <div class="limit-item">
              <span>مالک:</span>
              <span>{{ user.customer_owner_account_name || '---' }}</span>
            </div>
            <div class="limit-item">
              <span>سطح مشتری:</span>
              <span>{{ getCustomerTierLabel(user.customer_tier) }}</span>
            </div>
          </div>

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
            <input
              v-model.number="editMaxAccountants"
              type="number"
              min="0"
              step="1"
              class="form-input-sm max-accountants-input"
              :disabled="isUserMutationBusy"
              @change="saveMaxAccountants"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">حداکثر مشتریان مجاز</span>
          <div class="inline-edit">
            <input
              v-model.number="editMaxCustomers"
              type="number"
              min="0"
              step="1"
              class="form-input-sm max-customers-input"
              :disabled="isUserMutationBusy"
              @change="saveMaxCustomers"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">حق بلاک‌کردن دیگران</span>
          <div class="inline-edit">
            <button
              type="button"
              class="inline-control toggle-block-capability-btn"
              :class="{ 'is-disabled': !canBlockUsers }"
              :disabled="isUserMutationBusy"
              @click="toggleBlockCapability"
            >
              {{ canBlockUsers ? 'فعال' : 'غیرفعال' }}
            </button>
          </div>
        </div>
        <div class="detail-item owner-limit-row">
          <span class="label">سقف کاربران قابل بلاک</span>
          <div class="inline-edit">
            <input
              v-model.number="editMaxBlockedUsers"
              type="number"
              min="1"
              max="100"
              step="1"
              class="form-input-sm max-blocked-users-input"
              :disabled="isUserMutationBusy"
              @change="saveMaxBlockedUsers"
            />
          </div>
        </div>
        <div class="detail-item owner-limit-row terminate-sessions-row">
          <span class="label">نشست‌های فعال کاربر</span>
          <div class="inline-edit">
            <button
              type="button"
              class="inline-control danger-inline-btn terminate-sessions-btn"
              :disabled="isUserMutationBusy"
              @click="terminateAllSessions"
            >
              {{ isTerminatingSessions ? 'در حال قطع...' : 'پایان فوری همه نشست‌ها' }}
            </button>
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
        <div class="form-group">
            <label>انتخاب نقش جدید:</label>
            <AppSelect v-model="selectedRole" class="form-select" :options="roles" :disabled="isUserMutationBusy" />
        </div>
        <p v-if="roleError" class="user-action-feedback user-action-feedback--error" role="alert">{{ roleError }}</p>
        <div class="action-buttons">
            <button @click="saveRole" :disabled="isUserMutationBusy" class="save-btn">
              {{ isActionBusy('role') ? 'در حال ذخیره...' : 'ذخیره' }}
            </button>
            <button @click="isEditingRole = false" :disabled="isUserMutationBusy" class="cancel-btn">انصراف</button>
        </div>
      </div>

      <!-- منوی مدیریت (فقط ادمین) -->
      <template v-if="isAdminView">
        <div v-if="!showSettings || !canPerformSensitiveAdminActions" class="main-actions profile-menu-card card-with-help">
            <HelpPopover
              floating
              button-test="user-profile-admin-menu-help"
              note-test="user-profile-admin-menu-help-note"
              label="راهنمای منوی مدیریت کاربر"
              :text="canPerformSensitiveAdminActions
                ? 'عملیات این بخش فقط روی همین کاربر اعمال می‌شود. حذف کاربر، نشست‌ها و دسترسی‌های فعال او را هم مدیریت می‌کند.'
                : 'اطلاعات این حساب برای مشاهده نمایش داده می‌شود؛ عملیات حساس مدیریتی از این مسیر مجاز نیست.'"
            />
            <template v-if="canPerformSensitiveAdminActions">
              <button @click="showSettings = true" class="profile-control settings-btn">
                <span class="profile-control__icon" aria-hidden="true"><Settings :size="18" /></span>
                <span class="profile-control__label">تنظیمات کاربر</span>
              </button>
              <button @click="deleteUser" :disabled="isUserMutationBusy" class="profile-control delete-btn">
                <span class="profile-control__icon" aria-hidden="true"><Trash2 :size="18" /></span>
                <span class="profile-control__label">حذف کاربر</span>
              </button>
            </template>
            <button @click="$emit('navigate', 'manage_users')" class="profile-control back-btn">
              <span class="profile-control__icon" aria-hidden="true"><ChevronLeft :size="18" /></span>
              <span class="profile-control__label">بازگشت به لیست</span>
            </button>
        </div>

        <div v-else-if="canPerformSensitiveAdminActions" class="settings-menu profile-menu-card card-with-help">
          <HelpPopover
            floating
            button-test="user-profile-settings-menu-help"
            note-test="user-profile-settings-menu-help-note"
            label="راهنمای زیرمنوی تنظیمات کاربر"
            text="این زیرمنو برای تغییر وضعیت حساب، نقش، محدودیت و مسدودیت کاربر است. گزینه‌های حذف یا بازگشت در منوی قبلی قرار دارند."
          />
          <button @click="toggleAccountStatus" :disabled="isUserMutationBusy" class="profile-control">
            <span class="profile-control__icon" aria-hidden="true"><RotateCcw :size="18" /></span>
            <span class="profile-control__label">تغییر وضعیت حساب ({{ isAccountInactive ? 'غیرفعال' : 'فعال' }})</span>
            </button>
            <button v-if="canEditRole" @click="roleError = ''; isEditingRole = true" :disabled="isUserMutationBusy" class="profile-control">
              <span class="profile-control__icon" aria-hidden="true"><Pencil :size="18" /></span>
              <span class="profile-control__label">ویرایش نقش</span>
            </button>

            <button v-if="!hasLimitations" @click="openLimitationsModal" :disabled="isUserMutationBusy" class="profile-control">
              <span class="profile-control__icon" aria-hidden="true"><AlertTriangle :size="18" /></span>
              <span class="profile-control__label">اعمال محدودیت</span>
            </button>
            <button v-else @click="removeLimitations" :disabled="isUserMutationBusy" class="profile-control unlimit-btn">
                <span class="profile-control__icon" aria-hidden="true"><Check :size="18" /></span>
                <span class="profile-control__label">رفع محدودیت</span>
            </button>

            <button v-if="!isRestricted" @click="openBlockModal" :disabled="isUserMutationBusy" class="profile-control block-btn">
                <span class="profile-control__icon" aria-hidden="true"><Ban :size="18" /></span>
                <span class="profile-control__label">مسدود کردن</span>
            </button>
            <button v-else @click="unblockUser" :disabled="isUserMutationBusy" class="profile-control unblock-btn">
                <span class="profile-control__icon" aria-hidden="true"><Undo2 :size="18" /></span>
                <span class="profile-control__label">رفع مسدودیت</span>
            </button>

            <button @click="showSettings = false" class="profile-control back-btn">
              <span class="profile-control__icon" aria-hidden="true"><ChevronLeft :size="18" /></span>
              <span class="profile-control__label">بازگشت</span>
            </button>
        </div>
      </template>
    </div>

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
          <button v-for="duration in blockDurations" :key="duration.minutes"
                  :disabled="isUserMutationBusy"
                  @click="blockUser(duration.minutes)" class="duration-btn">
            {{ duration.label }}
          </button>
        </div>
      </div>

      <div v-else class="custom-date-section">
        <label>تاریخ و زمان پایان مسدودیت:</label>
        <div
          class="custom-date-trigger"
          @click="initDatePicker(customDate); showBlockDateModal = true"
        >
          {{ customDate || 'انتخاب تاریخ...' }}
        </div>

        <div class="action-buttons">
          <button @click="blockUserCustom" :disabled="isUserMutationBusy" class="save-btn">
            {{ isActionBusy('block') ? 'در حال ثبت...' : 'تایید نهایی' }}
          </button>
          <button @click="showCustomDateInput = false" :disabled="isUserMutationBusy" class="cancel-btn">بازگشت</button>
        </div>
      </div>

      <p v-if="blockError" class="user-action-feedback user-action-feedback--error" role="alert">{{ blockError }}</p>
      <button
        v-if="!showCustomDateInput"
        @click="closeBlockModal"
        :disabled="isUserMutationBusy"
        class="cancel-btn full-width"
      >
        انصراف
      </button>
    </AppResponsiveDialog>

    <!-- مودال اعمال محدودیت -->
    <AppResponsiveDialog
      :open="showLimitationsModal && canPerformSensitiveAdminActions"
      title="اعمال محدودیت"
      backdrop-class="modal-overlay"
      panel-class="modal-content"
      @close="closeLimitationsModal"
    >
      <div class="form-group">
        <label>مجموع تعداد معاملات:</label>
        <input type="number" v-model.number="limitMaxTrades" class="form-input" min="0" placeholder="نامحدود (خالی)" :disabled="isUserMutationBusy" />
      </div>
      <div class="form-group">
        <label>مجموع تعداد کالای معامله شده:</label>
        <input type="number" v-model.number="limitMaxCommodities" class="form-input" min="0" placeholder="نامحدود (خالی)" :disabled="isUserMutationBusy" />
      </div>
      <div class="form-group">
        <label>مجموع ارسال لفظ در کانال:</label>
        <input type="number" v-model.number="limitMaxRequests" class="form-input" min="0" placeholder="نامحدود (خالی)" :disabled="isUserMutationBusy" />
      </div>

      <div class="form-group">
        <label>مدت زمان محدودیت:</label>
        <AppSelect
          :model-value="limitDurationSelectValue"
          class="form-select"
          :options="limitDurationOptions"
          :disabled="isUserMutationBusy"
          @update:modelValue="setLimitDurationValue"
        />
      </div>

      <div v-if="limitDurationMinutes === -1" class="custom-date-section">
        <label>تاریخ پایان:</label>
        <div
          class="custom-date-trigger"
          @click="initDatePicker(customLimitDate); showLimitDateModal = true"
        >
          {{ customLimitDate || 'انتخاب تاریخ...' }}
        </div>
      </div>

      <p v-if="limitationsError" class="user-action-feedback user-action-feedback--error" role="alert">{{ limitationsError }}</p>
      <div class="action-buttons">
        <button @click="saveLimitations" :disabled="isUserMutationBusy" class="save-btn">
          {{ isActionBusy('limitations') ? 'در حال ذخیره...' : 'ذخیره' }}
        </button>
        <button @click="closeLimitationsModal" :disabled="isUserMutationBusy" class="cancel-btn">انصراف</button>
      </div>
    </AppResponsiveDialog>

    <template v-if="!isAdminView">
      <div class="profile-user-actions profile-menu-card card-with-help">
        <HelpPopover
          floating
          button-test="profile-user-actions-help"
          note-test="profile-user-actions-help-note"
          label="راهنمای پنل کاربری"
          text="از این بخش به پیام‌های سیستمی و تنظیمات مجاز همین حساب دسترسی داری. گزینه‌های مدیریتی فقط برای ادمین‌ها نمایش داده می‌شود."
        />
        <button class="profile-control notification-btn" @click="emit('navigate', 'notifications')">
          <span class="profile-control__icon" aria-hidden="true"><Bell :size="18" /></span>
          <span class="profile-control__label">صندوق پیام‌ها</span>
        </button>
        <!-- دکمه تنظیمات فقط برای نقش‌های غیر عادی -->
        <button
          v-if="user.role !== 'عادی'"
          class="profile-control settings-btn"
          @click="emit('navigate', 'user_settings')"
        >
          <span class="profile-control__icon" aria-hidden="true"><Settings :size="18" /></span>
          <span class="profile-control__label">تنظیمات</span>
        </button>
      </div>
    </template>
  </div>

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
          :clearable="false"
          :auto-close="false"
          @change="updateDatePart"
        />
        <!-- Step 2: Time - Using native HTML5 input for reliability -->
        <div v-if="pickerStep === 2" class="native-time-picker">
          <label>ساعت مورد نظر:</label>
          <input
            type="time"
            v-model="tempTimePart"
            class="time-input"
          />
        </div>
      </div>
      <!-- Footer moved outside wrapper to ensure visibility -->
      <div class="integrated-footer">
        <button @click="showBlockDateModal = false" class="integrated-cancel-btn">انصراف</button>

        <button v-if="pickerStep === 1" @click="handleNextStep" class="integrated-save-btn">ادامه</button>
        <button v-if="pickerStep === 2" @click="handleFinalSubmit" class="integrated-save-btn">تایید نهایی</button>
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
          :clearable="false"
          :auto-close="false"
          @change="updateDatePart"
        />
        <!-- Step 2: Time - Using native HTML5 input for reliability -->
        <div v-if="pickerStep === 2" class="native-time-picker">
          <label>ساعت مورد نظر:</label>
          <input
            type="time"
            v-model="tempTimePart"
            class="time-input"
          />
        </div>
      </div>
      <!-- Footer moved outside wrapper to ensure visibility -->
      <div class="integrated-footer">
        <button @click="showLimitDateModal = false" class="integrated-cancel-btn">انصراف</button>

        <button v-if="pickerStep === 1" @click="handleNextStep" class="integrated-save-btn">ادامه</button>
        <button v-if="pickerStep === 2" @click="handleFinalSubmit" class="integrated-save-btn">تایید نهایی</button>
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
</template>

<style>
/* Integrated Footer Styles */
.integrated-footer {
    width: 100%;
    max-width: 320px;
    padding: 10px;
    border-top: 1px solid #eee;
    background: #fff;
    text-align: center;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
    margin-top: -5px; /* Pull it up slightly to connect with picker */
    z-index: 10;
    display: flex;
    justify-content: space-between;
    gap: 10px;
}

.integrated-save-btn {
    flex: 1;
    background: linear-gradient(135deg, #f59e0b, #d97706);
    color: white;
    border: none;
    padding: 10px;
    border-radius: 10px;
    font-size: 15px;
    font-weight: bold;
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(245, 158, 11, 0.25);
}

.integrated-cancel-btn {
    flex: 1;
    background-color: white;
    color: #6b7280;
    border: 1px solid rgba(245, 158, 11, 0.15);
    padding: 10px;
    border-radius: 10px;
    font-size: 15px;
    font-weight: bold;
    cursor: pointer;
}

/* Native time picker styles */
.native-time-picker {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 20px;
    background: #f8f9fa;
    border-radius: 12px;
    margin: 10px 0;
}

.native-time-picker label {
    font-size: 18px;
    font-weight: bold;
    color: #333;
    margin-bottom: 15px;
}

.native-time-picker .time-input {
    font-size: 32px;
    padding: 15px 25px;
    border: 2px solid #f59e0b;
    border-radius: 12px;
    text-align: center;
    background: white;
    color: #1f2937;
    width: auto;
    min-width: 150px;
}

.native-time-picker .time-input:focus {
    outline: none;
    border-color: #d97706;
    box-shadow: 0 0 10px rgba(245, 158, 11, 0.3);
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
    padding: 0.375rem 0.75rem; /* Match Bootstrap/form-select padding */
    font-size: 1rem;
    font-weight: 400;
    line-height: 1.5;
    color: #212529;
    background-color: #fff;
    background-clip: padding-box;
    border: 1px solid #ced4da;
    border-radius: 0.25rem;
    cursor: pointer;
    user-select: none;
    -webkit-user-select: none;
    -webkit-tap-highlight-color: transparent;
    transition: border-color .15s ease-in-out,box-shadow .15s ease-in-out;
    text-align: center; /* Center text for better mobile look */
}

.custom-date-trigger:active {
    background-color: #f8f9fa;
    border-color: #86b7fe;
}

.date-picker-wrapper {
    display: flex;
    justify-content: center;
    margin-bottom: 20px;
}
</style>

<style scoped>
.card {
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(245, 158, 11, 0.1);
  border-radius: 1.25rem;
  padding: 1.25rem;
  box-shadow: 0 4px 16px rgba(0,0,0,0.04);
}
h2 {
  margin-top: 0;
  margin-bottom: 1.25rem;
  font-size: 1rem;
  font-weight: 800;
  color: #1f2937;
}
.profile-details {
  display: grid;
  gap: 0.625rem;
  margin-bottom: 1.25rem;
}
.detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 0.875rem;
  background: white;
  border: 1px solid rgba(245, 158, 11, 0.08);
  border-radius: 0.875rem;
}
.label {
  font-weight: 700;
  color: #9ca3af;
  font-size: 0.75rem;
}
.value {
  font-weight: 700;
  color: #1f2937;
  font-size: 0.85rem;
}
.value.code {
  direction: ltr;
  font-family: monospace;
}




.edit-section {
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  padding: 1rem;
  border-radius: 1rem;
  border: 1px solid rgba(245, 158, 11, 0.15);
  margin-top: 0.875rem;
}
.form-group {
  margin-bottom: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}
.form-group label {
  font-size: 0.75rem;
  font-weight: 700;
  color: #6b7280;
}
.form-select, .form-input {
  padding: 0.625rem;
  border-radius: 0.75rem;
  border: 1px solid rgba(245, 158, 11, 0.15);
  background: white;
  font-family: inherit;
  font-size: 0.85rem;
  width: 100%;
  box-sizing: border-box;
  outline: none;
  transition: all 0.2s;
}
.form-select:focus, .form-input:focus {
  border-color: #f59e0b;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1);
}
input[type="number"].form-select, input[type="number"].form-input {
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
  display: flex;
  gap: 0.625rem;
  margin-top: 1rem;
}
.save-btn {
  flex: 1;
  padding: 0.625rem;
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white;
  border: none;
  border-radius: 0.75rem;
  cursor: pointer;
  font-weight: 700;
  font-size: 0.85rem;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.save-btn:active { transform: scale(0.98); }
.cancel-btn {
  flex: 1;
  padding: 0.625rem;
  background: white;
  color: #6b7280;
  border: 1px solid rgba(245, 158, 11, 0.15);
  border-radius: 0.75rem;
  cursor: pointer;
  font-weight: 700;
  font-size: 0.85rem;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.cancel-btn:active { background: #f9fafb; }

.profile-menu-card {
  position: relative;
  margin-top: 0.875rem;
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

.profile-user-actions {
  margin-top: 1rem;
}

.profile-control {
  width: 100%;
  min-height: 3.15rem;
  padding: 0.72rem 0.8rem;
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
.profile-control:hover {
  border-color: rgba(245, 158, 11, 0.3);
  background: #fffbeb;
}
.profile-control:active {
  transform: scale(0.98);
}
.profile-control__icon {
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

.profile-control__label {
  flex: 1;
  min-width: 0;
}

.text-red { color: #ef4444; font-weight: bold; }

.block-btn {
  background: #fef2f2 !important;
  color: #991b1b !important;
  border-color: #fecaca !important;
}
.block-btn .profile-control__icon,
.delete-btn .profile-control__icon {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
}
.unblock-btn {
  background: #f0fdf4 !important;
  color: #166534 !important;
  border-color: #bbf7d0 !important;
}
.unlimit-btn {
  background: #f0fdf4 !important;
  color: #166534 !important;
  border-color: #bbf7d0 !important;
}
.unblock-btn .profile-control__icon,
.unlimit-btn .profile-control__icon {
  background: rgba(34, 197, 94, 0.14);
  color: #166534;
}
.settings-btn {
  background: linear-gradient(135deg, #fffbeb, #fef3c7) !important;
  color: #92400e !important;
  border-color: rgba(245, 158, 11, 0.2) !important;
}
.delete-btn {
  background: #fef2f2 !important;
  color: #dc2626 !important;
  border-color: #fecaca !important;
}
.back-btn {
  background: white !important;
  color: #6b7280 !important;
  border-color: rgba(245, 158, 11, 0.12) !important;
}

/* Modal Styles */
.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  backdrop-filter: blur(4px);
  display: flex;
  z-index: 1000;
  padding: 1.25rem;
  overflow-y: auto;
}
.modal-content {
  background: white;
  border-radius: 1.5rem;
  padding: 1.25rem;
  width: 100%;
  max-width: 320px;
  min-height: 420px;
  box-shadow: 0 16px 40px rgba(0,0,0,0.15);
  text-align: center;
  margin: auto;
}
.modal-content h3 {
  margin-top: 0;
  margin-bottom: 1rem;
  font-size: 1rem;
  font-weight: 800;
  color: #1f2937;
}
.duration-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem;
  margin-bottom: 1rem;
}
.duration-btn {
  padding: 0.75rem;
  border: 1px solid rgba(245, 158, 11, 0.15);
  border-radius: 0.875rem;
  background: white;
  color: #1f2937;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.duration-btn:hover {
  background: #fffbeb;
  border-color: rgba(245, 158, 11, 0.3);
  color: #92400e;
}
.duration-btn:active { transform: scale(0.95); }
.full-width { width: 100%; }
.custom-date-section {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-bottom: 1rem;
  text-align: right;
}

.limitations-box {
  background: linear-gradient(135deg, #fffbeb, #fff7ed);
  border: 1px solid #fed7aa;
  border-radius: 0.875rem;
  padding: 0.75rem;
  margin-top: 0.5rem;
}
.limitations-box h4 {
  margin: 0 0 0.5rem 0;
  font-size: 0.8rem;
  font-weight: 700;
  color: #c2410c;
}
.limit-item {
  display: flex; justify-content: space-between;
  font-size: 0.75rem; margin-bottom: 0.25rem; color: #9a3412;
}
.limit-expiry {
  margin-top: 0.5rem; padding-top: 0.5rem;
  border-top: 1px dashed #fed7aa;
  font-size: 0.7rem; color: #ea580c;
  display: flex; justify-content: space-between;
}
.usage-ratio {
  font-family: 'Vazirmatn', tahoma, sans-serif;
  font-weight: 700; color: #c2410c; direction: ltr;
}

/* Countdown Timer Styles */
.countdown-box {
  display: flex; align-items: center; gap: 0.5rem;
  padding: 0.75rem 1rem; border-radius: 0.875rem; margin: 0.75rem 0;
  animation: pulse 2s infinite;
}
.restriction-countdown {
  background: linear-gradient(135deg, #fef2f2, #fee2e2);
  border: 1px solid #fecaca;
}
.countdown-icon { font-size: 1rem; }
.countdown-label { font-size: 0.75rem; color: #7f1d1d; }
.countdown-value {
  font-family: 'Vazirmatn', monospace;
  font-size: 0.8rem; font-weight: 700; color: #dc2626;
  background: rgba(255,255,255,0.7);
  padding: 0.25rem 0.625rem; border-radius: 0.5rem;
  margin-right: auto; direction: ltr;
}
.countdown-inline {
  display: flex; align-items: center; gap: 0.375rem;
  margin-top: 0.625rem; padding-top: 0.625rem;
  border-top: 1px dashed #fbbf24;
}
.countdown-inline .countdown-icon { font-size: 0.8rem; }
.countdown-inline .countdown-label { font-size: 0.7rem; color: #92400e; }
.countdown-inline .countdown-value {
  font-size: 0.75rem; font-weight: 700; color: #d97706;
  background: rgba(254, 243, 199, 0.8);
  padding: 0.15rem 0.5rem; border-radius: 0.375rem;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.85; }
}

.sessions-config-box {
  margin: 0.75rem 0;
  padding: 0.75rem;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 0.75rem;
}
.admin-sensitive-readonly {
  margin: 0.75rem 0;
  padding: 0.7rem 0.8rem;
  border: 1px solid #bfdbfe;
  border-radius: 0.75rem;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.8rem;
  line-height: 1.75;
}
.user-action-feedback {
  margin: 0.75rem 0;
  padding: 0.65rem 0.75rem;
  border: 1px solid;
  border-radius: 0.75rem;
  font-size: 0.78rem;
  line-height: 1.7;
  text-align: right;
}
.user-action-feedback--success {
  color: #166534;
  background: #f0fdf4;
  border-color: #bbf7d0;
}
.user-action-feedback--error {
  color: #991b1b;
  background: #fef2f2;
  border-color: #fecaca;
}
.quota-feedback {
  margin-bottom: 0;
}
.sessions-config-box .detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.owner-limit-row {
  margin-top: 0.5rem;
}
.inline-edit {
  display: flex;
  align-items: center;
}
.form-select-sm {
  padding: 0.25rem 0.5rem;
  border-radius: 0.5rem;
  border: 1px solid #d1d5db;
  font-size: 0.8rem;
  background: white;
}
.form-input-sm {
  width: 4.5rem;
  padding: 0.25rem 0.5rem;
  border-radius: 0.5rem;
  border: 1px solid #d1d5db;
  font-size: 0.8rem;
  background: white;
  text-align: center;
}
.inline-control {
  border: 1px solid #cbd5e1;
  background: white;
  color: #0f172a;
  border-radius: 0.625rem;
  padding: 0.4rem 0.75rem;
  font-size: 0.8rem;
  font-weight: 700;
  cursor: pointer;
}
.inline-control.is-disabled {
  color: #991b1b;
  background: #fef2f2;
  border-color: #fecaca;
}
.danger-inline-btn {
  color: #b91c1c;
  background: #fff1f2;
  border-color: #fecdd3;
}
.danger-inline-btn:disabled {
  opacity: 0.7;
  cursor: wait;
}
.terminate-sessions-row {
  align-items: flex-start;
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
}
</style><style scoped>
.admin-lock-note {
  font-size: 0.75rem;
  color: #8f9296;
  margin-right: 0.5rem;
}
</style>
