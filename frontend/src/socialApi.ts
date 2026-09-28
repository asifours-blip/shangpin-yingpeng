/** 演示社交账号 / 联系人。自带小 request，不改 api.ts、不 import vue-router。 */

export type SocialPlatform = 'douyin' | 'xiaohongshu'

export type SocialAccountStatus = 'connected' | 'disconnected'

export type SocialAccount = {
  id: number
  owner_id?: number
  platform: SocialPlatform | string
  external_account_id: string
  display_name: string
  status: SocialAccountStatus | string
  data_source?: string
  last_synced_at?: string | null
}

export type SocialContact = {
  id: number
  account_id: number
  external_user_id: string
  nickname: string
  avatar_url?: string | null
  remark?: string | null
  tags: unknown
  data_source?: string
  platform?: SocialPlatform | string
  account_display_name?: string | null
}

export type ConnectSocialAccountBody = {
  platform: SocialPlatform
  external_account_id: string
}

export type ListContactsQuery = {
  q?: string
  platform?: SocialPlatform | string
  account_id?: number
}

export type PatchContactBody = {
  remark?: string | null
  tags?: string[]
}

export type SocialApiError = Error & { status: number; detail: unknown }

function unwrapItems<T>(data: T[] | { items?: T[] } | null | undefined): T[] {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.items)) return data.items
  return []
}

function hasFrozenCode(data: unknown): boolean {
  if (!data || typeof data !== 'object') return false
  const detail = 'detail' in data ? (data as { detail: unknown }).detail : data
  if (!detail || typeof detail !== 'object') return false
  return (detail as { code?: unknown }).code === 'ACCOUNT_FROZEN'
}

function detailMessage(data: unknown, fallback: string): string {
  if (typeof data === 'string' && data) return data
  if (!data || typeof data !== 'object') return fallback
  if (!('detail' in data)) return fallback
  const detail = (data as { detail: unknown }).detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return ''
      })
      .filter(Boolean)
    if (parts.length) return parts.join('；')
  }
  if (detail && typeof detail === 'object') {
    const obj = detail as { message?: unknown; msg?: unknown }
    if (typeof obj.message === 'string' && obj.message) return obj.message
    if (typeof obj.msg === 'string' && obj.msg) return obj.msg
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
  if (res.status === 204) return undefined as T
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (res.status === 403 && hasFrozenCode(data)) {
    window.dispatchEvent(new CustomEvent('account-frozen'))
  }
  if (res.status === 401) {
    throw Object.assign(new Error('未登录'), { status: 401, detail: data }) as SocialApiError
  }
  if (!res.ok) {
    throw Object.assign(new Error(detailMessage(data, res.statusText || '请求失败')), {
      status: res.status,
      detail: data,
    }) as SocialApiError
  }
  return data as T
}

function contactsQuery(params: ListContactsQuery = {}): string {
  const q = new URLSearchParams()
  const keyword = params.q?.trim()
  if (keyword) q.set('q', keyword)
  if (params.platform) q.set('platform', String(params.platform))
  if (params.account_id != null) q.set('account_id', String(params.account_id))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const socialApi = {
  listAccounts: async () => {
    const data = await request<SocialAccount[] | { items: SocialAccount[] }>('/api/social/accounts')
    return { items: unwrapItems(data) }
  },
  connectAccount: (body: ConnectSocialAccountBody) =>
    request<SocialAccount>('/api/social/accounts', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  disconnectAccount: (id: number) =>
    request<SocialAccount>(`/api/social/accounts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'disconnected' }),
    }),
  reconnectAccount: (id: number) =>
    request<SocialAccount>(`/api/social/accounts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'connected' }),
    }),
  syncAccount: (id: number) =>
    request<SocialAccount>(`/api/social/accounts/${id}/sync`, { method: 'POST' }),
  listContacts: async (params: ListContactsQuery = {}, init: RequestInit = {}) => {
    const data = await request<SocialContact[] | { items: SocialContact[] }>(
      `/api/social/contacts${contactsQuery(params)}`,
      init,
    )
    return { items: unwrapItems(data) }
  },
  patchContact: (id: number, body: PatchContactBody) =>
    request<SocialContact>(`/api/social/contacts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
}
