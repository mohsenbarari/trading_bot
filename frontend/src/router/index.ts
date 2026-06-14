import { createRouter, createWebHistory } from 'vue-router'
import { authGuard } from '../utils/auth'
import LoginView from '../views/LoginView.vue'

const withQuery = (query: Record<string, unknown>, extra: Record<string, unknown>) => ({
  ...query,
  ...extra,
})

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('../views/DashboardView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/setup-password',
      name: 'setup-password',
      component: () => import('../views/SetupPassword.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView
    },
    {
      path: '/market',
      name: 'market',
      component: () => import('../views/MarketView.vue'),
      meta: { requiresAuth: true, requiresMarketAccess: true }
    },
    {
      path: '/operations',
      name: 'operations',
      component: () => import('../views/OperationsView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/operations/customers',
      name: 'operations-customers',
      component: () => import('../views/CustomerWorkspaceView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/operations/customers/:relationId',
      name: 'operations-customers-detail',
      component: () => import('../views/CustomerWorkspaceView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/operations/accountants',
      name: 'operations-accountants',
      component: () => import('../views/AccountantWorkspaceView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/operations/accountants/:relationId',
      name: 'operations-accountants-detail',
      component: () => import('../views/AccountantWorkspaceView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/account',
      name: 'account',
      component: () => import('../views/AccountHubView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/account/security',
      name: 'account-security',
      redirect: (to) => ({
        name: 'settings',
        query: withQuery(to.query, { section: 'sessions' }),
      }),
      meta: { requiresAuth: true }
    },
    {
      path: '/account/storage',
      name: 'account-storage',
      redirect: (to) => ({
        name: 'settings',
        query: withQuery(to.query, { section: 'storage' }),
      }),
      meta: { requiresAuth: true }
    },
    {
      path: '/account/notifications',
      name: 'account-notifications',
      redirect: (to) => ({
        name: 'notifications',
        query: to.query,
      }),
      meta: { requiresAuth: true }
    },
    {
      path: '/chat',
      name: 'messenger',
      component: () => import('../views/MessengerView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/users/:id',
      name: 'public-profile',
      component: () => import('../views/PublicProfileView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/profile',
      name: 'profile',
      component: () => import('../views/ProfileView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('../views/SettingsView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('../views/AdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/admin/invitations',
      name: 'admin-invitations',
      component: () => import('../views/AdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/admin/channels',
      name: 'admin-channels',
      component: () => import('../views/AdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/admin/users',
      name: 'admin-users',
      component: () => import('../views/AdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/admin/users/:id',
      name: 'admin-user-profile',
      component: () => import('../views/AdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/admin/commodities',
      name: 'admin-commodities',
      component: () => import('../views/AdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/admin/messages',
      name: 'admin-messages',
      component: () => import('../views/AdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/admin/system',
      name: 'admin-system',
      component: () => import('../views/AdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true }
    },
    {
      path: '/i/:code',
      name: 'invite-landing',
      component: () => import('../views/InviteLanding.vue')
    },
    {
      path: '/register',
      name: 'web-register',
      component: () => import('../views/WebRegister.vue')
    },
    {
      path: '/notifications',
      name: 'notifications',
      component: () => import('../views/NotificationsView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/share-receive',
      name: 'share-receive',
      component: () => import('../views/ShareReceiveView.vue'),
      meta: { requiresAuth: true }
    }
  ]
})

router.beforeEach(authGuard)

// Handle dynamic module load errors (e.g. after a new version is built)
router.onError((error, to) => {
  const isChunkLoadFailed = error.message.includes('Failed to fetch dynamically imported module') || 
                            error.message.includes('Importing a module script failed') ||
                            error.name === 'ChunkLoadError'

  if (isChunkLoadFailed) {
    console.warn('Chunk load failed in router, forcing a hard reload for:', to.fullPath)
    window.location.href = to.fullPath
  }
})

export default router
