/** 自家商品与事实版本。 */

export type FactVersion = {
  id: number
  version: number
  facts: Record<string, unknown>
  claim_evidence: Record<string, unknown>
  created_at: string
}

export type OwnedProduct = {
  id: number
  sku: string
  name: string
  active: boolean
  primary_asset_id?: number | null
  latest_fact?: FactVersion | null
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
  if (!res.ok) {
    const detail =
      data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : null
    const message = typeof detail === 'string' && detail ? detail : '请求失败'
    throw new Error(message)
  }
  return data as T
}

export const productApi = {
  list: () => request<{ items: OwnedProduct[] }>('/api/products'),
  create: (body: { sku: string; name: string; facts: Record<string, unknown>; primary_asset_id?: number | null }) =>
    request<OwnedProduct>('/api/products', { method: 'POST', body: JSON.stringify(body) }),
  patchFacts: (id: number, body: { facts?: Record<string, unknown>; expected_version: number; primary_asset_id?: number }) =>
    request<OwnedProduct>(`/api/products/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
}
