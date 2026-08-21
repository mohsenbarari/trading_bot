<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { Search, X, ChevronLeft, ShieldAlert, Check } from 'lucide-vue-next';
import CustomerNameWithBadge from './CustomerNameWithBadge.vue';
import LoadingSkeleton from './LoadingSkeleton.vue';
import AppButton from './ui/AppButton.vue';
import AppEmptyState from './ui/AppEmptyState.vue';
import AppErrorState from './ui/AppErrorState.vue';
import AppFilterChips from './ui/AppFilterChips.vue';
import AppInput from './ui/AppInput.vue';
import AppListItem from './ui/AppListItem.vue';
import AppStatusBadge from './ui/AppStatusBadge.vue';
import { routeRequestJson } from '../utils/routeRequest';
import { isAppHttpError } from '../utils/httpErrorPolicy';

interface User {
  id: number;
  full_name: string;
  telegram_id: number;
  account_name: string;
  role: string;
  mobile_number: string;
  account_status?: string | null;
  is_customer?: boolean;
  customer_owner_account_name?: string | null;
  customer_management_name?: string | null;
  is_accountant?: boolean;
  accountant_owner_account_name?: string | null;
}

interface UserFlag {
  id: number;
  user_id: number;
  flag_type: string;
  flag_label: string;
  reason_code: string;
  reason_label: string;
  status: string;
  severity: string;
  details: {
    counts?: Partial<Record<'daily' | 'weekly' | 'monthly', number>>;
    device_name?: string | null;
  };
  trigger_count: number;
  first_flagged_at: string;
  last_flagged_at: string;
  user: User;
}

const props = withDefaults(defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
  query?: string;
}>(), {
  query: '',
});

const emit = defineEmits<{
  navigate: [destination: 'user_profile', user: User];
  'query-change': [query: string];
  loaded: [];
  settled: [];
}>();

function normalizeQuery(value: unknown) {
  return typeof value === 'string' ? value.trim() : '';
}

function isAbortError(error: unknown) {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError';
}

const users = ref<User[]>([]);
const userFlags = ref<UserFlag[]>([]);
const directoryMode = ref<'all' | 'suspicious'>('all');
const resolvingFlagId = ref<number | null>(null);
const isLoading = ref(true);
const errorMessage = ref('');
const errorKind = ref<'forbidden' | 'generic' | null>(null);
const committedQuery = ref(normalizeQuery(props.query));
const searchQuery = ref(committedQuery.value);
const displayedQuery = ref(committedQuery.value);
const hasSuccessfulResponse = ref(false);
const isShowingStaleResults = computed(
  () => hasSuccessfulResponse.value && displayedQuery.value !== committedQuery.value,
);
let usersRequestSequence = 0;
let usersAbortController: AbortController | null = null;

const directoryOptions = [
  { key: 'all', label: 'همه کاربران' },
  { key: 'suspicious', label: 'کاربران مشکوک' },
];
const directoryRows = computed(() => (
  directoryMode.value === 'all'
    ? users.value.map(user => ({ key: `user:${user.id}`, user, flag: null as UserFlag | null }))
    : userFlags.value.map(flag => ({ key: `flag:${flag.id}`, user: flag.user, flag }))
));

async function fetchUsers(query = committedQuery.value) {
  const requestQuery = normalizeQuery(query);
  const requestSequence = ++usersRequestSequence;
  usersAbortController?.abort();
  usersAbortController = new AbortController();
  isLoading.value = true;
  errorMessage.value = '';
  errorKind.value = null;

  try {
    const baseUrl = directoryMode.value === 'suspicious' ? '/api/user-flags/open' : '/api/users/';
    const url = requestQuery
      ? `${baseUrl}?search=${encodeURIComponent(requestQuery)}`
      : baseUrl;
    const payload = await routeRequestJson<unknown>(url, {
      signal: usersAbortController.signal,
      errorContext: {
        surface: 'admin',
        scope: 'list',
        operation: hasSuccessfulResponse.value ? 'background-refresh' : 'load-list',
        preserveExistingData: hasSuccessfulResponse.value,
        fallbackMessage: 'دریافت کاربران ممکن نشد.',
      },
    });

    if (requestSequence !== usersRequestSequence) return;
    if (!Array.isArray(payload)) throw new Error('invalid_users_payload');

    if (directoryMode.value === 'suspicious') {
      userFlags.value = payload as UserFlag[];
    } else {
      users.value = payload as User[];
    }
    displayedQuery.value = requestQuery;
    hasSuccessfulResponse.value = true;
    emit('loaded');
  } catch (error) {
    if (requestSequence !== usersRequestSequence || isAbortError(error)) return;
    if (isAppHttpError(error) && error.status === 403) {
      users.value = [];
      displayedQuery.value = '';
      hasSuccessfulResponse.value = false;
      errorKind.value = 'forbidden';
      errorMessage.value = 'مجوز مشاهده فهرست کاربران را ندارید.';
    } else {
      errorKind.value = 'generic';
      errorMessage.value = 'دریافت کاربران ممکن نشد. دوباره تلاش کنید.';
    }
  } finally {
    if (requestSequence === usersRequestSequence) {
      isLoading.value = false;
      // The parent may be holding a safe scroll context while this response
      // renders. Signal both accepted data and bounded error recoveries so it
      // can release that lock without leaving later user scrolling inert.
      emit('settled');
    }
  }
}

function changeDirectoryMode(nextMode: string) {
  if (nextMode !== 'all' && nextMode !== 'suspicious') return;
  if (directoryMode.value === nextMode) return;
  directoryMode.value = nextMode;
  hasSuccessfulResponse.value = false;
  errorMessage.value = '';
  void fetchUsers(committedQuery.value);
}

function formatFlagTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('fa-IR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(date);
}

function formatFlagCounts(flag: UserFlag) {
  const counts = flag.details?.counts || {};
  const parts = [
    counts.daily ? `${counts.daily} بار در ۲۴ ساعت` : '',
    counts.weekly ? `${counts.weekly} بار در ۷ روز` : '',
    counts.monthly ? `${counts.monthly} بار در ۳۰ روز` : '',
  ].filter(Boolean);
  return parts.join(' · ');
}

async function resolveFlag(flag: UserFlag) {
  if (resolvingFlagId.value !== null) return;
  resolvingFlagId.value = flag.id;
  errorMessage.value = '';
  try {
    await routeRequestJson(`/api/user-flags/${flag.id}/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
      errorContext: {
        surface: 'admin',
        scope: 'detail',
        operation: 'mutation',
        preserveExistingData: true,
        fallbackMessage: 'ثبت نتیجه بررسی ممکن نشد.',
      },
    });
    userFlags.value = userFlags.value.filter(item => item.id !== flag.id);
  } catch {
    errorMessage.value = 'ثبت نتیجه بررسی ممکن نشد. دوباره تلاش کنید.';
  } finally {
    resolvingFlagId.value = null;
  }
}

function submitSearch() {
  const nextQuery = normalizeQuery(searchQuery.value);
  const queryChanged = nextQuery !== committedQuery.value;

  searchQuery.value = nextQuery;
  committedQuery.value = nextQuery;

  if (queryChanged) {
    emit('query-change', nextQuery);
  }
  void fetchUsers(nextQuery);
}

function clearSearch() {
  const hasDraftOrCommittedQuery = Boolean(searchQuery.value || committedQuery.value);
  searchQuery.value = '';

  if (!hasDraftOrCommittedQuery) return;

  committedQuery.value = '';
  emit('query-change', '');
  void fetchUsers('');
}

function retryUsers() {
  void fetchUsers();
}

function selectUser(user: User) {
  emit('navigate', 'user_profile', user);
}

function getUserDisplayName(user: User) {
  return user.customer_management_name?.trim() || user.account_name || 'کاربر';
}

function userHasRelationTags(user: User) {
  return Boolean(
    user.customer_owner_account_name
    || user.is_accountant
    || user.accountant_owner_account_name,
  );
}

function roleBadgeTone(role: string): 'neutral' | 'primary' | 'success' | 'info' {
  if (role === 'مدیر') return 'primary';
  if (role === 'پلیس') return 'info';
  if (role === 'عادی') return 'success';
  return 'neutral';
}

watch(
  () => props.query,
  (nextQueryValue) => {
    const nextQuery = normalizeQuery(nextQueryValue);
    const shouldReload = nextQuery !== committedQuery.value;

    searchQuery.value = nextQuery;
    committedQuery.value = nextQuery;

    if (shouldReload) {
      void fetchUsers(nextQuery);
    }
  },
);

onMounted(() => {
  void fetchUsers();
});

onUnmounted(() => {
  usersRequestSequence += 1;
  usersAbortController?.abort();
});
</script>

<template>
  <div class="user-manager ds-page-content">
    <div class="ds-card">
      <AppFilterChips
        class="user-directory-tabs"
        :model-value="directoryMode"
        :options="directoryOptions"
        label="نوع فهرست کاربران"
        id-prefix="user-directory"
        focus-selection-on-keyboard
        @update:model-value="changeDirectoryMode"
      />
      <form class="user-search-form" @submit.prevent="submitSearch">
        <label class="sr-only" for="user-directory-search">جستجوی کاربر</label>
        <AppInput
          id="user-directory-search"
          v-model="searchQuery"
          class="user-search-input"
          placeholder="نام، نام کاربری یا موبایل..."
        />
        <AppButton type="submit" class="user-search-submit search-submit-btn">
          <template #icon>
            <Search :size="18" />
          </template>
          جستجو
        </AppButton>
        <AppButton
          v-if="searchQuery || committedQuery"
          type="button"
          variant="ghost"
          class="user-search-clear"
          @click="clearSearch"
        >
          <template #icon>
            <X :size="18" />
          </template>
          پاک کردن
        </AppButton>
      </form>

      <div v-if="isLoading && !hasSuccessfulResponse" class="loading-state">
        <LoadingSkeleton :count="6" :height="70" />
      </div>

      <AppErrorState
        v-else-if="errorMessage && !hasSuccessfulResponse"
        class="ds-message danger user-initial-error"
        :title="errorKind === 'forbidden' ? 'دسترسی به فهرست کاربران مجاز نیست' : 'دریافت کاربران انجام نشد'"
        :message="errorMessage"
      >
        <template #actions>
          <AppButton type="button" class="user-load-retry" variant="secondary" :loading="isLoading" @click="retryUsers">
            تلاش مجدد
          </AppButton>
        </template>
      </AppErrorState>

      <div v-else-if="hasSuccessfulResponse" class="users-result" :aria-busy="isLoading">
        <p v-if="isShowingStaleResults" class="user-query-stale-notice" role="status">
          نتایج فعلی مربوط به جست‌وجوی قبلی هستند.
        </p>
        <div v-if="errorMessage" class="user-refresh-error" role="alert">
          <span>{{ errorMessage }}</span>
          <AppButton type="button" size="sm" variant="ghost" :loading="isLoading" @click="retryUsers">تلاش مجدد</AppButton>
        </div>

        <AppEmptyState
          v-if="directoryRows.length === 0"
          class="no-results"
          :title="directoryMode === 'suspicious' ? 'کاربر مشکوکی برای بررسی وجود ندارد.' : 'کاربری یافت نشد.'"
          role="status"
        >
          <template #icon>
            <ShieldAlert v-if="directoryMode === 'suspicious'" :size="24" />
            <Search v-else :size="24" />
          </template>
        </AppEmptyState>

        <ul v-else class="users-list" :aria-label="directoryMode === 'suspicious' ? 'فهرست کاربران مشکوک' : 'فهرست کاربران'">
          <li
            v-for="row in directoryRows"
            :key="row.key"
            class="users-list-item"
            :class="{ 'users-list-item--flagged': row.flag }"
          >
            <AppListItem
              class="user-item"
              interactive
              :title="getUserDisplayName(row.user)"
              :description="row.user.mobile_number"
              :aria-label="`باز کردن پروفایل ${getUserDisplayName(row.user)}`"
              @select="selectUser(row.user)"
            >
              <template #leading>
                {{ getUserDisplayName(row.user)[0] || '?' }}
              </template>
              <template #title>
                <span class="user-title-block">
                  <span class="user-name" dir="auto">
                    <CustomerNameWithBadge
                      v-if="row.user.is_customer || row.user.customer_management_name"
                      :name="getUserDisplayName(row.user)"
                      compact
                    />
                    <template v-else>{{ getUserDisplayName(row.user) }}</template>
                  </span>
                  <span v-if="userHasRelationTags(row.user)" class="user-relation-tags">
                    <span v-if="row.user.customer_owner_account_name" class="relation-badge relation-badge--owner">
                      سرگروه: {{ row.user.customer_owner_account_name }}
                    </span>
                    <span v-if="row.user.is_accountant" class="relation-badge relation-badge--accountant">
                      حسابدار
                    </span>
                    <span v-if="row.user.accountant_owner_account_name" class="relation-badge relation-badge--owner">
                      سرگروه: {{ row.user.accountant_owner_account_name }}
                    </span>
                  </span>
                </span>
              </template>
              <template #trailing>
                <span class="user-meta">
                  <AppStatusBadge
                    v-if="row.user.account_status === 'inactive'"
                    class="user-account-status"
                    tone="danger"
                  >
                    حساب غیرفعال
                  </AppStatusBadge>
                  <AppStatusBadge class="role-badge" :class="row.user.role" :tone="roleBadgeTone(row.user.role)">
                    {{ row.user.role }}
                  </AppStatusBadge>
                  <ChevronLeft class="chevron-icon" :size="20" aria-hidden="true" />
                </span>
              </template>
            </AppListItem>
            <div v-if="row.flag" class="user-flag-summary">
              <div class="user-flag-copy">
                <strong>{{ row.flag.flag_label }}</strong>
                <span>{{ row.flag.reason_label }}</span>
                <small v-if="formatFlagCounts(row.flag)">
                  {{ formatFlagCounts(row.flag) }}
                </small>
                <small>
                  آخرین ثبت: {{ formatFlagTime(row.flag.last_flagged_at) }}
                  <template v-if="row.flag.details?.device_name">
                    · {{ row.flag.details.device_name }}
                  </template>
                </small>
              </div>
              <AppButton
                type="button"
                size="sm"
                variant="secondary"
                class="user-flag-resolve"
                :loading="resolvingFlagId === row.flag.id"
                :disabled="resolvingFlagId !== null && resolvingFlagId !== row.flag.id"
                @click="resolveFlag(row.flag)"
              >
                <template #icon><Check :size="16" /></template>
                بررسی شد
              </AppButton>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.user-manager {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  min-width: 0;
  /* Local bridge to the Figma Persian type scale; do not widen V2 scope. */
  font-family: Vazirmatn, Tahoma, Arial, sans-serif;
  font-synthesis: none;
}

.user-search-form {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.user-directory-tabs {
  margin-bottom: 0.75rem;
}

.user-search-input {
  flex: 1 1 12rem;
  min-width: 0;
}

.search-submit-btn,
.user-search-clear {
  flex: 0 0 auto;
}

.loading-state {
  padding: 0.5rem 0;
}

.users-result {
  min-width: 0;
}

.users-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
  /*
   * The directory also renders inside the deliberately narrow desktop PWA
   * column. Use the list's inline size rather than the viewport so metadata
   * never squeezes the account name into a third, unreadable grid column.
   */
  container: user-directory / inline-size;
}

.users-list-item {
  min-width: 0;
}

.users-list-item--flagged {
  overflow: hidden;
  border: 1px solid var(--ds-warning-100);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-warning-50);
}

.users-list-item--flagged .user-item {
  border: 0;
  border-radius: 0;
  background: transparent;
}

.user-flag-summary {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.65rem 0.8rem 0.75rem;
  border-top: 1px solid var(--ds-warning-100);
}

.user-flag-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.16rem;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
}

.user-flag-copy strong {
  color: var(--ds-warning-700);
  font-size: var(--ds-font-sm);
}

.user-flag-copy small {
  color: var(--ds-text-placeholder);
}

.user-flag-resolve {
  flex: 0 0 auto;
}

.user-refresh-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--ds-danger-100);
  border-radius: var(--ds-radius-md);
  background: var(--ds-danger-50);
  color: var(--ds-danger-700);
  font-size: var(--ds-font-xs);
}

.user-query-stale-notice {
  margin: 0 0 0.5rem;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
}

.no-results {
  padding: 3rem 1rem;
  text-align: center;
  color: var(--ds-text-placeholder);
}

.user-item {
  width: 100%;
  appearance: none;
}

.user-item :deep(.ui-list-item__leading) {
  width: var(--ds-native-row-min-height, 48px);
  height: var(--ds-native-row-min-height, 48px);
  background: var(--ds-primary-100);
  color: var(--ds-primary-700);
  font-size: 1.2rem;
  font-weight: 800;
}

.user-item :deep(.ui-list-item__copy > span) {
  direction: ltr;
  unicode-bidi: isolate;
  font-family: var(--ds-font-mono);
  color: var(--ds-text-placeholder);
}

.user-item :deep(.ui-list-item__trailing) {
  color: inherit;
}

.user-title-block {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.2rem;
}

.user-name {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ds-text-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 24px;
}

.user-relation-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-top: 0.25rem;
}

.relation-badge {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  min-height: 18px;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 0.62rem;
  font-weight: 900;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.relation-badge--owner {
  border: 1px solid var(--ds-border-medium);
  background: var(--ds-info-50);
  color: var(--ds-info-700);
}

.relation-badge--accountant {
  border: 1px solid var(--ds-primary-200);
  background: var(--ds-primary-50);
  color: var(--ds-primary-800);
}

.user-meta {
  display: flex;
  min-width: 0;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.4rem;
  text-align: left;
}

.chevron-icon {
  flex: 0 0 auto;
  color: var(--ds-text-disabled);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@container user-directory (max-width: 34rem) {
  .user-flag-summary {
    align-items: stretch;
    flex-direction: column;
  }

  .user-flag-resolve {
    align-self: flex-start;
  }

  .user-item {
    grid-template-columns: var(--ds-native-row-min-height, 48px) minmax(0, 1fr);
    grid-template-areas:
      'leading copy'
      'leading trailing';
    align-items: start;
    column-gap: 0.75rem;
    row-gap: 0.5rem;
  }

  .user-item :deep(.ui-list-item__leading) {
    grid-area: leading;
  }

  .user-item :deep(.ui-list-item__copy) {
    grid-area: copy;
    min-width: 0;
  }

  .user-item :deep(.ui-list-item__trailing) {
    grid-area: trailing;
    min-width: 0;
  }

  .user-item :deep(.ui-list-item__copy > strong),
  .user-item :deep(.ui-list-item__copy > span),
  .user-title-block,
  .user-name {
    min-width: 0;
    max-width: 100%;
  }

  .user-item :deep(.ui-list-item__copy > span),
  .user-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .user-relation-tags {
    max-width: 100%;
  }

  .relation-badge {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    overflow-wrap: normal;
  }

  .user-meta {
    justify-content: flex-start;
    gap: 0.25rem;
  }
}

/* Preserve the established narrow-viewport layout in older engines. */
@supports not (container-type: inline-size) {
  @media (max-width: 480px) {
    .user-item {
      grid-template-columns: var(--ds-native-row-min-height, 48px) minmax(0, 1fr);
      grid-template-areas:
        'leading copy'
        'leading trailing';
      align-items: start;
      column-gap: 0.75rem;
      row-gap: 0.5rem;
    }

    .user-item :deep(.ui-list-item__leading) {
      grid-area: leading;
    }

    .user-item :deep(.ui-list-item__copy) {
      grid-area: copy;
      min-width: 0;
    }

    .user-item :deep(.ui-list-item__trailing) {
      grid-area: trailing;
      min-width: 0;
    }

    .user-item :deep(.ui-list-item__copy > strong),
    .user-item :deep(.ui-list-item__copy > span),
    .user-title-block,
    .user-name {
      min-width: 0;
      max-width: 100%;
    }

    .user-item :deep(.ui-list-item__copy > span),
    .user-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .user-relation-tags {
      max-width: 100%;
    }

    .relation-badge {
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      overflow-wrap: normal;
    }

    .user-meta {
      justify-content: flex-start;
      gap: 0.25rem;
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .user-item {
    transition: none;
  }
}
</style>
