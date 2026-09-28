/** 采集配置与批次。 */

export type CollectionConfig = {
  id: number
  provider: string
  item_kind: string
  category: string
  query: string
  window: string
  sort_metric: string
  max_items: number
  schedule: string
  enabled: boolean
}

export type CollectionRun = {
  id: number
  config_id: number
  status: string
  actual_count: number
  scope_description: string
  error_summary?: string | null
  provider_response_version?: string | null
  dropped_duplicate: number
  dropped_invalid: number
  started_at: string
  finished_at?: string | null
}

export type CollectionRunSummary = CollectionRun & {
  provider: string; item_kind: string; sort_metric: string; window: string
}

export type SourceItem = {
  id: number
  platform: string
  item_kind: string
  external_id: string
  url?: string | null
  title?: string | null
  text_excerpt?: string | null
  source_rank?: number | null
  raw_metrics: Record<string, unknown>
  observed_at: string
  rights_scope: string
}

export type SourceItemPage = {
  items: SourceItem[]
  total: number
  scope_description: string
  actual_count: number
  status: string
}

type ApiError = Error & { status: number }

function detailMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== 'object' || !('detail' in data)) return fallback
  const detail = (data as { detail: unknown }).detail
  if (typeof detail === 'string' && detail) return detail
  return fallback
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const res = await fetch(path, { ...init, headers, credentials: 'include' })
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (res.status === 403 && data && typeof data === 'object') {
    const detail = (data as { detail?: { code?: string } }).detail
    if (detail && detail.code === 'ACCOUNT_FROZEN') {
      window.dispatchEvent(new CustomEvent('account-frozen'))
    }
  }
  if (!res.ok) {
    throw Object.assign(new Error(detailMessage(data, '请求失败')), { status: res.status }) as ApiError
  }
  return data as T
}

export const sourceApi = {
  listConfigs: () => request<{ items: CollectionConfig[] }>('/api/collections/configs'),
  listRuns: () => request<{ items: CollectionRunSummary[] }>('/api/collections/runs?per_page=50'),
  createConfig: (body: {
    provider: string
    item_kind: string
    category: string
    query: string
    window: string
    sort_metric: string
    max_items: number
  }) => request<CollectionConfig>('/api/collections/configs', { method: 'POST', body: JSON.stringify(body) }),
  startRun: (configId: number) =>
    request<CollectionRun>('/api/collections/runs', {
      method: 'POST',
      body: JSON.stringify({ config_id: configId }),
    }),
  getItems: (runId: number) => request<SourceItemPage>(`/api/collections/runs/${runId}/items?per_page=100`),
}
