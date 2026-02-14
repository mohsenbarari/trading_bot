import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import LoginView from '../views/LoginView.vue'
import { isTokenExpired, refreshOrLogout } from '../utils/auth'

const router = createRouter({
    history: createWebHistory(import.meta.env.BASE_URL),
    routes: [
        {
            path: '/',
            name: 'dashboard',
            component: DashboardView,
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
            meta: { requiresAuth: true }
        }
    ]
})

// Navigation Guard — بررسی اعتبار توکن (نه فقط وجود آن)
router.beforeEach(async (to, _from, next) => {
    if (!to.meta.requiresAuth) {
        next()
        return
    }

    const token = localStorage.getItem('auth_token')

    // توکن وجود نداره
    if (!token) {
        next({ name: 'login' })
        return
    }

    // توکن منقضی شده → تلاش برای refresh
    if (isTokenExpired(token)) {
        const refreshed = await refreshOrLogout()
        if (!refreshed) {
            next({ name: 'login' })
            return
        }
    }

    next()
})

export default router
