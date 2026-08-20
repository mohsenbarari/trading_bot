<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { Search, X, ChevronLeft } from 'lucide-vue-next';
import CustomerNameWithBadge from './CustomerNameWithBadge.vue';
import LoadingSkeleton from './LoadingSkeleton.vue';
import AppButton from './ui/AppButton.vue';
import AppEmptyState from './ui/AppEmptyState.vue';
import AppErrorState from './ui/AppErrorState.vue';
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

async function fetchUsers(query = committedQuery.value) {
  const requestQuery = normalizeQuery(query);
  const requestSequence = ++usersRequestSequence;
  usersAbortController?.abort();
  usersAbortController = new AbortController();
  isLoading.value = true;
  errorMessage.value = '';
  errorKind.value = null;

  try {
    const url = requestQuery
      ? `/api/users/?search=${encodeURIComponent(requestQuery)}`
      : '/api/users/';
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

    users.value = payload as User[];
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

        <AppEmptyState v-if="users.length === 0" class="no-results" title="کاربری یافت نشد." role="status">
          <template #icon>
            <Search :size="24" />
          </template>
        </AppEmptyState>

        <ul v-else class="users-list" aria-label="فهرست کاربران">
          <li v-for="user in users" :key="user.id" class="users-list-item">
            <AppListItem
              class="user-item"
              interactive
              :title="getUserDisplayName(user)"
              :description="user.mobile_number"
              :aria-label="`باز کردن پروفایل ${getUserDisplayName(user)}`"
              @select="selectUser(user)"
            >
              <template #leading>
                {{ getUserDisplayName(user)[0] || '?' }}
              </template>
              <template #title>
                <span class="user-title-block">
                  <span class="user-name" dir="auto">
                    <CustomerNameWithBadge
                      v-if="user.is_customer || user.customer_management_name"
                      :name="getUserDisplayName(user)"
                      compact
                    />
                    <template v-else>{{ getUserDisplayName(user) }}</template>
                  </span>
                  <span v-if="userHasRelationTags(user)" class="user-relation-tags">
                    <span v-if="user.customer_owner_account_name" class="relation-badge relation-badge--owner">
                      سرگروه: {{ user.customer_owner_account_name }}
                    </span>
                    <span v-if="user.is_accountant" class="relation-badge relation-badge--accountant">
                      حسابدار
                    </span>
                    <span v-if="user.accountant_owner_account_name" class="relation-badge relation-badge--owner">
                      سرگروه: {{ user.accountant_owner_account_name }}
                    </span>
                  </span>
                </span>
              </template>
              <template #trailing>
                <span class="user-meta">
                  <AppStatusBadge
                    v-if="user.account_status === 'inactive'"
                    class="user-account-status"
                    tone="danger"
                  >
                    حساب غیرفعال
                  </AppStatusBadge>
                  <AppStatusBadge class="role-badge" :class="user.role" :tone="roleBadgeTone(user.role)">
                    {{ user.role }}
                  </AppStatusBadge>
                  <ChevronLeft class="chevron-icon" :size="20" aria-hidden="true" />
                </span>
              </template>
            </AppListItem>
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
  width: 44px;
  height: 44px;
  background: var(--ds-gradient-primary);
  color: var(--ds-bg-card);
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
  .user-item {
    grid-template-columns: 44px minmax(0, 1fr);
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
      grid-template-columns: 44px minmax(0, 1fr);
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
