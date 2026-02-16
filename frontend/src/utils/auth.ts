// frontend/src/utils/auth.ts
import { RouteLocationNormalized, NavigationGuardNext } from 'vue-router';

export function isAuthenticated(): boolean {
  const token = localStorage.getItem('auth_token');
  return !!token;
}

export function isAdmin(): boolean {
  // Simple check, real validation happens on backend
  // We could decode JWT to check role here if needed
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
