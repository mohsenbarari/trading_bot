import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch } from '../utils/auth'

export const useNotificationStore = defineStore('notifications', () => {
    const chatUnreadCount = ref(0)
    const appNotifications = ref<any[]>([])

    const fetchInitialCounts = async () => {
        const token = localStorage.getItem('auth_token')
        if (!token) return

        try {
            // Get unread counts from chat poll endpoint
            const response = await apiFetch('/api/chat/poll')
            if (response.ok) {
                const data = await response.json()
                chatUnreadCount.value = data.total_unread || 0
            }
        } catch (error) {
            console.error('Failed to fetch initial notification counts:', error)
        }
    }

    const setChatUnreadCount = (count: number) => {
        chatUnreadCount.value = count
    }

    const incrementChatUnread = () => {
        chatUnreadCount.value++
    }

    const addAppNotification = (notification: any) => {
        appNotifications.value.unshift(notification)
        // Keep only last 50
        if (appNotifications.value.length > 50) {
            appNotifications.value.pop()
        }
    }

    return {
        chatUnreadCount,
        appNotifications,
        fetchInitialCounts,
        setChatUnreadCount,
        incrementChatUnread,
        addAppNotification
    }
})
