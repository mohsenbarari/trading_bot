import { ref } from 'vue';
import type { RouteLocationNormalized, NavigationGuardNext } from 'vue-router';

export const isAppConnecting = ref(false);
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// Helper to decode JWT payload (without validation)
function parseJwt(token: string) {
    try {
        const base64Url = token.split('.')[1];
        if (!base64Url) return null;
        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(window.atob(base64).split('').map(function (c) {
            return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        return JSON.parse(jsonPayload);
    } catch (e) {
        return null;
    }
}

export type RefreshResult = 'success' | 'network_error' | 'auth_error';

let isRefreshing = false;
let refreshPromise: Promise<RefreshResult> | null = null;

export async function tryRefreshToken(): Promise<RefreshResult> {
    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) return 'auth_error';
    
    if (isRefreshing && refreshPromise) {
        return refreshPromise;
    }
    
    isRefreshing = true;
    refreshPromise = (async (): Promise<RefreshResult> => {
        try {
            const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
            const res = await fetch(`${baseUrl}/api/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken }),
            });
            
            if (res.ok) {
                const data = await res.json();
                localStorage.setItem('auth_token', data.access_token);
                localStorage.setItem('refresh_token', data.refresh_token);
                return 'success';
            }
            
            // Explicit rejection from the server
            if (res.status === 401 || res.status === 403 || res.status === 404) {
                return 'auth_error';
            }
            
            // 5xx Server Error or 429 Rate Limit (treat as temporary network issue)
            return 'network_error';
        } catch {
            // Failed to connect to the server (offline, connection drop)
            return 'network_error';
        } finally {
            isRefreshing = false;
            refreshPromise = null;
        }
    })();
    
    return refreshPromise;
}

export async function isAuthenticated(): Promise<boolean> {
    const token = localStorage.getItem('auth_token');
    const refresh = localStorage.getItem('refresh_token');
    if (!token && !refresh) return false;

    if (token) {
        const payload = parseJwt(token);
        if (payload && payload.exp) {
            const now = Math.floor(Date.now() / 1000);
            if (payload.exp > now + 10) {
                return true; // Still valid
            }
        }
    }
    
    // Token expired but we have refresh token - try refreshing
    if (refresh) {
        const result = await tryRefreshToken();
        if (result === 'success') return true;
        
        // Prevent booting offline users to the login screen
        if (result === 'network_error') return true;
        
        // Only boot to login if the backend explicitly rejected the token
        return false;
    }
    return false;
}

export function isAdmin(): boolean {
    return true;
}

export async function authGuard(
    to: RouteLocationNormalized,
    from: RouteLocationNormalized,
    next: NavigationGuardNext
) {
    // Cast meta to any to avoid editor-specific TS errors if global augmentation is slow to pick up
    const meta = to.meta as any;

    if (meta.requiresAuth) {
        const isAuth = await isAuthenticated();
        if (!isAuth) {
            return next('/login');
        }
    } 
    if (meta.requiresAdmin && !isAdmin()) {
        return next('/dashboard');
    }
    next();
}

export function setupExpiryTimer() {
    setInterval(async () => {
        const token = localStorage.getItem('auth_token');
        if (token) {
            const payload = parseJwt(token);
            if (payload && payload.exp) {
                const now = Math.floor(Date.now() / 1000);
                // Attempt refresh 60 seconds before it actually expires
                if (now >= payload.exp - 60) {
                    const result = await tryRefreshToken();
                    if (result === 'auth_error') {
                        // The server explicitly invalidated the refresh token
                        suspendSession();
                    }
                    // For 'network_error', we do nothing and let the fetch retry automatically on the next interval
                }
            }
        }
    }, 30000);
}

export function logout() {
    forceLogout();
}

export function suspendSession() {
    const refreshToken = localStorage.getItem('refresh_token');
    if (refreshToken) {
        localStorage.setItem('suspended_refresh_token', refreshToken);
    }
    localStorage.removeItem('auth_token');
    localStorage.removeItem('refresh_token');
    window.location.href = '/login';
}

export function forceLogout() {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('suspended_refresh_token');
    window.location.href = '/login';
}

export async function apiFetch(url: string, options: RequestInit = {}) {
    let retries = 0;
    let didRefresh = false;

    while (true) {
        const token = localStorage.getItem('auth_token');

        const headers = {
            'Content-Type': 'application/json',
            ...(options.headers || {}),
        } as any;

        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        const config = {
            ...options,
            headers
        };

        const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
        const fullUrl = url.startsWith('http') ? url : `${baseUrl}${url}`;

        try {
            const response = await fetch(fullUrl, config);
            
            // If we were connecting/retrying, we reconnected successfully
            if (isAppConnecting.value) isAppConnecting.value = false;

            // 🔴 403 Forbidden with specific detail
            if (response.status === 403) {
                const clone = response.clone();
                try {
                    const errorData = await clone.json();
                    if (errorData?.detail === 'REQUIRES_PASSWORD_CHANGE') {
                        if (window.location.pathname !== '/setup-password') {
                            window.location.href = '/setup-password';
                        }
                        throw new Error('شما باید رمز عبور خود را تغییر دهید');
                    }
                } catch (e) {
                    // Ignore parsing errors for other 403s
                }
            }

            if (response.status === 401) {
                if (didRefresh) {
                     forceLogout();
                     throw new Error('Unauthorized');
                }

                // Try refresh before logging out
                const result = await tryRefreshToken();
                
                if (result === 'success') {
                    didRefresh = true;
                    // Start next iteration of the while-loop to retry original request with new token
                    continue;
                }
                
                // If specifically failed auth, boot to OTP screen
                if (result === 'auth_error') {
                    suspendSession();
                    throw new Error('نشست شما منقضی شده است. لطفا مجددا وارد شوید');
                }
                
                // Treat 'network_error' as a connection drop, loop again
                throw new Error('NetworkError');
            }

            return response;
        } catch (error: any) {
            // Is this a network fetch drop?
            if (
                error.name === 'TypeError' || 
                error.message === 'Failed to fetch' || 
                error.message === 'NetworkError' ||
                error.message === 'خطا در ارتباط با سرور.' ||
                error.message?.includes('fetch dynamically imported module') ||
                error.message?.includes('Load failed')
            ) {
                isAppConnecting.value = true;
                retries++;
                console.warn(`[apiFetch] Connection lost. Retrying (${retries})...`);
                await sleep(Math.min(3000, 1000 * Math.pow(1.5, retries))); // Max 3s backoff
                continue;
            }
            throw error; // Bubble up real app errors (400, validation, etc)
        }
    }
}

export async function apiFetchJson(url: string, options: RequestInit = {}) {
    const response = await apiFetch(url, options);
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `خطا: ${response.status}`);
    }
    if (response.status === 204) return null;
    return response.json();
}
