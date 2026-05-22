import { ref } from 'vue';
import type { RouteLocationNormalized, NavigationGuardNext } from 'vue-router';
import { isAdminRoleValue, readCachedCurrentUserRole } from './adminAccess';
import { cacheCurrentUserSummary } from './currentUser';

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

        suspendSession();
        return false;
    }
    return false;
}

function cacheCurrentUserSummaryFromAuthMe(payload: any) {
    cacheCurrentUserSummary(payload);
}

function readCachedCurrentUserAccountStatus(): string | null {
    try {
        const raw = JSON.parse(localStorage.getItem('current_user_summary') || '{}');
        return typeof raw.account_status === 'string' ? raw.account_status : null;
    } catch {
        return null;
    }
}

function isInactiveAccountStatus(status: string | null | undefined): boolean {
    return status === 'inactive';
}

export function isAdmin(): boolean {
    return isAdminRoleValue(readCachedCurrentUserRole());
}

async function ensureAdminAccess(): Promise<boolean> {
    const cachedRole = readCachedCurrentUserRole();
    if (cachedRole) {
        return isAdminRoleValue(cachedRole);
    }

    try {
        const response = await apiFetch('/api/auth/me');
        if (!response.ok) {
            return false;
        }

        const data = await response.json();
        cacheCurrentUserSummaryFromAuthMe(data);
        return isAdminRoleValue(data?.role);
    } catch {
        return false;
    }
}

async function ensureMarketAccess(): Promise<boolean> {
    const cachedStatus = readCachedCurrentUserAccountStatus();
    if (cachedStatus) {
        return !isInactiveAccountStatus(cachedStatus);
    }

    try {
        const response = await apiFetch('/api/auth/me');
        if (!response.ok) {
            return true;
        }

        const data = await response.json();
        cacheCurrentUserSummaryFromAuthMe(data);
        return !isInactiveAccountStatus(data?.account_status);
    } catch {
        return true;
    }
}

export async function authGuard(
    to: RouteLocationNormalized,
    from: RouteLocationNormalized,
    next: NavigationGuardNext
) {
    // Cast meta to any to avoid editor-specific TS errors if global augmentation is slow to pick up
    const meta = to.meta as any;

    if (to.path === '/login') {
        const isAuth = await isAuthenticated();
        if (isAuth) {
            return next('/');
        }
    }

    if (meta.requiresAuth) {
        const isAuth = await isAuthenticated();
        if (!isAuth) {
            return next('/login');
        }
    } 
    if (meta.requiresMarketAccess && !(await ensureMarketAccess())) {
        return next('/');
    }
    if (meta.requiresAdmin && !(await ensureAdminAccess())) {
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
                    if (result !== 'success') {
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
            const originalResponse = await fetch(fullUrl, config);
            
            // If we were connecting/retrying, we reconnected successfully
            if (isAppConnecting.value) isAppConnecting.value = false;

            // Proxy the response to intercept json() and clone()
            const response = new Proxy(originalResponse, {
                get(target: any, prop: string) {
                    if (prop === 'json') {
                        return async () => {
                            const data = await target.json();
                            return cleanDeletedSuffixes(data);
                        };
                    }
                    if (prop === 'clone') {
                        return () => {
                            const cloned = target.clone();
                            return new Proxy(cloned, this); // apply the same handler to the clone
                        };
                    }
                    const value = target[prop];
                    return typeof value === 'function' ? value.bind(target) : value;
                }
            });

            // 🔴 403 Forbidden with specific detail
            if (isAppConnecting.value) isAppConnecting.value = false;

            // 🔴 403 Forbidden with specific detail
            if (response.status === 403) {
                const clone = response.clone();
                let errorData: any = null;
                try {
                    errorData = await clone.json();
                } catch (e) {
                    // Ignore parsing errors for other 403s
                }

                if (errorData?.detail === 'REQUIRES_PASSWORD_CHANGE') {
                    if (window.location.pathname !== '/setup-password') {
                        window.location.href = '/setup-password';
                    }
                    throw new Error('شما باید رمز عبور خود را تغییر دهید');
                }
                if (errorData?.detail === 'حساب کاربری غیرفعال شده است' || errorData?.detail === 'User is blocked') {
                    forceLogout();
                    throw new Error('حساب کاربری شما غیرفعال شده است');
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
                
                    suspendSession();
                    throw new Error('نشست شما منقضی شده است. لطفا مجددا وارد شوید');
            }

            if (response.status >= 500) {
                throw new Error('NetworkError'); // Trigger auto-reconnect
            }

            return response;
        } catch (error: any) {
            // Is this a network fetch drop?
            if (
                error.name === 'TypeError' || 
                error.message?.includes('Failed to fetch') || 
                error.message?.toLowerCase().includes('network') ||
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

import { cleanDeletedSuffixes } from './formatters';

export async function apiFetchJson(url: string, options: RequestInit = {}) {
    const response = await apiFetch(url, options);
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `خطا: ${response.status}`);
    }
    if (response.status === 204) return null;
    return response.json();
}
