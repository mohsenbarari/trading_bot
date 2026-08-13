<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import AppDesignSystemScope from './components/ui/AppDesignSystemScope.vue'
import {
  getUiRouteContractByName,
  UI_ROUTE_PROTECTION,
  UI_ROUTE_SHELL,
  UI_V2_SCOPE,
} from './router/uiRouteContract'
import { isAppConnecting } from './utils/auth'
import { usePWAInstall } from './utils/pwaInstall'

const route = useRoute()
const router = useRouter()
const AuthenticatedShell = defineAsyncComponent(
  () => import('./components/AppAuthenticatedShell.vue'),
)
usePWAInstall()

// Track whether the router's FIRST navigation (which includes loading the
// lazy-loaded route component chunk from the network) has completed.
// Until then we show a full-screen spinner instead of a blank white page.
const isFirstRouteReady = ref(false)
router.isReady().then(() => {
  isFirstRouteReady.value = true
})

const shellClass = computed(() => {
  const value = route.meta.uiShellClass
  return Object.values(UI_ROUTE_SHELL).includes(
    value as (typeof UI_ROUTE_SHELL)[keyof typeof UI_ROUTE_SHELL],
  )
    ? value
    : UI_ROUTE_SHELL.SYSTEM_RECOVERY
})
const v2Scope = computed(() => {
  const value = route.meta.uiV2Scope
  return Object.values(UI_V2_SCOPE).includes(
    value as (typeof UI_V2_SCOPE)[keyof typeof UI_V2_SCOPE],
  )
    ? value
    : UI_V2_SCOPE.OFF
})
const isStandardAuthenticatedShell = computed(
  () => shellClass.value === UI_ROUTE_SHELL.STANDARD_AUTHENTICATED,
)
const isFocusedAuthenticatedShell = computed(
  () => shellClass.value === UI_ROUTE_SHELL.FOCUSED_AUTHENTICATED,
)
const isProtectedLegacyShell = computed(() => shellClass.value === UI_ROUTE_SHELL.PROTECTED_LEGACY)
const isSystemRecoveryShell = computed(() => shellClass.value === UI_ROUTE_SHELL.SYSTEM_RECOVERY)
function hasStoredSessionCredential() {
  try {
    return (
      Boolean(localStorage.getItem('auth_token')) || Boolean(localStorage.getItem('refresh_token'))
    )
  } catch {
    return false
  }
}

type AppBootWindow = Window & { __appBootTimeoutId?: unknown }
const isAuthenticatedSystemRecovery = computed(() => {
  // Reading the route identity here ensures credentials are re-evaluated on
  // every visit. Web Storage writes in the same tab do not emit `storage`.
  if (route.name !== 'system-recovery' || !isSystemRecoveryShell.value) return false
  return hasStoredSessionCredential()
})
const shouldRenderAuthenticatedShell = computed(
  () =>
    isFirstRouteReady.value &&
    (isFocusedAuthenticatedShell.value ||
      isStandardAuthenticatedShell.value ||
      isProtectedLegacyShell.value ||
      isAuthenticatedSystemRecovery.value),
)
const shouldScopeRoute = computed(
  () => isFirstRouteReady.value && v2Scope.value === UI_V2_SCOPE.ROUTE,
)
const informationalCopyExcludedRouteNames = new Set(['admin-messages', 'admin-system'])
const allowsInformationalCopy = computed(() => {
  if (!isFirstRouteReady.value) return false
  if (isProtectedLegacyShell.value) return false
  if (v2Scope.value === UI_V2_SCOPE.OFF) return false
  if (typeof route.name === 'string' && informationalCopyExcludedRouteNames.has(route.name)) {
    return false
  }
  return true
})
const allowsReducedMotionRouteTransition = computed(
  () =>
    isFirstRouteReady.value &&
    getUiRouteContractByName(route.name)?.protection === UI_ROUTE_PROTECTION.NONE &&
    v2Scope.value === UI_V2_SCOPE.SECTION,
)
// Typography unification is intentionally opt-in by the immutable route
// contract. The marker is bound to the individual route vnode, rather than
// the shared shell, so a concurrently leaving FULL/MIXED route never inherits
// the entering NONE route's typography during the legacy fade.
const usesApprovedPersianTypography = computed(
  () => getUiRouteContractByName(route.name)?.protection === UI_ROUTE_PROTECTION.NONE,
)
const persianTypographyRouteClass = computed(() =>
  usesApprovedPersianTypography.value ? 'app-route--persian-typography' : undefined,
)
const routeTransitionName = computed(() =>
  shouldScopeRoute.value ? 'ui-v2-route-fade' : 'fade',
)
const reducedMotionRouteClass = computed(() =>
  allowsReducedMotionRouteTransition.value ? 'app-reduced-motion-route' : undefined,
)
const pathKeyedSectionRouteNames = new Set([
  'operations-customers',
  'operations-customers-detail',
  'operations-accountants',
  'operations-accountants-detail',
])
const sharedSectionRouteKeyByName = new Map([
  ['admin-users', 'admin-user-directory'],
  ['admin-user-profile', 'admin-user-directory'],
])
const unscopedRouteKey = computed(() => {
  if (v2Scope.value !== UI_V2_SCOPE.SECTION || typeof route.name !== 'string') {
    return `legacy:${route.fullPath}`
  }

  const sharedKey = sharedSectionRouteKeyByName.get(route.name)
  if (sharedKey) return `section:${sharedKey}`
  return pathKeyedSectionRouteNames.has(route.name)
    ? `section:${route.path}`
    : `legacy:${route.fullPath}`
})
const shouldReserveDailyNavigation = computed(
  () =>
    isStandardAuthenticatedShell.value ||
    isProtectedLegacyShell.value ||
    isAuthenticatedSystemRecovery.value,
)

watch(
  isFirstRouteReady,
  (ready) => {
    if (!ready) return

    document.documentElement.setAttribute('data-app-mounted', '1')
    document.documentElement.removeAttribute('data-app-boot-timeout')

    const appWindow = window as AppBootWindow
    const bootTimeoutId = appWindow.__appBootTimeoutId
    if (typeof bootTimeoutId === 'number') {
      window.clearTimeout(bootTimeoutId)
      delete appWindow.__appBootTimeoutId
    }
  },
  { immediate: true },
)
</script>

<template>
  <div
    class="app-shell h-full flex flex-col font-sans text-gray-900 antialiased selection:bg-primary-500 selection:text-white overflow-hidden"
    :class="{ 'app-copyable-info': allowsInformationalCopy }"
  >
    <!-- Global Connecting State -->
    <AppDesignSystemScope
      v-if="isFirstRouteReady && isStandardAuthenticatedShell && isAppConnecting"
      as="aside"
      class="ui-v2-connection-banner"
      role="status"
      aria-live="polite"
    >
      <svg
        class="ui-v2-connection-banner__spinner"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        aria-hidden="true"
        data-ui-v2-motion="decorative"
      >
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
        ></path>
      </svg>
      ارتباط در حال بازیابی است…
    </AppDesignSystemScope>
    <div
      v-else-if="isFirstRouteReady && isProtectedLegacyShell && isAppConnecting"
      class="fixed top-0 left-0 w-full bg-amber-500 text-white text-sm py-1.5 flex items-center justify-center z-[200] gap-2 font-medium shadow-md"
    >
      <svg
        class="h-4 w-4 animate-spin text-white"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
      >
        <circle
          class="opacity-25"
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          stroke-width="4"
        ></circle>
        <path
          class="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
        ></path>
      </svg>
      در حال اتصال...
    </div>

    <!-- Page Content Container -->
    <div
      class="app-route-scroll flex-1 relative overflow-y-auto overflow-x-hidden min-h-0 bg-transparent"
      :class="{ 'app-route-scroll--no-daily-nav': !shouldReserveDailyNavigation }"
    >
      <!-- Full-screen spinner shown while the first route's JS chunk loads from
           the network (only visible on first incognito/cold load). Without this,
           the RouterView renders nothing during the async component download → blank white page. -->
      <div v-if="!isFirstRouteReady" class="flex items-center justify-center h-full min-h-screen">
        <div
          class="w-10 h-10 border-4 border-amber-400 border-t-transparent rounded-full animate-spin"
        ></div>
      </div>
      <RouterView v-else v-slot="{ Component }">
        <transition :name="routeTransitionName">
          <AppDesignSystemScope
            v-if="shouldScopeRoute"
            :key="`v2:${route.path}`"
            as="div"
            :class="['app-route-v2-scope', persianTypographyRouteClass]"
          >
            <component :is="Component" />
          </AppDesignSystemScope>
          <component
            v-else
            :is="Component"
            :key="unscopedRouteKey"
            :class="[reducedMotionRouteClass, persianTypographyRouteClass]"
          />
        </transition>
      </RouterView>
    </div>

    <AppDesignSystemScope
      v-if="
        shouldRenderAuthenticatedShell &&
        (isFocusedAuthenticatedShell ||
          isStandardAuthenticatedShell ||
          isAuthenticatedSystemRecovery)
      "
      as="div"
      class="app-authenticated-shell-v2"
    >
      <AuthenticatedShell
        v2-scope
        :show-daily-navigation="isStandardAuthenticatedShell || isAuthenticatedSystemRecovery"
      />
    </AppDesignSystemScope>
    <AuthenticatedShell v-else-if="shouldRenderAuthenticatedShell" />
  </div>
</template>

<style>
.app-shell {
  background: var(--ds-app-background);
}

/* Bound to each route vnode so concurrent leave/enter fades retain the old
 * typography on protected routes. Mono and LTR descendants retain their own
 * explicit font/direction declarations. */
.app-route--persian-typography {
  font-family: Vazirmatn, Tahoma, Arial, sans-serif;
  font-synthesis: none;
}

.app-route-v2-scope {
  min-height: 100%;
}

.app-authenticated-shell-v2 {
  display: contents;
}

.app-route-scroll--no-daily-nav {
  padding-bottom: 0;
  scroll-padding-bottom: var(--ds-safe-area-bottom);
}

.app-route-scroll--no-daily-nav
  :is(button, a[href], input, textarea, select, [role='button'], [tabindex]) {
  scroll-margin-bottom: var(--ds-safe-area-bottom);
}

/* Global Transitions */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .app-reduced-motion-route.fade-enter-active,
  .app-reduced-motion-route.fade-leave-active {
    transition: none;
  }
}
</style>
