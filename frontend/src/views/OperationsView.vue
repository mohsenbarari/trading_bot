<script setup lang="ts">
import { computed, onMounted } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bell,
  BriefcaseBusiness,
  Megaphone,
  Package,
  Settings,
  UserPlus,
  Users,
  WalletCards,
} from 'lucide-vue-next'
import {
  WorkspaceActionTile,
  WorkspaceNotice,
  WorkspaceSection,
  WorkspaceShell,
  WorkspaceStatTile,
} from '../components/workspace'
import { currentUserSummary, isAdminRole, primeCurrentUserSummary } from '../utils/currentUser'

const router = useRouter()

interface OperationAction {
  key: string
  title: string
  description: string
  icon: Component
  action: () => void
  badge?: string
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
  hidden?: boolean
}

const user = computed(() => currentUserSummary.value)
const userRole = computed(() => user.value?.role || '')
const isAdmin = computed(() => isAdminRole(userRole.value))
const isSuperAdmin = computed(() => userRole.value === 'مدیر ارشد')
const isCustomer = computed(() => user.value?.is_customer === true)
const canUseOwnerRelations = computed(() => !isCustomer.value)

const ownerActions = computed<OperationAction[]>(() => {
  if (!canUseOwnerRelations.value) return []
  return [
    {
      key: 'customers',
      title: 'مشتریان',
      description: 'دعوت، مدیریت، محدودیت و گزارش مشتریان',
      icon: Users,
      badge: 'مسیر جدید',
      tone: 'primary',
      action: () => router.push({ name: 'operations-customers' }),
    },
    {
      key: 'accountants',
      title: 'حسابداران',
      description: 'دعوت، مدیریت نشست و تنظیمات حسابداران',
      icon: BriefcaseBusiness,
      badge: 'مسیر جدید',
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
      description: 'ساخت لینک دعوت برای کاربران مجاز',
      icon: UserPlus,
      action: () => router.push({ name: 'admin-invitations' }),
    },
    {
      key: 'manage_users',
      title: 'مدیریت کاربران',
      description: 'مشاهده، جستجو و تنظیم کاربران پروژه',
      icon: Users,
      action: () => router.push({ name: 'admin-users' }),
    },
  ]

  if (isSuperAdmin.value) {
    actions.push(
      {
        key: 'manage_commodities',
        title: 'مدیریت کالاها',
        description: 'تعریف کالا و aliasهای بازار',
        icon: Package,
        action: () => router.push({ name: 'admin-commodities' }),
      },
      {
        key: 'admin_messages',
        title: 'پیام‌های مدیریت',
        description: 'مدیریت پیام‌های سراسری و مدیریتی',
        icon: Megaphone,
        action: () => router.push({ name: 'admin-messages' }),
      },
      {
        key: 'settings',
        title: 'تنظیمات سیستم',
        description: 'تنظیمات حساس بازار و سیستم',
        icon: Settings,
        action: () => router.push({ name: 'admin-system' }),
      },
    )
  }

  return actions
})

const utilityActions = computed<OperationAction[]>(() => [
  {
    key: 'notifications',
    title: 'اعلان‌ها',
    description: 'مشاهده اعلان‌های سیستم و معاملات',
    icon: Bell,
    action: () => router.push({ name: 'notifications' }),
  },
  {
    key: 'admin_panel',
    title: 'پنل مدیریت',
    description: 'ورود به منوی کامل مدیریت',
    icon: WalletCards,
    action: () => router.push({ name: 'admin' }),
    hidden: !isAdmin.value,
  },
].filter(action => !action.hidden))

const relationsEmptyState = computed(() => {
  if (isCustomer.value) {
    return {
      title: 'این بخش برای حساب مشتری فعال نیست',
      description: 'مدیریت مشتریان و حسابداران از حساب سرگروه انجام می‌شود. دسترسی‌های معاملاتی شما از همان مسیر کنترل می‌شود.',
    }
  }

  return {
    title: 'رابطه کاری فعالی برای نمایش وجود ندارد',
    description: 'اگر دسترسی شما باید شامل مشتری یا حسابدار باشد، این بخش بعد از همگام‌سازی نقش حساب فعال می‌شود.',
  }
})

const managementEmptyState = computed(() => {
  if (!user.value) {
    return {
      title: 'در حال دریافت نقش کاربر',
      description: 'بعد از شناسایی نقش، ابزارهای مدیریتی مجاز نمایش داده می‌شوند.',
    }
  }

  return {
    title: 'دسترسی مدیریتی فعال نیست',
    description: 'ابزارهای دعوت‌نامه، مدیریت کاربران، کالاها و تنظیمات سیستم فقط برای مدیران مجاز نمایش داده می‌شوند.',
  }
})

const managementNote = computed(() => {
  if (!isAdmin.value) return ''
  if (isSuperAdmin.value) return 'دسترسی کامل مدیریتی'
  return 'دسترسی مدیر میانی؛ تنظیمات سیستم و پیام‌های مدیریت فقط برای مدیر ارشد است.'
})

const roleLabel = computed(() => {
  if (!user.value) return 'در حال دریافت'
  if (isSuperAdmin.value) return 'مدیر ارشد'
  if (isAdmin.value) return 'مدیر میانی'
  if (isCustomer.value) return 'مشتری'
  return 'کاربر عادی'
})

const relationAccessLabel = computed(() => (ownerActions.value.length ? `${ownerActions.value.length} مسیر` : 'غیرفعال'))
const adminAccessLabel = computed(() => (adminActions.value.length ? `${adminActions.value.length} ابزار` : 'ندارد'))
const shortcutAccessLabel = computed(() => `${utilityActions.value.length} میانبر`)

onMounted(() => {
  void primeCurrentUserSummary()
})
</script>

<template>
  <div class="ds-page operations-page">
    <WorkspaceShell
      title="عملیات"
      eyebrow="فضای کاری"
      description="دسترسی‌های اجرایی، رابطه‌ای و مدیریتی حساب شما در یک مسیر واحد قرار گرفته‌اند."
      layout="split"
      show-back
      @back="router.back()"
    >
      <template #actions>
        <button type="button" class="ds-btn secondary operations-header-action" @click="router.push({ name: 'notifications' })">
          <Bell :size="16" />
          اعلان‌ها
        </button>
      </template>

      <WorkspaceSection
        title="روابط کاری"
        description="مدیریت مشتریان و حسابداران از مسیرهای مستقل و آماده مهاجرت."
        tone="primary"
      >
          <div v-if="ownerActions.length" class="action-grid">
            <WorkspaceActionTile
              v-for="action in ownerActions"
              :key="action.key"
              class="operations-action-tile"
              :title="action.title"
              :description="action.description"
              :badge="action.badge"
              :tone="action.tone || 'neutral'"
              @select="action.action"
            >
              <template #icon>
                <component :is="action.icon" :size="20" />
              </template>
            </WorkspaceActionTile>
          </div>
          <WorkspaceNotice
            v-else
            tone="warning"
            :title="relationsEmptyState.title"
            :message="relationsEmptyState.description"
          />
      </WorkspaceSection>

      <WorkspaceSection
        title="مدیریت"
        :description="managementNote || 'ابزارهای مدیریتی فقط برای نقش‌های مجاز نمایش داده می‌شوند.'"
        :tone="isAdmin ? 'success' : 'neutral'"
      >
          <div v-if="adminActions.length" class="action-grid">
            <WorkspaceActionTile
              v-for="action in adminActions"
              :key="action.key"
              class="operations-action-tile"
              :title="action.title"
              :description="action.description"
              tone="success"
              @select="action.action"
            >
              <template #icon>
                <component :is="action.icon" :size="20" />
              </template>
            </WorkspaceActionTile>
          </div>
          <WorkspaceNotice
            v-else
            tone="info"
            :title="managementEmptyState.title"
            :message="managementEmptyState.description"
          />
      </WorkspaceSection>

      <WorkspaceSection
        title="میانبرها"
        description="مسیرهای سریع برای کارهای کم‌تکرار یا عمومی."
      >
        <div class="action-grid">
          <WorkspaceActionTile
            v-for="action in utilityActions"
            :key="action.key"
            class="operations-action-tile"
            :title="action.title"
            :description="action.description"
            @select="action.action"
          >
            <template #icon>
              <component :is="action.icon" :size="20" />
            </template>
          </WorkspaceActionTile>
        </div>
      </WorkspaceSection>

      <template #aside>
        <WorkspaceSection
          title="وضعیت دسترسی"
          description="خلاصه مسیرهایی که برای نقش فعلی شما فعال است."
        >
          <div class="operations-stat-grid">
            <WorkspaceStatTile label="نقش" :value="roleLabel" />
            <WorkspaceStatTile label="روابط کاری" :value="relationAccessLabel" tone="primary" />
            <WorkspaceStatTile label="مدیریت" :value="adminAccessLabel" :tone="isAdmin ? 'success' : 'neutral'" />
            <WorkspaceStatTile label="میانبرها" :value="shortcutAccessLabel" />
          </div>

          <WorkspaceNotice
            class="operations-aside-note"
            tone="info"
            title="مسیر مهاجرت"
            message="در این مرحله مسیرهای جدید آماده شده‌اند و هر کارت به مقصد سازگار فعلی هدایت می‌شود."
          />

          <button
            v-if="isAdmin"
            type="button"
            class="ds-btn secondary operations-admin-full"
            @click="router.push({ name: 'admin' })"
          >
            <WalletCards :size="16" />
            منوی کامل مدیریت
          </button>
        </WorkspaceSection>
      </template>
    </WorkspaceShell>
  </div>
</template>

<style scoped>
.operations-page {
  min-height: 100dvh;
  padding-bottom: 5rem;
}

.operations-header-action {
  min-width: 116px;
}

.action-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.65rem;
}

.operations-action-tile {
  font-family: inherit;
}

.operations-stat-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.65rem;
}

.operations-aside-note {
  margin-top: 0.75rem;
}

.operations-admin-full {
  width: 100%;
  margin-top: 0.75rem;
}

@media (min-width: 720px) {
  .action-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 520px) {
  .operations-stat-grid {
    grid-template-columns: 1fr;
  }
}
</style>
