import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch } from '../utils/auth'

export const useNotificationStore = defineStore('notifications', () => {
    const chatUnreadCount = ref(0)
    const appNotifications = ref<any[]>([])
    const activeToasts = ref<any[]>([])

    const fetchInitialCounts = async () => {
        const token = localStorage.getItem('auth_token')
        if (!token) return

        try {
            // Get unread counts from chat poll endpoint
            const response = await apiFetch('/api/chat/poll')
            if (response.ok) {
                const data = await response.json()
                // Change: show unread CHATS instead of total messages
                chatUnreadCount.value = data.unread_chats_count || 0
            }
        } catch (error) {
            console.error('Failed to fetch initial notification counts:', error)
        }
    }

    const setChatUnreadCount = (count: number) => {
        chatUnreadCount.value = count
    }

    const incrementChatUnread = () => {
        // Instead of blind increment, re-fetch from server to be accurate on chat counts
        fetchInitialCounts()
    }

    const addAppNotification = (notification: any) => {
        appNotifications.value.unshift(notification)
        // Keep only last 50
        if (appNotifications.value.length > 50) {
            appNotifications.value.pop()
        }
    }

    const addToast = (title: string, body: string, route?: string) => {
        const id = Date.now() + Math.random()
        activeToasts.value.push({ id, title, body, route })
        // Auto remove after 5 seconds
        setTimeout(() => {
            removeToast(id)
        }, 5000)
    }

    const removeToast = (id: number) => {
        activeToasts.value = activeToasts.value.filter(t => t.id !== id)
    }

    return {
        chatUnreadCount,
        appNotifications,
        activeToasts,
        fetchInitialCounts,
        setChatUnreadCount,
        incrementChatUnread,
        addAppNotification,
        addToast,
        removeToast
    }
})
