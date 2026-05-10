<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { apiFetch, apiFetchJson } from '../../utils/auth'
import { Check, Loader2, LogOut, Shield, Trash2, UserPlus, UsersRound, X } from 'lucide-vue-next'

type PublicUser = {
  id: number
  account_name: string
  full_name?: string
  mobile_number?: string
}

type GroupMember = {
  user_id: number
  account_name: string
  full_name: string
  mobile_number: string
  role: 'admin' | 'member'
  is_group_creator: boolean
}

type GroupRoom = {
  id: number
  title: string
  member_count: number
  max_members: number
  current_user_role?: 'admin' | 'member' | null
}

type GroupDetail = {
  group: GroupRoom
  members: GroupMember[]
}

const props = defineProps<{
  show: boolean
  groupId?: number | null
  currentUserId: number
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', group: GroupRoom): void
  (e: 'updated', group: GroupRoom): void
  (e: 'left', chatId: number): void
}>()

const title = ref('')
const searchQuery = ref('')
const candidates = ref<PublicUser[]>([])
const members = ref<GroupMember[]>([])
const group = ref<GroupRoom | null>(null)
const selectedUserIds = ref<Set<number>>(new Set())
const isLoadingDetail = ref(false)
const isLoadingUsers = ref(false)
const isSaving = ref(false)
const mutatingUserId = ref<number | null>(null)
const errorMessage = ref('')
const successMessage = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null

const isCreateMode = computed(() => !props.groupId)
const isAdmin = computed(() => isCreateMode.value || group.value?.current_user_role === 'admin')
const activeAdminCount = computed(() => members.value.filter(member => member.role === 'admin').length)
const memberIds = computed(() => new Set(members.value.map(member => member.user_id)))

const availableCandidates = computed(() => {
  return candidates.value.filter(user => user.id !== props.currentUserId && !memberIds.value.has(user.id))
})

const selectedCount = computed(() => selectedUserIds.value.size)
const canSaveTitle = computed(() => title.value.trim().length > 0 && !isSaving.value)

function resetState() {
  title.value = ''
  searchQuery.value = ''
  candidates.value = []
  members.value = []
  group.value = null
  selectedUserIds.value = new Set()
  isLoadingDetail.value = false
  isLoadingUsers.value = false
  isSaving.value = false
  mutatingUserId.value = null
  errorMessage.value = ''
  successMessage.value = ''
}

function getAvatarInitial(name: string) {
  return name ? name.charAt(0).toUpperCase() : '?'
}

function setError(error: unknown, fallback: string) {
  errorMessage.value = error instanceof Error ? error.message : fallback
}

async function loadUsers(query = '') {
  isLoadingUsers.value = true
  try {
    const params = new URLSearchParams({ limit: '100' })
    const trimmed = query.trim()
    if (trimmed) params.set('q', trimmed)
    const data = await apiFetchJson(`/api/users-public/search?${params.toString()}`) as PublicUser[]
    candidates.value = Array.isArray(data) ? data : []
  } catch (error) {
    setError(error, 'خطا در دریافت کاربران')
  } finally {
    isLoadingUsers.value = false
  }
}

async function loadGroupDetail() {
  if (!props.groupId) return
  isLoadingDetail.value = true
  errorMessage.value = ''
  try {
    const data = await apiFetchJson(`/api/chat/groups/${props.groupId}`) as GroupDetail
    group.value = data.group
    members.value = Array.isArray(data.members) ? data.members : []
    title.value = data.group.title || ''
  } catch (error) {
    setError(error, 'خطا در دریافت گروه')
  } finally {
    isLoadingDetail.value = false
  }
}

function toggleCandidate(userId: number) {
  const next = new Set(selectedUserIds.value)
  if (next.has(userId)) next.delete(userId)
  else next.add(userId)
  selectedUserIds.value = next
}

async function createGroup() {
  if (!canSaveTitle.value) return
  isSaving.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await apiFetch('/api/chat/groups', {
      method: 'POST',
      body: JSON.stringify({
        title: title.value.trim(),
        member_ids: Array.from(selectedUserIds.value),
      }),
    })
    const data = await response.json() as { group?: GroupRoom; detail?: string }
    if (!response.ok || !data.group) {
      throw new Error(data.detail || 'خطا در ساخت گروه')
    }
    emit('created', data.group)
  } catch (error) {
    setError(error, 'خطا در ساخت گروه')
  } finally {
    isSaving.value = false
  }
}

async function updateTitle() {
  if (!props.groupId || !canSaveTitle.value || !isAdmin.value) return
  isSaving.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await apiFetch(`/api/chat/groups/${props.groupId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title: title.value.trim() }),
    })
    const data = await response.json() as GroupRoom | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در ویرایش گروه')
    }
    group.value = data as GroupRoom
    successMessage.value = 'نام گروه ذخیره شد.'
    emit('updated', group.value)
  } catch (error) {
    setError(error, 'خطا در ویرایش گروه')
  } finally {
    isSaving.value = false
  }
}

async function addSelectedMembers() {
  if (!props.groupId || selectedUserIds.value.size === 0 || !isAdmin.value) return
  isSaving.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    for (const userId of selectedUserIds.value) {
      const response = await apiFetch(`/api/chat/groups/${props.groupId}/members`, {
        method: 'POST',
        body: JSON.stringify({ user_id: userId }),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => ({})) as { detail?: string }
        throw new Error(data.detail || 'خطا در افزودن عضو')
      }
    }
    selectedUserIds.value = new Set()
    successMessage.value = 'اعضای انتخاب‌شده اضافه شدند.'
    await Promise.all([loadGroupDetail(), loadUsers(searchQuery.value)])
    if (group.value) emit('updated', group.value)
  } catch (error) {
    setError(error, 'خطا در افزودن عضو')
  } finally {
    isSaving.value = false
  }
}

async function mutateMember(member: GroupMember, endpoint: string, method: string, successText: string) {
  if (!props.groupId || !isAdmin.value) return
  mutatingUserId.value = member.user_id
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await apiFetch(endpoint, { method })
    const data = await response.json().catch(() => ({})) as { detail?: string }
    if (!response.ok) {
      throw new Error(data.detail || 'خطا در تغییر عضو')
    }
    successMessage.value = successText
    await Promise.all([loadGroupDetail(), loadUsers(searchQuery.value)])
    if (group.value) emit('updated', group.value)
  } catch (error) {
    setError(error, 'خطا در تغییر عضو')
  } finally {
    mutatingUserId.value = null
  }
}

function canDemote(member: GroupMember) {
  return isAdmin.value && member.role === 'admin' && activeAdminCount.value > 1
}

function canRemove(member: GroupMember) {
  if (!isAdmin.value || member.user_id === props.currentUserId) return false
  if (member.role === 'admin') return activeAdminCount.value > 1
  return true
}

async function promote(member: GroupMember) {
  await mutateMember(member, `/api/chat/groups/${props.groupId}/admins/${member.user_id}`, 'POST', `${member.account_name} ادمین شد.`)
}

async function demote(member: GroupMember) {
  await mutateMember(member, `/api/chat/groups/${props.groupId}/admins/${member.user_id}`, 'DELETE', `نقش ادمین ${member.account_name} برداشته شد.`)
}

async function removeMember(member: GroupMember) {
  await mutateMember(member, `/api/chat/groups/${props.groupId}/members/${member.user_id}`, 'DELETE', `${member.account_name} از گروه حذف شد.`)
}

async function leaveGroup() {
  if (!props.groupId) return
  isSaving.value = true
  errorMessage.value = ''
  try {
    const response = await apiFetch(`/api/chat/groups/${props.groupId}/leave`, { method: 'POST' })
    const data = await response.json().catch(() => ({})) as { detail?: string }
    if (!response.ok) {
      throw new Error(data.detail || 'خطا در خروج از گروه')
    }
    emit('left', props.groupId)
  } catch (error) {
    setError(error, 'خطا در خروج از گروه')
  } finally {
    isSaving.value = false
  }
}

watch(searchQuery, (query) => {
  if (!props.show) return
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    void loadUsers(query)
  }, 250)
})

watch(() => [props.show, props.groupId] as const, ([show]) => {
  if (!show) return
  resetState()
  void loadUsers()
  if (props.groupId) void loadGroupDetail()
}, { immediate: true })
</script>

<template>
  <Teleport to="body">
    <Transition name="group-manager-slide">
      <div v-if="show" class="group-manager-overlay" @click="emit('close')">
        <section class="group-manager-shell" @click.stop>
          <header class="group-manager-header">
            <button type="button" class="icon-btn" v-ripple @click="emit('close')">
              <X :size="22" />
            </button>
            <div class="header-copy">
              <h3>{{ isCreateMode ? 'ساخت گروه' : 'مدیریت گروه' }}</h3>
              <span v-if="!isCreateMode && group">{{ group.member_count.toLocaleString('fa-IR') }} عضو</span>
            </div>
          </header>

          <main class="group-manager-body">
            <div v-if="errorMessage" class="message-box error">{{ errorMessage }}</div>
            <div v-if="successMessage" class="message-box success">{{ successMessage }}</div>

            <div class="title-row">
              <label for="group-title">نام گروه</label>
              <div class="title-controls">
                <input id="group-title" v-model="title" type="text" maxlength="255" :disabled="!isCreateMode && !isAdmin" />
                <button v-if="isCreateMode" type="button" class="primary-btn" :disabled="!canSaveTitle" @click="createGroup">
                  <Loader2 v-if="isSaving" :size="18" class="spin" />
                  <UsersRound v-else :size="18" />
                  <span>ساخت</span>
                </button>
                <button v-else-if="isAdmin" type="button" class="secondary-btn" :disabled="!canSaveTitle" @click="updateTitle">
                  <Loader2 v-if="isSaving" :size="18" class="spin" />
                  <Check v-else :size="18" />
                  <span>ذخیره</span>
                </button>
              </div>
            </div>

            <div v-if="!isCreateMode" class="members-section">
              <div class="section-title">
                <span>اعضا</span>
                <span>{{ members.length.toLocaleString('fa-IR') }}</span>
              </div>

              <div v-if="isLoadingDetail" class="loading-row">
                <Loader2 :size="18" class="spin" />
                <span>در حال دریافت...</span>
              </div>

              <div v-else class="member-list">
                <div v-for="member in members" :key="member.user_id" class="member-row">
                  <div class="avatar">{{ getAvatarInitial(member.account_name) }}</div>
                  <div class="member-copy">
                    <div class="member-name-row">
                      <span class="member-name">{{ member.account_name }}</span>
                      <span class="role-chip" :class="member.role">{{ member.role === 'admin' ? 'ادمین' : 'عضو' }}</span>
                      <span v-if="member.is_group_creator" class="role-chip creator">سازنده</span>
                    </div>
                    <span class="member-details">{{ member.full_name }} • {{ member.mobile_number }}</span>
                  </div>
                  <div v-if="isAdmin" class="member-actions">
                    <button v-if="member.role === 'member'" type="button" class="member-action" :disabled="mutatingUserId === member.user_id" @click="promote(member)">
                      <Shield :size="16" />
                    </button>
                    <button v-else type="button" class="member-action" :disabled="mutatingUserId === member.user_id || !canDemote(member)" @click="demote(member)">
                      <Shield :size="16" />
                    </button>
                    <button type="button" class="member-action danger" :disabled="mutatingUserId === member.user_id || !canRemove(member)" @click="removeMember(member)">
                      <Trash2 :size="16" />
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="isCreateMode || isAdmin" class="candidate-section">
              <div class="section-title">
                <span>{{ isCreateMode ? 'اعضای اولیه' : 'افزودن عضو' }}</span>
                <span>{{ selectedCount.toLocaleString('fa-IR') }} انتخاب</span>
              </div>
              <input v-model="searchQuery" class="candidate-search" type="text" placeholder="جستجو با نام، اکانت یا موبایل..." />

              <div v-if="isLoadingUsers" class="loading-row">
                <Loader2 :size="18" class="spin" />
                <span>در حال جستجو...</span>
              </div>
              <div v-else-if="availableCandidates.length === 0" class="empty-row">کاربری برای نمایش نیست.</div>
              <div v-else class="candidate-list">
                <button
                  v-for="user in availableCandidates"
                  :key="user.id"
                  type="button"
                  class="candidate-row"
                  :class="{ selected: selectedUserIds.has(user.id) }"
                  @click="toggleCandidate(user.id)"
                >
                  <span class="candidate-check"><Check v-if="selectedUserIds.has(user.id)" :size="16" /></span>
                  <span class="avatar">{{ getAvatarInitial(user.account_name) }}</span>
                  <span class="candidate-copy">
                    <span class="candidate-name">{{ user.account_name }}</span>
                    <span class="candidate-details">{{ user.full_name }} • {{ user.mobile_number }}</span>
                  </span>
                </button>
              </div>
            </div>
          </main>

          <footer class="group-manager-footer">
            <button v-if="!isCreateMode" type="button" class="danger-text-btn" :disabled="isSaving" @click="leaveGroup">
              <LogOut :size="18" />
              <span>خروج از گروه</span>
            </button>
            <button v-if="!isCreateMode && isAdmin" type="button" class="primary-btn" :disabled="selectedCount === 0 || isSaving" @click="addSelectedMembers">
              <UserPlus :size="18" />
              <span>افزودن {{ selectedCount.toLocaleString('fa-IR') }} عضو</span>
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.group-manager-overlay {
  position: fixed;
  inset: 0;
  z-index: 2200;
  display: flex;
  justify-content: center;
  align-items: flex-end;
  background: rgba(15, 23, 42, 0.34);
}

.group-manager-shell {
  width: min(100vw, 560px);
  max-height: min(92vh, 780px);
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-radius: 18px 18px 0 0;
  overflow: hidden;
  box-shadow: 0 -18px 48px rgba(15, 23, 42, 0.22);
  direction: rtl;
}

.group-manager-header,
.group-manager-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid #eef2f7;
}

.group-manager-footer {
  justify-content: space-between;
  border-top: 1px solid #eef2f7;
  border-bottom: 0;
}

.icon-btn,
.member-action {
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: #64748b;
  cursor: pointer;
}

.icon-btn:hover,
.member-action:hover:not(:disabled) {
  background: #f1f5f9;
}

.member-action:disabled {
  opacity: 0.36;
  cursor: default;
}

.member-action.danger {
  color: #dc2626;
}

.header-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.header-copy h3 {
  margin: 0;
  font-size: 1rem;
  font-weight: 800;
  color: #111827;
}

.header-copy span,
.section-title span:last-child {
  font-size: 0.78rem;
  color: #64748b;
}

.group-manager-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message-box {
  border-radius: 12px;
  padding: 10px 12px;
  font-size: 0.84rem;
  font-weight: 700;
}

.message-box.error {
  background: #fef2f2;
  color: #b91c1c;
}

.message-box.success {
  background: #ecfdf5;
  color: #047857;
}

.title-row,
.members-section,
.candidate-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.title-row label,
.section-title {
  font-size: 0.86rem;
  font-weight: 800;
  color: #334155;
}

.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.title-controls {
  display: flex;
  gap: 8px;
  align-items: stretch;
  flex-wrap: wrap;
}

.title-controls input,
.candidate-search {
  flex: 1;
  min-width: 0;
  height: 48px;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 0 16px;
  font: inherit;
  font-size: 0.95rem;
  line-height: 1.2;
  outline: none;
  box-sizing: border-box;
  background: #f8fafc;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04);
  transition: border-color 0.14s ease, box-shadow 0.14s ease, background 0.14s ease;
}

.title-controls input:focus,
.candidate-search:focus {
  border-color: #3390ec;
  background: #ffffff;
  box-shadow: 0 0 0 4px rgba(51, 144, 236, 0.12);
}

.primary-btn,
.secondary-btn,
.danger-text-btn {
  border: 0;
  border-radius: 14px;
  padding: 0 14px;
  min-height: 48px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font: inherit;
  font-weight: 800;
  cursor: pointer;
  white-space: nowrap;
}

.primary-btn {
  background: #3390ec;
  color: white;
}

.secondary-btn {
  background: #e0f2fe;
  color: #0369a1;
}

.danger-text-btn {
  background: transparent;
  color: #dc2626;
}

.primary-btn:disabled,
.secondary-btn:disabled,
.danger-text-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.member-list,
.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.member-row,
.candidate-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 56px;
  border-radius: 14px;
  background: #f8fafc;
  border: 1px solid #eef2f7;
  padding: 8px 10px;
}

.candidate-row {
  width: 100%;
  text-align: right;
  cursor: pointer;
}

.candidate-row.selected {
  border-color: #3390ec;
  background: rgba(51, 144, 236, 0.08);
}

.avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: linear-gradient(135deg, #2563eb, #06b6d4);
  color: white;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  flex-shrink: 0;
}

.member-copy,
.candidate-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.member-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.member-name,
.candidate-name {
  font-weight: 800;
  color: #111827;
}

.member-details,
.candidate-details {
  color: #64748b;
  font-size: 0.78rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.role-chip {
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 0.7rem;
  font-weight: 800;
}

.role-chip.admin {
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
}

.role-chip.member {
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
}

.role-chip.creator {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.member-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.candidate-check {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid #cbd5e1;
  color: #3390ec;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.candidate-row.selected .candidate-check {
  border-color: #3390ec;
  background: #3390ec;
  color: white;
}

.loading-row,
.empty-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 52px;
  color: #64748b;
  font-size: 0.86rem;
}

.spin {
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.group-manager-slide-enter-active,
.group-manager-slide-leave-active {
  transition: opacity 0.2s ease;
}

.group-manager-slide-enter-active .group-manager-shell,
.group-manager-slide-leave-active .group-manager-shell {
  transition: transform 0.24s ease;
}

.group-manager-slide-enter-from,
.group-manager-slide-leave-to {
  opacity: 0;
}

.group-manager-slide-enter-from .group-manager-shell,
.group-manager-slide-leave-to .group-manager-shell {
  transform: translateY(100%);
}

@media (min-width: 700px) {
  .group-manager-overlay {
    align-items: center;
  }

  .group-manager-shell {
    border-radius: 18px;
  }
}

@media (max-width: 520px) {
  .title-controls,
  .group-manager-footer {
    flex-direction: column;
    align-items: stretch;
  }

  .primary-btn,
  .secondary-btn,
  .danger-text-btn {
    width: 100%;
  }
}
</style>