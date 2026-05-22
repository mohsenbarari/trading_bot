export const SUPER_ADMIN_ROLE = 'مدیر ارشد'
export const MIDDLE_MANAGER_ROLE = 'مدیر میانی'

export interface RoleOption {
  value: string
  label: string
}

const FULL_INVITABLE_ROLE_OPTIONS: RoleOption[] = [
  { value: 'تماشا', label: 'تماشا' },
  { value: 'عادی', label: 'عادی' },
  { value: 'پلیس', label: 'پلیس' },
  { value: 'مدیر میانی', label: 'مدیر میانی' },
]

const MIDDLE_MANAGER_INVITABLE_ROLE_OPTIONS: RoleOption[] = [
  { value: 'تماشا', label: 'تماشا' },
  { value: 'عادی', label: 'عادی' },
]

function readCachedCurrentUserSummary(): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem('current_user_summary')
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

export function readCachedCurrentUserRole(): string | null {
  const summary = readCachedCurrentUserSummary()
  return typeof summary?.role === 'string' ? summary.role : null
}

export function isAdminRoleValue(role: string | null | undefined): boolean {
  return role === SUPER_ADMIN_ROLE || role === MIDDLE_MANAGER_ROLE
}

export function isCachedMiddleManager(): boolean {
  return readCachedCurrentUserRole() === MIDDLE_MANAGER_ROLE
}

export function isCachedSuperAdmin(): boolean {
  return readCachedCurrentUserRole() === SUPER_ADMIN_ROLE
}

export function getInvitableRoleOptions(role: string | null | undefined = readCachedCurrentUserRole()): RoleOption[] {
  if (role === MIDDLE_MANAGER_ROLE) {
    return [...MIDDLE_MANAGER_INVITABLE_ROLE_OPTIONS]
  }
  return [...FULL_INVITABLE_ROLE_OPTIONS]
}