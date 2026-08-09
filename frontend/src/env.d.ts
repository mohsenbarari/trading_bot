/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

/* eslint-disable */
import 'vue-router'
import type { UiRouteShellClass, UiV2Scope } from './router/uiRouteContract'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    requiresAdmin?: boolean
    requiresMarketAccess?: boolean
    requiresOwnerAccess?: boolean
    uiShellClass?: UiRouteShellClass
    uiV2Scope?: UiV2Scope
    uiRouteTestId?: string
  }
}
