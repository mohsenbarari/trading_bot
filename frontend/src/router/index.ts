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
        }
    ]
})

// Navigation Guard Stub (will implement real auth check later)
router.beforeEach((to, from, next) => {
    // const isAuthenticated = checkAuth() 
    // if (to.meta.requiresAuth && !isAuthenticated) next({ name: 'login' })
    // else next()
    next()
})

export default router
