import { RouteLocationNormalized, NavigationGuardNext } from 'vue-router';

// Helper to decode JWT payload (without validation)
function parseJwt(token: string) {
    try {
        const base64Url = token.split('.')[1];
        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(window.atob(base64).split('').map(function(c) {
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
  // Client-side check only
  return true; 
}

export function authGuard(
  to: RouteLocationNormalized,
  from: RouteLocationNormalized,
  next: NavigationGuardNext
) {
  if (to.meta.requiresAuth && !isAuthenticated()) {
    next('/login');
  } else if (to.meta.requiresAdmin && !isAdmin()) {
    next('/dashboard');
  } else {
    next();
  }
}

export function setupExpiryTimer() {
    setInterval(() => {
        const token = localStorage.getItem('auth_token');
        if (token) {
            const payload = parseJwt(token);
            if (payload && payload.exp) {
                const now = Math.floor(Date.now() / 1000);
                if (now >= payload.exp) {
                    forceLogout();
                }
            }
        }
    }, 30000);
}

export function logout() {
    forceLogout();
}

export function forceLogout() {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('refresh_token');
    window.location.href = '/login';
}

// Wrapper for fetch that adds Authorization header
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
    
    if (response.status === 401) {
        // Unauthorized - try refresh or logout
        forceLogout();
        throw new Error('Unauthorized');
    }
    
    return response;
}
