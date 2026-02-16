/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

/* eslint-disable */
import 'vue-router';

declare module 'vue-router' {
    interface RouteMeta {
        requiresAuth?: boolean;
        requiresAdmin?: boolean;
    }
}
