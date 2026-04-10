import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { playNotificationSound } from '../utils/audio'


export const useNotificationStore = defineStore('notifications', () => {
    const chatUnreadCount = ref(0)
    const appNotifications = ref<any[]>([])
    const activeToasts = ref<any[]>([])
    const isLoadingHistory = ref(false)


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
        // Prevent exact duplicates if backend sends SSE and then we fetch or vice versa
        // Usually SSE comes in format: { id: ..., message: ..., level: ..., category: ... } or payload.content
        
        // Normalize for the UI
        const normalized = {
            ...notification,
            title: notification.title || (notification.category === 'system' ? 'پیام سیستم' : 'اعلان جدید'),
            body: notification.content || notification.message || notification.body,
            id: notification.id || Date.now() + Math.random() // Fallback ID
        }
        
        if (!appNotifications.value.some(n => n.id === normalized.id)) {
            appNotifications.value.unshift(normalized)
            // Keep only latest 100 for memory
            if (appNotifications.value.length > 100) {
                appNotifications.value.pop()
            }
        }
    }

    const fetchHistory = async () => {
        isLoadingHistory.value = true
        try {
            const res = await apiFetch('/api/notifications/')
            if (res.ok) {
                const data = await res.json()
                appNotifications.value = data.map((n: any) => ({
                    ...n,
                    title: n.category === 'system' ? 'پیام مدیریت' : 'اعلان جدید',
                    body: n.message
                }))
            }
        } catch (e) {
            console.error('Failed to fetch notifications history:', e)
        } finally {
            isLoadingHistory.value = false
        }
    }

    const markAllAsRead = async () => {
        try {
             await apiFetch('/api/notifications/mark-all-read', { method: 'POST' })
             appNotifications.value.forEach(n => n.is_read = true)
        } catch (e) {
             console.error('Failed to mark all as read:', e)
        }
    }

    const deleteNotification = async (id: number) => {
        // Optimistic delete
        const originalList = [...appNotifications.value]
        appNotifications.value = appNotifications.value.filter(n => n.id !== id)
        
        try {
            const res = await apiFetch(`/api/notifications/${id}`, { method: 'DELETE' })
            if (!res.ok) throw new Error()
        } catch (e) {
            appNotifications.value = originalList
            console.error('Delete failed:', e)
        }
    }

    const addToast = (title: string, body: string, route?: string) => {
        const id = Date.now() + Math.random()
        activeToasts.value.push({ id, title, body, route })
        
        // Play notification sound
        playNotificationSound()

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
        removeToast,
        isLoadingHistory,
        fetchHistory,
        markAllAsRead,
        deleteNotification
    }
})
