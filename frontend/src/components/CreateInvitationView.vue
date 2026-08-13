<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { routeRequest, routeRequestJson } from '../utils/routeRequest';
import { AppHttpError } from '../utils/httpErrorPolicy';
import { useActionState } from '../composables/useActionState';
import { getInvitableRoleOptions } from '../utils/adminAccess';
import { formatIranDateTime } from '../utils/iranTime';
import { invitationSmsStatusMessage, normalizeInvitationContract, type InvitationSmsStatus } from '../utils/invitationContract';
import { AppButton, AppConfirmDialog, AppEmptyState, AppErrorState, AppFormField, AppInput, AppLoadingState, AppSelect } from './ui';

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>();

interface PendingInvitation {
  id: number;
  account_name: string;
  mobile_number: string;
  role: string;
  bot_link?: string | null;
  web_link: string;
  web_short_link?: string | null;
  short_link?: string | null;
  bot_available?: boolean;
  web_available?: boolean;
  state?: string;
  sms_status?: InvitationSmsStatus | null;
  expires_at: string;
  created_at?: string | null;
}

// emit دیگر استفاده نمی‌شود
// const emit = defineEmits(['invite-created']);

const availableRoles = getInvitableRoleOptions();
const defaultInviteRole = availableRoles.find((role) => role.value === 'عادی')?.value ?? availableRoles[0]?.value ?? 'عادی';

const invite = reactive({
  account_name: '',
  mobile_number: '',
  role: defaultInviteRole,
});

const resultMessage = ref('');
const isLoading = ref(false);
const inviteLink = ref('');
const webLink = ref('');
const hasInvitationResult = ref(false);
const invitationCreated = ref<boolean | null>(null);
const invitationExpiresAt = ref('');
const smsStatusMessage = ref('');
const copyMessage = ref('');
const webCopyMessage = ref('');
const pendingInvitations = ref<PendingInvitation[]>([]);
const pendingLoading = ref(false);
const pendingError = ref('');
const pendingNotice = ref('');
const pendingHasLoaded = ref(false);
const pendingDeleteId = ref<number | null>(null);
const pendingDeleteCandidate = ref<PendingInvitation | null>(null);
const pendingDeleteError = ref('');
const pendingCopyState = reactive<Record<string, string>>({});
const pendingDeleteActions = useActionState<{ invitationId: number }, null>();

let isPendingViewActive = true;
let pendingRequestRevision = 0;
let pendingRequestController: AbortController | null = null;

onMounted(() => {
  if (props.jwtToken) {
    void loadPendingInvitations();
  }
});

onBeforeUnmount(() => {
  isPendingViewActive = false;
  invalidatePendingInvitationLoad();
});

function clearInvitationResult() {
  resultMessage.value = '';
  inviteLink.value = '';
  webLink.value = '';
  hasInvitationResult.value = false;
  invitationCreated.value = null;
  invitationExpiresAt.value = '';
  smsStatusMessage.value = '';
  copyMessage.value = '';
  webCopyMessage.value = '';
}

function resetForm() {
  invite.account_name = '';
  invite.mobile_number = '';
  invite.role = defaultInviteRole;
  clearInvitationResult();
}

function setCopyMessage(setter: (message: string) => void, message: string) {
  setter(message);
  setTimeout(() => setter(''), 2000);
}

function copyLink(link: string, setter: (message: string) => void) {
  if (!navigator.clipboard?.writeText) {
    setCopyMessage(setter, 'کپی نشد؛ دوباره تلاش کنید.');
    return;
  }

  void navigator.clipboard.writeText(link).then(
    () => setCopyMessage(setter, 'کپی شد!'),
    () => setCopyMessage(setter, 'کپی نشد؛ دوباره تلاش کنید.'),
  );
}

function copyToClipboard() {
  if (inviteLink.value) copyLink(inviteLink.value, (message) => { copyMessage.value = message; });
}

function copyWebLink() {
  if (webLink.value) copyLink(webLink.value, (message) => { webCopyMessage.value = message; });
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return 'نامشخص';
  return formatIranDateTime(value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }) || value;
}

function invalidatePendingInvitationLoad() {
  pendingRequestRevision += 1;
  pendingRequestController?.abort();
  pendingRequestController = null;
  if (isPendingViewActive) pendingLoading.value = false;
}

function clearPendingInvitationDataForForbidden() {
  invalidatePendingInvitationLoad();
  clearInvitationResult();
  pendingInvitations.value = [];
  pendingHasLoaded.value = false;
  pendingDeleteCandidate.value = null;
  pendingDeleteId.value = null;
  pendingDeleteError.value = '';
  pendingNotice.value = '';
  for (const key of Object.keys(pendingCopyState)) delete pendingCopyState[key];
  pendingError.value = 'دسترسی شما به فهرست دعوت‌نامه‌ها تأیید نشد. برای دریافت وضعیت تازه دوباره تلاش کنید.';
}

function isCurrentPendingRequest(revision: number, controller: AbortController) {
  return isPendingViewActive
    && revision === pendingRequestRevision
    && controller === pendingRequestController;
}

async function loadPendingInvitations(): Promise<boolean> {
  if (!props.jwtToken) {
    invalidatePendingInvitationLoad();
    if (isPendingViewActive) {
      pendingInvitations.value = [];
      pendingHasLoaded.value = false;
    }
    return false;
  }

  pendingRequestRevision += 1;
  const revision = pendingRequestRevision;
  pendingRequestController?.abort();
  const controller = new AbortController();
  pendingRequestController = controller;
  const preserveExistingData = pendingHasLoaded.value;

  pendingLoading.value = true;
  pendingError.value = '';
  pendingNotice.value = '';
  try {
    const data = await routeRequestJson<unknown>('/api/invitations/pending', {
      cache: 'no-store',
      signal: controller.signal,
      errorContext: {
        surface: 'admin',
        scope: 'list',
        operation: preserveExistingData ? 'background-refresh' : 'load-list',
        preserveExistingData,
        fallbackMessage: 'دریافت دعوت‌نامه‌ها ممکن نشد.',
      },
    });
    if (!isCurrentPendingRequest(revision, controller)) return false;
    if (!Array.isArray(data)) throw new Error('invalid_pending_invitations_payload');

    pendingInvitations.value = data as PendingInvitation[];
    pendingHasLoaded.value = true;
    return true;
  } catch (error) {
    if (!isCurrentPendingRequest(revision, controller) || controller.signal.aborted) return false;
    if (actionErrorStatus(error) === 403) {
      clearPendingInvitationDataForForbidden();
      return false;
    }
    pendingError.value = 'دریافت دعوت‌نامه‌ها ممکن نشد. دوباره تلاش کنید.';
    return false;
  } finally {
    if (isCurrentPendingRequest(revision, controller)) {
      pendingLoading.value = false;
      pendingRequestController = null;
    }
  }
}

function pendingCopyKey(invitationId: number, surface: 'bot' | 'web') {
  return `${invitationId}:${surface}`;
}

function copyPendingLink(invitation: PendingInvitation, surface: 'bot' | 'web') {
  const contract = normalizeInvitationContract(invitation);
  const link = surface === 'bot' ? contract.botLink : contract.webLink;
  if (!link) return;

  const copyKey = pendingCopyKey(invitation.id, surface);
  copyLink(link, (message) => { pendingCopyState[copyKey] = message; });
}

function requestPendingInvitationDelete(invitation: PendingInvitation) {
  if (pendingDeleteId.value !== null || pendingDeleteCandidate.value) return;
  pendingDeleteCandidate.value = invitation;
  pendingDeleteError.value = '';
  pendingNotice.value = '';
}

function cancelPendingInvitationDelete() {
  if (pendingDeleteId.value !== null) return;
  pendingDeleteCandidate.value = null;
  pendingDeleteError.value = '';
}

function actionErrorStatus(error: unknown): number | null {
  return error instanceof AppHttpError ? error.status : null;
}

async function reconcileNoLongerPendingInvitation(invitation: PendingInvitation) {
  const refreshed = await loadPendingInvitations();
  if (!isPendingViewActive) return;

  const invitationStillListed = pendingInvitations.value.some((item) => item.id === invitation.id);
  if (refreshed && !invitationStillListed) {
    pendingDeleteCandidate.value = null;
    pendingDeleteError.value = '';
    pendingNotice.value = 'دعوت‌نامه دیگر در انتظار نیست؛ فهرست به‌روز شد.';
    return;
  }

  pendingDeleteError.value = refreshed
    ? 'وضعیت دعوت‌نامه به‌روز شد؛ حذف آن در این لحظه تأیید نشد.'
    : 'وضعیت تازهٔ دعوت‌نامه دریافت نشد؛ دوباره تلاش کنید.';
}

async function confirmPendingInvitationDelete() {
  const invitation = pendingDeleteCandidate.value;
  if (!invitation || pendingDeleteId.value !== null) return;

  const actionKey = `delete-pending-invitation:${invitation.id}`;
  if (pendingDeleteActions.isBusy(actionKey)) return;

  pendingDeleteId.value = invitation.id;
  pendingDeleteError.value = '';
  const result = await pendingDeleteActions.run({
    key: actionKey,
    context: { invitationId: invitation.id },
    action: async () => {
      const response = await routeRequest(`/api/invitations/pending/${invitation.id}`, {
        method: 'DELETE',
        cache: 'no-store',
        errorContext: {
          surface: 'admin',
          scope: 'action',
          operation: 'delete',
          userInitiated: true,
          fallbackMessage: 'حذف دعوت‌نامه انجام نشد.',
        },
      });
      return { response, receipt: null };
    },
    validateReceipt: (receipt, _context, response) => receipt === null && response.status === 204,
  });

  if (result.outcome === 'success') {
    invalidatePendingInvitationLoad();
    pendingInvitations.value = pendingInvitations.value.filter((item) => item.id !== invitation.id);
    pendingDeleteCandidate.value = null;
    pendingDeleteError.value = '';
    pendingNotice.value = 'دعوت‌نامه از فهرست حذف شد.';
  } else if (result.outcome === 'error') {
    const status = actionErrorStatus(result.error);
    if (status === 400 || status === 404) {
      await reconcileNoLongerPendingInvitation(invitation);
    } else if (status === 403) {
      clearPendingInvitationDataForForbidden();
    } else {
      pendingDeleteError.value = 'حذف دعوت‌نامه انجام نشد؛ وضعیت آن از سرور تأیید نشد.';
    }
  }
  pendingDeleteId.value = null;
}

function invitationOutcomeMessage(created: boolean | null): string {
  if (created === true) return 'دعوت‌نامهٔ تازه ساخته شد.';
  if (created === false) return 'دعوت‌نامهٔ فعال قبلی بازیابی شد.';
  return 'دعوت‌نامهٔ فعال آماده است.';
}

async function createInvite() {
  if (isLoading.value) return;
  if (!props.jwtToken) {
    resultMessage.value = '❌ خطا: شما احراز هویت نشده‌اید.';
    return;
  }
  const normalizedMobile = normalizeMobile(invite.mobile_number);
  if (!/^09[0-9]{9}$/.test(normalizedMobile)) {
    resultMessage.value = '❌ شماره موبایل نامعتبر است. فرمت: 09xxxxxxxxx (فارسی یا انگلیسی)';
    return;
  }

  isLoading.value = true;
  clearInvitationResult();

  try {
    const data = await routeRequestJson<unknown>('/api/invitations/', {
      method: 'POST',
      cache: 'no-store',
      body: JSON.stringify({ ...invite, mobile_number: normalizedMobile }),
      errorContext: {
        surface: 'admin',
        scope: 'form',
        operation: 'submit',
        userInitiated: true,
        fallbackMessage: 'ساخت دعوت‌نامه انجام نشد.',
      },
    });

    const contract = normalizeInvitationContract(data);
    if (contract.state !== 'pending' || (!contract.botLink && !contract.webLink)) {
      resultMessage.value = '❌ لینک قابل استفاده‌ای برای این دعوت‌نامه آماده نشد.';
      throw new Error('unusable_invitation_contract');
    }

    inviteLink.value = contract.botLink;
    webLink.value = contract.webLink;
    invitationCreated.value = contract.created;
    invitationExpiresAt.value = contract.expiresAt;
    smsStatusMessage.value = invitationSmsStatusMessage(contract.smsStatus);
    hasInvitationResult.value = true;
    void loadPendingInvitations();
  } catch {
    if (!resultMessage.value.startsWith('❌')) {
      resultMessage.value = '❌ ساخت دعوت‌نامه انجام نشد. اطلاعات واردشده حفظ شده است؛ دوباره تلاش کنید.';
    }
  } finally {
    isLoading.value = false;
  }
}

function normalizeMobile(mobile: string): string {
  if (!mobile) return "";
  const persianMap = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
  };
  return mobile.replace(/[۰-۹٠-٩]/g, (match) => (persianMap as any)[match]);
}
</script>

<template>
  <div class="card">
    <form @submit.prevent="createInvite" autocomplete="off">
      <AppFormField class="form-group" id="account_name" label="نام کاربری (Account Name)">
        <AppInput v-model="invite.account_name" id="account_name" type="text" placeholder="مثلاً alireza" required />
      </AppFormField>
      <AppFormField class="form-group" id="mobile_number" label="شماره موبایل (ایران)">
        <AppInput v-model="invite.mobile_number" id="mobile_number" type="tel" placeholder="09123456789" required />
      </AppFormField>
      <AppFormField class="form-group" id="role" label="نقش">
        <AppSelect v-model="invite.role" id="role" :options="availableRoles" />
      </AppFormField>
      <div class="form-actions">
        <AppButton type="submit" :loading="isLoading" :disabled="pendingDeleteId !== null">
          {{ isLoading ? 'در حال ساخت...' : 'ارسال لینک دعوت' }}
        </AppButton>
        <AppButton type="button" class="secondary" variant="secondary" :disabled="isLoading || pendingDeleteId !== null" @click="resetForm">
          بازنشانی
        </AppButton>
      </div>
    </form>

    <div v-if="resultMessage && !hasInvitationResult" class="result-box error" role="alert">
      {{ resultMessage }}
    </div>

    <div v-if="hasInvitationResult" class="success-box" role="status">
      <div class="result-message">{{ invitationOutcomeMessage(invitationCreated) }}</div>
      <p class="invitation-expiry">مهلت دعوت: {{ formatDateTime(invitationExpiresAt) }}</p>
      <div v-if="inviteLink" class="link-label">لینک تلگرام آماده است.</div>
      <div v-if="inviteLink" class="copy-container">
        <AppButton type="button" @click="copyToClipboard" class="copy-btn">
          {{ copyMessage || 'کپی لینک تلگرام' }}
        </AppButton>
      </div>
      <div v-if="webLink" class="link-label" style="margin-top: 0.75rem;">لینک وب آماده است.</div>
      <div v-if="webLink" class="copy-container">
        <AppButton type="button" @click="copyWebLink" class="copy-btn web">
          {{ webCopyMessage || 'کپی لینک وب' }}
        </AppButton>
      </div>
      <p v-if="smsStatusMessage" class="sms-status" role="status">{{ smsStatusMessage }}</p>
    </div>

    <section class="pending-section" aria-labelledby="pending-invitations-title">
      <div class="pending-header">
        <div>
          <h3 id="pending-invitations-title">دعوت‌نامه‌های در انتظار</h3>
          <p>فهرست دعوت‌های در انتظار</p>
        </div>
        <AppButton type="button" class="pending-refresh-btn" variant="secondary" :loading="pendingLoading" :disabled="pendingDeleteId !== null" @click="loadPendingInvitations">
          {{ pendingLoading ? 'در حال دریافت...' : 'به‌روزرسانی' }}
        </AppButton>
      </div>

      <p v-if="pendingNotice" class="pending-notice" role="status">{{ pendingNotice }}</p>

      <AppErrorState
        v-if="pendingError && !pendingHasLoaded"
        class="pending-error"
        title="دریافت دعوت‌نامه‌ها انجام نشد"
        :message="pendingError"
      >
        <template #actions>
          <AppButton type="button" variant="secondary" :loading="pendingLoading" @click="loadPendingInvitations">
            تلاش مجدد
          </AppButton>
        </template>
      </AppErrorState>
      <div v-else-if="pendingError" class="pending-refresh-error" role="alert">
        <span>{{ pendingError }}</span>
        <AppButton type="button" variant="ghost" size="sm" :loading="pendingLoading" @click="loadPendingInvitations">تلاش مجدد</AppButton>
      </div>
      <AppLoadingState
        v-if="pendingLoading && !pendingHasLoaded"
        class="pending-state"
        label="در حال دریافت دعوت‌نامه‌ها..."
      />
      <AppEmptyState
        v-else-if="pendingHasLoaded && !pendingInvitations.length"
        class="pending-state empty"
        title="دعوت‌نامه‌ای در انتظار وجود ندارد."
        role="status"
      />
      <div v-else class="pending-list">
        <div v-for="pending in pendingInvitations" :key="pending.id" class="pending-row">
          <div class="pending-main">
            <div class="pending-title">{{ pending.account_name }}</div>
            <div class="pending-meta">
              <span>{{ pending.mobile_number }}</span>
              <span>{{ pending.role }}</span>
              <span>انقضا: {{ formatDateTime(pending.expires_at) }}</span>
            </div>
            <div v-if="normalizeInvitationContract(pending).botLink" class="pending-link-row">
              <AppButton type="button" class="pending-copy-btn" @click="copyPendingLink(pending, 'bot')">
                {{ pendingCopyState[pendingCopyKey(pending.id, 'bot')] || 'کپی لینک تلگرام' }}
              </AppButton>
            </div>
            <div v-if="normalizeInvitationContract(pending).webLink" class="pending-link-row">
              <AppButton type="button" class="pending-copy-btn" @click="copyPendingLink(pending, 'web')">
                {{ pendingCopyState[pendingCopyKey(pending.id, 'web')] || 'کپی لینک وب' }}
              </AppButton>
            </div>
            <p v-if="invitationSmsStatusMessage(pending.sms_status)" class="sms-status" role="status">{{ invitationSmsStatusMessage(pending.sms_status) }}</p>
          </div>
          <AppButton
            type="button"
            class="delete-pending-btn"
            variant="danger"
            :loading="pendingDeleteId === pending.id"
            :disabled="pendingDeleteId !== null || pendingDeleteCandidate !== null"
            @click="requestPendingInvitationDelete(pending)"
          >
            {{ pendingDeleteId === pending.id ? 'در حال حذف...' : 'حذف' }}
          </AppButton>
        </div>
      </div>
    </section>

    <AppConfirmDialog
      :open="Boolean(pendingDeleteCandidate)"
      :title="pendingDeleteCandidate ? `حذف دعوت‌نامه ${pendingDeleteCandidate.account_name}؟` : 'حذف دعوت‌نامه'"
      message="دعوت‌نامه فقط پس از تأیید پاسخ سرور از فهرست حذف می‌شود."
      confirm-label="تأیید حذف"
      cancel-label="انصراف"
      tone="danger"
      :busy="pendingDeleteId !== null"
      :error="pendingDeleteError || undefined"
      @confirm="confirmPendingInvitationDelete"
      @cancel="cancelPendingInvitationDelete"
    />

  </div>
</template>

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
.form-group { margin-bottom: 1rem; }
label { display: block; margin-bottom: 0.375rem; font-weight: 700; font-size: 0.78rem; color: #6b7280; }
input, select {
  width: 100%; padding: 0.625rem 0.875rem; border-radius: 0.75rem;
  border: 1px solid rgba(245, 158, 11, 0.15); background: white;
  font-size: 0.9rem; font-family: inherit; outline: none;
  transition: all 0.2s;
}
input:focus, select:focus {
  border-color: #f59e0b; background: white;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1);
}
.form-actions { display: flex; gap: 0.75rem; margin-top: 1.5rem; }
.form-actions button {
  flex-grow: 1; background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white; border: none; cursor: pointer; font-weight: 700;
  transition: all 0.2s; padding: 0.75rem; border-radius: 0.75rem;
  font-size: 0.9rem; -webkit-tap-highlight-color: transparent;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.25);
}
.form-actions button:active { transform: scale(0.98); }
.form-actions button:disabled { background: #d1d5db; box-shadow: none; cursor: not-allowed; color: white; }
.form-actions button.secondary {
  background: white; color: #6b7280; box-shadow: none;
  border: 1px solid rgba(245, 158, 11, 0.15); flex-grow: 0;
}
.form-actions button.secondary:active { background: #f9fafb; }

.result-box.error {
  margin-top: 1.25rem; padding: 0.75rem; border-radius: 0.75rem;
  background: var(--ds-danger-50); color: var(--ds-danger-800); border: 1px solid var(--ds-danger-200);
  font-size: 0.8rem; word-break: break-all;
}
.result-box :deep(strong) { color: var(--ds-danger-600); }

.success-box {
  margin-top: 1.25rem; padding: 1rem; border-radius: 1rem;
  background: linear-gradient(135deg, #f0fdf4, #dcfce7);
  border: 1px solid #bbf7d0;
}
.result-message {
  color: #166534; font-size: 0.8rem; font-weight: 700; margin-bottom: 0.75rem;
}
.copy-container {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.copy-container .copy-btn {
  flex: 0 0 auto; width: auto;
  font-weight: 700; font-size: 0.8rem; padding: 0.5rem 0.875rem;
  background: linear-gradient(135deg, #f59e0b, #d97706); color: white;
  border-radius: 0.625rem;
}
.copy-container .copy-btn:disabled { background: #d1d5db; }
.copy-container .copy-btn.web {
  background: linear-gradient(135deg, var(--ds-info-500), var(--ds-telegram-700));
  box-shadow: 0 4px 12px var(--ds-telegram-shadow);
}
.link-label {
  font-size: 0.78rem; font-weight: 700; color: #374151;
  margin-bottom: 0.375rem;
}
.invitation-expiry {
  margin: 0 0 0.75rem;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}
.sms-status {
  margin: 0.75rem 0 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}

.pending-section {
  margin-top: 1.5rem;
  padding-top: 1rem;
  border-top: 1px solid rgba(245, 158, 11, 0.16);
}
.pending-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.875rem;
}
.pending-header h3 {
  margin: 0;
  color: #1f2937;
  font-size: 0.95rem;
  font-weight: 800;
}
.pending-header p {
  margin: 0.25rem 0 0;
  color: #6b7280;
  font-size: 0.75rem;
}
.pending-refresh-btn,
.delete-pending-btn,
.pending-copy-btn {
  border: 0;
  border-radius: 0.625rem;
  cursor: pointer;
  font-family: inherit;
  font-size: 0.78rem;
  font-weight: 800;
  padding: 0.5rem 0.75rem;
  white-space: nowrap;
}
.pending-refresh-btn {
  background: white;
  color: #374151;
  border: 1px solid rgba(245, 158, 11, 0.18);
}
.pending-refresh-btn:disabled,
.delete-pending-btn:disabled,
.pending-copy-btn:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}
.pending-error {
  margin-bottom: 0.75rem;
  padding: 0.625rem 0.75rem;
  border-radius: 0.75rem;
  background: var(--ds-danger-50);
  border: 1px solid var(--ds-danger-200);
  color: var(--ds-danger-800);
  font-size: 0.78rem;
}
.pending-notice {
  margin: 0 0 0.75rem;
  padding: 0.625rem 0.75rem;
  border: 1px solid var(--ds-success-100);
  border-radius: var(--ds-radius-md);
  background: var(--ds-success-50);
  color: var(--ds-success-800);
  font-size: var(--ds-font-xs);
}
.pending-refresh-error {
  display: flex;
  gap: 0.6rem;
  margin-bottom: 0.75rem;
  padding: 0.75rem;
  border: 1px solid var(--ds-danger-200);
  border-radius: var(--ds-radius-md);
  background: var(--ds-danger-50);
  color: var(--ds-danger-800);
  font-size: var(--ds-font-xs);
}
.pending-refresh-error {
  align-items: center;
  justify-content: space-between;
}
.pending-state {
  padding: 0.875rem;
  border-radius: var(--ds-radius-md);
  background: var(--ds-bg-inset);
  color: var(--ds-text-muted);
  font-size: 0.8rem;
  text-align: center;
}
.pending-list {
  display: grid;
  gap: 0.75rem;
}
.pending-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.75rem;
  align-items: start;
  padding: 0.875rem;
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-md);
  background: var(--ds-bg-card);
}
.pending-title {
  color: var(--ds-text-primary);
  font-size: 0.9rem;
  font-weight: 800;
}
.pending-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem 0.625rem;
  margin-top: 0.35rem;
  color: var(--ds-text-muted);
  font-size: 0.74rem;
}
.pending-link-row {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.625rem;
}
.pending-copy-btn {
  background: linear-gradient(135deg, var(--ds-info-500), var(--ds-telegram-700));
  color: white;
}
.delete-pending-btn {
  background: var(--ds-danger-50);
  color: var(--ds-danger-700);
  border: 1px solid var(--ds-danger-200);
}

@media (max-width: 540px) {
  .pending-header {
    align-items: stretch;
    flex-direction: column;
  }
  .pending-refresh-btn {
    width: 100%;
  }
  .pending-row {
    grid-template-columns: 1fr;
  }
  .delete-pending-btn {
    width: 100%;
  }
  .pending-link-row {
    flex-direction: column;
  }
  .pending-copy-btn {
    width: 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .card,
  .card * {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
</style>
