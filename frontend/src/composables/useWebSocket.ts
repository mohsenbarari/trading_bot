import { ref } from 'vue';

import { cleanDeletedSuffixes } from '../utils/formatters';

const isConnected = ref(false);
let socket: WebSocket | null = null;
let reconnectInterval: any = null;
let heartbeatInterval: any = null;

// Event callbacks
const eventListeners: Record<string, ((data: any) => void)[]> = {};

export function useWebSocket() {
    const connect = () => {
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

        // Get auth token for authenticated WebSocket connection
        const authToken = localStorage.getItem('auth_token');
        if (!authToken) {
            console.warn('⚠️ No auth token found, skipping WebSocket connection');
            return;
        }

        const wsUrl = `${protocol}//${window.location.host}/api/realtime/ws?token=${encodeURIComponent(authToken)}`;

        console.log('Connecting to WebSocket:', wsUrl);
        socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            console.log('✅ WebSocket Connected');
            isConnected.value = true;
            startHeartbeat();
            if (reconnectInterval) {
                clearInterval(reconnectInterval);
                reconnectInterval = null;
            }
            
            // Notify local listeners that WS is connected/reconnected
            if (eventListeners['ws:reconnect']) {
                eventListeners['ws:reconnect'].forEach((callback) => callback({}));
            }
        };

        socket.onmessage = (event) => {
            try {
                if (event.data === 'pong') return;

                let message = JSON.parse(event.data);
                message = cleanDeletedSuffixes(message);
                if (message.type === 'heartbeat') return;

                // Dispatch to listeners
                const listeners = eventListeners[message.type];
                if (listeners) {
                    listeners.forEach((callback) => callback(message.data));
                } else if (eventListeners['*']) {
                    eventListeners['*'].forEach((callback) => callback(message));
                }
            } catch (e) {
                console.error('Error parsing WS message:', e);
            }
        };

        socket.onclose = () => {
            console.log('❌ WebSocket Disconnected');
            isConnected.value = false;
            stopHeartbeat();
            if (!reconnectInterval) {
                reconnectInterval = setInterval(connect, 3000); // Try to reconnect every 3s
            }
        };

        socket.onerror = (err) => {
            console.error('WebSocket Error:', err);
            socket?.close();
        };
    };

    const disconnect = () => {
        if (socket) {
            socket.close();
            socket = null;
        }
        stopHeartbeat();
        if (reconnectInterval) {
            clearInterval(reconnectInterval);
            reconnectInterval = null;
        }
    };

    const startHeartbeat = () => {
        stopHeartbeat();
        heartbeatInterval = setInterval(() => {
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send('ping');
            }
        }, 25000); // 25s heartbeat (server timeout is usually 30s)
    };

    const stopHeartbeat = () => {
        if (heartbeatInterval) {
            clearInterval(heartbeatInterval);
            heartbeatInterval = null;
        }
    };

    const sendJson = (message: Record<string, unknown>) => {
        if (!socket || socket.readyState !== WebSocket.OPEN) return false;
        socket.send(JSON.stringify(message));
        return true;
    };

    const sendPresenceUpdate = (path: string, visible: boolean) => {
        return sendJson({
            type: 'presence:update',
            data: {
                path,
                visible,
            },
        });
    };

    const on = (event: string, callback: (data: any) => void) => {
        if (!eventListeners[event]) {
            eventListeners[event] = [];
        }
        eventListeners[event].push(callback);
    };

    const off = (event: string, callback: (data: any) => void) => {
        if (!eventListeners[event]) return;
        eventListeners[event] = eventListeners[event].filter(cb => cb !== callback);
    };

    return {
        isConnected,
        connect,
        disconnect,
        sendJson,
        sendPresenceUpdate,
        on,
        off
    };
}
