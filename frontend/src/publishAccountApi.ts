/** 真实发布账号；与 /api/social 的演示联系人完全分开。 */

export type PublishAccount = {
  id: number
  platform: string
  purpose: string
  external_account_id: string
  status: string
  scopes: string[]
  expires_at: string | null
  credential_kind: string
  missing: string[]
  app_publish_capability: string
}

export type PublishAccountsResponse = {
  items: PublishAccount[]
  douyin: {
    oauth_enabled: boolean
    configuration_missing: string[]
    app_publish_capability: string
  }
  xiaohongshu: { implemented: boolean; status: string }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init.headers },
  })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload
      ? (payload as { detail: unknown }).detail : null
    const code = detail && typeof detail === 'object' && 'code' in detail
      ? (detail as { code: unknown }).code : null
    const safeCode = typeof code === 'string' && /^[A-Za-z_]{1,64}$/.test(code) ? code : ''
    if (response.status === 403 && safeCode === 'ACCOUNT_FROZEN') {
      window.dispatchEvent(new CustomEvent('account-frozen'))
    }
    if (response.status === 401) throw new Error('登录已过期，请重新登录')
    throw new Error(errorLabel(safeCode) || `请求失败（HTTP ${response.status}）`)
  }
  return payload as T
}

export function errorLabel(code: string): string {
  const labels: Record<string, string> = {
    oauth_disabled: '授权入口尚未启用',
    oauth_unconfigured: '授权配置缺失或无效',
    session_required: '登录会话已失效，请重新登录',
    state_invalid: '授权状态无效或已使用，请重新发起授权',
    authorization_declined: '授权未完成，请重新发起授权',
    authorization_rejected: '平台未接受此次授权，请重新发起授权',
    authorization_stale: '授权已被更新，请刷新账号状态后再决定是否重新授权',
    permission_denied: '未获得代发权限，请检查应用能力及用户授权',
    auth_expired: '授权已过期，请重新授权',
    reauthorize_required: '刷新凭证已过期，请重新授权',
    wrong_application: '应用标识不匹配，请检查部署配置',
    account_missing: '发布账号不存在或不可用',
    refresh_unknown: '刷新结果不明，不要重试旧刷新凭证；请重新授权',
    exchange_unknown: '本次换取授权凭证结果不明；请重新发起授权，取得新的状态和授权码',
    response_invalid: '平台响应无法确认，请联系管理员核查',
    credential_invalid: '发布凭证不可用，请重新授权',
    encryption_unavailable: '凭证加密配置不可用，请联系管理员',
  }
  return labels[code] || ''
}

export const publishAccountApi = {
  list: () => request<PublishAccountsResponse>('/api/publish-accounts'),
  start: (connectionId?: number) => request<{ authorization_url: string }>('/api/publish-accounts/douyin/start', {
    method: 'POST', body: JSON.stringify(connectionId == null ? {} : { connection_id: connectionId }),
  }),
  refresh: (id: number) => request<{ status: string }>(`/api/publish-accounts/${id}/refresh`, { method: 'POST' }),
  disconnect: (id: number) => request<{ status: string; platform_revoked: boolean }>(`/api/publish-accounts/${id}/disconnect`, { method: 'POST' }),
}
