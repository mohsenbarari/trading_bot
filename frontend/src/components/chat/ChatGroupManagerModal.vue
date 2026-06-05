<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ChatUserListRow from './ChatUserListRow.vue'
import { apiFetch, apiFetchJson } from '../../utils/auth'
import { popBackState, pushBackState } from '../../composables/useBackButton'
import { buildChatFileUrl, getAvatarInitial, uploadAvatarImage } from '../../utils/chatFiles'
import {
  getGroupDetailCacheKey,
  invalidateChatManagerCache,
  readChatManagerCache,
  writeChatManagerCache,
} from '../../services/chat/chatManagerCache'
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Info,
  Loader2,
  LogOut,
  PencilLine,
  Shield,
  UserPlus,
  UsersRound,
  X,
} from 'lucide-vue-next'

type PublicUser = {
  id: number
  account_name: string
  full_name?: string
  mobile_number?: string
  avatar_file_id?: string | null
}

type GroupMember = {
  user_id: number
  account_name: string
  full_name: string
  mobile_number: string
  avatar_file_id?: string | null
  role: 'admin' | 'member'
  is_group_creator: boolean
}

type GroupRoom = {
  id: number
  title: string
  description?: string | null
  avatar_file_id?: string | null
  member_count: number
  max_members: number
  current_user_role?: 'admin' | 'member' | null
}

type GroupDetail = {
  group: GroupRoom
  members: GroupMember[]
}

type GroupManagerPage = 'select-members' | 'details' | 'overview' | 'members' | 'admins' | 'add-members' | 'edit'

const props = defineProps<{
  show: boolean
  groupId?: number | null
  currentUserId: number
  apiBaseUrl?: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', group: GroupRoom): void
  (e: 'updated', group: GroupRoom): void
  (e: 'left', chatId: number): void
  (e: 'open-public-profile', payload: { id: number; account_name: string }): void
}>()

const page = ref<GroupManagerPage>('select-members')
const title = ref('')
const description = ref('')
const directoryQuery = ref('')
const memberQuery = ref('')
const adminQuery = ref('')
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
const pageHistory = ref<GroupManagerPage[]>([])
const managerBackStateActive = ref(false)
const avatarFileId = ref<string | null>(null)
const avatarBusy = ref(false)
const avatarInput = ref<HTMLInputElement | null>(null)
let searchTimer: ReturnType<typeof setTimeout> | null = null

const isCreateMode = computed(() => !props.groupId)
const isAdmin = computed(() => isCreateMode.value || group.value?.current_user_role === 'admin')
const memberIds = computed(() => new Set(members.value.map((member) => member.user_id)))
const activeAdminCount = computed(() => members.value.filter((member) => member.role === 'admin').length)
const selectedCount = computed(() => selectedUserIds.value.size)
const canContinueCreate = computed(() => selectedCount.value > 0)
const canSubmitAddMembers = computed(() => selectedCount.value > 0 && isAdmin.value)
const canSaveDetails = computed(() => title.value.trim().length > 0)
const overviewTitle = computed(() => title.value.trim() || group.value?.title || 'گروه جدید')
const overviewDescription = computed(() => {
  const nextDescription = description.value.trim() || group.value?.description || ''
  return nextDescription || 'توضیحی برای این گروه ثبت نشده است.'
})
const groupAvatarUrl = computed(() => buildChatFileUrl(avatarFileId.value, props.apiBaseUrl ?? ''))
const canEditOverviewAvatar = computed(() => !isCreateMode.value && isAdmin.value)
const currentGroupRoleLabel = computed(() => {
  if (isCreateMode.value) return 'سازنده گروه'
  if (isAdmin.value) return 'ادمین گروه'
  return 'عضو گروه'
})

const availableCandidates = computed(() => {
  return candidates.value.filter((user) => user.id !== props.currentUserId && !memberIds.value.has(user.id))
})

function normalizeSearch(value: string) {
  return value.trim().toLowerCase()
}

function compareMemberOrder(left: GroupMember, right: GroupMember) {
  if (left.is_group_creator !== right.is_group_creator) return left.is_group_creator ? -1 : 1
  if (left.role !== right.role) return left.role === 'admin' ? -1 : 1
  return left.account_name.localeCompare(right.account_name, 'fa')
}

const filteredMembers = computed(() => {
  const query = normalizeSearch(memberQuery.value)
  return members.value
    .slice()
    .sort(compareMemberOrder)
    .filter((member) => {
      if (!query) return true
      const haystack = [member.account_name, member.full_name, member.mobile_number].join(' ').toLowerCase()
      return haystack.includes(query)
    })
})

const filteredAdmins = computed(() => {
  const query = normalizeSearch(adminQuery.value)
  return members.value
    .filter((member) => member.role === 'admin')
    .slice()
    .sort(compareMemberOrder)
    .filter((member) => {
      if (!query) return true
      const haystack = [member.account_name, member.full_name, member.mobile_number].join(' ').toLowerCase()
      return haystack.includes(query)
    })
})

const promotableMembers = computed(() => {
  const query = normalizeSearch(adminQuery.value)
  return members.value
    .filter((member) => member.role === 'member')
    .slice()
    .sort(compareMemberOrder)
    .filter((member) => {
      if (!query) return true
      const haystack = [member.account_name, member.full_name, member.mobile_number].join(' ').toLowerCase()
      return haystack.includes(query)
    })
})

const pageTitle = computed(() => {
  if (isCreateMode.value) {
    if (page.value === 'details') return 'اطلاعات گروه'
    return 'افزودن اعضا'
  }

  switch (page.value) {
    case 'members':
      return 'اعضای گروه'
    case 'admins':
      return 'مدیریت ادمین‌ها'
    case 'add-members':
      return 'افزودن عضو'
    case 'edit':
      return 'ویرایش اطلاعات گروه'
    default:
      return 'مدیریت گروه'
  }
})

const pageSubtitle = computed(() => {
  if (isCreateMode.value) {
    return page.value === 'details'
      ? `${selectedCount.value.toLocaleString('fa-IR')} عضو انتخاب شده`
      : 'اعضایی را که می‌خواهید در گروه باشند انتخاب کنید.'
  }

  switch (page.value) {
    case 'members':
      return `${filteredMembers.value.length.toLocaleString('fa-IR')} عضو نمایش داده می‌شود`
    case 'admins':
      return `${filteredAdmins.value.length.toLocaleString('fa-IR')} ادمین فعال`
    case 'add-members':
      return 'کاربران پروژه را انتخاب و به گروه اضافه کنید.'
    case 'edit':
      return 'نام و توضیحات گروه را دقیقاً از همین صفحه مدیریت کنید.'
    default:
      return group.value
        ? `${(group.value.member_count || 0).toLocaleString('fa-IR')} عضو`
        : 'در حال آماده‌سازی...'
  }
})

const canGoBack = computed(() => {
  if (isCreateMode.value) return page.value !== 'select-members'
  return page.value !== 'overview'
})

function resetState() {
  page.value = isCreateMode.value ? 'select-members' : 'overview'
  title.value = ''
  description.value = ''
  directoryQuery.value = ''
  memberQuery.value = ''
  adminQuery.value = ''
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
  pageHistory.value = []
  avatarFileId.value = null
  avatarBusy.value = false
}

function setError(error: unknown, fallback: string) {
  errorMessage.value = error instanceof Error ? error.message : fallback
}

function clearFlashMessages() {
  errorMessage.value = ''
  successMessage.value = ''
}

function triggerAvatarPicker() {
  if (avatarBusy.value) return
  avatarInput.value?.click()
}

async function handleAvatarSelected(event: Event) {
  const input = event.target as HTMLInputElement | null
  const file = input?.files?.[0]
  if (!file) return

  const previousAvatarFileId = avatarFileId.value
  avatarBusy.value = true
  clearFlashMessages()
  try {
    const uploaded = await uploadAvatarImage(file, props.apiBaseUrl ?? '')
    avatarFileId.value = uploaded.file_id
    if (!isCreateMode.value && page.value === 'overview') {
      await persistExistingGroupAvatar(uploaded.file_id)
    }
  } catch (error) {
    avatarFileId.value = previousAvatarFileId
    setError(error, 'آپلود آواتار گروه ناموفق بود')
  } finally {
    avatarBusy.value = false
    if (input) input.value = ''
  }
}

async function clearAvatar() {
  if (avatarBusy.value) return
  const previousAvatarFileId = avatarFileId.value
  avatarFileId.value = null
  if (isCreateMode.value || page.value !== 'overview') return

  avatarBusy.value = true
  clearFlashMessages()
  try {
    await persistExistingGroupAvatar(null)
  } catch (error) {
    avatarFileId.value = previousAvatarFileId
    setError(error, 'حذف آواتار گروه ناموفق بود')
  } finally {
    avatarBusy.value = false
  }
}

function getPrimaryUserName(accountName: string, fullName?: string | null) {
  const normalizedFullName = (fullName || '').trim()
  return normalizedFullName || accountName
}

function getGroupMemberBadges(member: GroupMember): Array<{ label: string; tone: 'admin' | 'member' | 'creator' }> {
  if (member.is_group_creator) {
    return [{ label: 'owner', tone: 'creator' as const }]
  }
  return [{ label: member.role === 'admin' ? 'admin' : 'member', tone: member.role === 'admin' ? 'admin' : 'member' as const }]
}

function getPromotableMemberBadges(member: GroupMember): Array<{ label: string; tone: 'admin' | 'member' | 'creator' }> {
  return getGroupMemberBadges(member)
}

function openMemberProfile(member: GroupMember) {
  emit('open-public-profile', {
    id: member.user_id,
    account_name: member.account_name,
  })
}

function canDemote(member: GroupMember) {
  return isAdmin.value && member.role === 'admin' && !member.is_group_creator && member.user_id !== props.currentUserId && activeAdminCount.value > 1
}

function canRemove(member: GroupMember) {
  if (!isAdmin.value) return false
  if (member.user_id === props.currentUserId) return false
  if (member.is_group_creator) return false
  if (member.role === 'admin') return activeAdminCount.value > 1
  return true
}

function getMemberGuardReason(member: GroupMember) {
  if (member.is_group_creator) return 'سازنده گروه باید همیشه عضو و ادمین بماند.'
  if (member.user_id === props.currentUserId) return 'برای خروج از گروه از گزینه خروج استفاده کنید.'
  if (member.role === 'admin' && activeAdminCount.value <= 1) return 'گروه باید حداقل یک ادمین فعال داشته باشد.'
  return ''
}

function applyPage(nextPage: GroupManagerPage) {
  page.value = nextPage
  if (nextPage === 'add-members' || nextPage === 'select-members') {
    void loadUsers(directoryQuery.value)
  }
}

function setPage(nextPage: GroupManagerPage) {
  clearFlashMessages()
  if (nextPage === page.value) return
  pageHistory.value.push(page.value)
  applyPage(nextPage)
}

function setPageDirect(nextPage: GroupManagerPage) {
  clearFlashMessages()
  applyPage(nextPage)
  while (pageHistory.value.length > 0 && pageHistory.value[pageHistory.value.length - 1] === nextPage) {
    pageHistory.value.pop()
  }
}

function requestClose(fromBack = false) {
  if (!fromBack && managerBackStateActive.value) {
    managerBackStateActive.value = false
    popBackState()
  }
  emit('close')
}

function pushManagerBackState() {
  if (!props.show || managerBackStateActive.value) return
  managerBackStateActive.value = true
  pushBackState(() => {
    managerBackStateActive.value = false
    const stillOpen = handleBack(true)
    if (stillOpen && props.show) {
      pushManagerBackState()
    }
  })
}

function handleBack(fromBack = false) {
  clearFlashMessages()
  const previousPage = pageHistory.value.pop()
  if (previousPage) {
    applyPage(previousPage)
    return true
  }
  requestClose(fromBack)
  return false
}

function toggleCandidate(userId: number) {
  const next = new Set(selectedUserIds.value)
  if (next.has(userId)) next.delete(userId)
  else next.add(userId)
  selectedUserIds.value = next
  void loadUsers(directoryQuery.value)
}

function buildGroupCandidateUrl(query = '') {
  const params = new URLSearchParams({ limit: '100' })
  if (props.groupId) params.set('exclude_chat_id', String(props.groupId))
  const trimmed = query.trim()
  if (trimmed) params.set('q', trimmed)
  Array.from(selectedUserIds.value)
    .sort((left, right) => left - right)
    .forEach((userId) => params.append('selected_user_ids', String(userId)))
  return `/api/chat/groups/member-candidates?${params.toString()}`
}

async function loadUsers(query = '') {
  isLoadingUsers.value = true
  try {
    const data = await apiFetchJson(buildGroupCandidateUrl(query)) as
      | PublicUser[]
      | { items?: Array<PublicUser | { user_id: number; account_name: string; full_name?: string; mobile_number?: string; avatar_file_id?: string | null }> }
    const rawItems = Array.isArray(data)
      ? data
      : Array.isArray(data?.items)
        ? data.items
        : []
    candidates.value = rawItems
      .map((item) => ({
        id: Number((item as PublicUser).id ?? (item as { user_id: number }).user_id),
        account_name: item.account_name,
        full_name: item.full_name,
        mobile_number: item.mobile_number,
        avatar_file_id: item.avatar_file_id ?? null,
      }))
      .filter((item) => Number.isInteger(item.id) && item.id > 0)
  } catch (error) {
    setError(error, 'خطا در دریافت کاربران')
  } finally {
    isLoadingUsers.value = false
  }
}

async function loadGroupDetail() {
  if (!props.groupId) return
  const cacheKey = getGroupDetailCacheKey(props.groupId)
  const cached = readChatManagerCache<GroupDetail>(cacheKey)
  if (cached) {
    group.value = cached.group
    members.value = Array.isArray(cached.members) ? cached.members : []
    title.value = cached.group.title || ''
    description.value = cached.group.description || ''
    avatarFileId.value = cached.group.avatar_file_id || null
    return
  }

  isLoadingDetail.value = true
  try {
    const data = await apiFetchJson(`/api/chat/groups/${props.groupId}`) as GroupDetail
    writeChatManagerCache(cacheKey, data)
    group.value = data.group
    members.value = Array.isArray(data.members) ? data.members : []
    title.value = data.group.title || ''
    description.value = data.group.description || ''
    avatarFileId.value = data.group.avatar_file_id || null
  } catch (error) {
    setError(error, 'خطا در دریافت گروه')
  } finally {
    isLoadingDetail.value = false
  }
}

async function createGroup() {
  if (!canSaveDetails.value || !canContinueCreate.value) return
  isSaving.value = true
  clearFlashMessages()
  try {
    const response = await apiFetch('/api/chat/groups', {
      method: 'POST',
      body: JSON.stringify({
        title: title.value.trim(),
        description: description.value.trim() || undefined,
        avatar_file_id: avatarFileId.value || null,
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

async function updateGroupSettings() {
  if (!props.groupId || !canSaveDetails.value || !isAdmin.value) return
  isSaving.value = true
  clearFlashMessages()
  try {
    const response = await apiFetch(`/api/chat/groups/${props.groupId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        title: title.value.trim(),
        description: description.value.trim() || undefined,
        avatar_file_id: avatarFileId.value || null,
      }),
    })
    const data = await response.json() as GroupRoom | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در ذخیره اطلاعات گروه')
    }
    invalidateChatManagerCache(getGroupDetailCacheKey(props.groupId))
    group.value = data as GroupRoom
    avatarFileId.value = group.value.avatar_file_id || null
    successMessage.value = 'اطلاعات گروه ذخیره شد.'
    emit('updated', group.value)
    setPageDirect('overview')
  } catch (error) {
    setError(error, 'خطا در ذخیره اطلاعات گروه')
  } finally {
    isSaving.value = false
  }
}

async function persistExistingGroupAvatar(nextAvatarFileId: string | null) {
  if (!props.groupId || !group.value || !isAdmin.value) return

  const response = await apiFetch(`/api/chat/groups/${props.groupId}`, {
    method: 'PATCH',
    body: JSON.stringify({
      title: group.value.title,
      description: group.value.description || undefined,
      avatar_file_id: nextAvatarFileId,
    }),
  })
  const data = await response.json() as GroupRoom | { detail?: string }
  if (!response.ok) {
    throw new Error((data as { detail?: string }).detail || 'خطا در ذخیره آواتار گروه')
  }

  invalidateChatManagerCache(getGroupDetailCacheKey(props.groupId))
  group.value = data as GroupRoom
  avatarFileId.value = group.value.avatar_file_id || null
  successMessage.value = nextAvatarFileId ? 'عکس گروه ذخیره شد.' : 'عکس گروه حذف شد.'
  emit('updated', group.value)
}

async function addSelectedMembers() {
  if (!props.groupId || !canSubmitAddMembers.value) return
  isSaving.value = true
  clearFlashMessages()
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
    invalidateChatManagerCache(getGroupDetailCacheKey(props.groupId))
    await Promise.all([loadGroupDetail(), loadUsers(directoryQuery.value)])
    if (group.value) emit('updated', group.value)
    setPageDirect('members')
  } catch (error) {
    setError(error, 'خطا در افزودن عضو')
  } finally {
    isSaving.value = false
  }
}

async function mutateMember(member: GroupMember, endpoint: string, method: string, successText: string) {
  if (!props.groupId || !isAdmin.value) return
  mutatingUserId.value = member.user_id
  clearFlashMessages()
  try {
    const response = await apiFetch(endpoint, { method })
    const data = await response.json().catch(() => ({})) as { detail?: string }
    if (!response.ok) {
      throw new Error(data.detail || 'خطا در تغییر عضو')
    }
    successMessage.value = successText
    invalidateChatManagerCache(getGroupDetailCacheKey(props.groupId))
    await Promise.all([loadGroupDetail(), loadUsers(directoryQuery.value)])
    if (group.value) emit('updated', group.value)
  } catch (error) {
    setError(error, 'خطا در تغییر عضو')
  } finally {
    mutatingUserId.value = null
  }
}

async function promote(member: GroupMember) {
  await mutateMember(member, `/api/chat/groups/${props.groupId}/admins/${member.user_id}`, 'POST', `${member.account_name} ادمین شد.`)
}

async function demote(member: GroupMember) {
  await mutateMember(member, `/api/chat/groups/${props.groupId}/admins/${member.user_id}`, 'DELETE', `نقش ادمینی ${member.account_name} برداشته شد.`)
}

async function removeMember(member: GroupMember) {
  await mutateMember(member, `/api/chat/groups/${props.groupId}/members/${member.user_id}`, 'DELETE', `${member.account_name} از گروه حذف شد.`)
}

async function leaveGroup() {
  if (!props.groupId) return
  isSaving.value = true
  clearFlashMessages()
  try {
    const response = await apiFetch(`/api/chat/groups/${props.groupId}/leave`, { method: 'POST' })
    const data = await response.json().catch(() => ({})) as { detail?: string }
    if (!response.ok) {
      throw new Error(data.detail || 'خطا در خروج از گروه')
    }
    invalidateChatManagerCache(getGroupDetailCacheKey(props.groupId))
    emit('left', props.groupId)
  } catch (error) {
    setError(error, 'خطا در خروج از گروه')
  } finally {
    isSaving.value = false
  }
}

watch(directoryQuery, (query) => {
  if (!props.show) return
  if (page.value !== 'select-members' && page.value !== 'add-members') return
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    void loadUsers(query)
  }, 220)
})

watch(() => [props.show, props.groupId] as const, ([show]) => {
  if (!show) {
    if (managerBackStateActive.value) {
      managerBackStateActive.value = false
      popBackState()
    }
    resetState()
    return
  }

  resetState()
  pushManagerBackState()
  if (isCreateMode.value) {
    void loadUsers()
    return
  }
  void loadGroupDetail()
}, { immediate: true })
</script>

<template>
  <Teleport to="body">
    <Transition name="group-manager-fade">
      <div v-if="show" class="group-manager-overlay" @click="requestClose()">
        <section class="group-manager-shell" @click.stop>
          <input ref="avatarInput" type="file" accept="image/*" class="hidden-avatar-input" @change="handleAvatarSelected" />
          <header class="manager-header">
            <button type="button" class="header-icon-btn" @click="handleBack()">
              <ChevronRight :size="22" />
            </button>
            <div class="header-copy">
              <h3>{{ pageTitle }}</h3>
              <span>{{ pageSubtitle }}</span>
            </div>
            <button type="button" class="header-icon-btn" @click="requestClose()">
              <X :size="20" />
            </button>
          </header>

          <main class="manager-body">
            <div v-if="errorMessage" class="flash-box error">{{ errorMessage }}</div>
            <div v-if="successMessage" class="flash-box success">{{ successMessage }}</div>

            <template v-if="isCreateMode && page === 'select-members'">
              <div class="search-shell">
                <input v-model="directoryQuery" type="text" class="search-input" placeholder="جستجو با نام، اکانت یا موبایل..." />
              </div>

              <div class="selection-banner">
                <span>{{ selectedCount.toLocaleString('fa-IR') }} عضو انتخاب شده</span>
                <button type="button" class="primary-chip" :disabled="!canContinueCreate" @click="setPage('details')">
                  ادامه
                </button>
              </div>

              <div v-if="isLoadingUsers" class="state-box">
                <Loader2 :size="18" class="spin" />
                <span>در حال دریافت کاربران...</span>
              </div>
              <div v-else-if="candidates.length === 0" class="state-box muted">کاربری برای نمایش پیدا نشد.</div>
              <div v-else class="telegram-list">
                <ChatUserListRow
                  v-for="user in candidates.filter(candidate => candidate.id !== currentUserId)"
                  :key="user.id"
                  tag="button"
                  :interactive="true"
                  :selected="selectedUserIds.has(user.id)"
                  :name="user.account_name"
                  :avatar-file-id="user.avatar_file_id || null"
                  @click="toggleCandidate(user.id)"
                >
                  <template #subtitle>
                    {{ user.full_name }} • <span dir="ltr">{{ user.mobile_number }}</span>
                  </template>
                  <template #trailing>
                    <div class="row-check" :class="{ active: selectedUserIds.has(user.id) }">
                      <Check v-if="selectedUserIds.has(user.id)" :size="16" />
                    </div>
                  </template>
                </ChatUserListRow>
              </div>
            </template>

            <template v-else-if="isCreateMode && page === 'details'">
              <section class="hero-card preview">
                <div class="hero-avatar">
                  <img v-if="groupAvatarUrl" :src="groupAvatarUrl" :alt="overviewTitle" class="hero-avatar-image" />
                  <template v-else>{{ getAvatarInitial(overviewTitle) }}</template>
                  <div v-if="avatarBusy" class="avatar-busy-overlay"><Loader2 :size="20" class="spin" /></div>
                </div>
                <div class="hero-title">{{ overviewTitle }}</div>
                <div class="hero-meta">{{ selectedCount.toLocaleString('fa-IR') }} عضو اولیه</div>
                <p class="hero-description">{{ overviewDescription }}</p>
                <div class="avatar-tool-row">
                  <button type="button" class="secondary-btn compact" :disabled="avatarBusy" @click="triggerAvatarPicker">
                    {{ avatarFileId ? 'تغییر عکس گروه' : 'افزودن عکس گروه' }}
                  </button>
                  <button v-if="avatarFileId" type="button" class="ghost-action danger" :disabled="avatarBusy" @click="clearAvatar">
                    حذف عکس
                  </button>
                </div>
              </section>

              <section class="editor-card">
                <label class="field-label" for="group-title">نام گروه</label>
                <input id="group-title" v-model="title" class="editor-input" type="text" maxlength="255" placeholder="مثلاً تیم فروش" />

                <label class="field-label" for="group-description">توضیحات گروه</label>
                <textarea id="group-description" v-model="description" class="editor-textarea" rows="4" maxlength="2000" placeholder="چند خط کوتاه درباره موضوع گروه"></textarea>
              </section>
            </template>

            <template v-else-if="!isCreateMode && page === 'overview'">
              <section v-if="isLoadingDetail" class="state-box">
                <Loader2 :size="18" class="spin" />
                <span>در حال دریافت اطلاعات گروه...</span>
              </section>

              <template v-else>
                <section class="hero-card">
                  <button
                    type="button"
                    class="hero-avatar"
                    :class="{ 'editable-avatar': canEditOverviewAvatar }"
                    :disabled="!canEditOverviewAvatar || avatarBusy"
                    @click="canEditOverviewAvatar ? triggerAvatarPicker() : undefined"
                  >
                    <img v-if="groupAvatarUrl" :src="groupAvatarUrl" :alt="overviewTitle" class="hero-avatar-image" />
                    <template v-else>{{ getAvatarInitial(overviewTitle) }}</template>
                    <div v-if="avatarBusy" class="avatar-busy-overlay"><Loader2 :size="20" class="spin" /></div>
                    <span v-else-if="canEditOverviewAvatar" class="avatar-edit-badge">ویرایش</span>
                  </button>
                  <div class="hero-title">{{ overviewTitle }}</div>
                  <div class="hero-meta">{{ (group?.member_count || 0).toLocaleString('fa-IR') }} عضو</div>
                  <p class="hero-description">{{ overviewDescription }}</p>
                  <div v-if="canEditOverviewAvatar" class="avatar-tool-row compact centered-overview-tools">
                    <button type="button" class="secondary-btn compact" :disabled="avatarBusy" @click="triggerAvatarPicker">
                      {{ avatarFileId ? 'تغییر عکس گروه' : 'افزودن عکس گروه' }}
                    </button>
                    <button v-if="avatarFileId" type="button" class="ghost-action danger" :disabled="avatarBusy" @click="clearAvatar">
                      حذف عکس
                    </button>
                  </div>
                </section>

                <div class="manager-role-strip">
                  <span>نقش شما</span>
                  <strong>{{ currentGroupRoleLabel }}</strong>
                </div>

                <section class="section-shell manager-action-group">
                  <div class="section-heading">اعضا و دسترسی‌ها</div>
                  <div class="telegram-list nav-list">
                    <button type="button" class="telegram-row nav" @click="setPage('members')">
                      <div class="row-icon soft"><UsersRound :size="18" /></div>
                      <div class="row-copy">
                        <div class="row-title">اعضای گروه</div>
                        <div class="row-subtitle">فهرست کامل اعضا و وضعیت نقش‌ها</div>
                      </div>
                      <div class="row-meta">{{ (group?.member_count || 0).toLocaleString('fa-IR') }}</div>
                      <ChevronLeft :size="18" class="row-chevron" />
                    </button>

                    <button v-if="isAdmin" type="button" class="telegram-row nav" @click="setPage('admins')">
                      <div class="row-icon amber"><Shield :size="18" /></div>
                      <div class="row-copy">
                        <div class="row-title">مدیریت ادمین‌ها</div>
                        <div class="row-subtitle">تعیین، تغییر و حذف دسترسی ادمین‌ها</div>
                      </div>
                      <div class="row-meta">{{ activeAdminCount.toLocaleString('fa-IR') }}</div>
                      <ChevronLeft :size="18" class="row-chevron" />
                    </button>

                    <button v-if="isAdmin" type="button" class="telegram-row nav" @click="setPage('add-members')">
                      <div class="row-icon blue"><UserPlus :size="18" /></div>
                      <div class="row-copy">
                        <div class="row-title">افزودن عضو</div>
                        <div class="row-subtitle">کاربران پروژه را به گروه دعوت کنید</div>
                      </div>
                      <ChevronLeft :size="18" class="row-chevron" />
                    </button>
                  </div>
                </section>

                <section v-if="isAdmin" class="section-shell manager-action-group">
                  <div class="section-heading">تنظیمات</div>
                  <div class="telegram-list nav-list">
                    <button type="button" class="telegram-row nav" @click="setPage('edit')">
                      <div class="row-icon muted"><PencilLine :size="18" /></div>
                      <div class="row-copy">
                        <div class="row-title">تنظیمات گروه</div>
                        <div class="row-subtitle">نام و توضیحات گروه را ویرایش کنید</div>
                      </div>
                      <ChevronLeft :size="18" class="row-chevron" />
                    </button>
                  </div>
                </section>

                <section class="section-shell manager-action-group danger-zone">
                  <div class="section-heading">خروج</div>
                  <div class="telegram-list nav-list">
                    <button type="button" class="telegram-row nav danger" :disabled="isSaving" @click="leaveGroup">
                      <div class="row-icon danger"><LogOut :size="18" /></div>
                      <div class="row-copy">
                        <div class="row-title">خروج از گروه</div>
                        <div class="row-subtitle">از این گفتگو خارج شوید</div>
                      </div>
                    </button>
                  </div>
                </section>
              </template>
            </template>

            <template v-else-if="page === 'members'">
              <div class="search-shell slim">
                <input v-model="memberQuery" type="text" class="search-input" placeholder="جستجو در اعضای گروه..." />
              </div>

              <div class="telegram-list">
                <ChatUserListRow
                  v-for="member in filteredMembers"
                  :key="member.user_id"
                  :name="getPrimaryUserName(member.account_name, member.full_name)"
                  :avatar-file-id="member.avatar_file_id || null"
                  :badges="getGroupMemberBadges(member)"
                >
                  <template #subtitle>
                    <span dir="ltr">{{ member.mobile_number }}</span>
                  </template>
                  <template #actions>
                    <button
                      type="button"
                      class="chat-user-row__action-btn"
                      @click.stop="openMemberProfile(member)"
                    >
                      پروفایل
                    </button>
                    <button
                      v-if="isAdmin && canRemove(member)"
                      type="button"
                      class="chat-user-row__action-btn chat-user-row__action-btn--danger"
                      :disabled="mutatingUserId === member.user_id"
                      @click.stop="removeMember(member)"
                    >
                      حذف
                    </button>
                  </template>
                </ChatUserListRow>
              </div>
            </template>

            <template v-else-if="page === 'admins'">
              <div class="search-shell slim">
                <input v-model="adminQuery" type="text" class="search-input" placeholder="جستجو در ادمین‌ها و اعضا..." />
              </div>

              <section class="section-shell">
                <div class="section-heading">ادمین‌های فعلی</div>
                <div class="telegram-list compact">
                  <ChatUserListRow
                    v-for="member in filteredAdmins"
                    :key="member.user_id"
                    :name="getPrimaryUserName(member.account_name, member.full_name)"
                    :avatar-file-id="member.avatar_file_id || null"
                    :badges="getGroupMemberBadges(member)"
                  >
                    <template #subtitle>
                      <span dir="ltr">{{ member.mobile_number }}</span>
                    </template>
                    <template #actions>
                      <button
                        type="button"
                        class="chat-user-row__action-btn"
                        @click.stop="openMemberProfile(member)"
                      >
                        پروفایل
                      </button>
                      <button
                        v-if="canDemote(member)"
                        type="button"
                        class="chat-user-row__action-btn"
                        :disabled="mutatingUserId === member.user_id"
                        @click.stop="demote(member)"
                      >
                        حذف ادمین
                      </button>
                    </template>
                  </ChatUserListRow>
                </div>
              </section>

              <section class="section-shell">
                <div class="section-heading">اعضای قابل ارتقا</div>
                <div v-if="promotableMembers.length === 0" class="state-box muted">عضوی برای ارتقا باقی نمانده است.</div>
                <div v-else class="telegram-list compact">
                  <ChatUserListRow
                    v-for="member in promotableMembers"
                    :key="member.user_id"
                    :name="getPrimaryUserName(member.account_name, member.full_name)"
                    :avatar-file-id="member.avatar_file_id || null"
                    :badges="getPromotableMemberBadges(member)"
                  >
                    <template #subtitle>
                      <span dir="ltr">{{ member.mobile_number }}</span>
                    </template>
                    <template #actions>
                      <button
                        type="button"
                        class="chat-user-row__action-btn"
                        @click.stop="openMemberProfile(member)"
                      >
                        پروفایل
                      </button>
                      <button
                        type="button"
                        class="chat-user-row__action-btn chat-user-row__action-btn--primary"
                        :disabled="mutatingUserId === member.user_id"
                        @click.stop="promote(member)"
                      >
                        ارتقا به ادمین
                      </button>
                    </template>
                  </ChatUserListRow>
                </div>
              </section>
            </template>

            <template v-else-if="page === 'add-members'">
              <div class="search-shell">
                <input v-model="directoryQuery" type="text" class="search-input" placeholder="جستجو با نام، اکانت یا موبایل..." />
              </div>

              <div class="selection-banner">
                <span>{{ selectedCount.toLocaleString('fa-IR') }} عضو انتخاب شده</span>
                <button type="button" class="primary-chip" :disabled="!canSubmitAddMembers || isSaving" @click="addSelectedMembers">
                  افزودن
                </button>
              </div>

              <div v-if="isLoadingUsers" class="state-box">
                <Loader2 :size="18" class="spin" />
                <span>در حال دریافت کاربران...</span>
              </div>
              <div v-else-if="availableCandidates.length === 0" class="state-box muted">کاربری برای افزودن باقی نمانده است.</div>
              <div v-else class="telegram-list">
                <ChatUserListRow
                  v-for="user in availableCandidates"
                  :key="user.id"
                  tag="button"
                  :interactive="true"
                  :selected="selectedUserIds.has(user.id)"
                  :name="getPrimaryUserName(user.account_name, user.full_name)"
                  :avatar-file-id="user.avatar_file_id || null"
                  @click="toggleCandidate(user.id)"
                >
                  <template #subtitle>
                    <span dir="ltr">{{ user.mobile_number }}</span>
                  </template>
                  <template #trailing>
                    <div class="row-check" :class="{ active: selectedUserIds.has(user.id) }">
                      <Check v-if="selectedUserIds.has(user.id)" :size="16" />
                    </div>
                  </template>
                </ChatUserListRow>
              </div>
            </template>

            <template v-else-if="page === 'edit'">
              <section class="editor-card">
                <div class="avatar-editor-block">
                  <div class="hero-avatar small-editor">
                    <img v-if="groupAvatarUrl" :src="groupAvatarUrl" :alt="overviewTitle" class="hero-avatar-image" />
                    <template v-else>{{ getAvatarInitial(overviewTitle) }}</template>
                    <div v-if="avatarBusy" class="avatar-busy-overlay"><Loader2 :size="20" class="spin" /></div>
                  </div>
                  <div class="avatar-tool-row compact">
                    <button type="button" class="secondary-btn compact" :disabled="avatarBusy" @click="triggerAvatarPicker">
                      {{ avatarFileId ? 'تغییر عکس گروه' : 'افزودن عکس گروه' }}
                    </button>
                    <button v-if="avatarFileId" type="button" class="ghost-action danger" :disabled="avatarBusy" @click="clearAvatar">
                      حذف عکس
                    </button>
                  </div>
                </div>

                <label class="field-label" for="group-edit-title">نام گروه</label>
                <input id="group-edit-title" v-model="title" class="editor-input" type="text" maxlength="255" placeholder="نام گروه" />

                <label class="field-label" for="group-edit-description">توضیحات گروه</label>
                <textarea id="group-edit-description" v-model="description" class="editor-textarea" rows="5" maxlength="2000" placeholder="توضیحات گروه برای اعضا"></textarea>

                <button type="button" class="primary-btn" :disabled="!canSaveDetails || isSaving" @click="updateGroupSettings">
                  <Loader2 v-if="isSaving" :size="18" class="spin" />
                  <Check v-else :size="18" />
                  <span>ذخیره تغییرات</span>
                </button>
              </section>
            </template>
          </main>

          <footer v-if="isCreateMode && page === 'details'" class="manager-footer">
            <button type="button" class="secondary-btn" @click="handleBack()">بازگشت به انتخاب اعضا</button>
            <button type="button" class="primary-btn" :disabled="!canSaveDetails || !canContinueCreate || isSaving" @click="createGroup">
              <Loader2 v-if="isSaving" :size="18" class="spin" />
              <UsersRound v-else :size="18" />
              <span>ساخت گروه</span>
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
  background: var(--messenger-overlay-medium, rgba(0, 0, 0, 0.34));
  backdrop-filter: blur(8px);
  display: flex;
  align-items: stretch;
  justify-content: center;
}

.group-manager-shell {
  width: min(100vw, 560px);
  height: 100vh;
  min-height: 0;
  background: var(--messenger-manager-shell-bg, linear-gradient(180deg, #f7fafc 0%, #edf3f8 100%));
  display: flex;
  flex-direction: column;
  direction: rtl;
  box-shadow: var(--messenger-shadow-panel, 0 18px 50px rgba(15, 23, 42, 0.12));
}

.manager-header,
.manager-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: var(--messenger-panel-glass-bg, rgba(255, 255, 255, 0.92));
  backdrop-filter: blur(16px);
  border-bottom: 1px solid rgba(148, 163, 184, 0.14);
}

.manager-footer {
  border-top: 1px solid rgba(148, 163, 184, 0.14);
  border-bottom: 0;
  justify-content: space-between;
}

.header-icon-btn {
  width: var(--messenger-touch-target, 44px);
  height: var(--messenger-touch-target, 44px);
  border: 0;
  border-radius: var(--messenger-radius-control, 8px);
  background: rgba(226, 232, 240, 0.72);
  color: #334155;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
}

.header-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.header-copy h3 {
  margin: 0;
  font-size: 1rem;
  font-weight: 900;
  color: #0f172a;
}

.header-copy span {
  color: #64748b;
  font-size: 0.78rem;
}

.manager-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  touch-action: pan-y;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  padding: 18px 16px 28px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.flash-box,
.state-box,
.selection-banner,
.manager-role-strip,
.info-strip {
  border-radius: 18px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.flash-box.error {
  background: rgba(254, 242, 242, 0.95);
  color: #b91c1c;
  border: 1px solid rgba(248, 113, 113, 0.18);
}

.flash-box.success {
  background: rgba(236, 253, 245, 0.94);
  color: #047857;
  border: 1px solid rgba(52, 211, 153, 0.18);
}

.state-box {
  justify-content: center;
  min-height: 58px;
  background: rgba(255, 255, 255, 0.84);
  border: 1px solid rgba(148, 163, 184, 0.12);
  color: #475569;
}

.state-box.muted {
  color: #64748b;
}

.search-shell {
  position: sticky;
  top: 0;
  z-index: 2;
}

.search-shell.slim {
  position: static;
}

.search-input,
.editor-input,
.editor-textarea {
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: var(--messenger-radius-panel, 18px);
  background: var(--messenger-panel-glass-bg, rgba(255, 255, 255, 0.92));
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.05);
  color: #0f172a;
  font: inherit;
  font-size: 0.98rem;
  outline: none;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
}

.search-input,
.editor-input {
  min-height: 56px;
  padding: 0 18px;
}

.editor-textarea {
  min-height: 132px;
  resize: vertical;
  padding: 14px 16px;
  line-height: 1.8;
}

.search-input:focus,
.editor-input:focus,
.editor-textarea:focus {
  border-color: #3390ec;
  box-shadow: 0 0 0 4px rgba(51, 144, 236, 0.12);
}

.selection-banner {
  justify-content: space-between;
  background: linear-gradient(135deg, rgba(51, 144, 236, 0.16), rgba(14, 165, 233, 0.08));
  border: 1px solid rgba(51, 144, 236, 0.16);
  color: #0f172a;
  font-weight: 800;
}

.manager-role-strip {
  justify-content: space-between;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(148, 163, 184, 0.14);
  color: #64748b;
  font-size: 0.82rem;
  font-weight: 800;
}

.manager-role-strip strong {
  color: #0f172a;
  font-size: 0.86rem;
}

.primary-chip,
.primary-btn,
.secondary-btn,
.ghost-action {
  border: 0;
  border-radius: var(--messenger-radius-panel, 18px);
  font: inherit;
  font-weight: 800;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.primary-chip {
  min-height: 38px;
  padding: 0 16px;
  background: #3390ec;
  color: #fff;
}

.primary-btn,
.secondary-btn {
  min-height: 52px;
  padding: 0 18px;
}

.primary-btn {
  background: #3390ec;
  color: #fff;
  box-shadow: 0 12px 28px rgba(51, 144, 236, 0.24);
}

.secondary-btn {
  background: rgba(226, 232, 240, 0.86);
  color: #0f172a;
}

.primary-chip:disabled,
.primary-btn:disabled,
.secondary-btn:disabled,
.ghost-action:disabled,
.header-icon-btn:disabled {
  opacity: 0.55;
  cursor: default;
}

.hero-card,
.editor-card,
.section-shell {
  border-radius: var(--messenger-radius-sheet, 28px);
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(148, 163, 184, 0.14);
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
}

.hero-card {
  padding: 26px 20px 22px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 8px;
}

.hero-card.preview {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92), rgba(236, 245, 255, 0.94));
}

.hero-avatar,
.row-avatar {
  background: linear-gradient(135deg, #3390ec, #0ea5e9 58%, #22c55e 100%);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 900;
  flex-shrink: 0;
}

.hero-avatar {
  position: relative;
  overflow: hidden;
  width: 86px;
  height: 86px;
  border: 0;
  padding: 0;
  border-radius: 50%;
  font-size: 2rem;
}

.hero-avatar.editable-avatar {
  cursor: pointer;
}

.avatar-edit-badge {
  position: absolute;
  inset-inline-start: 6px;
  bottom: 6px;
  min-height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.82);
  color: #fff;
  font-size: 0.64rem;
  font-weight: 900;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.row-avatar {
  overflow: hidden;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  font-size: 1rem;
}

.hero-avatar-image,
.row-avatar-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.hidden-avatar-input {
  display: none;
}

.avatar-busy-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.34);
  color: #fff;
}

.avatar-tool-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  flex-wrap: wrap;
}

.avatar-tool-row.compact {
  justify-content: flex-start;
}

.avatar-tool-row.centered-overview-tools {
  justify-content: center;
}

.avatar-editor-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-bottom: 6px;
}

.hero-avatar.small-editor {
  width: 74px;
  height: 74px;
  font-size: 1.65rem;
}

.hero-title {
  font-size: 1.18rem;
  font-weight: 900;
  color: #0f172a;
}

.hero-meta,
.hero-description {
  color: #64748b;
}

.hero-description {
  margin: 0;
  max-width: 36ch;
  line-height: 1.85;
}

.telegram-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.telegram-list.compact {
  gap: 8px;
}

.telegram-row {
  width: 100%;
  border: 0;
  border-radius: var(--messenger-radius-panel, 18px);
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.12);
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: right;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.05);
}

.telegram-row.selectable,
.telegram-row.nav {
  cursor: pointer;
}

.telegram-row.selectable.selected {
  border-color: rgba(51, 144, 236, 0.28);
  background: rgba(240, 248, 255, 0.96);
}

.row-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.row-title {
  font-size: 0.96rem;
  font-weight: 900;
  color: #0f172a;
}

.row-title.with-badges {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.row-subtitle {
  font-size: 0.8rem;
  color: #64748b;
  line-height: 1.7;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-check {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid #cbd5e1;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.row-check.active {
  border-color: #3390ec;
  background: #3390ec;
}

.row-icon {
  width: 42px;
  height: 42px;
  border-radius: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.row-icon.soft {
  background: rgba(226, 232, 240, 0.82);
  color: #334155;
}

.row-icon.amber {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.row-icon.blue {
  background: rgba(51, 144, 236, 0.14);
  color: #0369a1;
}

.row-icon.muted {
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
}

.row-icon.danger {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
}

.row-meta {
  color: #64748b;
  font-size: 0.82rem;
  font-weight: 800;
}

.row-chevron {
  color: #94a3b8;
  flex-shrink: 0;
}

.editor-card,
.section-shell {
  padding: 18px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.manager-action-group {
  padding: 14px;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
}

.manager-action-group.danger-zone {
  border-color: rgba(239, 68, 68, 0.14);
}

.field-label,
.section-heading {
  font-size: 0.84rem;
  font-weight: 800;
  color: #475569;
}

.info-strip {
  background: rgba(241, 245, 249, 0.92);
  color: #475569;
  font-size: 0.82rem;
  line-height: 1.8;
}

.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 900;
}

.badge.admin {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.badge.member {
  background: rgba(148, 163, 184, 0.16);
  color: #475569;
}

.badge.creator {
  background: rgba(34, 197, 94, 0.12);
  color: #15803d;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.ghost-action {
  min-height: 36px;
  padding: 0 12px;
  background: rgba(241, 245, 249, 0.96);
  color: #334155;
}

.ghost-action.primary {
  background: rgba(51, 144, 236, 0.12);
  color: #0369a1;
}

.ghost-action.danger,
.telegram-row.nav.danger .row-title,
.telegram-row.nav.danger .row-subtitle {
  color: #b91c1c;
}

.guard-text {
  color: #94a3b8;
  font-size: 0.74rem;
  font-weight: 700;
  line-height: 1.6;
  max-width: 18ch;
}

.spin {
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.group-manager-fade-enter-active,
.group-manager-fade-leave-active {
  transition: opacity 0.22s ease;
}

.group-manager-fade-enter-from,
.group-manager-fade-leave-to {
  opacity: 0;
}

@media (min-width: 700px) {
  .group-manager-overlay {
    padding: 24px;
    align-items: center;
  }

  .group-manager-shell {
    height: min(94vh, 920px);
    border-radius: var(--messenger-radius-sheet, 28px);
    overflow: hidden;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spin,
  .group-manager-fade-enter-active,
  .group-manager-fade-leave-active {
    animation: none;
    transition: none;
  }
}

@media (max-width: 520px) {
  .manager-footer {
    flex-direction: column;
    align-items: stretch;
  }

  .primary-btn,
  .secondary-btn {
    width: 100%;
  }

  .row-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .telegram-row.member-row {
    flex-wrap: wrap;
  }
}
</style>
