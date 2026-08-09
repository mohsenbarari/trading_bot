/**
 * Browser Notification Utility
 * Handles permission requests and displaying system-level notifications.
 */

import {
    sanitizeNotificationBody,
    sanitizeNotificationTitle,
    type BrowserNotificationClickDetail,
} from '../types/notifications'

export const BROWSER_NOTIFICATION_CLICK_EVENT = 'app-browser-notification-click'

type RoutedNotificationOptions = NotificationOptions & {
    route?: string
}

export const requestNotificationPermission = async (): Promise<boolean> => {
    if (!('Notification' in window)) {
        console.warn('Browser does not support notifications');
        return false;
    }

    if (Notification.permission === 'granted') return true;

    if (Notification.permission !== 'denied') {
        const permission = await Notification.requestPermission();
        return permission === 'granted';
    }

    return false;
};

export const showBrowserNotification = (title: string, body: string, options: RoutedNotificationOptions = {}): boolean => {
    if (!('Notification' in window) || Notification.permission !== 'granted') return false;

    const { route, ...notificationOptions } = options

    // Truncate body to 300 characters as requested by user
    const safeBody = sanitizeNotificationBody(body)
    const truncatedBody = safeBody.length > 300 ? safeBody.substring(0, 297) + '...' : safeBody;

    try {
        const notification = new Notification(sanitizeNotificationTitle(title), {
            ...notificationOptions,
            body: truncatedBody,
            icon: '/pwa-192x192.png',
            badge: '/pwa-192x192.png',
            vibrate: [200, 100, 200],
        } as any);

        notification.onclick = () => {
            window.focus();
            if (route) {
                window.dispatchEvent(new CustomEvent<BrowserNotificationClickDetail>(BROWSER_NOTIFICATION_CLICK_EVENT, {
                    detail: { route }
                }));
            }
            notification.close();
        };
        
        return true;
    } catch {
        return false;
    }
};
