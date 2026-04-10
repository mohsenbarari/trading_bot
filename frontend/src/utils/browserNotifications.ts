/**
 * Browser Notification Utility
 * Handles permission requests and displaying system-level notifications.
 */

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

export const showBrowserNotification = (title: string, body: string, options: NotificationOptions = {}): boolean => {
    if (!('Notification' in window) || Notification.permission !== 'granted') return false;

    // Truncate body to 300 characters as requested by user
    const truncatedBody = body.length > 300 ? body.substring(0, 297) + '...' : body;

    try {
        const notification = new Notification(title, {
            body: truncatedBody,
            icon: '/pwa-192x192.png', // Default icon from PWA manifest
            vibrate: [200, 100, 200], // Vibration pattern for supported devices
            ...options
        } as any);

        notification.onclick = () => {
            window.focus();
            notification.close();
            // Custom click handlers can be added if needed
        };
        
        return true;
    } catch (err) {
        console.error('Failed to show notification:', err);
        return false;
    }
};
