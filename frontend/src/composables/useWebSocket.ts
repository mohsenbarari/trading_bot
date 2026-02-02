import { ref } from 'vue';

const isConnected = ref(false);
let socket: WebSocket | null = null;
let reconnectInterval: any = null;
let heartbeatInterval: any = null;

// Event callbacks
const eventListeners: Record<string, ((data: any) => void)[]> = {};

export function useWebSocket() {
    const connect = () => {
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;

        // If running in dev (port 5173), connect directly to backend (8000) to bypass Vite proxy issues
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

        // If running in dev (port 5173), connect directly to backend (8000) to bypass Vite proxy issues
        const isDev = window.location.port === '5173';

        // In dev, we force ws://localhost:8000
        // In prod, we use relative (which behaves as window.location.host)

        let wsUrl = '';
        if (isDev) {
            wsUrl = `${protocol}//${window.location.hostname}:8000/api/realtime/ws`;
        } else {
            wsUrl = `${protocol}//${window.location.host}/api/realtime/ws`;
        }

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
        };

        socket.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
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
        on,
        off
    };
}
