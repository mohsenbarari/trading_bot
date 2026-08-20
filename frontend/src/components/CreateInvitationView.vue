<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
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
const pendingDeleteTrigger = ref<HTMLElement | null>(null);
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
  pendingDeleteTrigger.value = null;
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

function requestPendingInvitationDelete(invitation: PendingInvitation, event: MouseEvent) {
  if (pendingDeleteId.value !== null || pendingDeleteCandidate.value) return;
  pendingDeleteTrigger.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null;
  pendingDeleteCandidate.value = invitation;
  pendingDeleteError.value = '';
  pendingNotice.value = '';
}

function cancelPendingInvitationDelete() {
  if (pendingDeleteId.value !== null) return;
  pendingDeleteCandidate.value = null;
  pendingDeleteError.value = '';
  const trigger = pendingDeleteTrigger.value;
  pendingDeleteTrigger.value = null;
  void nextTick()
    .then(() => nextTick())
    .then(() => {
      if (trigger?.isConnected && !trigger.matches(':disabled')) trigger.focus();
    });
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
    pendingDeleteTrigger.value = null;
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
    pendingDeleteTrigger.value = null;
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
  <div class="invitation-manager">
    <form class="invitation-form" @submit.prevent="createInvite" autocomplete="off">
      <AppFormField class="form-group" id="account_name" label="نام کاربری (Account Name)">
        <AppInput v-model="invite.account_name" id="account_name" type="text" placeholder="مثلاً alireza" required />
      </AppFormField>
      <AppFormField class="form-group" id="mobile_number" label="شماره موبایل (ایران)">
        <AppInput v-model="invite.mobile_number" id="mobile_number" type="tel" placeholder="09123456789" required />
      </AppFormField>
      <AppFormField class="form-group" id="role" label="نقش">
        <AppSelect v-model="invite.role" id="role" :options="availableRoles" />
      </AppFormField>
      <div class="form-actions ds-native-actions ds-native-actions--stack">
        <AppButton type="submit" block :loading="isLoading" :disabled="pendingDeleteId !== null">
          {{ isLoading ? 'در حال ساخت...' : 'ارسال لینک دعوت' }}
        </AppButton>
        <AppButton type="button" class="secondary" variant="secondary" block :disabled="isLoading || pendingDeleteId !== null" @click="resetForm">
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
        <AppButton type="button" variant="secondary" @click="copyToClipboard" class="copy-btn">
          {{ copyMessage || 'کپی لینک تلگرام' }}
        </AppButton>
      </div>
      <div v-if="webLink" class="link-label link-label--spaced">لینک وب آماده است.</div>
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
              <AppButton type="button" class="pending-copy-btn" variant="secondary" @click="copyPendingLink(pending, 'bot')">
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
            class="delete-pending-btn ds-native-danger"
            variant="ghost"
            :loading="pendingDeleteId === pending.id"
            :disabled="pendingDeleteId !== null || pendingDeleteCandidate !== null"
            @click="requestPendingInvitationDelete(pending, $event)"
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
.invitation-manager {
  display: grid;
  gap: var(--ds-section-gap);
  min-width: 0;
  color: var(--ds-text-primary);
  font-family: Vazirmatn, Tahoma, Arial, sans-serif;
  font-synthesis: none;
}

.invitation-form {
  display: grid;
  gap: var(--ds-section-gap);
}

.form-group { margin: 0; }

.copy-container {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ds-section-gap);
}

.result-box.error,
.success-box,
.pending-notice,
.pending-refresh-error,
.pending-state,
.pending-row {
  border: 1px solid var(--ds-border-medium);
  border-radius: var(--ds-radius-md);
  background: var(--ds-bg-card);
}

.result-box.error,
.pending-refresh-error {
  border-color: var(--ds-danger-200);
  background: var(--ds-danger-50);
  color: var(--ds-danger-800);
}

.result-box.error {
  padding: 1rem;
  font-size: var(--ds-font-sm);
  line-height: 1.75;
  overflow-wrap: anywhere;
}

.result-box :deep(strong) { color: var(--ds-danger-600); }

.success-box {
  display: grid;
  gap: var(--ds-section-gap);
  padding: 1rem;
}

.result-message,
.pending-title {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 700;
  line-height: 1.7;
}

.pending-title { overflow-wrap: anywhere; }

.link-label {
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  font-weight: 600;
  line-height: 1.7;
}

.link-label--spaced { margin-top: 0.25rem; }

.copy-container .copy-btn {
  flex: 1 1 10rem;
}

.invitation-expiry,
.sms-status {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}

.pending-section {
  display: grid;
  gap: var(--ds-section-gap);
  padding-top: 1.25rem;
  border-top: 1px solid var(--ds-border-light);
}

.pending-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ds-section-gap);
}

.pending-header h3 {
  margin: 0;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md, 1rem);
  font-weight: 700;
  line-height: 1.6;
}

.pending-header p {
  margin: 0.25rem 0 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.7;
}

.pending-refresh-btn { flex: 0 0 auto; }

.pending-error { margin: 0; }

.pending-notice,
.pending-refresh-error,
.pending-state {
  margin: 0;
  padding: 0.75rem;
  font-size: var(--ds-font-xs);
  line-height: 1.7;
}

.pending-notice {
  border-color: var(--ds-success-100);
  background: var(--ds-success-50);
  color: var(--ds-success-800);
}

.pending-refresh-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ds-section-gap);
}

.pending-state {
  background: var(--ds-bg-inset);
  color: var(--ds-text-muted);
  text-align: center;
}

.pending-list {
  display: grid;
  gap: var(--ds-section-gap);
}

.pending-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--ds-section-gap);
  align-items: start;
  padding: 1rem;
}

.pending-main { min-width: 0; }

.pending-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem var(--ds-section-gap);
  margin-top: 0.25rem;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.7;
}

.pending-link-row {
  display: flex;
  margin-top: var(--ds-section-gap);
}

.pending-copy-btn { width: 100%; }

.delete-pending-btn {
  min-width: max-content;
}

@media (max-width: 540px) {
  .pending-header {
    flex-direction: column;
    align-items: stretch;
  }

  .pending-row { grid-template-columns: 1fr; }

  .pending-refresh-btn,
  .delete-pending-btn {
    width: 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .invitation-manager :deep(.ui-button__spinner),
  .invitation-manager :deep(.ui-loading-state__spinner) {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
</style>
