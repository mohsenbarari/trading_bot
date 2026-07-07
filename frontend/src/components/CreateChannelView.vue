<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import ChatUserListRow from './chat/ChatUserListRow.vue'
import HelpPopover from './HelpPopover.vue'
import {
  AppActionCard,
  AppButton,
  AppCheckbox,
  AppEmptyState,
  AppFormField,
  AppIconButton,
  AppInput,
  AppLoadingState,
  AppSectionCard,
  AppStatusBadge,
  AppTextarea,
} from './ui'
import { apiFetch, apiFetchJson } from '../utils/auth'
import { getAccountantOwnerBadge, getChatRoleBadge } from '../utils/chatRoleBadges'
import type { ChatUserListRowBadge } from './chat/ChatUserListRow.vue'
import type { ChatRoleKind } from '../types/chat'
import { discardBackState, popBackState, pushBackState } from '../composables/useBackButton'
import { buildChatFileUrl, getAvatarInitial, uploadAvatarImage } from '../utils/chatFiles'
import {
  getChannelCandidatesCacheKey,
  getChannelListCacheKey,
  getChannelMembersCacheKey,
  invalidateChatManagerCache,
  readChatManagerCache,
  writeChatManagerCache,
} from '../services/chat/chatManagerCache'
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

const router = useRouter()

type ChannelRoom = {
  id: number
  type: 'channel'
  title: string
  description: string | null
  avatar_file_id?: string | null
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
  avatar_file_id?: string | null
  is_already_member: boolean
  chat_role_kind?: ChatRoleKind | null
  chat_role_label?: string | null
  chat_accountant_owner_name?: string | null
  chat_accountant_owner_label?: string | null
  customer_management_name?: string | null
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
  avatar_file_id?: string | null
  role: 'admin' | 'member'
  joined_at: string
  is_channel_creator: boolean
  chat_role_kind?: ChatRoleKind | null
  chat_role_label?: string | null
  chat_accountant_owner_name?: string | null
  chat_accountant_owner_label?: string | null
  customer_management_name?: string | null
}

type ChannelMemberMutationResponse = {
  chat_id: number
  user_id: number
  role: 'admin' | 'member' | null
  removed: boolean
  member_count: number
  left?: boolean
  unchanged?: boolean
}

type ChannelManagerPage = 'home' | 'create' | 'overview' | 'members' | 'admins' | 'add-members' | 'edit'

const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
  currentUserId?: number
  showCloseButton?: boolean
  initialChannelId?: number | null
}>()

const emit = defineEmits<{
  (e: 'refresh-conversations', channel?: ChannelRoom | null): void
  (e: 'open-channel', payload: { chatId: number; title: string }): void
  (e: 'left', chatId: number): void
  (e: 'close'): void
  (e: 'open-public-profile', payload: { id: number; account_name: string }): void
}>()

const page = ref<ChannelManagerPage>('home')
const title = ref('')
const description = ref('')
const candidateQuery = ref('')
const memberQuery = ref('')
const adminQuery = ref('')
const activeChannel = ref<ChannelRoom | null>(null)
const existingChannels = ref<ChannelRoom[]>([])
const members = ref<ChannelMember[]>([])
const candidates = ref<ChannelInviteCandidate[]>([])
const activeTotal = ref(0)
const isLoadingChannels = ref(false)
const isLoadingMembers = ref(false)
const isLoadingCandidates = ref(false)
const isSaving = ref(false)
const isSubmittingMembers = ref(false)
const mutatingUserId = ref<number | null>(null)
const errorMessage = ref('')
const successMessage = ref('')
const selectAllActiveUsers = ref(false)
const selectedUserIds = ref<Set<number>>(new Set())
const pageHistory = ref<ChannelManagerPage[]>([])
const managerBackStateActive = ref(false)
const avatarFileId = ref<string | null>(null)
const avatarBusy = ref(false)
const avatarInput = ref<HTMLInputElement | null>(null)
let searchTimer: ReturnType<typeof setTimeout> | null = null

const isMembershipManagementLocked = computed(() => Boolean(activeChannel.value?.is_mandatory || activeChannel.value?.is_system))
const selectedCount = computed(() => (selectAllActiveUsers.value ? activeTotal.value : selectedUserIds.value.size))
const canSaveDetails = computed(() => title.value.trim().length > 0)
const currentUserMembership = computed(() => {
  if (typeof props.currentUserId !== 'number') return null
  return members.value.find((member) => member.user_id === props.currentUserId) ?? null
})
const canOpenCurrentChannelInMessenger = computed(() => {
  return typeof props.currentUserId === 'number' && Boolean(activeChannel.value && currentUserMembership.value)
})
const currentUserCanPostInCurrentChannel = computed(() => currentUserMembership.value?.role === 'admin')
const activeAdminCount = computed(() => members.value.filter((member) => member.role === 'admin').length)
const channelAvatarUrl = computed(() => buildChatFileUrl(avatarFileId.value, props.apiBaseUrl))
const isCurrentUserChannelCreator = computed(() => Boolean(currentUserMembership.value?.is_channel_creator))
const canEditOverviewAvatar = computed(() => Boolean(activeChannel.value && currentUserMembership.value?.role === 'admin' && !isMembershipManagementLocked.value))
const currentChannelExitLabel = computed(() => isCurrentUserChannelCreator.value ? 'حذف کانال' : 'خروج از کانال')
const currentChannelExitSubtitle = computed(() => {
  if (isCurrentUserChannelCreator.value) {
    return 'با این کار کانال برای همه اعضا حذف می‌شود.'
  }
  return 'از این کانال خارج شوید'
})
const currentChannelRoleLabel = computed(() => {
  if (isMembershipManagementLocked.value) return 'کانال سیستمی'
  if (!currentUserMembership.value) return 'عضو نیستید'
  if (isCurrentUserChannelCreator.value) return 'سازنده کانال'
  if (currentUserMembership.value.role === 'admin') return 'ادمین کانال'
  return 'عضو کانال'
})
const currentChannelKindTone = computed(() => (
  activeChannel.value?.is_mandatory ? 'warning' : activeChannel.value?.is_system ? 'info' : 'neutral'
))

function normalizeSearch(value: string) {
  return value.trim().toLowerCase()
}

function compareMemberOrder(left: ChannelMember, right: ChannelMember) {
  if (left.is_channel_creator !== right.is_channel_creator) return left.is_channel_creator ? -1 : 1
  if (left.role !== right.role) return left.role === 'admin' ? -1 : 1
  return getChannelUserDisplayName(left).localeCompare(getChannelUserDisplayName(right), 'fa')
}

const filteredMembers = computed(() => {
  const query = normalizeSearch(memberQuery.value)
  return members.value
    .slice()
    .sort(compareMemberOrder)
    .filter((member) => {
      if (!query) return true
      const haystack = [member.customer_management_name, member.account_name, member.full_name, member.mobile_number].join(' ').toLowerCase()
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
      const haystack = [member.customer_management_name, member.account_name, member.full_name, member.mobile_number].join(' ').toLowerCase()
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
      const haystack = [member.customer_management_name, member.account_name, member.full_name, member.mobile_number].join(' ').toLowerCase()
      return haystack.includes(query)
    })
})

const pageTitle = computed(() => {
  switch (page.value) {
    case 'create':
      return 'ساخت کانال'
    case 'overview':
      return 'مدیریت کانال'
    case 'members':
      return 'اعضای کانال'
    case 'admins':
      return 'مدیریت ادمین‌ها'
    case 'add-members':
      return 'افزودن عضو'
    case 'edit':
      return 'تنظیمات کانال'
    default:
      return 'کانال‌ها'
  }
})

const pageSubtitle = computed(() => {
  switch (page.value) {
    case 'create':
      return 'کانال را بسازید و بعد اعضا و ادمین‌ها را مدیریت کنید.'
    case 'overview':
      return activeChannel.value
        ? `${activeChannel.value.member_count.toLocaleString('fa-IR')} عضو فعال`
        : 'یک کانال را برای مدیریت انتخاب کنید.'
    case 'members':
      return `${filteredMembers.value.length.toLocaleString('fa-IR')} عضو نمایش داده می‌شود`
    case 'admins':
      return `${filteredAdmins.value.length.toLocaleString('fa-IR')} ادمین فعال`
    case 'add-members':
      return 'کاربران پروژه را به کانال اضافه کنید.'
    case 'edit':
      return 'نام و توضیحات کانال را ویرایش کنید.'
    default:
      return `${existingChannels.value.length.toLocaleString('fa-IR')} کانال قابل مدیریت`
  }
})

const canGoBack = computed(() => page.value !== 'home')

function clearFlashMessages() {
  errorMessage.value = ''
  successMessage.value = ''
}

function getUserAvatarUrl(fileId?: string | null) {
  return buildChatFileUrl(fileId ?? null)
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
    const uploaded = await uploadAvatarImage(file, props.apiBaseUrl)
    avatarFileId.value = uploaded.file_id
    if (activeChannel.value && page.value === 'overview') {
      await persistActiveChannelAvatar(uploaded.file_id)
    }
  } catch (error) {
    avatarFileId.value = previousAvatarFileId
    setError(error, 'آپلود آواتار کانال ناموفق بود')
  } finally {
    avatarBusy.value = false
    if (input) input.value = ''
  }
}

async function clearAvatar() {
  if (avatarBusy.value) return
  const previousAvatarFileId = avatarFileId.value
  avatarFileId.value = null
  if (!activeChannel.value || page.value !== 'overview') return

  avatarBusy.value = true
  clearFlashMessages()
  try {
    await persistActiveChannelAvatar(null)
  } catch (error) {
    avatarFileId.value = previousAvatarFileId
    setError(error, 'حذف آواتار کانال ناموفق بود')
  } finally {
    avatarBusy.value = false
  }
}

function getPrimaryUserName(accountName: string, fullName?: string | null) {
  const normalizedFullName = (fullName || '').trim()
  return normalizedFullName || accountName
}

function getChannelUserDisplayName(user: Pick<ChannelMember | ChannelInviteCandidate, 'account_name' | 'full_name' | 'customer_management_name'>) {
  const customerName = (user.customer_management_name || '').trim()
  if (customerName) return customerName
  return getPrimaryUserName(user.account_name, user.full_name)
}

function openMemberProfile(member: { user_id: number; account_name: string }) {
  const normalizedId = Number(member.user_id)
  if (!Number.isInteger(normalizedId) || normalizedId <= 0) {
    return
  }

  const target = {
    name: 'public-profile',
    params: { id: String(normalizedId) },
    query: member.account_name ? { account_name: member.account_name } : undefined,
  } as const

  const currentFullPath = router.currentRoute.value.fullPath
  const resolvedTarget = router.resolve(target)

  void router.push(target)

  window.setTimeout(() => {
    if (router.currentRoute.value.fullPath !== currentFullPath) {
      return
    }
    if (!resolvedTarget.href) {
      return
    }
    window.location.assign(resolvedTarget.href)
  }, 200)
}

function applyPage(nextPage: ChannelManagerPage) {
  page.value = nextPage
}

function setPage(nextPage: ChannelManagerPage, options: { recordHistory?: boolean } = {}) {
  clearFlashMessages()
  if (nextPage === page.value) return
  if (options.recordHistory !== false) {
    pageHistory.value.push(page.value)
  }
  applyPage(nextPage)
}

function setPageDirect(nextPage: ChannelManagerPage) {
  clearFlashMessages()
  applyPage(nextPage)
  while (pageHistory.value.length > 0 && pageHistory.value[pageHistory.value.length - 1] === nextPage) {
    pageHistory.value.pop()
  }
}

function requestClose(fromBack = false) {
  if (!props.showCloseButton) return
  if (!fromBack && managerBackStateActive.value) {
    managerBackStateActive.value = false
    popBackState()
  }
  emit('close')
}

function pushManagerBackState() {
  if (!props.showCloseButton || managerBackStateActive.value) return
  managerBackStateActive.value = true
  pushBackState(() => {
    managerBackStateActive.value = false
    const stillOpen = handleManagerBack(true)
    if (stillOpen && props.showCloseButton) {
      pushManagerBackState()
    }
  })
}

function discardManagerBackState() {
  if (!managerBackStateActive.value) return
  managerBackStateActive.value = false
  discardBackState()
}

function setError(error: unknown, fallback: string) {
  errorMessage.value = error instanceof Error ? error.message : fallback
}

function resetSelection() {
  selectAllActiveUsers.value = false
  selectedUserIds.value = new Set()
  candidateQuery.value = ''
}

function resetCreateForm() {
  title.value = ''
  description.value = ''
  avatarFileId.value = null
}

function syncEditorWithActiveChannel(channel: ChannelRoom | null) {
  title.value = channel?.title ?? ''
  description.value = channel?.description ?? ''
  avatarFileId.value = channel?.avatar_file_id ?? null
}

function getChannelKindLabel(channel: ChannelRoom) {
  if (channel.is_mandatory) return 'اجباری'
  if (channel.is_system) return 'سیستمی'
  return 'اختیاری'
}

function getChannelMemberBadges(member: ChannelMember): ChatUserListRowBadge[] {
  const badges: ChatUserListRowBadge[] = []
  if (member.is_channel_creator) {
    badges.push({ label: 'owner', tone: 'creator' as const })
  } else {
    badges.push({ label: member.role === 'admin' ? 'admin' : 'member', tone: member.role === 'admin' ? 'admin' : 'member' as const })
  }
  const roleBadge = getChatRoleBadge(member)
  if (roleBadge) badges.push(roleBadge)
  const ownerBadge = getAccountantOwnerBadge(member)
  if (ownerBadge) badges.push(ownerBadge)
  return badges
}

function getRoleOnlyBadges(user: ChannelInviteCandidate): ChatUserListRowBadge[] {
  const badges: ChatUserListRowBadge[] = []
  const roleBadge = getChatRoleBadge(user)
  if (roleBadge) badges.push(roleBadge)
  const ownerBadge = getAccountantOwnerBadge(user)
  if (ownerBadge) badges.push(ownerBadge)
  return badges
}

function getPromotableMemberBadges(member: ChannelMember): ChatUserListRowBadge[] {
  return getChannelMemberBadges(member)
}

function canDemoteMember(member: ChannelMember) {
  if (typeof props.currentUserId === 'number' && member.user_id === props.currentUserId) return false
  if (member.role !== 'admin') return false
  if (member.is_channel_creator) return false
  return activeAdminCount.value > 1
}

function canRemoveMember(member: ChannelMember) {
  if (typeof props.currentUserId === 'number' && member.user_id === props.currentUserId) return false
  if (member.is_channel_creator) return false
  if (member.role === 'admin') return activeAdminCount.value > 1
  return true
}

function getMemberGuardReason(member: ChannelMember) {
  if (member.is_channel_creator) return 'سازنده کانال باید عضو و ادمین باقی بماند.'
  if (member.role === 'admin' && activeAdminCount.value <= 1) return 'کانال باید حداقل یک ادمین فعال داشته باشد.'
  return ''
}

function upsertExistingChannel(channel: ChannelRoom) {
  const next = existingChannels.value.filter((item) => item.id !== channel.id)
  next.unshift(channel)
  existingChannels.value = next
}

function handleManagerBack(fromBack = false) {
  clearFlashMessages()
  const previousPage = pageHistory.value.pop()
  if (previousPage) {
    applyPage(previousPage)
    if (previousPage === 'home') {
      activeChannel.value = null
      resetSelection()
    }
    return true
  }
  requestClose(fromBack)
  return false
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

function openCreatePage() {
  clearFlashMessages()
  activeChannel.value = null
  resetSelection()
  resetCreateForm()
  setPage('create')
}

function openChannel(channel: ChannelRoom, options: { recordHistory?: boolean } = {}) {
  clearFlashMessages()
  activeChannel.value = channel
  syncEditorWithActiveChannel(channel)
  resetSelection()
  setPage('overview', options)
  void loadMembers()
}

async function openChannelById(channelId: number) {
  if (!existingChannels.value.some((channel) => channel.id === channelId)) {
    await loadExistingChannels()
  }

  const channel = existingChannels.value.find((item) => item.id === channelId)
  if (!channel) return

  openChannel(channel, { recordHistory: false })
}

async function loadExistingChannels() {
  const cacheKey = getChannelListCacheKey()
  const cached = readChatManagerCache<ChannelRoom[]>(cacheKey)
  if (cached) {
    existingChannels.value = cached
    return
  }

  isLoadingChannels.value = true
  try {
    const data = await apiFetchJson('/api/chat/channels') as ChannelRoom[]
    existingChannels.value = Array.isArray(data) ? data : []
    writeChatManagerCache(cacheKey, existingChannels.value)
  } catch (error) {
    setError(error, 'خطا در دریافت فهرست کانال‌ها')
  } finally {
    isLoadingChannels.value = false
  }
}

async function loadMembers() {
  if (!activeChannel.value) return
  const cacheKey = getChannelMembersCacheKey(activeChannel.value.id)
  const cached = readChatManagerCache<ChannelMember[]>(cacheKey)
  if (cached) {
    members.value = cached
    return
  }

  isLoadingMembers.value = true
  try {
    const data = await apiFetchJson(`/api/chat/channels/${activeChannel.value.id}/members`) as ChannelMember[]
    members.value = Array.isArray(data) ? data : []
    writeChatManagerCache(cacheKey, members.value)
  } catch (error) {
    setError(error, 'خطا در دریافت اعضای کانال')
  } finally {
    isLoadingMembers.value = false
  }
}

async function loadCandidates(query = '') {
  if (!activeChannel.value || isMembershipManagementLocked.value) {
    candidates.value = []
    activeTotal.value = 0
    return
  }
  const cacheKey = getChannelCandidatesCacheKey(activeChannel.value.id, query)
  const cached = readChatManagerCache<ChannelInviteCandidateResponse>(cacheKey)
  if (cached) {
    candidates.value = Array.isArray(cached.items) ? cached.items : []
    activeTotal.value = Number(cached.active_total || 0)
    return
  }

  isLoadingCandidates.value = true
  try {
    const params = new URLSearchParams({
      limit: '100',
      exclude_chat_id: String(activeChannel.value.id),
    })
    const trimmed = query.trim()
    if (trimmed) params.set('q', trimmed)
    const data = await apiFetchJson(`/api/chat/channels/invite-candidates?${params.toString()}`) as ChannelInviteCandidateResponse
    candidates.value = Array.isArray(data.items) ? data.items : []
    activeTotal.value = Number(data.active_total || 0)
    writeChatManagerCache(cacheKey, {
      ...data,
      items: candidates.value,
      active_total: activeTotal.value,
    })
  } catch (error) {
    setError(error, 'خطا در دریافت کاربران فعال')
  } finally {
    isLoadingCandidates.value = false
  }
}

async function createChannel() {
  if (!canSaveDetails.value) return
  isSaving.value = true
  clearFlashMessages()
  try {
    const response = await apiFetch('/api/chat/channels', {
      method: 'POST',
      body: JSON.stringify({
        title: title.value.trim(),
        description: description.value.trim() || undefined,
        avatar_file_id: avatarFileId.value || null,
      }),
    })
    const data = await response.json() as ChannelCreateResponse | { detail?: string }
    if (!response.ok || !(data as ChannelCreateResponse).channel) {
      throw new Error((data as { detail?: string }).detail || 'خطا در ساخت کانال')
    }
    activeChannel.value = (data as ChannelCreateResponse).channel
    invalidateChatManagerCache(getChannelListCacheKey())
    upsertExistingChannel(activeChannel.value)
    syncEditorWithActiveChannel(activeChannel.value)
    emit('refresh-conversations', activeChannel.value)
    successMessage.value = 'کانال ساخته شد. حالا اعضا و ادمین‌ها را مدیریت کنید.'
    pageHistory.value = isMembershipManagementLocked.value ? ['home'] : ['home', 'overview']
    applyPage(isMembershipManagementLocked.value ? 'overview' : 'add-members')
    resetSelection()
    await Promise.all([loadMembers(), loadCandidates()])
  } catch (error) {
    setError(error, 'خطا در ساخت کانال')
  } finally {
    isSaving.value = false
  }
}

async function updateChannelDetails() {
  if (!activeChannel.value || !canSaveDetails.value) return
  isSaving.value = true
  clearFlashMessages()
  try {
    const response = await apiFetch(`/api/chat/channels/${activeChannel.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        title: title.value.trim(),
        description: description.value.trim() || undefined,
        avatar_file_id: avatarFileId.value || null,
      }),
    })
    const data = await response.json() as ChannelRoom | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در ذخیره تنظیمات کانال')
    }
    activeChannel.value = data as ChannelRoom
    invalidateChatManagerCache(getChannelListCacheKey())
    avatarFileId.value = activeChannel.value.avatar_file_id || null
    upsertExistingChannel(activeChannel.value)
    emit('refresh-conversations', activeChannel.value)
    successMessage.value = 'تنظیمات کانال ذخیره شد.'
    setPageDirect('overview')
  } catch (error) {
    setError(error, 'خطا در ذخیره تنظیمات کانال')
  } finally {
    isSaving.value = false
  }
}

async function persistActiveChannelAvatar(nextAvatarFileId: string | null) {
  if (!activeChannel.value || isMembershipManagementLocked.value || currentUserMembership.value?.role !== 'admin') return

  const response = await apiFetch(`/api/chat/channels/${activeChannel.value.id}`, {
    method: 'PATCH',
    body: JSON.stringify({
      title: activeChannel.value.title,
      description: activeChannel.value.description || undefined,
      avatar_file_id: nextAvatarFileId,
    }),
  })
  const data = await response.json() as ChannelRoom | { detail?: string }
  if (!response.ok) {
    throw new Error((data as { detail?: string }).detail || 'خطا در ذخیره آواتار کانال')
  }

  activeChannel.value = data as ChannelRoom
  invalidateChatManagerCache(getChannelListCacheKey())
  avatarFileId.value = activeChannel.value.avatar_file_id || null
  upsertExistingChannel(activeChannel.value)
  emit('refresh-conversations', activeChannel.value)
  successMessage.value = nextAvatarFileId ? 'عکس کانال ذخیره شد.' : 'عکس کانال حذف شد.'
}

async function submitMembers() {
  if (!activeChannel.value || isMembershipManagementLocked.value || selectedCount.value === 0) return
  isSubmittingMembers.value = true
  clearFlashMessages()
  try {
    const payload = selectAllActiveUsers.value
      ? { select_all_active_users: true }
      : { user_ids: Array.from(selectedUserIds.value) }
    const response = await apiFetch(`/api/chat/channels/${activeChannel.value.id}/members/bulk`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const data = await response.json() as ChannelBulkMemberAddResponse | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در افزودن اعضا')
    }
    const summary = data as ChannelBulkMemberAddResponse
    invalidateChatManagerCache(getChannelMembersCacheKey(activeChannel.value.id))
    invalidateChatManagerCache(getChannelCandidatesCacheKey(activeChannel.value.id, ''))
    invalidateChatManagerCache(`channel:${activeChannel.value.id}:candidates:`)
    activeChannel.value = { ...activeChannel.value, member_count: summary.member_count }
    upsertExistingChannel(activeChannel.value)
    emit('refresh-conversations', activeChannel.value)
    successMessage.value = 'اعضای انتخاب‌شده به کانال اضافه شدند.'
    resetSelection()
    await Promise.all([loadMembers(), loadCandidates()])
    setPageDirect('members')
  } catch (error) {
    setError(error, 'خطا در افزودن اعضا')
  } finally {
    isSubmittingMembers.value = false
  }
}

async function mutateMember(member: ChannelMember, payload: { role?: 'admin' | 'member'; remove_member?: boolean }, successText: string) {
  if (!activeChannel.value || isMembershipManagementLocked.value) return
  mutatingUserId.value = member.user_id
  clearFlashMessages()
  try {
    const response = await apiFetch(`/api/chat/channels/${activeChannel.value.id}/members/${member.user_id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    const data = await response.json() as ChannelMemberMutationResponse | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در تغییر عضو کانال')
    }
    const summary = data as ChannelMemberMutationResponse
    invalidateChatManagerCache(getChannelMembersCacheKey(activeChannel.value.id))
    invalidateChatManagerCache(`channel:${activeChannel.value.id}:candidates:`)
    activeChannel.value = { ...activeChannel.value, member_count: summary.member_count }
    upsertExistingChannel(activeChannel.value)
    emit('refresh-conversations', activeChannel.value)
    successMessage.value = successText
    await Promise.all([loadMembers(), loadCandidates(candidateQuery.value)])
  } catch (error) {
    setError(error, 'خطا در تغییر عضو کانال')
  } finally {
    mutatingUserId.value = null
  }
}

async function unfollowCurrentChannel() {
  if (!activeChannel.value || isMembershipManagementLocked.value) return
  isSaving.value = true
  clearFlashMessages()
  const shouldDeleteChannel = isCurrentUserChannelCreator.value
  const activeChannelId = activeChannel.value.id
  try {
    const response = await apiFetch(`/api/chat/channels/${activeChannelId}/unfollow`, { method: 'POST' })
    const data = await response.json().catch(() => ({})) as ChannelMemberMutationResponse | { detail?: string }
    if (!response.ok) {
      throw new Error((data as { detail?: string }).detail || 'خطا در ترک کانال')
    }
    invalidateChatManagerCache(getChannelListCacheKey())
    invalidateChatManagerCache(getChannelMembersCacheKey(activeChannelId))
    invalidateChatManagerCache(`channel:${activeChannelId}:candidates:`)
    emit('left', activeChannelId)
    if (props.showCloseButton) {
      return
    }
    activeChannel.value = null
    members.value = []
    pageHistory.value = []
    applyPage('home')
    successMessage.value = shouldDeleteChannel ? 'کانال حذف شد.' : 'از کانال خارج شدید.'
    await loadExistingChannels()
  } catch (error) {
    setError(error, shouldDeleteChannel ? 'خطا در حذف کانال' : 'خطا در ترک کانال')
  } finally {
    isSaving.value = false
  }
}

async function promoteMember(member: ChannelMember) {
  await mutateMember(member, { role: 'admin' }, `${getChannelUserDisplayName(member)} به ادمین کانال تبدیل شد.`)
}

async function demoteMember(member: ChannelMember) {
  await mutateMember(member, { role: 'member' }, `نقش ادمینی ${getChannelUserDisplayName(member)} برداشته شد.`)
}

async function removeMember(member: ChannelMember) {
  await mutateMember(member, { remove_member: true }, `${getChannelUserDisplayName(member)} از کانال حذف شد.`)
}

function openCurrentChannelInMessenger() {
  if (!activeChannel.value) return
  discardManagerBackState()
  emit('open-channel', {
    chatId: activeChannel.value.id,
    title: activeChannel.value.title,
  })
}

watch(candidateQuery, (value) => {
  if (page.value !== 'add-members' || !activeChannel.value) return
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    void loadCandidates(value)
  }, 220)
})

watch(activeChannel, (channel) => {
  syncEditorWithActiveChannel(channel)
})

watch(() => props.initialChannelId, (channelId) => {
  if (!channelId) return
  void openChannelById(channelId)
}, { immediate: true })

watch(page, (nextPage) => {
  if (nextPage === 'members' || nextPage === 'admins' || nextPage === 'overview') {
    void loadMembers()
  }
  if (nextPage === 'add-members') {
    void loadCandidates(candidateQuery.value)
  }
})

if (props.showCloseButton) {
  pushManagerBackState()
}

if (!props.initialChannelId) {
  void loadExistingChannels()
}

defineExpose({
  activeChannel,
  members,
  candidates,
  existingChannels,
  page,
  pageHistory,
  title,
  description,
  avatarFileId,
  memberQuery,
  adminQuery,
  candidateQuery,
  selectAllActiveUsers,
  selectedUserIds,
  activeTotal,
  errorMessage,
  successMessage,
  isMembershipManagementLocked,
  selectedCount,
  canSaveDetails,
  currentUserMembership,
  canOpenCurrentChannelInMessenger,
  currentUserCanPostInCurrentChannel,
  activeAdminCount,
  channelAvatarUrl,
  isCurrentUserChannelCreator,
  canEditOverviewAvatar,
  currentChannelExitLabel,
  currentChannelExitSubtitle,
  currentChannelRoleLabel,
  filteredMembers,
  filteredAdmins,
  promotableMembers,
  pageTitle,
  pageSubtitle,
  normalizeSearch,
  compareMemberOrder,
  clearFlashMessages,
  getUserAvatarUrl,
  triggerAvatarPicker,
  handleAvatarSelected,
  clearAvatar,
  getPrimaryUserName,
  openMemberProfile,
  setPageDirect,
  requestClose,
  pushManagerBackState,
  discardManagerBackState,
  setError,
  resetSelection,
  resetCreateForm,
  syncEditorWithActiveChannel,
  getChannelKindLabel,
  getChannelMemberBadges,
  getPromotableMemberBadges,
  openCreatePage,
  openChannel,
  openChannelById,
  canDemoteMember,
  canRemoveMember,
  getMemberGuardReason,
  upsertExistingChannel,
  handleManagerBack,
  toggleUser,
  handleToggleSelectAll,
  loadExistingChannels,
  loadMembers,
  loadCandidates,
  createChannel,
  updateChannelDetails,
  submitMembers,
  mutateMember,
  unfollowCurrentChannel,
  promoteMember,
  demoteMember,
  removeMember,
  openCurrentChannelInMessenger,
})

onBeforeUnmount(() => {
  if (managerBackStateActive.value) {
    managerBackStateActive.value = false
    popBackState()
  }
})
</script>

<template>
  <section class="channel-admin-shell">
    <input ref="avatarInput" type="file" accept="image/*" class="hidden-avatar-input" @change="handleAvatarSelected" />
    <header class="channel-admin-header">
      <AppIconButton label="بازگشت" size="md" :disabled="!canGoBack" @click="handleManagerBack()">
        <ChevronRight :size="22" />
      </AppIconButton>
      <div class="header-copy">
        <h2>{{ pageTitle }}</h2>
        <span>{{ pageSubtitle }}</span>
      </div>
      <AppIconButton v-if="showCloseButton" label="بستن" size="md" @click="requestClose()">
        <X :size="20" />
      </AppIconButton>
      <div v-else class="header-spacer"></div>
    </header>

    <main class="manager-body">
      <div v-if="errorMessage" class="channel-status-banner error">{{ errorMessage }}</div>
      <div v-if="successMessage" class="channel-status-banner success">{{ successMessage }}</div>

      <template v-if="page === 'home'">
        <AppSectionCard class="manager-section-card card-with-help" title="ساخت کانال جدید" description="کانال اختیاری را بسازید، اعضا را دعوت کنید و نقش ادمین‌ها را مدیریت کنید.">
          <template #actions>
            <HelpPopover
              floating
              button-test="channel-home-help"
              note-test="channel-home-help-note"
              label="راهنمای ساخت کانال"
              text="کانال اختیاری را بسازید، اعضا را دعوت کنید و نقش ادمین‌ها را از همین بخش مدیریت کنید."
            />
          </template>
          <AppActionCard title="کانال جدید" description="ساخت کانال و ورود به فرم تنظیمات اولیه" tone="primary" @select="openCreatePage">
            <template #icon>
              <UsersRound :size="18" />
            </template>
          </AppActionCard>
        </AppSectionCard>

        <AppSectionCard class="manager-section-card" title="کانال‌های موجود" description="یک کانال را برای مشاهده اعضا، ادمین‌ها و تنظیمات انتخاب کنید.">
          <AppLoadingState v-if="isLoadingChannels" label="در حال دریافت کانال‌ها" />
          <AppEmptyState v-else-if="existingChannels.length === 0" title="هنوز کانالی ساخته نشده است" message="بعد از ساخت اولین کانال، فهرست مدیریت آن در همین بخش نمایش داده می‌شود." />
          <div v-else class="manager-action-list">
            <AppActionCard
              v-for="channel in existingChannels"
              :key="channel.id"
              :title="channel.title"
              :description="channel.description || `${channel.member_count.toLocaleString('fa-IR')} عضو`"
              :badge="getChannelKindLabel(channel)"
              tone="neutral"
              @select="openChannel(channel)"
            />
          </div>
        </AppSectionCard>
      </template>

      <template v-else-if="page === 'create'">
        <AppSectionCard class="manager-section-card manager-preview-card card-with-help" title="پیش‌نمایش کانال" description="نام، تصویر و توضیح کانال را پیش از ساخت مرور کنید.">
          <template #actions>
            <HelpPopover
              floating
              button-test="channel-create-preview-help"
              note-test="channel-create-preview-help-note"
              label="راهنمای پیش‌نمایش کانال"
              text="نام و توضیح روشن کمک می‌کند صفحه معرفی کانال کامل‌تر باشد. توضیح کانال پس از ثبت به اعضا نمایش داده می‌شود."
            />
          </template>
          <div class="manager-avatar">
            <img v-if="channelAvatarUrl" :src="channelAvatarUrl" :alt="title || 'کانال جدید'" class="hero-avatar-image" />
            <template v-else>{{ getAvatarInitial(title || 'کانال') }}</template>
            <div v-if="avatarBusy" class="avatar-busy-overlay"><Loader2 :size="20" class="spin" /></div>
          </div>
          <div class="manager-preview-title">{{ title || 'کانال جدید' }}</div>
          <AppStatusBadge tone="info">{{ description.trim() ? 'آماده برای ساخت' : 'بدون توضیحات' }}</AppStatusBadge>
          <p v-if="description.trim()" class="manager-preview-description">{{ description.trim() }}</p>
          <div class="avatar-tool-row">
            <AppButton type="button" size="sm" variant="secondary" :disabled="avatarBusy" @click="triggerAvatarPicker">{{ avatarFileId ? 'تغییر عکس کانال' : 'افزودن عکس کانال' }}</AppButton>
            <AppButton v-if="avatarFileId" type="button" size="sm" variant="danger" :disabled="avatarBusy" @click="clearAvatar">حذف عکس</AppButton>
          </div>
        </AppSectionCard>

        <AppSectionCard class="manager-section-card" title="مشخصات کانال" description="حداقل نام کانال را وارد کنید و در صورت نیاز توضیح کوتاه بنویسید.">
          <AppFormField label="نام کانال">
            <template #default="{ id }">
              <AppInput id="channel-title" v-model="title" :maxlength="255" placeholder="مثلاً اطلاعیه‌های ویژه" />
            </template>
          </AppFormField>

          <AppFormField label="توضیحات کانال">
            <template #default="{ id }">
              <AppTextarea id="channel-description" v-model="description" rows="5" maxlength="2000" placeholder="چند خط کوتاه درباره موضوع کانال" />
            </template>
          </AppFormField>

          <AppButton type="button" :loading="isSaving" :disabled="!canSaveDetails || isSaving" @click="createChannel">
            <template #icon>
              <Check v-if="!isSaving" :size="18" />
            </template>
            ساخت کانال
          </AppButton>
        </AppSectionCard>
      </template>

      <template v-else-if="page === 'overview' && activeChannel">
        <AppSectionCard class="manager-section-card manager-preview-card" title="نمای کلی کانال" :description="activeChannel.description || 'جزئیات این کانال برای اعضا و ادمین‌ها از همین بخش مدیریت می‌شود.'">
          <template #actions>
            <AppStatusBadge :tone="currentChannelKindTone">{{ getChannelKindLabel(activeChannel) }}</AppStatusBadge>
          </template>
          <button
            type="button"
            class="manager-avatar"
            :class="{ 'editable-avatar': canEditOverviewAvatar }"
            :disabled="!canEditOverviewAvatar || avatarBusy"
            @click="canEditOverviewAvatar ? triggerAvatarPicker() : undefined"
          >
            <img v-if="channelAvatarUrl" :src="channelAvatarUrl" :alt="activeChannel.title" class="hero-avatar-image" />
            <template v-else>{{ getAvatarInitial(activeChannel.title) }}</template>
            <div v-if="avatarBusy" class="avatar-busy-overlay"><Loader2 :size="20" class="spin" /></div>
            <span v-else-if="canEditOverviewAvatar" class="avatar-edit-badge">ویرایش</span>
          </button>
          <div class="manager-preview-title">{{ activeChannel.title }}</div>
          <div class="manager-preview-meta">{{ activeChannel.member_count.toLocaleString('fa-IR') }} عضو</div>
          <div v-if="canEditOverviewAvatar" class="avatar-tool-row compact centered-overview-tools">
            <AppButton type="button" size="sm" variant="secondary" :disabled="avatarBusy" @click="triggerAvatarPicker">{{ avatarFileId ? 'تغییر عکس کانال' : 'افزودن عکس کانال' }}</AppButton>
            <AppButton v-if="avatarFileId" type="button" size="sm" variant="danger" :disabled="avatarBusy" @click="clearAvatar">حذف عکس</AppButton>
          </div>
          <div v-if="canOpenCurrentChannelInMessenger" class="hero-actions">
            <AppButton type="button" size="sm" variant="secondary" @click="openCurrentChannelInMessenger">باز کردن در پیام‌رسان</AppButton>
          </div>
        </AppSectionCard>

        <div v-if="typeof currentUserId === 'number' && !currentUserMembership" class="channel-status-banner warning">شما عضو فعال این کانال نیستید. تا قبل از اضافه شدن، این کانال در فهرست گفتگوهای شما دیده نمی‌شود.</div>
        <div v-else-if="typeof currentUserId === 'number' && currentUserMembership && !currentUserCanPostInCurrentChannel" class="channel-status-banner info">شما عضو این کانال هستید اما فقط ادمین‌های کانال امکان ارسال پست دارند.</div>
        <div v-else-if="typeof currentUserId === 'number' && currentUserMembership && currentUserCanPostInCurrentChannel" class="channel-status-banner info">شما ادمین این کانال هستید و می‌توانید مستقیماً از پیام‌رسان در آن پست بگذارید.</div>
        <div class="manager-role-strip">
          <span>نقش شما</span>
          <strong>{{ currentChannelRoleLabel }}</strong>
        </div>

        <AppSectionCard class="manager-section-card" title="اعضا و دسترسی‌ها" description="فهرست اعضا، ادمین‌ها و دعوت افراد جدید از این بخش انجام می‌شود.">
          <div class="manager-action-list">
            <AppActionCard title="اعضای کانال" description="فهرست کامل اعضا و نقش‌ها" :badge="activeChannel.member_count.toLocaleString('fa-IR')" @select="setPage('members')">
              <template #icon><UsersRound :size="18" /></template>
            </AppActionCard>
            <AppActionCard v-if="!isMembershipManagementLocked" title="مدیریت ادمین‌ها" description="تعیین و تغییر ادمین‌های کانال" :badge="activeAdminCount.toLocaleString('fa-IR')" tone="warning" @select="setPage('admins')">
              <template #icon><Shield :size="18" /></template>
            </AppActionCard>
            <AppActionCard v-if="!isMembershipManagementLocked" title="افزودن عضو" description="دعوت اعضای جدید به کانال" tone="info" @select="setPage('add-members')">
              <template #icon><UserPlus :size="18" /></template>
            </AppActionCard>
          </div>
        </AppSectionCard>

        <AppSectionCard class="manager-section-card" title="تنظیمات" description="نام، توضیح و تصویر کانال را در این بخش ویرایش کنید.">
          <div class="manager-action-list">
            <AppActionCard title="تنظیمات کانال" description="ویرایش نام، توضیح و تصویر کانال" @select="setPage('edit')">
              <template #icon><PencilLine :size="18" /></template>
            </AppActionCard>
          </div>
        </AppSectionCard>

        <AppSectionCard v-if="currentUserMembership && !isMembershipManagementLocked" class="manager-section-card" title="خروج و حذف" :description="currentChannelExitSubtitle" tone="danger">
          <div class="manager-action-list">
            <AppActionCard :title="currentChannelExitLabel" :description="currentChannelExitSubtitle" tone="danger" :disabled="isSaving" @select="unfollowCurrentChannel">
              <template #icon><LogOut :size="18" /></template>
            </AppActionCard>
          </div>
        </AppSectionCard>
      </template>

      <template v-else-if="page === 'members'">
        <div class="search-shell slim">
          <AppInput v-model="memberQuery" type="text" class="search-input" placeholder="جستجو در اعضای کانال..." />
        </div>

        <AppLoadingState v-if="isLoadingMembers" class="state-box" label="در حال دریافت اعضای کانال..." />
        <div v-else class="channel-list">
          <ChatUserListRow
            v-for="member in filteredMembers"
            :key="member.user_id"
            :name="getChannelUserDisplayName(member)"
            :avatar-file-id="member.avatar_file_id || null"
            :badges="getChannelMemberBadges(member)"
          >
            <template #subtitle>
              <span dir="ltr">{{ member.mobile_number }}</span>
            </template>
            <template #actions>
              <AppButton
                type="button"
                class="channel-member-action"
                variant="secondary"
                @click.stop="openMemberProfile(member)"
              >
                پروفایل
              </AppButton>
              <AppButton
                v-if="!isMembershipManagementLocked && canRemoveMember(member)"
                type="button"
                class="channel-member-action channel-member-action--danger"
                variant="danger"
                :disabled="mutatingUserId === member.user_id"
                @click.stop="removeMember(member)"
              >
                حذف
              </AppButton>
            </template>
          </ChatUserListRow>
        </div>
      </template>

      <template v-else-if="page === 'admins' && !isMembershipManagementLocked">
        <div class="search-shell slim">
          <AppInput v-model="adminQuery" type="text" class="search-input" placeholder="جستجو در ادمین‌ها و اعضا..." />
        </div>

        <section class="section-shell">
          <div class="section-heading">ادمین‌های فعلی</div>
          <div class="channel-list compact">
            <ChatUserListRow
              v-for="member in filteredAdmins"
              :key="member.user_id"
              :name="getChannelUserDisplayName(member)"
              :avatar-file-id="member.avatar_file_id || null"
              :badges="getChannelMemberBadges(member)"
            >
              <template #subtitle>
                <span dir="ltr">{{ member.mobile_number }}</span>
              </template>
              <template #actions>
                <AppButton
                  type="button"
                  class="channel-member-action"
                  variant="secondary"
                  @click.stop="openMemberProfile(member)"
                >
                  پروفایل
                </AppButton>
                <AppButton
                  v-if="canDemoteMember(member)"
                  type="button"
                  class="channel-member-action"
                  variant="secondary"
                  :disabled="mutatingUserId === member.user_id"
                  @click.stop="demoteMember(member)"
                >
                  حذف ادمین
                </AppButton>
              </template>
            </ChatUserListRow>
          </div>
        </section>

        <section class="section-shell">
          <div class="section-heading">اعضای قابل ارتقا</div>
          <AppEmptyState v-if="promotableMembers.length === 0" class="state-box muted" title="عضوی برای ارتقا باقی نمانده است." />
          <div v-else class="channel-list compact">
            <ChatUserListRow
              v-for="member in promotableMembers"
              :key="member.user_id"
              :name="getChannelUserDisplayName(member)"
              :avatar-file-id="member.avatar_file_id || null"
              :badges="getPromotableMemberBadges(member)"
            >
              <template #subtitle>
                <span dir="ltr">{{ member.mobile_number }}</span>
              </template>
              <template #actions>
                <AppButton
                  type="button"
                  class="channel-member-action"
                  variant="secondary"
                  @click.stop="openMemberProfile(member)"
                >
                  پروفایل
                </AppButton>
                <AppButton
                  type="button"
                  class="channel-member-action channel-member-action--primary"
                  variant="primary"
                  :disabled="mutatingUserId === member.user_id"
                  @click.stop="promoteMember(member)"
                >
                  ارتقا به ادمین
                </AppButton>
              </template>
            </ChatUserListRow>
          </div>
        </section>
      </template>

      <template v-else-if="page === 'add-members' && !isMembershipManagementLocked && activeChannel">
        <div class="search-shell">
          <AppInput v-model="candidateQuery" type="text" class="search-input" placeholder="جستجو با نام، اکانت یا موبایل..." :disabled="selectAllActiveUsers" />
        </div>

        <label class="select-all-toggle" :class="{ active: selectAllActiveUsers }">
          <AppCheckbox :checked="selectAllActiveUsers" @change="handleToggleSelectAll" />
          <span>انتخاب همه کاربران فعال ({{ activeTotal.toLocaleString('fa-IR') }})</span>
        </label>

        <div class="selection-banner">
          <span>{{ selectedCount.toLocaleString('fa-IR') }} عضو انتخاب شده</span>
          <AppButton type="button" class="primary-chip" :disabled="selectedCount === 0 || isSubmittingMembers" @click="submitMembers">
            افزودن
          </AppButton>
        </div>

        <AppLoadingState v-if="isLoadingCandidates" class="state-box" label="در حال دریافت کاربران فعال..." />
        <AppEmptyState v-else-if="!selectAllActiveUsers && candidates.length === 0" class="state-box muted" title="کاربری برای دعوت باقی نمانده است." />
        <div v-else-if="!selectAllActiveUsers" class="channel-list">
          <ChatUserListRow
            v-for="candidate in candidates"
            :key="candidate.user_id"
            tag="button"
            :interactive="true"
            :selected="selectedUserIds.has(candidate.user_id)"
            :name="getChannelUserDisplayName(candidate)"
            :avatar-file-id="candidate.avatar_file_id || null"
            :badges="getRoleOnlyBadges(candidate)"
            @click="toggleUser(candidate.user_id)"
          >
            <template #subtitle>
              <span dir="ltr">{{ candidate.mobile_number }}</span>
            </template>
            <template #trailing>
              <div class="row-check" :class="{ active: selectedUserIds.has(candidate.user_id) }">
                <Check v-if="selectedUserIds.has(candidate.user_id)" :size="16" />
              </div>
            </template>
          </ChatUserListRow>
        </div>
      </template>

      <template v-else-if="page === 'edit' && activeChannel">
        <AppSectionCard class="manager-section-card" title="تنظیمات کانال" description="نام، توضیحات و تصویر کانال را از این بخش تغییر دهید.">
          <div class="avatar-editor-block">
            <div class="manager-avatar small-editor">
              <img v-if="channelAvatarUrl" :src="channelAvatarUrl" :alt="activeChannel.title" class="hero-avatar-image" />
              <template v-else>{{ getAvatarInitial(activeChannel.title) }}</template>
              <div v-if="avatarBusy" class="avatar-busy-overlay"><Loader2 :size="20" class="spin" /></div>
            </div>
            <div class="avatar-tool-row compact">
              <AppButton type="button" size="sm" variant="secondary" :disabled="avatarBusy" @click="triggerAvatarPicker">{{ avatarFileId ? 'تغییر عکس کانال' : 'افزودن عکس کانال' }}</AppButton>
              <AppButton v-if="avatarFileId" type="button" size="sm" variant="danger" :disabled="avatarBusy" @click="clearAvatar">حذف عکس</AppButton>
            </div>
          </div>

          <div class="info-strip">
            <Info :size="16" />
            <span>از این بخش می‌توانید نام و توضیحات کانال را ویرایش کنید.</span>
          </div>

          <AppFormField label="نام کانال">
            <template #default="{ id }">
              <AppInput id="edit-channel-title" v-model="title" :maxlength="255" placeholder="نام کانال" />
            </template>
          </AppFormField>

          <AppFormField label="توضیحات کانال">
            <template #default="{ id }">
              <AppTextarea id="edit-channel-description" v-model="description" rows="5" maxlength="2000" placeholder="توضیحات کانال برای اعضا" />
            </template>
          </AppFormField>

          <AppButton type="button" :loading="isSaving" :disabled="!canSaveDetails || isSaving" @click="updateChannelDetails">
            <template #icon>
              <Check v-if="!isSaving" :size="18" />
            </template>
            ذخیره تغییرات
          </AppButton>
        </AppSectionCard>
      </template>
    </main>
  </section>
</template>

<style scoped>
.channel-admin-shell {
  width: 100%;
  min-height: 0;
  max-height: min(88vh, 880px);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  direction: rtl;
  background: var(--messenger-manager-shell-bg, linear-gradient(180deg, #f7fafc 0%, #edf3f8 100%));
  border-radius: var(--messenger-radius-sheet, 28px);
}

.channel-admin-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: var(--messenger-panel-glass-bg, rgba(255, 255, 255, 0.92));
  backdrop-filter: blur(16px);
  border-bottom: 1px solid rgba(148, 163, 184, 0.14);
}

.channel-admin-header-btn {
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

.header-copy h2 {
  margin: 0;
  font-size: 1rem;
  font-weight: 900;
  color: #0f172a;
}

.header-copy span {
  color: #64748b;
  font-size: 0.78rem;
}

.header-spacer {
  width: 40px;
  height: 40px;
  flex-shrink: 0;
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

.channel-status-banner,
.state-box,
.selection-banner,
.manager-role-strip,
.info-strip,
.select-all-toggle {
  border-radius: 18px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.channel-status-banner.error {
  background: rgba(254, 242, 242, 0.95);
  color: #b91c1c;
  border: 1px solid rgba(248, 113, 113, 0.18);
}

.channel-status-banner.success {
  background: rgba(236, 253, 245, 0.94);
  color: #047857;
  border: 1px solid rgba(52, 211, 153, 0.18);
}

.channel-status-banner.warning {
  background: rgba(255, 247, 237, 0.96);
  color: #9a3412;
  border: 1px solid rgba(245, 158, 11, 0.18);
}

.channel-status-banner.info,
.info-strip {
  background: rgba(239, 246, 255, 0.96);
  color: #1d4ed8;
  border: 1px solid rgba(59, 130, 246, 0.16);
}

.state-box {
  justify-content: center;
  min-height: 58px;
  background: rgba(255, 255, 255, 0.84);
  border: 1px solid rgba(148, 163, 184, 0.12);
  color: #475569;
}

.state-box.ui-loading-state,
.state-box.ui-empty-state {
  min-height: 58px;
  padding: 12px 14px;
}

.state-box.ui-loading-state {
  flex-direction: row;
}

.state-box.ui-empty-state :deep(h2) {
  margin: 0;
  font: inherit;
  font-weight: 800;
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

.search-input {
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
  min-height: 56px;
  padding: 0 18px;
}
.search-input:focus {
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

.select-all-toggle {
  justify-content: space-between;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.14);
  color: #334155;
  cursor: pointer;
}

.select-all-toggle input {
  margin: 0;
}

.select-all-toggle.active {
  border-color: rgba(51, 144, 236, 0.24);
  box-shadow: 0 0 0 4px rgba(51, 144, 236, 0.08);
}

.primary-chip {
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

.primary-chip:disabled,
.channel-admin-header-btn:disabled {
  opacity: 0.55;
  cursor: default;
}

.manager-section-card,
.section-shell {
  border-radius: var(--messenger-radius-sheet, 28px);
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(148, 163, 184, 0.14);
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
}

.manager-section-card.card-with-help {
  padding-left: 4rem;
}

.manager-preview-card {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92), rgba(236, 245, 255, 0.94));
}

.manager-avatar,
.row-avatar {
  background: linear-gradient(135deg, #3390ec, #0ea5e9 58%, #f59e0b 100%);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 900;
  flex-shrink: 0;
}

.manager-avatar {
  position: relative;
  overflow: hidden;
  width: 86px;
  height: 86px;
  border: 0;
  padding: 0;
  border-radius: 50%;
  font-size: 2rem;
}

.manager-avatar.editable-avatar {
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
  font-size: var(--ds-font-badge);
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

.manager-avatar.small-editor {
  width: 74px;
  height: 74px;
  font-size: 1.65rem;
}

.manager-preview-title {
  font-size: 1.18rem;
  font-weight: 900;
  color: #0f172a;
}

.manager-preview-meta,
.manager-preview-description {
  color: #64748b;
  font-size: var(--ds-font-meta);
}

.manager-preview-description {
  margin: 0;
  max-width: 40ch;
  line-height: 1.85;
}

.hero-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: center;
}

.channel-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.channel-list.compact {
  gap: 8px;
}

.manager-action-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
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
  font-size: var(--ds-font-meta);
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
  font-size: var(--ds-font-meta);
  font-weight: 800;
}

.row-chevron {
  color: #94a3b8;
  flex-shrink: 0;
}

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

.section-heading {
  font-size: var(--ds-font-helper);
  font-weight: 800;
  color: #475569;
}

.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: var(--ds-font-badge);
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

.channel-member-action {
  min-height: 36px;
  padding: 0.45rem 0.8rem;
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.96);
  color: #475569;
  font-size: var(--ds-font-badge);
  font-weight: 800;
  line-height: 1;
  cursor: pointer;
  box-shadow: 0 10px 22px rgba(15, 23, 42, 0.05);
  transition: background-color 0.2s ease, border-color 0.2s ease, color 0.2s ease, transform 0.2s ease;
}

.channel-member-action:hover:not(:disabled),
.channel-member-action:focus-visible {
  background: rgba(241, 245, 249, 0.98);
  border-color: rgba(51, 65, 85, 0.2);
  color: #1e293b;
}

.channel-member-action:focus-visible {
  outline: 3px solid rgba(245, 158, 11, 0.18);
  outline-offset: 2px;
}

.channel-member-action:active:not(:disabled) {
  transform: scale(0.98);
}

.channel-member-action:disabled {
  opacity: 0.58;
  cursor: not-allowed;
  box-shadow: none;
}

.channel-member-action--primary {
  background: rgba(245, 158, 11, 0.12);
  border-color: rgba(245, 158, 11, 0.18);
  color: #b45309;
}

.channel-member-action--danger {
  background: rgba(254, 242, 242, 0.96);
  border-color: rgba(239, 68, 68, 0.16);
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

@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}

@media (max-width: 520px) {
  .row-actions {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
