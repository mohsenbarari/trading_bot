import { createRouter, createWebHistory } from 'vue-router'
import { authGuard } from '../utils/auth'
import LoginView from '../views/LoginView.vue'
import SystemRecoveryView from '../views/SystemRecoveryView.vue'
import { decideChunkReload } from './chunkRecovery'
import { getUiRouteContractByName } from './uiRouteContract'
import {
  createSystemRecoveryLocation,
  SYSTEM_RECOVERY_FALLBACK_HREF,
  SYSTEM_RECOVERY_OUTCOME,
} from './systemRecovery'

function routeMeta(name: string, access: Record<string, boolean> = {}) {
  const contract = getUiRouteContractByName(name)
  if (!contract) throw new Error(`Missing UI route contract for ${name}`)

  return {
    ...access,
    uiShellClass: contract.shellClass,
    uiV2Scope: contract.v2Scope,
    uiRouteTestId: contract.testId,
  }
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('../views/DashboardView.vue'),
      meta: routeMeta('home', { requiresAuth: true }),
    },
    {
      path: '/setup-password',
      name: 'setup-password',
      component: () => import('../views/SetupPassword.vue'),
      meta: routeMeta('setup-password', { requiresAuth: true }),
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: routeMeta('login'),
    },
    {
      path: '/market',
      name: 'market',
      component: () => import('../views/MarketView.vue'),
      meta: routeMeta('market', { requiresAuth: true, requiresMarketAccess: true }),
    },
    {
      path: '/operations',
      name: 'operations',
      component: () => import('../views/OperationsView.vue'),
      meta: routeMeta('operations', { requiresAuth: true }),
    },
    {
      path: '/operations/customers',
      name: 'operations-customers',
      component: () => import('../views/CustomerWorkspaceView.vue'),
      meta: routeMeta('operations-customers', { requiresAuth: true, requiresOwnerAccess: true }),
    },
    {
      path: '/operations/customers/:relationId',
      name: 'operations-customers-detail',
      component: () => import('../views/CustomerWorkspaceView.vue'),
      meta: routeMeta('operations-customers-detail', {
        requiresAuth: true,
        requiresOwnerAccess: true,
      }),
    },
    {
      path: '/operations/accountants',
      name: 'operations-accountants',
      component: () => import('../views/AccountantWorkspaceView.vue'),
      meta: routeMeta('operations-accountants', { requiresAuth: true, requiresOwnerAccess: true }),
    },
    {
      path: '/operations/accountants/:relationId',
      name: 'operations-accountants-detail',
      component: () => import('../views/AccountantWorkspaceView.vue'),
      meta: routeMeta('operations-accountants-detail', {
        requiresAuth: true,
        requiresOwnerAccess: true,
      }),
    },
    {
      path: '/account',
      name: 'account',
      component: () => import('../views/AccountHubView.vue'),
      meta: routeMeta('account', { requiresAuth: true }),
    },
    {
      path: '/account/security',
      name: 'account-security',
      component: () => import('../views/SettingsView.vue'),
      meta: routeMeta('account-security', { requiresAuth: true }),
    },
    {
      path: '/account/storage',
      name: 'account-storage',
      component: () => import('../views/SettingsView.vue'),
      meta: routeMeta('account-storage', { requiresAuth: true }),
    },
    {
      path: '/account/notifications',
      name: 'account-notifications',
      component: () => import('../views/NotificationsView.vue'),
      meta: routeMeta('account-notifications', { requiresAuth: true }),
    },
    {
      path: '/chat',
      name: 'messenger',
      component: () => import('../views/MessengerView.vue'),
      meta: routeMeta('messenger', { requiresAuth: true }),
    },
    {
      path: '/users/:id',
      name: 'public-profile',
      component: () => import('../views/PublicProfileView.vue'),
      meta: routeMeta('public-profile', { requiresAuth: true }),
    },
    {
      path: '/profile',
      name: 'profile',
      component: () => import('../views/ProfileView.vue'),
      meta: routeMeta('profile', { requiresAuth: true }),
    },
    {
      path: '/settings',
      name: 'settings',
      redirect: { name: 'account-security', query: {}, hash: '' },
      meta: routeMeta('settings', { requiresAuth: true }),
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('../views/AdminView.vue'),
      meta: routeMeta('admin', { requiresAuth: true, requiresAdmin: true }),
    },
    {
      path: '/admin/invitations',
      name: 'admin-invitations',
      component: () => import('../views/AdminView.vue'),
      meta: routeMeta('admin-invitations', { requiresAuth: true, requiresAdmin: true }),
    },
    {
      path: '/admin/channels',
      name: 'admin-channels',
      component: () => import('../views/AdminView.vue'),
      meta: routeMeta('admin-channels', { requiresAuth: true, requiresAdmin: true }),
    },
    {
      path: '/admin/users',
      name: 'admin-users',
      component: () => import('../views/AdminView.vue'),
      meta: routeMeta('admin-users', { requiresAuth: true, requiresAdmin: true }),
    },
    {
      path: '/admin/users/:id',
      name: 'admin-user-profile',
      component: () => import('../views/AdminView.vue'),
      meta: routeMeta('admin-user-profile', { requiresAuth: true, requiresAdmin: true }),
    },
    {
      path: '/admin/commodities',
      name: 'admin-commodities',
      component: () => import('../views/AdminView.vue'),
      meta: routeMeta('admin-commodities', { requiresAuth: true, requiresAdmin: true }),
    },
    {
      path: '/admin/messages',
      name: 'admin-messages',
      component: () => import('../views/AdminView.vue'),
      meta: routeMeta('admin-messages', { requiresAuth: true, requiresAdmin: true }),
    },
    {
      path: '/admin/system',
      name: 'admin-system',
      component: () => import('../views/AdminView.vue'),
      meta: routeMeta('admin-system', { requiresAuth: true, requiresAdmin: true }),
    },
    {
      path: '/i/:code',
      name: 'invite-landing',
      component: () => import('../views/InviteLanding.vue'),
      meta: routeMeta('invite-landing'),
    },
    {
      path: '/register',
      name: 'web-register',
      component: () => import('../views/WebRegister.vue'),
      meta: routeMeta('web-register'),
    },
    {
      path: '/notifications',
      name: 'notifications',
      redirect: { name: 'account-notifications', query: {}, hash: '' },
      meta: routeMeta('notifications', { requiresAuth: true }),
    },
    {
      path: '/share-receive',
      name: 'share-receive',
      component: () => import('../views/ShareReceiveView.vue'),
      meta: routeMeta('share-receive', { requiresAuth: true }),
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'system-recovery',
      component: SystemRecoveryView,
      meta: routeMeta('system-recovery'),
    },
  ],
})

router.beforeEach(authGuard)

// Handle dynamic module load errors (e.g. after a new version is built)
router.onError((error, to) => {
  const isChunkLoadFailed =
    error.message.includes('Failed to fetch dynamically imported module') ||
    error.message.includes('Importing a module script failed') ||
    error.name === 'ChunkLoadError'

  if (isChunkLoadFailed) {
    const decision = decideChunkReload(to.path)

    if (decision.kind === 'reload') {
      console.warn('Chunk load failed; attempting one bounded hard reload')
      window.location.replace(decision.path)
      return
    }

    console.warn('Chunk load failed after the bounded retry; opening system recovery')
    void router
      .replace(createSystemRecoveryLocation(SYSTEM_RECOVERY_OUTCOME.DEEP_LINK_FAILURE))
      .catch(() => {
        window.location.replace(SYSTEM_RECOVERY_FALLBACK_HREF)
      })
  }
})

export default router
