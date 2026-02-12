import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import LoginView from '../views/LoginView.vue'

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

// Navigation Guard — redirect to login if not authenticated
router.beforeEach((to, _from, next) => {
    const token = localStorage.getItem('auth_token')
    if (to.meta.requiresAuth && !token) {
        next({ name: 'login' })
    } else {
        next()
    }
})

export default router
