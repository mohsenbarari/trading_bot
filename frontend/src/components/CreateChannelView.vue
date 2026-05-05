<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { apiFetch, apiFetchJson } from '../utils/auth'

type ChannelRoom = {
  id: number
  type: 'channel'
  title: string
  description: string | null
  created_by_id: number | null
  is_system: boolean
  is_mandatory: boolean
  member_count: number
  created_at: string
}

type ChannelCreateResponse = {
  channel: ChannelRoom
  member_picker_required: boolean
}

type ChannelInviteCandidate = {
  user_id: number
  account_name: string
  full_name: string
  mobile_number: string
  is_already_member: boolean
}

type ChannelInviteCandidateResponse = {
  items: ChannelInviteCandidate[]
  total: number
  active_total: number
}

type ChannelBulkMemberAddResponse = {
  chat_id: number
  processed_user_ids: number[]
  added_count: number
  reactivated_count: number
  already_member_count: number
  member_count: number
  select_all_active_users: boolean
}

const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
}>()

const form = reactive({
  title: '',
  description: '',
})

const searchQuery = ref('')
const isCreating = ref(false)
const isLoadingCandidates = ref(false)
const isSubmittingMembers = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const createdChannel = ref<ChannelRoom | null>(null)
const candidates = ref<ChannelInviteCandidate[]>([])
const candidateTotal = ref(0)
const activeTotal = ref(0)
const selectAllActiveUsers = ref(false)
const selectedUserIds = ref<Set<number>>(new Set())

const isPickerActive = computed(() => Boolean(createdChannel.value))
const canSubmitMembers = computed(() => {
  return !!createdChannel.value && (selectAllActiveUsers.value || selectedUserIds.value.size > 0)
})
const selectedCount = computed(() => {
  return selectAllActiveUsers.value ? activeTotal.value : selectedUserIds.value.size
})

function resetFormState() {
  searchQuery.value = ''
  candidates.value = []
  candidateTotal.value = 0
  activeTotal.value = 0
  selectAllActiveUsers.value = false
  selectedUserIds.value = new Set()
}

function resetAll() {
  form.title = ''
  form.description = ''
  errorMessage.value = ''
  successMessage.value = ''
  createdChannel.value = null
  resetFormState()
}

function getAvatarInitial(name: string) {
  return name ? name.charAt(0).toUpperCase() : '?'
}

function toggleUser(userId: number) {
  if (selectAllActiveUsers.value) return
  const next = new Set(selectedUserIds.value)
  if (next.has(userId)) next.delete(userId)
  else next.add(userId)
  selectedUserIds.value = next
}

function handleToggleSelectAll() {
  selectAllActiveUsers.value = !selectAllActiveUsers.value
  if (selectAllActiveUsers.value) {
    selectedUserIds.value = new Set()
  }
}

async function loadCandidates(query = '') {
  if (!createdChannel.value) return
  isLoadingCandidates.value = true
  errorMessage.value = ''
  try {
    const params = new URLSearchParams({
      limit: '100',
      exclude_chat_id: String(createdChannel.value.id),
    })
    const trimmed = query.trim()
    if (trimmed) params.set('q', trimmed)
    const data = await apiFetchJson(`/api/chat/channels/invite-candidates?${params.toString()}`) as ChannelInviteCandidateResponse
    candidates.value = Array.isArray(data.items) ? data.items : []
    candidateTotal.value = Number(data.total || 0)
    activeTotal.value = Number(data.active_total || 0)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'خطا در دریافت کاربران فعال'
  } finally {
    isLoadingCandidates.value = false
  }
}

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(searchQuery, (value) => {
  if (!createdChannel.value) return
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    void loadCandidates(value)
  }, 250)
})

async function createChannel() {
  if (!props.jwtToken) {
    errorMessage.value = '❌ شما احراز هویت نشده‌اید.'
    return
  }

  isCreating.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await apiFetch('/api/chat/channels', {
      method: 'POST',
      body: JSON.stringify({
        title: form.title,
        description: form.description || undefined,
      }),
    })
    const data = await response.json() as ChannelCreateResponse | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در ساخت کانال')
    }
    createdChannel.value = (data as ChannelCreateResponse).channel
    successMessage.value = '✅ کانال ساخته شد. حالا اعضای اولیه را انتخاب کنید.'
    resetFormState()
    await loadCandidates()
  } catch (error) {
    errorMessage.value = `❌ ${error instanceof Error ? error.message : 'خطا در ساخت کانال'}`
  } finally {
    isCreating.value = false
  }
}

async function submitMembers() {
  if (!createdChannel.value || !canSubmitMembers.value) return
  isSubmittingMembers.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const payload = selectAllActiveUsers.value
      ? { select_all_active_users: true }
      : { user_ids: Array.from(selectedUserIds.value) }

    const response = await apiFetch(`/api/chat/channels/${createdChannel.value.id}/members/bulk`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const data = await response.json() as ChannelBulkMemberAddResponse | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در افزودن اعضا')
    }

    const summary = data as ChannelBulkMemberAddResponse
    createdChannel.value = {
      ...createdChannel.value,
      member_count: summary.member_count,
    }
    successMessage.value = `✅ اعضا با موفقیت افزوده شدند. اعضای فعال کانال: ${summary.member_count}`
    selectAllActiveUsers.value = false
    selectedUserIds.value = new Set()
    await loadCandidates(searchQuery.value)
  } catch (error) {
    errorMessage.value = `❌ ${error instanceof Error ? error.message : 'خطا در افزودن اعضا'}`
  } finally {
    isSubmittingMembers.value = false
  }
}
</script>

<template>
  <div class="channel-card">
    <div class="header-block">
      <h2>{{ isPickerActive ? 'انتخاب اعضای اولیه کانال' : 'ساخت کانال اختیاری' }}</h2>
      <p>
        {{ isPickerActive
          ? 'کاربران فعال پروژه را برای عضویت در این کانال invite کنید. عضویت در این کانال فقط با دعوت مستقیم ممکن است.'
          : 'فقط مدیر ارشد می‌تواند کانال اختیاری بسازد. بعد از ساخت، member picker بلافاصله باز می‌شود.' }}
      </p>
    </div>

    <div v-if="!isPickerActive" class="form-shell">
      <div class="form-group">
        <label for="channel-title">نام کانال</label>
        <input id="channel-title" v-model="form.title" type="text" maxlength="255" placeholder="مثلاً اطلاعیه‌های ویژه" />
      </div>

      <div class="form-group">
        <label for="channel-description">توضیحات</label>
        <textarea id="channel-description" v-model="form.description" rows="4" maxlength="2000" placeholder="توضیح کوتاه برای ادمین‌ها"></textarea>
      </div>

      <div class="form-actions">
        <button type="button" class="primary-btn" :disabled="isCreating" @click="createChannel">
          {{ isCreating ? 'در حال ساخت...' : 'ساخت کانال و ادامه' }}
        </button>
        <button type="button" class="secondary-btn" :disabled="isCreating" @click="resetAll">بازنشانی</button>
      </div>
    </div>

    <div v-else class="picker-shell">
      <div class="channel-summary">
        <div>
          <div class="summary-title">{{ createdChannel?.title }}</div>
          <div class="summary-meta">اعضای فعال: {{ createdChannel?.member_count ?? 0 }}</div>
        </div>
        <button type="button" class="secondary-btn compact" @click="resetAll">کانال جدید</button>
      </div>

      <div class="picker-toolbar">
        <label class="select-all-toggle" :class="{ active: selectAllActiveUsers }">
          <input type="checkbox" :checked="selectAllActiveUsers" @change="handleToggleSelectAll" />
          <span>انتخاب همه کاربران فعال ({{ activeTotal }})</span>
        </label>

        <input
          v-model="searchQuery"
          type="text"
          class="picker-search"
          placeholder="جستجو با نام، اکانت یا موبایل..."
          :disabled="selectAllActiveUsers"
        />
      </div>

      <div class="picker-state" v-if="isLoadingCandidates">در حال دریافت کاربران فعال...</div>
      <div class="picker-state empty" v-else-if="!selectAllActiveUsers && candidates.length === 0">کاربری برای دعوت باقی نمانده است.</div>

      <div v-else-if="!selectAllActiveUsers" class="candidate-list">
        <button
          v-for="candidate in candidates"
          :key="candidate.user_id"
          type="button"
          class="candidate-row"
          :class="{ selected: selectedUserIds.has(candidate.user_id) }"
          @click="toggleUser(candidate.user_id)"
        >
          <div class="candidate-check" :class="{ checked: selectedUserIds.has(candidate.user_id) }">
            <svg v-if="selectedUserIds.has(candidate.user_id)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          </div>
          <div class="candidate-avatar">{{ getAvatarInitial(candidate.account_name) }}</div>
          <div class="candidate-copy">
            <span class="candidate-name">{{ candidate.account_name }}</span>
            <span class="candidate-details">{{ candidate.full_name }} • {{ candidate.mobile_number }}</span>
          </div>
        </button>
      </div>

      <div class="picker-footer">
        <div class="picker-count">انتخاب‌شده: {{ selectedCount }} از {{ activeTotal }}</div>
        <button type="button" class="primary-btn" :disabled="!canSubmitMembers || isSubmittingMembers" @click="submitMembers">
          {{ isSubmittingMembers ? 'در حال ثبت...' : 'ثبت اعضای انتخاب‌شده' }}
        </button>
      </div>
    </div>

    <div v-if="errorMessage" class="message-box error">{{ errorMessage }}</div>
    <div v-if="successMessage" class="message-box success">{{ successMessage }}</div>
  </div>
</template>

<style scoped>
.channel-card {
  background: rgba(255, 255, 255, 0.74);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid rgba(245, 158, 11, 0.14);
  border-radius: 24px;
  padding: 20px;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.08);
}

.header-block h2 {
  margin: 0;
  font-size: 1.12rem;
  font-weight: 900;
  color: #1f2937;
}

.header-block p {
  margin: 8px 0 0;
  color: #6b7280;
  font-size: 0.85rem;
  line-height: 1.8;
}

.form-shell,
.picker-shell {
  margin-top: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-group label {
  font-size: 0.85rem;
  font-weight: 700;
  color: #374151;
}

.form-group input,
.form-group textarea,
.picker-search {
  width: 100%;
  border: 1px solid rgba(245, 158, 11, 0.18);
  background: rgba(255, 251, 235, 0.55);
  border-radius: 16px;
  padding: 12px 14px;
  font: inherit;
  color: #111827;
  outline: none;
  transition: border-color 0.2s ease, background 0.2s ease;
}

.form-group input:focus,
.form-group textarea:focus,
.picker-search:focus {
  border-color: #f59e0b;
  background: #fff;
}

.form-group textarea {
  resize: vertical;
  min-height: 110px;
}

.form-actions,
.picker-footer {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
}

.primary-btn,
.secondary-btn {
  border: none;
  border-radius: 999px;
  font: inherit;
  font-weight: 800;
  cursor: pointer;
  transition: transform 0.08s ease, opacity 0.15s ease, background 0.2s ease;
}

.primary-btn {
  background: linear-gradient(135deg, #d97706, #f59e0b);
  color: white;
  padding: 12px 18px;
  min-width: 168px;
}

.secondary-btn {
  background: #fff7ed;
  color: #9a3412;
  padding: 11px 16px;
  border: 1px solid rgba(249, 115, 22, 0.18);
}

.secondary-btn.compact {
  min-width: auto;
}

.primary-btn:disabled,
.secondary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.primary-btn:not(:disabled):active,
.secondary-btn:not(:disabled):active {
  transform: scale(0.98);
}

.channel-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  background: linear-gradient(135deg, rgba(255, 247, 237, 0.95), rgba(254, 243, 199, 0.88));
  border-radius: 18px;
  border: 1px solid rgba(245, 158, 11, 0.14);
}

.summary-title {
  font-size: 1rem;
  font-weight: 900;
  color: #7c2d12;
}

.summary-meta,
.picker-count {
  font-size: 0.82rem;
  color: #92400e;
}

.picker-toolbar {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.select-all-toggle {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 16px;
  background: rgba(255, 251, 235, 0.75);
  border: 1px solid rgba(245, 158, 11, 0.14);
  font-size: 0.88rem;
  font-weight: 700;
  color: #78350f;
  cursor: pointer;
}

.select-all-toggle.active {
  background: rgba(254, 243, 199, 0.9);
  border-color: rgba(245, 158, 11, 0.28);
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow-y: auto;
  padding-left: 2px;
}

.candidate-row {
  width: 100%;
  border: 1px solid rgba(245, 158, 11, 0.12);
  background: rgba(255, 255, 255, 0.82);
  border-radius: 18px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: right;
  cursor: pointer;
}

.candidate-row.selected {
  background: rgba(254, 243, 199, 0.78);
  border-color: rgba(245, 158, 11, 0.34);
}

.candidate-check {
  width: 24px;
  height: 24px;
  min-width: 24px;
  border-radius: 50%;
  border: 2px solid #d1d5db;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  background: #fff;
}

.candidate-check.checked {
  background: #f59e0b;
  border-color: #f59e0b;
}

.candidate-check svg {
  width: 14px;
  height: 14px;
}

.candidate-avatar {
  width: 44px;
  height: 44px;
  min-width: 44px;
  border-radius: 50%;
  background: linear-gradient(135deg, #d97706, #f59e0b);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1rem;
  font-weight: 800;
}

.candidate-copy {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
}

.candidate-name {
  font-size: 0.95rem;
  font-weight: 800;
  color: #111827;
}

.candidate-details {
  font-size: 0.78rem;
  color: #6b7280;
  line-height: 1.6;
}

.picker-state,
.message-box {
  margin-top: 16px;
  border-radius: 16px;
  padding: 12px 14px;
  font-size: 0.86rem;
  line-height: 1.8;
}

.picker-state {
  background: rgba(255, 251, 235, 0.72);
  color: #92400e;
}

.picker-state.empty {
  background: rgba(243, 244, 246, 0.9);
  color: #6b7280;
}

.message-box.success {
  background: rgba(236, 253, 245, 0.96);
  color: #065f46;
}

.message-box.error {
  background: rgba(254, 242, 242, 0.96);
  color: #b91c1c;
}

@media (max-width: 640px) {
  .channel-card {
    padding: 16px;
    border-radius: 20px;
  }

  .form-actions,
  .picker-footer,
  .channel-summary {
    flex-direction: column;
    align-items: stretch;
  }

  .primary-btn,
  .secondary-btn {
    width: 100%;
  }
}
</style>