<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import {
  BriefcaseBusiness,
  ChevronLeft,
  Megaphone,
  Package,
  Settings,
  UserPlus,
  Users,
} from 'lucide-vue-next'
import {
  WorkspaceNotice,
  WorkspaceSection,
  WorkspaceShell,
} from '../components/workspace'
import {
  AppButton,
  AppEmptyState,
  AppErrorState,
  AppInsetGroup,
  AppListItem,
  AppLoadingState,
} from '../components/ui'
import {
  currentUserSummary,
  isAdminRole,
  isAuthoritativeCurrentUserSummary,
  loadCurrentUserSummary,
} from '../utils/currentUser'

const router = useRouter()
const identityState = ref<'loading' | 'ready' | 'stale' | 'error'>(
  isAuthoritativeCurrentUserSummary(currentUserSummary.value) ? 'stale' : 'loading',
)
const identityBusy = ref(false)

interface OperationAction {
  key: string
  title: string
  icon: Component
  action: () => void
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
}

const user = computed(() => currentUserSummary.value)
const hasIdentity = computed(() => isAuthoritativeCurrentUserSummary(user.value))
const userRole = computed(() => user.value?.role || '')
const isAdmin = computed(() => isAdminRole(userRole.value))
const isSuperAdmin = computed(() => userRole.value === 'مدیر ارشد')
const isCustomer = computed(() => user.value?.is_customer === true)
const isAccountant = computed(() => user.value?.is_accountant === true)
const canUseOwnerRelations = computed(() => (
  hasIdentity.value
  && !isCustomer.value
  && !isAccountant.value
))

const ownerActions = computed<OperationAction[]>(() => {
  if (!canUseOwnerRelations.value) return []
  return [
    {
      key: 'customers',
      title: 'مشتریان',
      icon: Users,
      tone: 'primary',
      action: () => router.push({ name: 'operations-customers' }),
    },
    {
      key: 'accountants',
      title: 'حسابداران',
      icon: BriefcaseBusiness,
      tone: 'primary',
      action: () => router.push({ name: 'operations-accountants' }),
    },
  ]
})

const adminActions = computed<OperationAction[]>(() => {
  if (!isAdmin.value) return []

  const actions: OperationAction[] = [
    {
      key: 'create_invitation',
      title: 'ارسال دعوت‌نامه',
      icon: UserPlus,
      action: () => router.push({ name: 'admin-invitations' }),
    },
    {
      key: 'manage_users',
      title: 'مدیریت کاربران',
      icon: Users,
      action: () => router.push({ name: 'admin-users' }),
    },
  ]

  if (isSuperAdmin.value) {
    actions.push(
      {
        key: 'manage_commodities',
        title: 'مدیریت کالاها',
        icon: Package,
        action: () => router.push({ name: 'admin-commodities' }),
      },
      {
        key: 'admin_messages',
        title: 'پیام‌های مدیریت',
        icon: Megaphone,
        action: () => router.push({ name: 'admin-messages' }),
      },
      {
        key: 'settings',
        title: 'تنظیمات سیستم',
        icon: Settings,
        action: () => router.push({ name: 'admin-system' }),
      },
    )
  }

  return actions
})
const hasOperationActions = computed(() => (
  ownerActions.value.length > 0 || adminActions.value.length > 0
))

async function refreshIdentity() {
  if (identityBusy.value) return
  identityBusy.value = true
  if (!hasIdentity.value) identityState.value = 'loading'
  try {
    const result = await loadCurrentUserSummary({ force: true })
    if (!isAuthoritativeCurrentUserSummary(result.user)) {
      identityState.value = 'error'
      return
    }
    identityState.value = result.state === 'stale' ? 'stale' : 'ready'
  } catch {
    identityState.value = hasIdentity.value ? 'stale' : 'error'
  } finally {
    identityBusy.value = false
  }
}

onMounted(refreshIdentity)
</script>

<template>
  <div class="ds-page operations-page ui-v2-daily-page ui-v2-operations-page">
    <WorkspaceShell
      title="عملیات"
      layout="stack"
      v2-scope
    >
      <AppLoadingState
        v-if="identityState === 'loading' && !hasIdentity"
        class="operations-identity-loading"
        label="در حال دریافت عملیات"
      />
      <AppErrorState
        v-else-if="identityState === 'error' && !hasIdentity"
        class="operations-identity-error"
        title="عملیات بارگذاری نشد"
        message="تا اطلاعات حساب دریافت نشود، اقدامی نمایش داده نمی‌شود."
      >
        <template #actions>
          <AppButton type="button" class="operations-identity-retry" :loading="identityBusy" @click="refreshIdentity">تلاش دوباره</AppButton>
        </template>
      </AppErrorState>
      <WorkspaceNotice
        v-if="identityState === 'stale' && hasIdentity"
        class="operations-identity-stale"
        tone="warning"
        v2-scope
        title="اطلاعات حساب به‌روز نشد"
        message="اقدام‌های ذخیره‌شده قبلی نمایش داده شده‌اند."
      >
        <AppButton type="button" size="sm" variant="secondary" :loading="identityBusy" @click="refreshIdentity">به‌روزرسانی</AppButton>
      </WorkspaceNotice>

      <WorkspaceSection
        v-if="hasIdentity && ownerActions.length"
        title="روابط کاری"
        v2-scope
      >
        <AppInsetGroup class="operations-action-group">
          <AppListItem
            v-for="action in ownerActions"
            :key="action.key"
            class="operations-action-tile"
            interactive
            :title="action.title"
            @select="action.action"
          >
            <template #leading>
              <component :is="action.icon" :size="20" />
            </template>
            <template #trailing>
              <ChevronLeft :size="18" aria-hidden="true" />
            </template>
          </AppListItem>
        </AppInsetGroup>
      </WorkspaceSection>

      <WorkspaceSection
        v-if="hasIdentity && adminActions.length"
        title="مدیریت"
        v2-scope
      >
        <AppInsetGroup class="operations-action-group">
          <AppListItem
            v-for="action in adminActions"
            :key="action.key"
            class="operations-action-tile"
            interactive
            :title="action.title"
            @select="action.action"
          >
            <template #leading>
              <component :is="action.icon" :size="20" />
            </template>
            <template #trailing>
              <ChevronLeft :size="18" aria-hidden="true" />
            </template>
          </AppListItem>
        </AppInsetGroup>
      </WorkspaceSection>

      <AppEmptyState
        v-if="hasIdentity && !hasOperationActions"
        class="operations-empty-state"
        title="اقدام فعالی در این بخش ندارید"
        message="برای ادامه کارهای شخصی و تنظیمات، به حساب بروید."
        tone="info"
        role="status"
      >
        <template #actions>
          <AppButton type="button" @click="router.push({ name: 'account' })">
            رفتن به حساب
          </AppButton>
        </template>
      </AppEmptyState>
    </WorkspaceShell>
  </div>
</template>

<style scoped>
.operations-page {
  min-height: 100dvh;
}

.operations-action-group {
  margin: 0;
}

.operations-action-tile {
  font-family: inherit;
}

.operations-empty-state {
  margin: 0;
}

@media (max-width: 767px) {
  .operations-page :deep(.ds-workspace-main),
  .operations-page :deep(.ds-workspace-aside) {
    padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 1.5rem);
  }
}

</style>
