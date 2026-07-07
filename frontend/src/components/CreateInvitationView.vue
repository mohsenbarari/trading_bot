<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { apiFetch } from '../utils/auth';
import { getInvitableRoleOptions } from '../utils/adminAccess';
import { formatIranDateTime } from '../utils/iranTime';
import { AppButton, AppFormField, AppInput, AppSelect } from './ui';

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>();

interface PendingInvitation {
  id: number;
  account_name: string;
  mobile_number: string;
  role: string;
  web_link: string;
  short_link?: string | null;
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
const copyMessage = ref('');
const webCopyMessage = ref('');
const pendingInvitations = ref<PendingInvitation[]>([]);
const pendingLoading = ref(false);
const pendingError = ref('');
const pendingDeleteId = ref<number | null>(null);
const pendingCopyState = reactive<Record<number, string>>({});

onMounted(() => {
  if (props.jwtToken) {
    void loadPendingInvitations();
  }
});

function resetForm() {
  invite.account_name = '';
  invite.mobile_number = '';
  invite.role = defaultInviteRole;
  resultMessage.value = '';
  inviteLink.value = '';
  webLink.value = '';
  copyMessage.value = '';
  webCopyMessage.value = '';
}

function fallbackCopyTextToClipboard(text: string, isWeb: boolean = false) {
  const textArea = document.createElement("textarea");
  textArea.value = text;
  
  // Avoid scrolling to bottom
  textArea.style.top = "0";
  textArea.style.left = "0";
  textArea.style.position = "fixed";

  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();

  try {
    const successful = document.execCommand('copy');
    const msg = successful ? 'کپی شد!' : 'خطا';
    if (isWeb) {
      webCopyMessage.value = msg;
      setTimeout(() => { webCopyMessage.value = ''; }, 2000);
    } else {
      copyMessage.value = msg;
      setTimeout(() => { copyMessage.value = ''; }, 2000);
    }
  } catch (err) {
    if (isWeb) {
      webCopyMessage.value = 'خطا';
      setTimeout(() => { webCopyMessage.value = ''; }, 2000);
    } else {
      copyMessage.value = 'خطا';
      setTimeout(() => { copyMessage.value = ''; }, 2000);
    }
  }

  document.body.removeChild(textArea);
}

function copyToClipboard() {
  if (!inviteLink.value) return;
  if (!navigator.clipboard) {
    fallbackCopyTextToClipboard(inviteLink.value, false);
    return;
  }
  navigator.clipboard.writeText(inviteLink.value).then(function() {
    copyMessage.value = 'کپی شد!';
    setTimeout(() => { copyMessage.value = ''; }, 2000);
  }, function(err) {
    copyMessage.value = 'خطا';
    setTimeout(() => { copyMessage.value = ''; }, 2000);
  });
}

function copyWebLink() {
  if (!webLink.value) return;
  if (!navigator.clipboard) {
    fallbackCopyTextToClipboard(webLink.value, true);
    return;
  }
  navigator.clipboard.writeText(webLink.value).then(function() {
    webCopyMessage.value = 'کپی شد!';
    setTimeout(() => { webCopyMessage.value = ''; }, 2000);
  }, function(err) {
    webCopyMessage.value = 'خطا';
    setTimeout(() => { webCopyMessage.value = ''; }, 2000);
  });
}

function toLocalDisplayLink(link: string | null | undefined): string {
  if (!link) return '';
  try {
    const url = new URL(link);
    return `${window.location.origin}${url.pathname}${url.search}`;
  } catch {
    return link;
  }
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

async function readErrorDetail(resp: Response, fallback: string): Promise<string> {
  try {
    const data = await resp.json();
    return data.detail || fallback;
  } catch {
    return fallback;
  }
}

async function loadPendingInvitations() {
  if (!props.jwtToken) {
    pendingInvitations.value = [];
    return;
  }

  pendingLoading.value = true;
  pendingError.value = '';
  try {
    const resp = await apiFetch('/api/invitations/pending');
    if (!resp.ok) {
      throw new Error(await readErrorDetail(resp, 'خطا در دریافت دعوت‌نامه‌های pending'));
    }
    const data = await resp.json();
    pendingInvitations.value = Array.isArray(data) ? data : [];
  } catch (e: any) {
    pendingError.value = e.message || 'خطا در دریافت دعوت‌نامه‌های pending';
  } finally {
    pendingLoading.value = false;
  }
}

function fallbackCopyPendingLink(text: string, invitationId: number) {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.top = '0';
  textArea.style.left = '0';
  textArea.style.position = 'fixed';
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();

  try {
    pendingCopyState[invitationId] = document.execCommand('copy') ? 'کپی شد!' : 'خطا';
  } catch {
    pendingCopyState[invitationId] = 'خطا';
  }

  document.body.removeChild(textArea);
  setTimeout(() => { pendingCopyState[invitationId] = ''; }, 2000);
}

function copyPendingWebLink(invitation: PendingInvitation) {
  const link = toLocalDisplayLink(invitation.web_link);
  if (!link) return;
  if (!navigator.clipboard) {
    fallbackCopyPendingLink(link, invitation.id);
    return;
  }

  navigator.clipboard.writeText(link).then(() => {
    pendingCopyState[invitation.id] = 'کپی شد!';
    setTimeout(() => { pendingCopyState[invitation.id] = ''; }, 2000);
  }, () => {
    pendingCopyState[invitation.id] = 'خطا';
    setTimeout(() => { pendingCopyState[invitation.id] = ''; }, 2000);
  });
}

async function deletePendingInvitation(invitation: PendingInvitation) {
  const confirmed = window.confirm(`دعوت‌نامه ${invitation.account_name} حذف شود؟`);
  if (!confirmed) return;

  pendingDeleteId.value = invitation.id;
  pendingError.value = '';
  try {
    const resp = await apiFetch(`/api/invitations/pending/${invitation.id}`, { method: 'DELETE' });
    if (!resp.ok) {
      throw new Error(await readErrorDetail(resp, 'خطا در حذف دعوت‌نامه'));
    }
    pendingInvitations.value = pendingInvitations.value.filter((item) => item.id !== invitation.id);
  } catch (e: any) {
    pendingError.value = e.message || 'خطا در حذف دعوت‌نامه';
  } finally {
    pendingDeleteId.value = null;
  }
}

async function createInvite() {
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
  resultMessage.value = '';
  inviteLink.value = '';
  copyMessage.value = '';

  try {
    const resp = await apiFetch(`/api/invitations/`, {
      method: 'POST',
      body: JSON.stringify({ ...invite, mobile_number: normalizedMobile }),
    });
    
    const data = await resp.json();
    if (!resp.ok) {
      const detail = data.detail || 'خطا در ایجاد دعوت‌نامه';
      resultMessage.value = `❌ ${detail.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')}`;
      throw new Error(detail);
    }

    // Use links directly from API response (no need for /api/config)
    inviteLink.value = data.link;

    if (data.short_link) {
      try {
        const url = new URL(data.short_link);
        webLink.value = `${window.location.origin}${url.pathname}${url.search}`;
      } catch {
        webLink.value = data.short_link;
      }
    } else {
      webLink.value = '';
    }
    
    resultMessage.value = '✅ لینک دعوت با موفقیت ایجاد شد.';
    await loadPendingInvitations();
    
    // emit('invite-created', plainTextMessage); // (حذف شد)
    
  } catch (e: any) {
    if (!resultMessage.value.startsWith('❌')) {
       resultMessage.value = `❌ ${e.message}`;
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
        <AppButton type="submit" :loading="isLoading">
          {{ isLoading ? 'در حال ساخت...' : 'ارسال لینک دعوت' }}
        </AppButton>
        <AppButton type="button" class="secondary" variant="secondary" :disabled="isLoading" @click="resetForm">
          بازنشانی
        </AppButton>
      </div>
    </form>

    <div v-if="resultMessage && !inviteLink" class="result-box error" v-html="resultMessage">
    </div>

    <div v-if="inviteLink" class="success-box">
      <div class="result-message">✅ لینک دعوت با موفقیت ایجاد شد:</div>
      <div class="link-label">🔵 لینک تلگرام:</div>
      <div class="copy-container">
        <input type="text" :value="inviteLink" @click="copyToClipboard" readonly />
        <button type="button" @click="copyToClipboard" class="copy-btn">
          {{ copyMessage ? copyMessage : 'کپی' }}
        </button>
      </div>
      <div v-if="webLink" class="link-label" style="margin-top: 0.75rem;">🌐 لینک وب:</div>
      <div v-if="webLink" class="copy-container">
        <input type="text" :value="webLink" @click="copyWebLink" readonly />
        <button type="button" @click="copyWebLink" class="copy-btn web">
          {{ webCopyMessage ? webCopyMessage : 'کپی' }}
        </button>
      </div>
    </div>

    <section class="pending-section" aria-labelledby="pending-invitations-title">
      <div class="pending-header">
        <div>
          <h3 id="pending-invitations-title">دعوت‌نامه‌های pending</h3>
          <p>{{ pendingInvitations.length }} دعوت‌نامه فعال</p>
        </div>
        <button type="button" class="pending-refresh-btn" :disabled="pendingLoading" @click="loadPendingInvitations">
          {{ pendingLoading ? 'در حال دریافت...' : 'به‌روزرسانی' }}
        </button>
      </div>

      <div v-if="pendingError" class="pending-error">{{ pendingError }}</div>
      <div v-if="pendingLoading && !pendingInvitations.length" class="pending-state">در حال دریافت دعوت‌نامه‌ها...</div>
      <div v-else-if="!pendingInvitations.length" class="pending-state empty">دعوت‌نامه pending وجود ندارد.</div>
      <div v-else class="pending-list">
        <div v-for="pending in pendingInvitations" :key="pending.id" class="pending-row">
          <div class="pending-main">
            <div class="pending-title">{{ pending.account_name }}</div>
            <div class="pending-meta">
              <span>{{ pending.mobile_number }}</span>
              <span>{{ pending.role }}</span>
              <span>انقضا: {{ formatDateTime(pending.expires_at) }}</span>
            </div>
            <div class="pending-link-row">
              <input type="text" :value="toLocalDisplayLink(pending.web_link)" readonly @click="copyPendingWebLink(pending)" />
              <button type="button" class="pending-copy-btn" @click="copyPendingWebLink(pending)">
                {{ pendingCopyState[pending.id] || 'کپی لینک' }}
              </button>
            </div>
          </div>
          <button
            type="button"
            class="delete-pending-btn"
            :disabled="pendingDeleteId === pending.id"
            @click="deletePendingInvitation(pending)"
          >
            {{ pendingDeleteId === pending.id ? 'در حال حذف...' : 'حذف' }}
          </button>
        </div>
      </div>
    </section>

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
}
.copy-container input[type="text"] {
  width: 0; flex: 1 1 0;
  direction: ltr; font-family: monospace; font-size: 0.8rem;
  background: white; color: #166534;
  border: 1px solid #bbf7d0; cursor: pointer;
  border-radius: 0.625rem; padding: 0.5rem 0.75rem;
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
.pending-state {
  padding: 0.875rem;
  border-radius: 0.875rem;
  background: #f9fafb;
  color: #6b7280;
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
  border: 1px solid rgba(245, 158, 11, 0.14);
  border-radius: 0.875rem;
  background: rgba(255, 255, 255, 0.74);
}
.pending-title {
  color: #111827;
  font-size: 0.9rem;
  font-weight: 800;
}
.pending-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem 0.625rem;
  margin-top: 0.35rem;
  color: #6b7280;
  font-size: 0.74rem;
}
.pending-link-row {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.625rem;
}
.pending-link-row input[type="text"] {
  width: 0;
  flex: 1 1 0;
  direction: ltr;
  font-family: monospace;
  font-size: 0.76rem;
  border-radius: 0.625rem;
  padding: 0.5rem 0.75rem;
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
}
</style>
