export const VIEWPORTS = [
  { width: 360, height: 740, label: 'mobile-360' },
  { width: 390, height: 844, label: 'mobile-390' },
  { width: 430, height: 932, label: 'mobile-430' },
  { width: 768, height: 1024, label: 'tablet-768' },
  { width: 1440, height: 900, label: 'desktop-1440' },
] as const

export const SENSITIVE_VIEWPORTS = [
  { width: 390, height: 844, label: 'mobile-390' },
  { width: 1440, height: 900, label: 'desktop-1440' },
] as const

export type RouteFamily =
  | 'auth'
  | 'profile'
  | 'operations'
  | 'admin'
  | 'messenger'
  | 'share-receive'
  | 'account'
  | 'home'
  | 'recovery'
  | 'overlays'

export type StateId =
  | 'initial'
  | 'loading'
  | 'slow'
  | 'empty'
  | 'normal'
  | 'full'
  | 'error'
  | 'retry'
  | 'offline'
  | 'stale'
  | 'long-persian'
  | 'unbroken'
  | 'ltr'
  | 'unauthorized'

export type ScrollerContract =
  | { kind: 'standard'; expected: 1 }
  | { kind: 'messenger'; expectedMin: 1; documentScrollForbidden: true }
  | { kind: 'none-extra'; expected: 1 }

export type RouteDescriptor = {
  id: string
  path: string
  family: RouteFamily
  auth: boolean
  readyText: string
  readyBy?: 'text' | 'accessible-name'
  h1: string
  h1Mode?: 'visual' | 'accessible'
  ctaRequired: boolean
  ctaName?: string
  holdPath?: string
  errorPath?: string
  emptyText?: string
  errorText?: RegExp
  loadingText?: RegExp
  scroller: ScrollerContract
  states: Record<StateId, { applicable: true } | { applicable: false; naCode: string; naReason: string }>
}

const NA = (naCode: string, naReason: string) => ({ applicable: false as const, naCode, naReason })
const YES = { applicable: true as const }

const FORM_NO_COLLECTION = NA('no-collection-surface', 'این مسیر فرم است و فهرست خالی/کامل محصولی ندارد.')
const FORM_NO_PAGE_LOAD = NA('no-page-load-resource', 'بارگذاری صفحه به فهرست شبکه‌ای وابسته نیست.')
const NO_STALE = NA('no-stale-resource', 'این مسیر منبع زمان‌دار با قرارداد stale/refresh ندارد.')
const NO_FORBIDDEN = NA('not-authorization-gated', 'این مسیر برای کاربر نشست‌دار ممنوع نیست.')
const PUBLIC_NO_FORBIDDEN = NA('public-route', 'مسیر عمومی مقصد ورود است نه سطح ممنوع.')
const HUB_ALWAYS_ROWS = NA('hub-always-populated', 'هاب همیشه ردیف ناوبری دارد و empty محصولی ندارد.')
const RECOVERY_LOCAL = NA('local-recovery', 'بازیابی مسیر ناشناخته محلی است و درخواست فهرست ندارد.')
const SHARE_NO_PAYLOAD = NA('share-target-empty-is-normal', 'بدون payload اشتراک، حالت عادی همان پیام آماده نشدن است.')
const NO_FULL_COLLECTION = NA('no-full-collection', 'این مسیر فهرست پر متمایز از حالت عادی ندارد.')
const NO_LONG_PERSIAN = NA('no-long-persian-copy', 'این مسیر نام یا متن بلند فارسی محصولی نشان نمی‌دهد.')
const NO_UNBROKEN = NA('no-unbroken-token', 'این مسیر شناسهٔ بی‌فاصلهٔ قابل مشاهده ندارد.')
const NO_LTR = NA('no-ltr-token', 'این مسیر شناسهٔ LTR متمایز از حالت عادی ندارد.')

function listStates(options: {
  unauthorized?: boolean
  stale?: boolean
  empty?: boolean
  full?: boolean
  longPersian?: boolean
  unbroken?: boolean
  ltr?: boolean
}): RouteDescriptor['states'] {
  return {
    initial: YES,
    loading: YES,
    slow: YES,
    empty: options.empty === false ? HUB_ALWAYS_ROWS : YES,
    normal: YES,
    full: options.full ? YES : NO_FULL_COLLECTION,
    error: YES,
    retry: YES,
    offline: YES,
    stale: options.stale ? YES : NO_STALE,
    'long-persian': options.longPersian ? YES : NO_LONG_PERSIAN,
    unbroken: options.unbroken ? YES : NO_UNBROKEN,
    ltr: options.ltr ? YES : NO_LTR,
    unauthorized: options.unauthorized ? YES : NO_FORBIDDEN,
  }
}

function formStates(): RouteDescriptor['states'] {
  return {
    initial: YES,
    loading: FORM_NO_PAGE_LOAD,
    slow: FORM_NO_PAGE_LOAD,
    empty: FORM_NO_COLLECTION,
    normal: YES,
    full: FORM_NO_COLLECTION,
    error: FORM_NO_PAGE_LOAD,
    retry: FORM_NO_PAGE_LOAD,
    offline: YES,
    stale: NO_STALE,
    'long-persian': NO_LONG_PERSIAN,
    unbroken: NO_UNBROKEN,
    ltr: NO_LTR,
    unauthorized: PUBLIC_NO_FORBIDDEN,
  }
}

export const ROUTE_DESCRIPTORS: RouteDescriptor[] = [
  {
    id: 'home',
    path: '/',
    family: 'home',
    auth: true,
    readyText: 'خانه',
    h1: 'خانه',
    ctaRequired: false,
    holdPath: '/api/trades/my/page',
    errorPath: '/api/trades/my/page',
    emptyText: 'ورود به بازار',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ empty: true, stale: false }),
  },
  {
    id: 'setup-password',
    path: '/setup-password',
    family: 'auth',
    auth: true,
    readyText: 'تنظیم رمز عبور',
    h1: 'تنظیم رمز عبور',
    ctaRequired: true,
    ctaName: 'ثبت و ورود',
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...formStates(),
      offline: FORM_NO_PAGE_LOAD,
    },
  },
  {
    id: 'operations',
    path: '/operations',
    family: 'operations',
    auth: true,
    readyText: 'عملیات',
    h1: 'عملیات',
    ctaRequired: false,
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...listStates({ empty: false }),
      loading: HUB_ALWAYS_ROWS,
      slow: HUB_ALWAYS_ROWS,
      error: HUB_ALWAYS_ROWS,
      retry: HUB_ALWAYS_ROWS,
      offline: HUB_ALWAYS_ROWS,
    },
  },
  {
    id: 'customers',
    path: '/operations/customers',
    family: 'operations',
    auth: true,
    readyText: 'مشتریان',
    h1: 'مشتریان',
    ctaRequired: false,
    holdPath: '/api/customers/owner-relations',
    errorPath: '/api/customers/owner-relations',
    emptyText: 'مشتری',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, stale: false, full: true, longPersian: true }),
  },
  {
    id: 'customer-detail',
    path: '/operations/customers/13',
    family: 'operations',
    auth: true,
    readyText: 'مشتری پذیرش',
    h1: 'مشتریان',
    ctaRequired: false,
    holdPath: '/api/customers/owner-relations',
    errorPath: '/api/customers/owner-relations',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, stale: false, empty: false, longPersian: true }),
  },
  {
    id: 'accountants',
    path: '/operations/accountants',
    family: 'operations',
    auth: true,
    readyText: 'حسابداران',
    h1: 'حسابداران',
    ctaRequired: false,
    holdPath: '/api/accountants/owner-relations',
    errorPath: '/api/accountants/owner-relations',
    emptyText: 'حسابدار',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, stale: false, full: true, longPersian: true }),
  },
  {
    id: 'accountant-detail',
    path: '/operations/accountants/13',
    family: 'operations',
    auth: true,
    readyText: 'حسابدار پذیرش',
    h1: 'حسابداران',
    ctaRequired: false,
    holdPath: '/api/accountants/owner-relations',
    errorPath: '/api/accountants/owner-relations',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, stale: false, empty: false, longPersian: true }),
  },
  {
    id: 'account',
    path: '/account',
    family: 'account',
    auth: true,
    readyText: 'حساب',
    h1: 'حساب',
    ctaRequired: false,
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...listStates({ empty: false }),
      loading: HUB_ALWAYS_ROWS,
      slow: HUB_ALWAYS_ROWS,
      error: HUB_ALWAYS_ROWS,
      retry: HUB_ALWAYS_ROWS,
      offline: HUB_ALWAYS_ROWS,
    },
  },
  {
    id: 'account-security',
    path: '/account/security',
    family: 'account',
    auth: true,
    readyText: 'امنیت حساب',
    h1: 'امنیت حساب',
    ctaRequired: false,
    holdPath: '/api/sessions/active',
    errorPath: '/api/sessions/active',
    emptyText: 'نشست',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ stale: true, full: true, longPersian: true }),
  },
  {
    id: 'account-storage',
    path: '/account/storage',
    family: 'account',
    auth: true,
    readyText: 'حافظه و داده‌ها',
    h1: 'حافظه و داده‌ها',
    ctaRequired: false,
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...listStates({ empty: false }),
      loading: HUB_ALWAYS_ROWS,
      slow: HUB_ALWAYS_ROWS,
      error: HUB_ALWAYS_ROWS,
      retry: HUB_ALWAYS_ROWS,
      offline: HUB_ALWAYS_ROWS,
    },
  },
  {
    id: 'account-notifications',
    path: '/account/notifications',
    family: 'account',
    auth: true,
    readyText: 'اعلان‌ها',
    h1: 'اعلان‌ها',
    ctaRequired: false,
    holdPath: '/api/notifications',
    errorPath: '/api/notifications',
    emptyText: 'هیچ اعلانی یافت نشد',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ stale: false, full: true, longPersian: true }),
  },
  {
    id: 'messenger',
    path: '/chat',
    family: 'messenger',
    auth: true,
    readyText: 'جستجو',
    readyBy: 'accessible-name',
    h1: 'پیام‌رسان',
    h1Mode: 'accessible',
    ctaRequired: false,
    holdPath: '/api/chat/conversations',
    errorPath: '/api/chat/conversations',
    emptyText: 'گفتگو',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'messenger', expectedMin: 1, documentScrollForbidden: true },
    states: listStates({ stale: false, full: true, longPersian: true }),
  },
  {
    id: 'public-profile',
    path: '/users/9001',
    family: 'profile',
    auth: true,
    readyText: 'اطلاعات شخصی',
    h1: 'native_app_v2_user',
    ctaRequired: false,
    holdPath: '/api/users-public/9001',
    errorPath: '/api/users-public/9001',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ empty: false, stale: true, longPersian: true, unbroken: true, ltr: true }),
  },
  {
    id: 'profile',
    path: '/profile',
    family: 'profile',
    auth: true,
    readyText: 'اطلاعات شخصی',
    h1: 'native_app_v2_user',
    ctaRequired: false,
    holdPath: '/api/users-public/9001',
    errorPath: '/api/users-public/9001',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ empty: false, stale: true, longPersian: true, unbroken: true, ltr: true }),
  },
  {
    id: 'settings',
    path: '/settings',
    family: 'account',
    auth: true,
    readyText: 'تنظیمات حساب',
    h1: 'تنظیمات حساب',
    ctaRequired: false,
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...listStates({ empty: false }),
      loading: HUB_ALWAYS_ROWS,
      slow: HUB_ALWAYS_ROWS,
      error: HUB_ALWAYS_ROWS,
      retry: HUB_ALWAYS_ROWS,
      offline: HUB_ALWAYS_ROWS,
    },
  },
  {
    id: 'admin',
    path: '/admin',
    family: 'admin',
    auth: true,
    readyText: 'مرکز مدیریت',
    h1: 'مرکز مدیریت',
    ctaRequired: false,
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...listStates({ unauthorized: true, empty: false }),
      loading: HUB_ALWAYS_ROWS,
      slow: HUB_ALWAYS_ROWS,
      error: HUB_ALWAYS_ROWS,
      retry: HUB_ALWAYS_ROWS,
      offline: HUB_ALWAYS_ROWS,
    },
  },
  {
    id: 'admin-invitations',
    path: '/admin/invitations',
    family: 'admin',
    auth: true,
    readyText: 'ارسال دعوت‌نامه',
    h1: 'ارسال دعوت‌نامه',
    ctaRequired: true,
    ctaName: 'ارسال لینک دعوت',
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...formStates(),
      unauthorized: YES,
    },
  },
  {
    id: 'admin-channels',
    path: '/admin/channels',
    family: 'admin',
    auth: true,
    readyText: 'ساخت کانال',
    h1: 'ساخت کانال',
    ctaRequired: false,
    holdPath: '/api/chat/channels',
    errorPath: '/api/chat/channels',
    emptyText: 'هنوز کانالی ساخته نشده است',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال دریافت کانال/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, stale: false, full: true, longPersian: true }),
  },
  {
    id: 'admin-users',
    path: '/admin/users',
    family: 'admin',
    auth: true,
    readyText: 'مدیریت کاربران',
    h1: 'مدیریت کاربران',
    ctaRequired: false,
    holdPath: '/api/users',
    errorPath: '/api/users',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, stale: false, full: true, longPersian: true }),
  },
  {
    id: 'admin-user-profile',
    path: '/admin/users/9001',
    family: 'admin',
    auth: true,
    readyText: 'native_app_v2_user',
    h1: 'پروفایل کاربر',
    ctaRequired: false,
    holdPath: '/api/users/9001',
    errorPath: '/api/users/9001',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, empty: false, stale: false, longPersian: true, unbroken: true, ltr: true }),
  },
  {
    id: 'admin-commodities',
    path: '/admin/commodities',
    family: 'admin',
    auth: true,
    readyText: 'مدیریت کالاها',
    h1: 'مدیریت کالاها',
    ctaRequired: false,
    holdPath: '/api/commodities',
    errorPath: '/api/commodities',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, stale: false, full: true, longPersian: true }),
  },
  {
    id: 'admin-messages',
    path: '/admin/messages',
    family: 'admin',
    auth: true,
    readyText: 'پیام‌های مدیریت',
    h1: 'پیام‌های مدیریت',
    ctaRequired: false,
    holdPath: '/api/admin-messages/broadcasts/history',
    errorPath: '/api/admin-messages/broadcasts/history',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, stale: false, full: true, longPersian: true }),
  },
  {
    id: 'admin-system',
    path: '/admin/system',
    family: 'admin',
    auth: true,
    readyText: 'تنظیمات سیستم',
    h1: 'تنظیمات سیستم',
    ctaRequired: false,
    holdPath: '/api/trading-settings/',
    errorPath: '/api/trading-settings/',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ unauthorized: true, empty: false, stale: false }),
  },
  {
    id: 'notifications',
    path: '/notifications',
    family: 'account',
    auth: true,
    readyText: 'اعلان‌ها',
    h1: 'اعلان‌ها',
    ctaRequired: false,
    holdPath: '/api/notifications',
    errorPath: '/api/notifications',
    emptyText: 'هیچ اعلانی یافت نشد',
    errorText: /ناموفق|خطا|دوباره|دریافت/i,
    loadingText: /در حال|بارگذاری/i,
    scroller: { kind: 'standard', expected: 1 },
    states: listStates({ stale: false, full: true, longPersian: true }),
  },
  {
    id: 'share-receive',
    path: '/share-receive',
    family: 'share-receive',
    auth: true,
    readyText: 'اشتراک‌گذاری آماده نشد',
    h1: 'دریافت اشتراک',
    h1Mode: 'accessible',
    ctaRequired: false,
    scroller: { kind: 'standard', expected: 1 },
    states: {
      initial: YES,
      loading: SHARE_NO_PAYLOAD,
      slow: SHARE_NO_PAYLOAD,
      empty: SHARE_NO_PAYLOAD,
      normal: YES,
      full: SHARE_NO_PAYLOAD,
      error: SHARE_NO_PAYLOAD,
      retry: SHARE_NO_PAYLOAD,
      offline: SHARE_NO_PAYLOAD,
      stale: NO_STALE,
      'long-persian': NO_LONG_PERSIAN,
      unbroken: NO_UNBROKEN,
      ltr: NO_LTR,
      unauthorized: NO_FORBIDDEN,
    },
  },
  {
    id: 'login',
    path: '/login',
    family: 'auth',
    auth: false,
    readyText: 'ورود به سامانه',
    h1: 'ورود به سامانه',
    ctaRequired: true,
    ctaName: 'دریافت کد تأیید',
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...formStates(),
      offline: FORM_NO_PAGE_LOAD,
    },
  },
  {
    id: 'register',
    path: '/register',
    family: 'auth',
    auth: false,
    readyText: 'تکمیل ثبت‌نام',
    h1: 'تکمیل ثبت‌نام',
    ctaRequired: true,
    ctaName: 'دریافت کد تأیید',
    holdPath: '/api/auth/registration-context',
    errorPath: '/api/auth/registration-context',
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...formStates(),
      loading: YES,
      slow: YES,
      error: YES,
      retry: YES,
    },
  },
  {
    id: 'invite-landing',
    path: '/i/uiux-baseline',
    family: 'auth',
    auth: false,
    readyText: 'ثبت‌نام در وب‌اپ',
    h1: 'ثبت‌نام در وب‌اپ',
    ctaRequired: true,
    ctaName: 'ادامه ثبت‌نام در وب‌اپ',
    holdPath: '/api/invitations/lookup/uiux-baseline',
    errorPath: '/api/invitations/lookup/uiux-baseline',
    scroller: { kind: 'standard', expected: 1 },
    states: {
      ...formStates(),
      loading: YES,
      slow: YES,
      error: YES,
      retry: YES,
    },
  },
  {
    id: 'system-recovery',
    path: '/this-route-does-not-exist',
    family: 'recovery',
    auth: false,
    readyText: 'این صفحه پیدا نشد',
    h1: 'این صفحه پیدا نشد',
    ctaRequired: false,
    scroller: { kind: 'standard', expected: 1 },
    states: {
      initial: YES,
      loading: RECOVERY_LOCAL,
      slow: RECOVERY_LOCAL,
      empty: RECOVERY_LOCAL,
      normal: YES,
      full: RECOVERY_LOCAL,
      error: RECOVERY_LOCAL,
      retry: RECOVERY_LOCAL,
      offline: RECOVERY_LOCAL,
      stale: NO_STALE,
      'long-persian': NO_LONG_PERSIAN,
      unbroken: NO_UNBROKEN,
      ltr: NO_LTR,
      unauthorized: PUBLIC_NO_FORBIDDEN,
    },
  },
]

export const SENSITIVE_FAMILIES = new Set<RouteFamily>([
  'auth',
  'profile',
  'operations',
  'admin',
  'messenger',
  'share-receive',
  'recovery',
  'overlays',
])

export const ZOOM_FAMILY_REPRESENTATIVES = [
  '/login',
  '/profile',
  '/operations',
  '/admin',
  '/chat',
  '/share-receive',
  '/this-route-does-not-exist',
] as const

export const KEYBOARD_FORM_ROUTES = [
  { id: 'login', path: '/login', field: 'شماره موبایل', submit: 'دریافت کد تأیید' },
  { id: 'register', path: '/register', field: null, submit: 'دریافت کد تأیید' },
  { id: 'invite-landing', path: '/i/uiux-baseline', field: null, submit: 'ادامه ثبت‌نام در وب‌اپ' },
  {
    id: 'setup-password',
    path: '/setup-password',
    field: 'رمز عبور جدید',
    submit: 'ثبت و ورود',
    typeIntoField: 'Abcdefg1!',
    confirmField: 'تکرار رمز عبور جدید',
    confirmValue: 'Abcdefg1!',
  },
  {
    id: 'customer-invite',
    path: '/operations/customers',
    field: 'نام مدیریتی',
    submit: 'ثبت دعوت مشتری',
    openName: 'افزودن مشتری',
  },
  {
    id: 'customer-edit',
    path: '/operations/customers/13',
    field: 'حداقل مقدار معامله',
    submit: 'مرور تغییرات',
    tabName: 'محدودیت‌ها',
  },
  {
    id: 'accountant-invite',
    path: '/operations/accountants',
    field: 'نام نمایشی رابطه',
    submit: 'ثبت دعوت حسابدار',
    openName: 'افزودن حسابدار',
  },
  {
    id: 'accountant-edit',
    path: '/operations/accountants/13',
    field: 'شرح وظیفه',
    submit: 'ذخیره تغییرات',
    tabName: 'شرح وظیفه',
  },
  {
    id: 'create-channel',
    path: '/admin/channels',
    field: 'نام کانال',
    submit: 'ساخت کانال',
    openName: 'کانال جدید',
    typeIntoField: 'کانال آزمایشی',
  },
  {
    id: 'messenger-composer',
    path: '/chat',
    field: 'متن پیام',
    submit: 'ارسال پیام',
    openName: 'گفتگوی نمونه',
    typeIntoField: 'آ',
  },
] as const

export const STATE_OBSERVATIONS: Partial<Record<StateId, Partial<Record<string, string>>>> = {
  full: {
    customers: 'مشتری دوم پذیرش',
    accountants: 'حسابدار دوم پذیرش',
    'account-security': 'نشست دوم پذیرش',
    'account-notifications': 'اعلان دوم پذیرش',
    messenger: 'گفتگوی دوم پذیرش',
    'admin-channels': 'کانال دوم پذیرش',
    'admin-users': 'کاربر دوم پذیرش',
    'admin-commodities': 'کالای دوم پذیرش',
    'admin-messages': 'پیام دوم همگانی',
    notifications: 'اعلان دوم پذیرش',
  },
  'long-persian': {
    customers: 'نام بسیار بلند فارسی',
    'customer-detail': 'نام بسیار بلند فارسی',
    accountants: 'نام بسیار بلند فارسی',
    'accountant-detail': 'نام بسیار بلند فارسی',
    'account-security': 'نام بسیار بلند فارسی',
    'account-notifications': 'عنوان بسیار بلند فارسی',
    messenger: 'گفتگوی بسیار بلند فارسی',
    'public-profile': 'نام بسیار بلند فارسی',
    profile: 'نام بسیار بلند فارسی',
    'admin-channels': 'کانال بسیار بلند فارسی',
    'admin-users': 'نام بسیار بلند فارسی',
    'admin-user-profile': 'نام بسیار بلند فارسی',
    'admin-commodities': 'نام بسیار بلند فارسی',
    'admin-messages': 'متن بسیار بلند فارسی',
    notifications: 'عنوان بسیار بلند فارسی',
  },
  unbroken: {
    'public-profile': 'unbroken_ltr_accountnamewithoutspaces_9001',
    profile: 'unbroken_ltr_accountnamewithoutspaces_9001',
    'admin-user-profile': 'unbroken_ltr_accountnamewithoutspaces_9001',
  },
  ltr: {
    'public-profile': 'ltr_account_9001',
    profile: 'ltr_account_9001',
    'admin-user-profile': 'ltr_account_9001',
  },
}

export function observationForState(routeId: string, state: StateId) {
  return STATE_OBSERVATIONS[state]?.[routeId]
}

export type MatrixCell = {
  id: string
  route: RouteDescriptor
  viewport: { width: number; height: number; label: string }
  state: StateId
  applicable: boolean
  naCode?: string
  naReason?: string
}

export function buildMatrixCells(
  viewports: readonly { width: number; height: number; label: string }[],
  routes = ROUTE_DESCRIPTORS,
): MatrixCell[] {
  const cells: MatrixCell[] = []
  for (const viewport of viewports) {
    for (const route of routes) {
      for (const state of Object.keys(route.states) as StateId[]) {
        const rule = route.states[state]
        cells.push({
          id: `v2:${route.id}:${viewport.label}:${state}`,
          route,
          viewport,
          state,
          applicable: rule.applicable,
          naCode: rule.applicable ? undefined : rule.naCode,
          naReason: rule.applicable ? undefined : rule.naReason,
        })
      }
    }
  }
  return cells
}

export function applicableCells(
  viewports: readonly { width: number; height: number; label: string }[],
  routes = ROUTE_DESCRIPTORS,
) {
  return buildMatrixCells(viewports, routes).filter((cell) => cell.applicable)
}

export function naCells(
  viewports: readonly { width: number; height: number; label: string }[],
  routes = ROUTE_DESCRIPTORS,
) {
  return buildMatrixCells(viewports, routes).filter((cell) => !cell.applicable)
}

export function assertMatrixCoverage() {
  if (ROUTE_DESCRIPTORS.length !== 29) {
    throw new Error(`expected 29 live non-market routes, got ${ROUTE_DESCRIPTORS.length}`)
  }
  const paths = new Set(ROUTE_DESCRIPTORS.map((route) => route.path))
  if (paths.size !== 29) throw new Error('duplicate route paths in Native App V2 matrix')
  for (const route of ROUTE_DESCRIPTORS) {
    for (const state of Object.keys(route.states) as StateId[]) {
      const rule = route.states[state]
      if (!rule.applicable && (!rule.naCode || !rule.naReason)) {
        throw new Error(`N/A without machine-readable reason: ${route.id}:${state}`)
      }
      if (
        rule.applicable
        && (state === 'full' || state === 'long-persian' || state === 'unbroken' || state === 'ltr')
        && !observationForState(route.id, state)
      ) {
        throw new Error(`applicable copy state missing distinct observation: ${route.id}:${state}`)
      }
    }
  }
}
