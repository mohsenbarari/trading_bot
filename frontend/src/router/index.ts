import { createRouter, createWebHistory } from 'vue-router'
import { authGuard } from '../utils/auth'

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
      component: () => import('../views/LoginView.vue')
    },
    {
      path: '/market',
      name: 'market',
      component: () => import('../views/MarketView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/chat',
      name: 'messenger',
      component: () => import('../views/MessengerView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/profile',
      name: 'profile',
      component: () => import('../views/ProfileView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/admin',
      name: 'admin',
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
      path: '/attachment-view',
      name: 'attachment-view',
      component: () => import('../views/AttachmentViewerView.vue'),
      meta: { requiresAuth: true, hideBottomNav: true }
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
