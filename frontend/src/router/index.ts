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
    }
  ]
})

router.beforeEach(authGuard)

export default router
