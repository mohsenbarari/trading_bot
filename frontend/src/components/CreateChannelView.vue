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

type ChannelUpdatePayload = {
  title: string
  description: string
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

type ChannelMember = {
  user_id: number
  account_name: string
  full_name: string
  mobile_number: string
  role: 'admin' | 'member'
  joined_at: string
  is_channel_creator: boolean
}

type ChannelMemberMutationResponse = {
  chat_id: number
  user_id: number
  role: 'admin' | 'member' | null
  removed: boolean
  member_count: number
}

const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
}>()

const form = reactive({
  title: '',
  description: '',
})

const editForm = reactive<ChannelUpdatePayload>({
  title: '',
  description: '',
})

const searchQuery = ref('')
const isCreating = ref(false)
const isLoadingChannels = ref(false)
const isLoadingCandidates = ref(false)
const isSubmittingMembers = ref(false)
const isUpdatingChannel = ref(false)
const isLoadingMembers = ref(false)
const mutatingMemberId = ref<number | null>(null)
const errorMessage = ref('')
const successMessage = ref('')
const createdChannel = ref<ChannelRoom | null>(null)
const existingChannels = ref<ChannelRoom[]>([])
const members = ref<ChannelMember[]>([])
const candidates = ref<ChannelInviteCandidate[]>([])
const candidateTotal = ref(0)
const activeTotal = ref(0)
const selectAllActiveUsers = ref(false)
const selectedUserIds = ref<Set<number>>(new Set())

const isPickerActive = computed(() => Boolean(createdChannel.value))
const canSubmitMembers = computed(() => {
  return !!createdChannel.value && (selectAllActiveUsers.value || selectedUserIds.value.size > 0)
})
const canUpdateChannel = computed(() => {
  const current = createdChannel.value
  if (!current) return false
  const nextTitle = editForm.title.trim()
  const nextDescription = editForm.description.trim()
  const currentDescription = current.description ?? ''
  return nextTitle.length > 0 && (nextTitle !== current.title || nextDescription !== currentDescription)
})
const selectedCount = computed(() => {
  return selectAllActiveUsers.value ? activeTotal.value : selectedUserIds.value.size
})

const activeAdminCount = computed(() => {
  return members.value.filter((member) => member.role === 'admin').length
})

function resetFormState() {
  searchQuery.value = ''
  members.value = []
  candidates.value = []
  candidateTotal.value = 0
  activeTotal.value = 0
  selectAllActiveUsers.value = false
  selectedUserIds.value = new Set()
}

function upsertExistingChannel(channel: ChannelRoom) {
  const next = existingChannels.value.filter((item) => item.id !== channel.id)
  next.unshift(channel)
  existingChannels.value = next
}

function resetAll() {
  form.title = ''
  form.description = ''
  editForm.title = ''
  editForm.description = ''
  errorMessage.value = ''
  successMessage.value = ''
  createdChannel.value = null
  resetFormState()
}

function openExistingChannel(channel: ChannelRoom) {
  errorMessage.value = ''
  successMessage.value = `✅ مدیریت کانال «${channel.title}» فعال شد.`
  createdChannel.value = channel
  resetFormState()
  void loadMembers()
  void loadCandidates()
}

function syncEditForm(channel: ChannelRoom | null) {
  editForm.title = channel?.title ?? ''
  editForm.description = channel?.description ?? ''
}

function getAvatarInitial(name: string) {
  return name ? name.charAt(0).toUpperCase() : '?'
}

function canDemoteMember(member: ChannelMember) {
  if (member.role !== 'admin') return false
  if (member.is_channel_creator) return false
  return activeAdminCount.value > 1
}

function canRemoveMember(member: ChannelMember) {
  if (member.is_channel_creator) return false
  if (member.role === 'admin') {
    return activeAdminCount.value > 1
  }
  return true
}

function getMemberGuardReason(member: ChannelMember) {
  if (member.is_channel_creator) {
    return 'سازنده کانال باید عضو و ادمین باقی بماند.'
  }
  if (member.role === 'admin' && activeAdminCount.value <= 1) {
    return 'کانال باید حداقل یک ادمین فعال داشته باشد.'
  }
  return ''
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

async function loadMembers() {
  if (!createdChannel.value) return
  isLoadingMembers.value = true
  try {
    const data = await apiFetchJson(`/api/chat/channels/${createdChannel.value.id}/members`) as ChannelMember[]
    members.value = Array.isArray(data) ? data : []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'خطا در دریافت اعضای کانال'
  } finally {
    isLoadingMembers.value = false
  }
}

async function loadExistingChannels() {
  isLoadingChannels.value = true
  try {
    const data = await apiFetchJson('/api/chat/channels') as ChannelRoom[]
    existingChannels.value = Array.isArray(data) ? data : []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'خطا در دریافت فهرست کانال‌ها'
  } finally {
    isLoadingChannels.value = false
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

watch(createdChannel, (channel) => {
  syncEditForm(channel)
}, { immediate: true })

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
    upsertExistingChannel((data as ChannelCreateResponse).channel)
    successMessage.value = '✅ کانال ساخته شد. حالا اعضای اولیه را انتخاب کنید.'
    resetFormState()
    await Promise.all([loadMembers(), loadCandidates()])
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
    upsertExistingChannel(createdChannel.value)
    successMessage.value = `✅ اعضا با موفقیت افزوده شدند. اعضای فعال کانال: ${summary.member_count}`
    selectAllActiveUsers.value = false
    selectedUserIds.value = new Set()
    await Promise.all([loadMembers(), loadCandidates(searchQuery.value)])
  } catch (error) {
    errorMessage.value = `❌ ${error instanceof Error ? error.message : 'خطا در افزودن اعضا'}`
  } finally {
    isSubmittingMembers.value = false
  }
}

async function updateChannelDetails() {
  if (!createdChannel.value || !canUpdateChannel.value) return
  isUpdatingChannel.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await apiFetch(`/api/chat/channels/${createdChannel.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        title: editForm.title,
        description: editForm.description || undefined,
      }),
    })
    const data = await response.json() as ChannelRoom | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در ویرایش کانال')
    }

    createdChannel.value = data as ChannelRoom
    upsertExistingChannel(createdChannel.value)
    successMessage.value = `✅ مشخصات کانال «${createdChannel.value.title}» ذخیره شد.`
  } catch (error) {
    errorMessage.value = `❌ ${error instanceof Error ? error.message : 'خطا در ویرایش کانال'}`
  } finally {
    isUpdatingChannel.value = false
  }
}

async function mutateMember(member: ChannelMember, payload: { role?: 'admin' | 'member'; remove_member?: boolean }, successText: string) {
  if (!createdChannel.value) return
  mutatingMemberId.value = member.user_id
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await apiFetch(`/api/chat/channels/${createdChannel.value.id}/members/${member.user_id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    const data = await response.json() as ChannelMemberMutationResponse | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در تغییر عضو کانال')
    }

    const summary = data as ChannelMemberMutationResponse
    createdChannel.value = {
      ...createdChannel.value,
      member_count: summary.member_count,
    }
    upsertExistingChannel(createdChannel.value)
    successMessage.value = successText
    await Promise.all([loadMembers(), loadCandidates(searchQuery.value)])
  } catch (error) {
    errorMessage.value = `❌ ${error instanceof Error ? error.message : 'خطا در تغییر عضو کانال'}`
  } finally {
    mutatingMemberId.value = null
  }
}

async function promoteMember(member: ChannelMember) {
  await mutateMember(member, { role: 'admin' }, `✅ ${member.account_name} به ادمین کانال تبدیل شد.`)
}

async function demoteMember(member: ChannelMember) {
  await mutateMember(member, { role: 'member' }, `✅ نقش ادمینی ${member.account_name} برداشته شد.`)
}

async function removeMember(member: ChannelMember) {
  await mutateMember(member, { remove_member: true }, `✅ ${member.account_name} از کانال حذف شد.`)
}

void loadExistingChannels()
</script>

<template>
  <div class="channel-card">
    <div class="header-block">
      <h2>{{ isPickerActive ? 'مدیریت اعضای کانال' : 'ساخت کانال اختیاری' }}</h2>
      <p>
        {{ isPickerActive
          ? 'اعضای فعلی کانال را مدیریت کنید و سپس کاربران فعال پروژه را به‌صورت invite-only به آن اضافه کنید.'
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

      <div v-if="isLoadingChannels" class="picker-state">در حال دریافت کانال‌های اختیاری موجود...</div>

      <div v-else-if="existingChannels.length > 0" class="existing-shell">
        <div class="existing-title-row">
          <h3>کانال‌های اختیاری موجود</h3>
          <span>{{ existingChannels.length }} کانال</span>
        </div>

        <button
          v-for="channel in existingChannels"
          :key="channel.id"
          type="button"
          class="existing-channel-row"
          @click="openExistingChannel(channel)"
        >
          <div class="existing-channel-avatar">{{ getAvatarInitial(channel.title) }}</div>
          <div class="existing-channel-copy">
            <span class="existing-channel-name">{{ channel.title }}</span>
            <span class="existing-channel-meta">
              اعضای فعال: {{ channel.member_count }}
              <template v-if="channel.description"> • {{ channel.description }}</template>
            </span>
          </div>
          <span class="existing-channel-action">ادامه دعوت اعضا</span>
        </button>
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

      <div class="channel-edit-shell">
        <div class="form-group">
          <label for="edit-channel-title">نام کانال</label>
          <input id="edit-channel-title" v-model="editForm.title" type="text" maxlength="255" placeholder="نام کانال" />
        </div>

        <div class="form-group">
          <label for="edit-channel-description">توضیحات</label>
          <textarea id="edit-channel-description" v-model="editForm.description" rows="3" maxlength="2000" placeholder="توضیح کوتاه برای ادمین‌ها"></textarea>
        </div>

        <div class="edit-actions">
          <button type="button" class="secondary-btn compact" :disabled="!canUpdateChannel || isUpdatingChannel" @click="updateChannelDetails">
            {{ isUpdatingChannel ? 'در حال ذخیره...' : 'ذخیره مشخصات کانال' }}
          </button>
        </div>
      </div>

      <div class="member-shell">
        <div class="member-shell-header">
          <h3>اعضای فعلی کانال</h3>
          <span>{{ createdChannel?.member_count ?? 0 }} عضو فعال</span>
        </div>

        <div v-if="isLoadingMembers" class="picker-state">در حال دریافت اعضای فعلی کانال...</div>

        <div v-else class="member-list">
          <div v-for="member in members" :key="member.user_id" class="member-row">
            <div class="member-avatar">{{ getAvatarInitial(member.account_name) }}</div>
            <div class="member-copy">
              <div class="member-name-row">
                <span class="member-name">{{ member.account_name }}</span>
                <span class="member-role" :class="member.role">{{ member.role === 'admin' ? 'ادمین' : 'عضو' }}</span>
                <span v-if="member.is_channel_creator" class="member-creator">سازنده</span>
              </div>
              <span class="member-details">{{ member.full_name }} • {{ member.mobile_number }}</span>
            </div>
            <div class="member-actions">
              <button
                v-if="member.role === 'member'"
                type="button"
                class="member-action-btn"
                :disabled="mutatingMemberId === member.user_id"
                @click="promoteMember(member)"
              >
                ادمین کردن
              </button>
              <button
                v-else
                type="button"
                class="member-action-btn"
                :disabled="mutatingMemberId === member.user_id || !canDemoteMember(member)"
                :title="getMemberGuardReason(member)"
                @click="demoteMember(member)"
              >
                برداشتن ادمین
              </button>
              <button
                type="button"
                class="member-action-btn danger"
                :disabled="mutatingMemberId === member.user_id || !canRemoveMember(member)"
                :title="getMemberGuardReason(member)"
                @click="removeMember(member)"
              >
                حذف
              </button>
            </div>
          </div>
        </div>
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

.channel-edit-shell {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border-radius: 18px;
  background: rgba(255, 251, 235, 0.55);
  border: 1px solid rgba(245, 158, 11, 0.14);
}

.edit-actions {
  display: flex;
  justify-content: flex-end;
}

.member-shell {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(245, 158, 11, 0.12);
}

.member-shell-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.member-shell-header h3 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 800;
  color: #1f2937;
}

.member-shell-header span {
  font-size: 0.8rem;
  color: #6b7280;
}

.member-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.member-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border-radius: 16px;
  background: rgba(255, 251, 235, 0.52);
  border: 1px solid rgba(245, 158, 11, 0.12);
}

.member-avatar {
  width: 42px;
  height: 42px;
  border-radius: 14px;
  background: linear-gradient(135deg, #f59e0b, #f97316);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  flex-shrink: 0;
}

.member-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.member-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.member-name {
  font-weight: 800;
  color: #111827;
}

.member-role,
.member-creator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 0.72rem;
  font-weight: 700;
}

.member-role.admin {
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
}

.member-role.member {
  background: rgba(148, 163, 184, 0.16);
  color: #475569;
}

.member-creator {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.member-details {
  color: #6b7280;
  font-size: 0.8rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.member-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.member-action-btn {
  border: 0;
  border-radius: 12px;
  padding: 9px 12px;
  font: inherit;
  font-size: 0.8rem;
  font-weight: 700;
  background: rgba(245, 158, 11, 0.12);
  color: #b45309;
  cursor: pointer;
}

.member-action-btn:disabled {
  opacity: 0.55;
  cursor: default;
}

.member-action-btn.danger {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
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

.existing-shell {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.existing-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.existing-title-row h3 {
  margin: 0;
  font-size: 0.94rem;
  font-weight: 900;
  color: #1f2937;
}

.existing-title-row span {
  font-size: 0.78rem;
  color: #92400e;
}

.existing-channel-row {
  width: 100%;
  border: 1px solid rgba(245, 158, 11, 0.14);
  background: rgba(255, 255, 255, 0.82);
  border-radius: 18px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: right;
  cursor: pointer;
}

.existing-channel-avatar {
  width: 42px;
  height: 42px;
  min-width: 42px;
  border-radius: 50%;
  background: linear-gradient(135deg, #0f766e, #10b981);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.95rem;
  font-weight: 900;
}

.existing-channel-copy {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
}

.existing-channel-name {
  font-size: 0.92rem;
  font-weight: 800;
  color: #111827;
}

.existing-channel-meta {
  font-size: 0.76rem;
  color: #6b7280;
  line-height: 1.6;
}

.existing-channel-action {
  flex: none;
  color: #0f766e;
  font-size: 0.78rem;
  font-weight: 800;
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