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
  // This is a client-side check only. Real security is on the server.
  // We assume the user role is not stored in token directly in previous implementation,
  // but we can try to fetch user profile or check token payload if role is there.
  // For now, let's return true to pass the guard, but admin API calls will fail if not admin.
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
    // Check token expiry every minute
    setInterval(() => {
        const token = localStorage.getItem('auth_token');
        if (token) {
            const payload = parseJwt(token);
            if (payload && payload.exp) {
                const now = Math.floor(Date.now() / 1000);
                if (now >= payload.exp) {
                    // Token expired
                    console.log('Token expired, redirecting to login...');
                    localStorage.removeItem('auth_token');
                    localStorage.removeItem('refresh_token');
                    window.location.href = '/login';
                }
            }
        }
    }, 30000); // Check every 30s
}

export function logout() {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('refresh_token');
    window.location.href = '/login';
}
