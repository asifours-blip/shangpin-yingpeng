/** 营销文案。Wave 3 工作台用。自带小 request，不改 api.ts、不 import vue-router。 */

export type CopyPlatform = 'douyin' | 'xiaohongshu'

export type CopyStatus = 'processing' | 'succeeded' | 'failed'

export type GeneratedContent = {
  title: string
  body: string
  hashtags: string[]
  facts_to_confirm: string[]
}

export type CopywritingOperation = {
  id: number
  user_id?: number
  input_asset_id: number
  platform: CopyPlatform
  product_facts: Record<string, unknown>
  generated_content: GeneratedContent | null
  edited_content?: GeneratedContent | null
  risk_result?: Record<string, unknown> | null
  status: CopyStatus | string
  error_message?: string | null
  created_at: string
  updated_at?: string
}

export type CreateCopyBody = {
  asset_id: number
  platform: CopyPlatform
  product_name?: string
  selling_points?: string
  campaign?: string
}

export type PatchCopyBody = {
  edited_content?: GeneratedContent
}

export type CopyApiError = Error & { status: number; detail: unknown }

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
    throw Object.assign(new Error('未登录'), { status: 401, detail: data }) as CopyApiError
  }
  if (!res.ok) {
    throw Object.assign(new Error(detailMessage(data, res.statusText || '请求失败')), {
      status: res.status,
      detail: data,
    }) as CopyApiError
  }
  return data as T
}

export const copyApi = {
  create: (body: CreateCopyBody) =>
    request<CopywritingOperation>('/api/copywriting', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  list: async () => {
    const data = await request<CopywritingOperation[] | { items: CopywritingOperation[] }>(
      '/api/copywriting',
    )
    return { items: unwrapItems(data) }
  },
  patch: (id: number, body: PatchCopyBody) =>
    request<CopywritingOperation>(`/api/copywriting/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  delete: (id: number) => request<void>(`/api/copywriting/${id}`, { method: 'DELETE' }),
}
