/** 管理端请求。不要往 api.ts 里塞管理员接口。 */

import type { ReviewStatus, PatchReviewBody } from './api'

export type AdminUser = {
  id: number
  username: string
  role: string
  is_active: boolean
  is_frozen?: boolean
  created_at: string
}

/** 管理端核验图：预签名 url 或同源 /file */
export type AdminAsset = {
  id: number
  role: 'input' | 'output' | 'product' | 'scene' | 'reference' | string
  url?: string | null
  file_path: string
  mime?: string | null
}

export type AdminGeneration = {
  id: number
  user_id: number
  username: string
  mode: string
  status: string
  prompt: string
  params?: Record<string, unknown>
  error_message?: string | null
  created_at: string
  finished_at?: string | null
  assets?: AdminAsset[]
  legacy_unlabeled?: boolean
  reverse_op_id?: number | null
  optimize_op_id?: number | null
  review_status?: ReviewStatus | string | null
  review_reason?: string | null
  reviewed_at?: string | null
  reviewed_by_id?: number | null
  user_deleted_at?: string | null
  user_deleted_by?: number | null
}

export type AdminAppeal = {
  id: number
  user_id: number
  freeze_event_id: number
  content: string
  status: string
  admin_reply?: string | null
  handled_by_id?: number | null
  handled_at?: string | null
  created_at: string
}

export type AdminCopywriting = {
  id: number
  user_id: number
  input_asset_id: number
  platform: string
  product_facts?: Record<string, unknown>
  generated_content?: unknown
  edited_content?: unknown
  risk_result?: unknown
  status: string
  error_message?: string | null
  created_at: string
  updated_at?: string
  user_deleted_at?: string | null
}

/** 优先用管理端签发的预签名；否则走已允许 admin 的 /file，绝不打用户 /url */
export function adminAssetPreview(asset: AdminAsset): string {
  if (asset.url) return asset.url
  return asset.file_path || `/api/assets/${asset.id}/file`
}

function isAccountFrozenPayload(data: unknown): boolean {
  if (!data || typeof data !== 'object') return false
  const detail = 'detail' in data ? (data as { detail: unknown }).detail : data
  if (!detail || typeof detail !== 'object') return false
  return (detail as { code?: unknown }).code === 'ACCOUNT_FROZEN'
}

function detailMessage(data: unknown, fallback: string): string {
  if (typeof data === 'string' && data) return data
  if (!data || typeof data !== 'object' || !('detail' in data)) return fallback
  const detail = (data as { detail: unknown }).detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const message = (detail as { message: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  return fallback
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(path, {
    ...init,
    headers,
    credentials: 'include',
  })
  if (res.status === 401) {
    throw Object.assign(new Error('未登录'), { status: 401 })
  }
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (!res.ok) {
    if (res.status === 403 && isAccountFrozenPayload(data)) {
      window.dispatchEvent(new CustomEvent('account-frozen'))
    }
    throw Object.assign(new Error(detailMessage(data, res.statusText)), { status: res.status })
  }
  return data as T
}

export const adminApi = {
  listUsers: () => request<{ items: AdminUser[] }>('/api/admin/users'),
  createUser: (body: { username: string; password: string; role?: string }) =>
    request<AdminUser>('/api/admin/users', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  setUserStatus: (id: number, is_active: boolean) =>
    request<AdminUser>(`/api/admin/users/${id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ is_active }),
    }),
  resetPassword: (id: number, password: string) =>
    request<{ ok: boolean }>(`/api/admin/users/${id}/reset-password`, {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  freezeUser: (id: number, body: { action: 'freeze' | 'unfreeze'; reason?: string }) =>
    request<AdminUser>(`/api/admin/users/${id}/freeze`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  listAppeals: (status?: 'pending' | 'closed') => {
    const q = status ? `?status=${encodeURIComponent(status)}` : ''
    return request<{ items: AdminAppeal[] }>(`/api/admin/appeals${q}`)
  },
  patchAppeal: (id: number, body: { status: 'closed'; admin_reply: string }) =>
    request<AdminAppeal>(`/api/admin/appeals/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  listGenerations: () => request<{ items: AdminGeneration[] }>('/api/admin/generations'),
  getGeneration: (id: number) => request<AdminGeneration>(`/api/admin/generations/${id}`),
  /** 管理员修改人工决定 */
  patchReview: (id: number, body: PatchReviewBody) =>
    request<AdminGeneration>(`/api/admin/generations/${id}/review`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  listCopywriting: () => request<{ items: AdminCopywriting[] }>('/api/admin/copywriting'),
}
