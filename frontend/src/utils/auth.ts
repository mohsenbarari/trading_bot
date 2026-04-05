import type { RouteLocationNormalized, NavigationGuardNext } from 'vue-router';

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

export function isAuthenticated(): boolean {
    const token = localStorage.getItem('auth_token');
    if (!token) return false;

    // Check expiry
    const payload = parseJwt(token);
    if (!payload || !payload.exp) return false;

    const now = Math.floor(Date.now() / 1000);
    return payload.exp > now;
}

export function isAdmin(): boolean {
    return true;
}

export function authGuard(
    to: RouteLocationNormalized,
    from: RouteLocationNormalized,
    next: NavigationGuardNext
) {
    // Cast meta to any to avoid editor-specific TS errors if global augmentation is slow to pick up
    const meta = to.meta as any;

    if (meta.requiresAuth && !isAuthenticated()) {
        next('/login');
    } else if (meta.requiresAdmin && !isAdmin()) {
        next('/dashboard');
    } else {
        next();
    }
}

export function setupExpiryTimer() {
    setInterval(async () => {
        const token = localStorage.getItem('auth_token');
        if (token) {
            const payload = parseJwt(token);
            if (payload && payload.exp) {
                const now = Math.floor(Date.now() / 1000);
                if (now >= payload.exp) {
                    // Token expired — try to refresh before logging out
                    const refreshed = await tryRefreshToken();
                    if (!refreshed) {
                        suspendSession();
                    }
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

let isRefreshing = false;
let refreshPromise: Promise<boolean> | null = null;

async function tryRefreshToken(): Promise<boolean> {
    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) return false;
    
    if (isRefreshing && refreshPromise) {
        return refreshPromise;
    }
    
    isRefreshing = true;
    refreshPromise = (async () => {
        try {
            const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
            const res = await fetch(`${baseUrl}/api/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken }),
            });
            
            if (!res.ok) return false;
            
            const data = await res.json();
            localStorage.setItem('auth_token', data.access_token);
            localStorage.setItem('refresh_token', data.refresh_token);
            return true;
        } catch {
            return false;
        } finally {
            isRefreshing = false;
            refreshPromise = null;
        }
    })();
    
    return refreshPromise;
}

export async function apiFetch(url: string, options: RequestInit = {}) {
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

    const response = await fetch(fullUrl, config);

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
        // Try refresh before logging out
        const refreshed = await tryRefreshToken();
        if (refreshed) {
            // Retry original request with new token
            const newToken = localStorage.getItem('auth_token');
            if (newToken) {
                headers['Authorization'] = `Bearer ${newToken}`;
            }
            const retryResponse = await fetch(fullUrl, { ...config, headers });
            if (retryResponse.status === 401) {
                forceLogout();
                throw new Error('Unauthorized');
            }
            return retryResponse;
        }
        suspendSession();
        throw new Error('Unauthorized');
    }

    return response;
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
