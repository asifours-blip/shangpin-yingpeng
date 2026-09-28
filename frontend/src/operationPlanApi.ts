export type OperationPlan = {
  id: number
  version: number
  enabled: boolean
  timezone: string
  local_time: string
  source_config_id: number | null
  product_ids: number[]
  target_platforms: string[]
  daily_campaign_limit: number
  daily_budget_limit: number
  generation_budget: number
  auto_advance_to_review: boolean
  next_due_at: string | null
}

export type PlanRun = {
  id: number
  plan_id: number
  version: number
  scheduled_for: string
  status: string
  source_run_id: number | null
  actual_count: number
  source_provider: string | null
  source_observed_at: string | null
  source_sort_metric: string | null
  source_scope_description: string | null
  source_items: { id: number; source_rank: number | null; title: string | null; observed_at: string }[]
  product_candidates: { id: number; name: string }[]
  campaign_ids: number[]
  current_daily_campaign_limit?: number
  current_daily_budget_limit?: number
  config_snapshot?: Partial<Pick<OperationPlan,
    'timezone' | 'local_time' | 'source_config_id' | 'product_ids' | 'target_platforms'
    | 'daily_campaign_limit' | 'daily_budget_limit' | 'generation_budget' | 'auto_advance_to_review'
  >> & { plan_version?: number }
  blocker_code: string | null
  blocker_message: string | null
  next_action: string | null
  missed_from?: string | null
  missed_count?: number
  created_at: string
  finished_at: string | null
}

export type PlanDraft = Pick<OperationPlan,
  'enabled' | 'timezone' | 'local_time' | 'source_config_id' | 'product_ids' | 'target_platforms'
  | 'daily_campaign_limit' | 'daily_budget_limit' | 'generation_budget' | 'auto_advance_to_review'
>

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...init, headers, credentials: 'include' })
  const data: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : null
    const message = typeof detail === 'string' ? detail : detail && typeof detail === 'object' && 'message' in detail
      ? String((detail as { message: unknown }).message) : '请求失败'
    throw Object.assign(new Error(message), { status: response.status, detail })
  }
  return data as T
}

const base = '/api/operation-plans'
export const operationPlanApi = {
  list: () => request<{ items: OperationPlan[] }>(base),
  create: (draft: PlanDraft) => request<OperationPlan>(base, { method: 'POST', body: JSON.stringify(draft) }),
  update: (id: number, version: number, change: Partial<PlanDraft>) =>
    request<OperationPlan>(`${base}/${id}`, { method: 'PATCH', body: JSON.stringify({ ...change, expected_version: version }) }),
  runs: (id: number) => request<{ items: PlanRun[] }>(`${base}/${id}/runs`),
  run: (id: number) => request<PlanRun>(`${base}/runs/${id}`),
  backfill: (planId: number, version: number, scheduledFor: string) =>
    request<PlanRun>(`${base}/${planId}/backfill`, { method: 'POST', body: JSON.stringify({ expected_version: version, scheduled_for: scheduledFor }) }),
  resume: (runId: number, version: number) =>
    request<PlanRun>(`${base}/runs/${runId}/resume`, { method: 'POST', body: JSON.stringify({ expected_version: version }) }),
  resolve: (runId: number, version: number, sourceItemId: number, productId: number) =>
    request<PlanRun>(`${base}/runs/${runId}/resolve`, { method: 'POST', body: JSON.stringify({ expected_version: version, source_item_id: sourceItemId, product_id: productId }) }),
}
