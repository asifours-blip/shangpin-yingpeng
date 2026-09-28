import type { ReviewPlatform } from './campaignApi'

export function hasCurrentApproval(side: ReviewPlatform): boolean {
  return side.current.status === 'approved' && side.reviews.some(
    (review) => review.decision === 'approved'
      && review.variant_id === side.current.id
      && review.version === side.current.version,
  )
}

function exportUrl(campaignId: number, platform: string, version: number): string {
  return `/api/campaigns/${campaignId}/variants/${encodeURIComponent(platform)}/export?version=${version}`
}

function errorMessage(status: number, code: string | null): string {
  if (status === 404) return '活动或平台不存在，或当前账号无权查看。'
  if (status === 409) return '活动版本已变化，请刷新后核对当前已审版本。'
  if (status === 422) {
    if (code === 'asset_unreadable' || code === 'assets_missing') return '审核资产缺失或无法读取，请先核对媒体。'
    return '当前版本未通过有效审核，或交付资产不完整。请先查看审核阻塞项。'
  }
  return '素材导出暂不可用，请稍后重试。'
}

export async function downloadApprovedAssets(
  campaignId: number,
  side: ReviewPlatform,
  onDownloadError: (message: string) => void,
): Promise<void> {
  if (!hasCurrentApproval(side)) throw new Error('只有当前有效审核版本可以导出。')
  const url = exportUrl(campaignId, side.platform, side.current.version)
  const preflight = await fetch(url, { method: 'HEAD', credentials: 'include', cache: 'no-store' })
  if (!preflight.ok) {
    throw Object.assign(new Error(errorMessage(preflight.status, preflight.headers.get('X-Export-Error-Code'))), { status: preflight.status })
  }

  const frame = document.createElement('iframe')
  frame.hidden = true
  frame.title = '素材下载'
  const cleanup = () => { frame.remove() }
  frame.addEventListener('load', () => {
    try {
      const content = frame.contentDocument?.body?.textContent?.trim()
      if (!content) return
      if (!content.startsWith('{')) {
        onDownloadError('下载未完成，请刷新后核对当前版本与媒体。')
        cleanup()
        return
      }
      const response = JSON.parse(content) as { detail?: { message?: string } }
      onDownloadError(response.detail?.message || '下载时版本或资产发生变化，请刷新后再核对。')
      cleanup()
    } catch {
      // 浏览器下载成功时不会提供可读取的文档。
    }
  })
  document.body.appendChild(frame)
  frame.src = url
}
