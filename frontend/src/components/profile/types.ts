import type { Component } from 'vue'

export type ProfileProjection = 'self' | 'public' | 'admin'

export type ProfileSurfaceStatus = 'loading' | 'error' | 'ready'

export type ProfileStatItem = {
  key: string
  label: string
  value: string
}

export type ProfileActionTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'

export type ProfileActionItem = {
  key: string
  label: string
  description?: string | null
  disabled?: boolean
  tone?: ProfileActionTone
  className?: string | string[] | Record<string, boolean>
  icon?: Component
}

export type ProfileCustomerContext = {
  managementName: string
  ownerAccountName?: string | null
  customerTier?: string | null
  showTier?: boolean
}

export type ProfileAccountantRelation = {
  accountant_user_id?: number | null
  accountant_account_name?: string | null
  relation_display_name: string
  duty_description?: string | null
}

export type ProfileCustomerRelation = {
  customer_user_id?: number | null
  customer_account_name?: string | null
  management_name: string
  customer_tier: 'tier1' | 'tier2'
}
