/** 活动审核和发布安排。真实发布由后端开关及账号校验控制。 */

export type ReviewAsset = { asset_id: number; role: string; position: number }

export type StoryboardShot = {
  prompt: string
  first_frame_task_id: number | null
  first_frame_asset_id: number | null
  first_frame_review: 'approved' | 'rejected' | null
  video_task_id: number | null
  video_asset_id: number | null
  video_review: 'approved' | 'rejected' | null
  locked: boolean
}
export type Storyboard = {
  mode: 'reviewed_shots_v1'
  product_asset_id: number
  fact_version_id: number
  shots: StoryboardShot[]
}

export type ReviewVariant = {
  id: number
  platform: string
  version: number
  title: string | null
  body: string | null
  hashtags: string[]
  status: string
  fact_version_id: number | null
  qc_result: Record<string, unknown> | null
  storyboard?: Storyboard | null
  assets: ReviewAsset[]
}

export type ReviewRecord = {
  id: number
  variant_id: number
  version: number
  reviewer_id: number
  decision: string
  comment: string | null
  reviewed_at: string
  copy_snapshot: { title?: string | null; body?: string | null; hashtags?: string[] }
  asset_order: ReviewAsset[]
  fact_version_id: number
  fact_snapshot: Record<string, unknown>
  qc_snapshot: Record<string, unknown>
}

export type ReviewPlatform = {
  platform: string
  current: ReviewVariant
  versions: ReviewVariant[]
  reviews: ReviewRecord[]
  storyboard_tasks: Record<string, { status: string; error_message: string | null }>
  blockers: { code: string; message: string }[]
  steps: {
    step_key: string
    version: number
    status: string
    error_code: string | null
    variant_id: number | null
    qc_issue: string | null
    review_notes: string[]
    lines: string[]
    source?: string | null
  }[]
}

export type ReviewPayload = {
  campaign_id: number
  status: string
  fact_version_id: number
  fact_version: number | null
  facts: Record<string, unknown>
  product_name: string
  primary_asset_id: number | null
  generation_budget: number
  budget_reserved: number
  budget_remaining: number
  provider_ready: { image: boolean; video: boolean }
  platforms: ReviewPlatform[]
  ark_live: string
}

export type CampaignStep = {
  id: number; step_key: string; variant_platform: string; version: number
  status: string; depends_on: string[]; attempt: number; error_code: string | null
  local_request_id?: string | null; provider_request_id?: string | null
  output: Record<string, unknown>
}

export type CampaignDetail = {
  id: number; product_id: number; fact_version_id: number; status: string
  brief: Record<string, unknown>; selected_source_item_ids: number[]
  target_platforms: string[]; variants: ReviewVariant[]
  run: { id: number; generation_budget: number; budget_reserved?: number; started_at: string | null; steps: CampaignStep[] } | null
}

export type OverviewBucket = 'needs_info' | 'pending_generation' | 'generating' | 'needs_review' | 'approved_ready' | 'publication_exception'
export type OverviewItem = {
  campaign_id: number; platform: string; version: number; bucket: OverviewBucket
  status: string; blockers: { code: string; message: string }[]; next_action: string
  generation_connection?: string
}
export type Overview = { dimension: 'platform_variant'; counts: Record<OverviewBucket, number>; items: OverviewItem[] }

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
    const detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : data
    let message = '请求失败'
    if (typeof detail === 'string') message = detail
    else if (detail && typeof detail === 'object' && 'message' in detail) {
      const text = (detail as { message: unknown }).message
      if (typeof text === 'string' && text) message = text
    }
    const error = new Error(message) as Error & { status?: number; detail?: unknown }
    error.status = res.status
    error.detail = detail
    throw error
  }
  return data as T
}

export const campaignApi = {
  list: () => request<{ items: { id: number; status: string; product_id: number }[] }>('/api/campaigns'),
  overview: () => request<Overview>('/api/campaigns/overview'),
  detail: (id: number) => request<CampaignDetail>(`/api/campaigns/${id}`),
  create: (body: { product_id: number; fact_version_id: number; source_item_ids: number[]; target_platforms: string[]; generation_budget: number; generation_requirements: string }) =>
    request<CampaignDetail>('/api/campaigns', { method: 'POST', body: JSON.stringify(body) }),
  start: (id: number, key: string) =>
    request<CampaignDetail>(`/api/campaigns/${id}/start`, { method: 'POST', headers: { 'Idempotency-Key': key } }),
  review: (id: number) => request<ReviewPayload>(`/api/campaigns/${id}/review`),
  startStoryboard: (id: number, version: number) =>
    request<{ id: number; version: number }>(`/api/campaigns/${id}/storyboard/start`, {
      method: 'POST', headers: { 'If-Match': String(version) }, body: JSON.stringify({ expected_version: version }),
    }),
  shotAction: (id: number, shotIndex: number, action: string, version: number, extra: Record<string, unknown> = {}) =>
    request<{ id: number; version: number }>(`/api/campaigns/${id}/storyboard/shots/${shotIndex}/${action}`, {
      method: 'POST', headers: { 'If-Match': String(version) },
      body: JSON.stringify({ expected_version: version, ...extra }),
    }),
  edit: (id: number, platform: string, version: number, body: { title: string; body: string; hashtags: string[] }) =>
    request(`/api/campaigns/${id}/variants/${platform}`, {
      method: 'PATCH',
      headers: { 'If-Match': String(version) },
      body: JSON.stringify({ ...body, expected_version: version }),
    }),
  approve: (id: number, platform: string, version: number, comment: string) =>
    request(`/api/campaigns/${id}/variants/${platform}/approve`, {
      method: 'POST',
      headers: { 'If-Match': String(version) },
      body: JSON.stringify({ expected_version: version, comment }),
    }),
  reject: (id: number, platform: string, version: number, comment: string) =>
    request(`/api/campaigns/${id}/variants/${platform}/reject`, {
      method: 'POST',
      headers: { 'If-Match': String(version) },
      body: JSON.stringify({ expected_version: version, comment }),
    }),
  redo: (id: number, platform: string, version: number, stepKeys: string[], comment: string) =>
    request(`/api/campaigns/${id}/variants/${platform}/redo`, {
      method: 'POST',
      headers: { 'If-Match': String(version) },
      body: JSON.stringify({ expected_version: version, step_keys: stepKeys, comment }),
    }),
  revoke: (id: number, platform: string, version: number, comment: string) =>
    request<{ notice?: string | null }>(`/api/campaigns/${id}/variants/${platform}/revoke`, {
      method: 'POST',
      headers: { 'If-Match': String(version) },
      body: JSON.stringify({ expected_version: version, comment }),
    }),
  publishDesk: (id: number) => request<PublishDesk>(`/api/campaigns/${id}/publish`),
  schedule: (
    id: number,
    body: {
      platform: string
      expected_version: number
      scheduled_at: string
      connection_id: number | null
      idempotency_key: string
    },
  ) =>
    request<PublishJob>(`/api/campaigns/${id}/publish`, {
      method: 'POST',
      headers: { 'Idempotency-Key': body.idempotency_key },
      body: JSON.stringify(body),
    }),
  cancelPublish: (id: number, jobId: number) =>
    request<PublishJob>(`/api/campaigns/${id}/publish/${jobId}/cancel`, { method: 'POST' }),
  reconfirmPublish: (id: number, jobId: number, scheduled_at: string) =>
    request<PublishJob>(`/api/campaigns/${id}/publish/${jobId}/reconfirm`, {
      method: 'POST',
      body: JSON.stringify({ scheduled_at }),
    }),
}

export type PublishConnection = {
  id: number
  external_account_id: string
  status: string
  scope_set: string[]
  expires_at: string | null
  missing: string[]
  readiness: string
}

export type PublishSide = {
  platform: string
  variant_id: number
  version: number
  variant_status: string
  review_id: number | null
  content_ready: boolean
  content_blockers: { code: string; message: string }[]
  readiness: string
  connection_state: string
  missing: string[]
  catalog: {
    account_type?: string
    limits?: string[]
    server_publish?: boolean
    implemented?: boolean
    live_enabled?: boolean
    live_verified?: string
  }
  connections: PublishConnection[]
  campaign_status: string
}

export type PublishJob = {
  id: number
  platform: string
  connection_id: number | null
  variant_id: number
  version: number
  review_id: number
  scheduled_at: string
  status: string
  phase: string
  next_action: string | null
  readiness: string
  copy_snapshot: { title?: string | null; body?: string | null }
  missing: string[]
  error_code: string | null
  error_message: string | null
  maybe_submitted: boolean
  notice: string | null
  cancelable: boolean
  publish_url: null
  cover_image_id: string | null
  video_upload_id: string | null
  content_item_id: string | null
  content_video_id: string | null
  cover_log_id: string | null
  video_log_id: string | null
  create_log_id: string | null
}

export type PublishDesk = {
  campaign_id: number
  campaign_status: string
  timezone_storage: string
  platforms: PublishSide[]
  jobs: PublishJob[]
}
