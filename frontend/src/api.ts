export type User = {
  id: number
  username: string
  role: string
  is_frozen?: boolean
  freeze_reason?: string | null
  frozen_at?: string | null
}

export type Asset = {
  id: number
  role: string
  url?: string | null
  mime?: string | null
  size_bytes?: number | null
  width?: number | null
  height?: number | null
}

/** 人工审核状态：默认未审；仅 succeeded 可标可用/需重做 */
export type ReviewStatus = 'unreviewed' | 'usable' | 'needs_revision'

export type Generation = {
  id: number
  mode: string
  prompt: string
  params: Record<string, unknown>
  status: string
  error_message?: string | null
  retry_of_id?: number | null
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  assets: Asset[]
  /** 旧 input 未回填标注时由后端给出 */
  legacy_unlabeled?: boolean
  reverse_op_id?: number | null
  optimize_op_id?: number | null
  review_status?: ReviewStatus | string | null
  review_reason?: string | null
  reviewed_at?: string | null
  reviewed_by_id?: number | null
}

export type PromptOperation = {
  id: number
  op_type: string
  input_text?: string | null
  input_asset_id?: number | null
  input_object_key?: string | null
  output_prompt?: string | null
  status: string
  error_message?: string | null
  created_at: string
  asset?: Asset | null
}

export type CreateGenerationBody = {
  prompt: string
  size: string
  mode?: string
  retry_of_id?: number
  /** 兼容旧契约：[0]=商品 [1]=场景 */
  input_asset_ids?: number[]
  /** 新 i2i：商品资产 */
  product_asset_id?: number
  /** 新 i2i：场景资产，可空 */
  scene_asset_id?: number
  /** 保留特征意图文案，写入 params.keep_features */
  keep_features?: string
  reverse_op_id?: number
  optimize_op_id?: number
}

export type PatchReviewBody = {
  review_status: ReviewStatus
  review_reason?: string
}

/** 历史「填回工作台」sessionStorage key */
export const WORKBENCH_FILL_KEY = 'workbench_fill_v1'

export type WorkbenchFillPayload = {
  mode?: 't2i' | 'i2i' | 'i2v'
  prompt?: string
  size?: string
  keep_features?: string
  product_asset_id?: number
  scene_asset_id?: number
  /** 旧 input 按 position 填回；不标成已标注 product/scene */
  input_asset_ids?: number[]
  product_preview_url?: string
  scene_preview_url?: string
  input_preview_urls?: string[]
  retry_of_id?: number
  notification_id?: number
}

export type RevisionNotice = {
  id: number
  task_id: number
  reason: string
  status: string
  created_at: string
  handled_at?: string | null
}

type AccountFrozenHandler = () => void
let accountFrozenHandler: AccountFrozenHandler | null = null

/** AppLayout 注册跳转限制页；本模块不 import router。 */
export function setOnAccountFrozen(handler: AccountFrozenHandler | null) {
  accountFrozenHandler = handler
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
      accountFrozenHandler?.()
    }
    throw Object.assign(new Error(detailMessage(data, res.statusText)), { status: res.status, detail: data })
  }
  return data as T
}

async function downloadAssetFile(id: number): Promise<void> {
  const res = await fetch(`/api/assets/${id}/file`, { credentials: 'include' })
  if (res.status === 401) {
    throw Object.assign(new Error('未登录'), { status: 401 })
  }
  if (!res.ok) {
    const text = await res.text()
    let data: unknown = text
    if (text) {
      try {
        data = JSON.parse(text)
      } catch {
        data = text
      }
    }
    if (res.status === 403 && isAccountFrozenPayload(data)) {
      window.dispatchEvent(new CustomEvent('account-frozen'))
      accountFrozenHandler?.()
    }
    throw Object.assign(new Error(detailMessage(data, res.statusText)), { status: res.status, detail: data })
  }
  const blob = await res.blob()
  const header = res.headers.get('Content-Disposition')
  const matched = header && /filename="([^"]+)"/.exec(header)
  const filename = matched?.[1] || `yingpeng-${id}.png`
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export const api = {
  login: (username: string, password: string) =>
    request<User>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  me: () => request<User>('/api/auth/me'),
  createGeneration: (body: CreateGenerationBody) =>
    request<Generation>('/api/generations', {
      method: 'POST',
      body: JSON.stringify({ mode: 't2i', ...body }),
    }),
  getGeneration: (id: number) => request<Generation>(`/api/generations/${id}`),
  listGenerations: () => request<{ items: Generation[] }>('/api/generations'),
  /** 任务 owner 提交/修改人工决定 */
  patchReview: (id: number, body: PatchReviewBody) =>
    request<Generation>(`/api/generations/${id}/review`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  uploadAsset: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{ id: number; mime: string; size_bytes: number }>('/api/assets', {
      method: 'POST',
      body: fd,
    })
  },
  downloadAsset: (id: number) => downloadAssetFile(id),
  optimizePrompt: (text: string) =>
    request<{ prompt: string }>('/api/prompts/optimize', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  reversePrompt: (assetId: number) =>
    request<{ prompt: string }>('/api/prompts/reverse', {
      method: 'POST',
      body: JSON.stringify({ asset_id: assetId }),
    }),
  listPromptOperations: () =>
    request<{ items: PromptOperation[] }>('/api/prompts/operations'),
  deleteGeneration: (id: number) =>
    request<Generation>(`/api/generations/${id}`, { method: 'DELETE' }),
  listNotifications: () => request<{ items: RevisionNotice[] }>('/api/notifications'),
  handleNotification: (id: number) =>
    request<RevisionNotice>(`/api/notifications/${id}/handle`, { method: 'PATCH' }),
}
