<template>
  <AppLayout>
    <div class="panel admin-page">
      <div class="page-bar">
        <h2>生成记录</h2>
        <div class="actions">
          <el-button size="small" :loading="currentLoading" @click="load">刷新</el-button>
        </div>
      </div>

      <el-radio-group v-model="mainTab" class="record-tabs" @change="onMainTabChange">
        <el-radio-button label="images">生成任务</el-radio-button>
        <el-radio-button label="copy">文案记录</el-radio-button>
      </el-radio-group>

      <template v-if="mainTab === 'images'">
        <div class="page-filters">
          <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 140px">
            <el-option label="排队中" value="queued" />
            <el-option label="生成中" value="running" />
            <el-option label="成功" value="succeeded" />
            <el-option label="失败" value="failed" />
          </el-select>
          <el-select v-model="modeFilter" placeholder="全部类型" clearable style="width: 140px">
            <el-option label="文生图" value="t2i" />
            <el-option label="参考图" value="i2i" />
            <el-option label="图生视频" value="i2v" />
          </el-select>
          <el-select v-model="reviewFilter" placeholder="全部决定" clearable style="width: 140px">
            <el-option label="未审核" value="unreviewed" />
            <el-option label="可用" value="usable" />
            <el-option label="需重做" value="needs_revision" />
          </el-select>
          <el-select v-model="deletedFilter" placeholder="全部删除状态" clearable style="width: 150px">
            <el-option label="用户已删除" value="deleted" />
            <el-option label="未删除" value="active" />
          </el-select>
        </div>

        <div v-if="loading && !items.length" class="state-inline">加载中…</div>
        <div v-else-if="!filtered.length" class="state-block">
          <p v-if="items.length">没有符合筛选的记录</p>
          <p v-else>还没有全站记录</p>
        </div>
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>任务 ID</th>
                <th>用户</th>
                <th>类型</th>
                <th>状态</th>
                <th>人工决定</th>
                <th>预览</th>
                <th>删除</th>
                <th>时间</th>
                <th>失败原因</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in filtered"
                :key="row.id"
                class="clickable"
                :class="{ 'is-deleted': isUserDeleted(row) }"
                @click="openDetail(row)"
              >
                <td>
                  <button
                    type="button"
                    class="task-id"
                    title="点击复制任务 ID"
                    @click.stop="copyTaskId(row.id)"
                  >
                    #{{ row.id }}
                  </button>
                </td>
                <td>{{ row.username }}</td>
                <td>{{ modeLabel(row.mode) }}</td>
                <td>
                  <span class="status-pill" :class="row.status">{{ statusLabel(row.status) }}</span>
                </td>
                <td>
                  <span>{{ reviewStatusLabel(row.review_status) }}</span>
                  <span v-if="row.review_reason" class="muted reason-inline"> · {{ row.review_reason }}</span>
                </td>
                <td>
                  <div v-if="thumbAssets(row).length" class="thumb-row" @click.stop>
                    <a
                      v-for="asset in thumbAssets(row)"
                      :key="asset.id"
                      class="thumb"
                      :href="previewUrl(asset)"
                      target="_blank"
                      rel="noopener"
                      :title="roleLabel(asset.role) + ' #' + asset.id"
                    >
                      <video
                        v-if="isVideoMime(asset.mime)"
                        :src="previewUrl(asset)"
                        muted
                        playsinline
                        preload="metadata"
                      />
                      <img v-else :src="previewUrl(asset)" :alt="roleLabel(asset.role)" />
                    </a>
                  </div>
                  <span v-else class="muted">—</span>
                </td>
                <td>
                  <span v-if="isUserDeleted(row)" class="deleted-tag">用户已删除</span>
                  <span v-else class="muted">—</span>
                </td>
                <td class="mono">{{ formatTime(row.created_at) }}</td>
                <td class="err">{{ row.error_message || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <template v-else>
        <p class="tab-note">独立文案记录，不是生图任务。</p>
        <div v-if="copyLoading && !copyItems.length" class="state-inline">加载中…</div>
        <div v-else-if="!copyItems.length" class="state-block">
          <p>还没有文案记录</p>
        </div>
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>记录 ID</th>
                <th>用户</th>
                <th>平台</th>
                <th>状态</th>
                <th>标题摘要</th>
                <th>删除</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in copyItems"
                :key="'copy-' + row.id"
                class="clickable"
                :class="{ 'is-deleted': isUserDeleted(row) }"
                @click="openCopyDetail(row)"
              >
                <td>
                  <button
                    type="button"
                    class="task-id"
                    title="点击复制记录 ID"
                    @click.stop="copyTaskId(row.id)"
                  >
                    #{{ row.id }}
                  </button>
                </td>
                <td>{{ copyUserLabel(row) }}</td>
                <td>{{ platformLabel(row.platform) }}</td>
                <td>
                  <span class="status-pill" :class="copyStatusClass(row.status)">
                    {{ copyStatusLabel(row.status) }}
                  </span>
                </td>
                <td>
                  <span class="title-clip" :title="copyTitle(row)">{{ copyTitle(row) }}</span>
                </td>
                <td>
                  <span v-if="isUserDeleted(row)" class="deleted-tag">用户已删除</span>
                  <span v-else class="muted">—</span>
                </td>
                <td class="mono">{{ formatTime(row.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </div>

    <el-dialog
      v-model="detailOpen"
      title="任务核验"
      width="640px"
      :close-on-click-modal="true"
    >
      <template v-if="detail">
        <dl class="detail">
          <div>
            <dt>任务 ID</dt>
            <dd>
              <button type="button" class="task-id" @click="copyTaskId(detail.id)">
                {{ detail.id }}
              </button>
            </dd>
          </div>
          <div>
            <dt>用户</dt>
            <dd>{{ detail.username }}</dd>
          </div>
          <div>
            <dt>类型</dt>
            <dd>{{ modeLabel(detail.mode) }}</dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>
              <span class="status-pill" :class="detail.status">{{ statusLabel(detail.status) }}</span>
            </dd>
          </div>
          <div>
            <dt>时间</dt>
            <dd class="mono">{{ formatTime(detail.created_at) }}</dd>
          </div>
          <div v-if="isUserDeleted(detail)">
            <dt>删除标记</dt>
            <dd>
              <span class="deleted-tag">用户已删除</span>
              <span v-if="detail.user_deleted_at" class="muted">
                · {{ formatTime(detail.user_deleted_at) }}
              </span>
              <span v-if="detail.user_deleted_by" class="muted">
                · 用户 #{{ detail.user_deleted_by }}
              </span>
            </dd>
          </div>
          <div v-if="detail.error_message">
            <dt>失败原因</dt>
            <dd class="err">{{ detail.error_message }}</dd>
          </div>
          <div v-if="detailPrompt">
            <dt>最终 Prompt</dt>
            <dd class="prompt">{{ detailPrompt }}</dd>
          </div>
          <div>
            <dt>params</dt>
            <dd>
              <ul v-if="paramEntries.length" class="param-list">
                <li v-for="row in paramEntries" :key="row.key">
                  <span class="param-key">{{ row.key }}</span>
                  <span class="param-val mono">{{ row.value }}</span>
                </li>
              </ul>
              <span v-else class="muted">不可用</span>
            </dd>
          </div>
          <div>
            <dt>人工决定</dt>
            <dd>
              <span>{{ reviewStatusLabel(detail.review_status) }}</span>
              <span v-if="detail.review_reason"> · {{ detail.review_reason }}</span>
              <span v-if="detail.reviewed_at" class="muted"> · {{ formatTime(detail.reviewed_at) }}</span>
            </dd>
          </div>
          <div v-if="detailInputs.length || detailLegacyInputs.length">
            <dt>输入图</dt>
            <dd>
              <p v-if="detailLegacyInputs.length || detail.legacy_unlabeled" class="legacy-note">
                旧记录未标注
              </p>
              <div class="asset-grid">
                <a
                  v-for="asset in detailInputs"
                  :key="'in-' + asset.id"
                  class="asset-card"
                  :href="previewUrl(asset)"
                  target="_blank"
                  rel="noopener"
                >
                  <img :src="previewUrl(asset)" :alt="roleLabel(asset.role)" />
                  <span class="asset-meta">#{{ asset.id }} · {{ roleLabel(asset.role) }}</span>
                </a>
                <a
                  v-for="(asset, idx) in detailLegacyInputs"
                  :key="'legacy-' + asset.id"
                  class="asset-card"
                  :href="previewUrl(asset)"
                  target="_blank"
                  rel="noopener"
                >
                  <img :src="previewUrl(asset)" alt="旧记录未标注" />
                  <span class="asset-meta">
                    #{{ asset.id }} · 旧记录未标注
                    <template v-if="idx === 0">（疑似商品）</template>
                    <template v-else-if="idx === 1">（疑似场景）</template>
                  </span>
                </a>
              </div>
            </dd>
          </div>
          <div v-if="detailOutputs.length">
            <dt>结果</dt>
            <dd>
              <div class="asset-grid">
                <a
                  v-for="asset in detailOutputs"
                  :key="'out-' + asset.id"
                  class="asset-card"
                  :href="previewUrl(asset)"
                  target="_blank"
                  rel="noopener"
                >
                  <video
                    v-if="isVideoMime(asset.mime)"
                    :src="previewUrl(asset)"
                    muted
                    playsinline
                    preload="metadata"
                  />
                  <img v-else :src="previewUrl(asset)" alt="结果" />
                  <span class="asset-meta">#{{ asset.id }} · 结果</span>
                </a>
              </div>
            </dd>
          </div>
          <div v-if="compareProductUrl || compareOutputUrl">
            <dt>对照</dt>
            <dd>
              <div class="compare-grid">
                <figure class="compare-card">
                  <img v-if="compareProductUrl" :src="compareProductUrl" alt="原商品" />
                  <div v-else class="compare-empty muted">无原商品</div>
                  <figcaption>原商品</figcaption>
                </figure>
                <figure class="compare-card">
                  <video
                    v-if="detail && isVideoItem(detail) && compareOutputUrl"
                    :src="compareOutputUrl"
                    controls
                    playsinline
                    preload="metadata"
                  />
                  <img v-else-if="compareOutputUrl" :src="compareOutputUrl" alt="生成结果" />
                  <div v-else class="compare-empty muted">无结果</div>
                  <figcaption>生成结果</figcaption>
                </figure>
              </div>
            </dd>
          </div>
          <div v-if="!detailInputs.length && !detailLegacyInputs.length && !detailOutputs.length">
            <dt>资产</dt>
            <dd class="muted">无关联图片</dd>
          </div>
        </dl>

        <div class="review-box">
          <p class="review-title">修改人工决定</p>
          <p class="review-hint">管理员可改当前决定。仅成功任务可标「可用 / 需重做」；需人工确认，不是自动通过。</p>
          <div class="review-actions">
            <el-button
              type="success"
              size="small"
              :loading="reviewSaving && reviewDraft === 'usable'"
              :disabled="reviewSaving || detail.status !== 'succeeded'"
              @click="saveReview('usable')"
            >
              可用
            </el-button>
            <el-button
              type="warning"
              size="small"
              :loading="reviewSaving && reviewDraft === 'needs_revision'"
              :disabled="reviewSaving || detail.status !== 'succeeded'"
              @click="prepareNeedsRevision"
            >
              需重做
            </el-button>
          </div>
          <template v-if="reviewDraft === 'needs_revision' || detail.review_status === 'needs_revision'">
            <el-select
              v-model="reviewReason"
              placeholder="选择或填写原因（必填）"
              filterable
              allow-create
              default-first-option
              style="width: 100%; margin-top: 10px"
            >
              <el-option v-for="opt in REVIEW_REASONS" :key="opt" :label="opt" :value="opt" />
            </el-select>
            <p v-if="reviewDraft === 'needs_revision' && !reviewReason.trim()" class="review-required">
              打回必须填写原因
            </p>
          </template>
          <el-button
            v-if="reviewDraft === 'needs_revision'"
            style="margin-top: 10px"
            type="primary"
            size="small"
            :loading="reviewSaving"
            :disabled="reviewSaving || !reviewReason.trim()"
            @click="saveReview('needs_revision')"
          >
            确认需重做
          </el-button>
        </div>
      </template>
      <div v-else-if="detailLoading" class="state-inline">加载详情…</div>
    </el-dialog>

    <CopyDetailDialog
      v-model="copyDetailOpen"
      :record="copyDetail"
      :username="copyDetail ? copyUserLabel(copyDetail) : ''"
    />
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import CopyDetailDialog from '../components/CopyDetailDialog.vue'
import {
  adminApi,
  adminAssetPreview,
  type AdminAsset,
  type AdminGeneration,
} from '../adminApi'
import type { ReviewStatus } from '../api'

/** 本页扩展：adminApi 并行在写，删除字段先在此声明 */
type AdminGenerationRow = AdminGeneration & {
  user_deleted_at?: string | null
  user_deleted_by?: number | null
}

type AdminCopywriting = {
  id: number
  user_id: number
  username?: string
  platform: string
  status: string
  product_facts?: Record<string, unknown> | null
  generated_content?: { title?: string; body?: string; hashtags?: string[]; facts_to_confirm?: string[] } | null
  edited_content?: { title?: string; body?: string; hashtags?: string[]; facts_to_confirm?: string[] } | null
  risk_result?: unknown
  error_message?: string | null
  created_at: string
  updated_at?: string
  user_deleted_at?: string | null
  user_deleted_by?: number | null
}

const REVIEW_REASONS = ['结构变化', '颜色偏差', '细节错误', '融合不自然', '原商品残留', '其他'] as const

const mainTab = ref<'images' | 'copy'>('images')
const items = ref<AdminGenerationRow[]>([])
const loading = ref(false)
const copyItems = ref<AdminCopywriting[]>([])
const copyLoading = ref(false)
const copyLoaded = ref(false)
const statusFilter = ref('')
const modeFilter = ref('')
const reviewFilter = ref('')
const deletedFilter = ref('')
const detailOpen = ref(false)
const detail = ref<AdminGenerationRow | null>(null)
const detailLoading = ref(false)
const copyDetailOpen = ref(false)
const copyDetail = ref<AdminCopywriting | null>(null)
const reviewDraft = ref<ReviewStatus | ''>('')
const reviewReason = ref('')
const reviewSaving = ref(false)

const currentLoading = computed(() => (mainTab.value === 'copy' ? copyLoading.value : loading.value))

const filtered = computed(() =>
  items.value.filter((item) => {
    if (statusFilter.value && item.status !== statusFilter.value) return false
    if (modeFilter.value && item.mode !== modeFilter.value) return false
    if (reviewFilter.value) {
      const st = item.review_status || 'unreviewed'
      if (st !== reviewFilter.value) return false
    }
    if (deletedFilter.value === 'deleted' && !isUserDeleted(item)) return false
    if (deletedFilter.value === 'active' && isUserDeleted(item)) return false
    return true
  }),
)

const detailPrompt = computed(() => detail.value?.prompt || '')

const paramEntries = computed(() => {
  const params = detail.value?.params
  if (!params || typeof params !== 'object' || Array.isArray(params)) return []
  return Object.entries(params).map(([key, value]) => ({
    key,
    value: formatParamValue(value),
  }))
})

const detailInputs = computed(() =>
  (detail.value?.assets || []).filter((a) => a.role === 'product' || a.role === 'scene'),
)

const detailLegacyInputs = computed(() =>
  (detail.value?.assets || []).filter((a) => a.role === 'input' || a.role === 'reference'),
)

const detailOutputs = computed(() =>
  (detail.value?.assets || []).filter((a) => a.role === 'output'),
)

const compareProductUrl = computed(() => {
  const assets = detail.value?.assets || []
  const product = assets.find((a) => a.role === 'product')
  if (product) return previewUrl(product)
  const legacy = assets.find((a) => a.role === 'input' || a.role === 'reference')
  return legacy ? previewUrl(legacy) : ''
})

const compareOutputUrl = computed(() => {
  const out = detailOutputs.value[0]
  return out ? previewUrl(out) : ''
})

function isUserDeleted(row: { user_deleted_at?: string | null }) {
  return Boolean(row.user_deleted_at)
}

function modeLabel(mode: string) {
  if (mode === 't2i') return '文生图'
  if (mode === 'i2i') return '参考图'
  if (mode === 'i2v') return '图生视频'
  if (mode === 'reverse' || mode === 'caption') return '反推'
  if (mode === 'optimize') return '优化'
  return mode
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '生成中',
    succeeded: '成功',
    failed: '失败',
    unknown: '未知',
  }
  return map[status] || status
}

function reviewStatusLabel(status?: string | null) {
  if (status === 'usable') return '可用'
  if (status === 'needs_revision') return '需重做'
  if (!status || status === 'unreviewed') return '未审核'
  return status
}

function roleLabel(role: string) {
  if (role === 'product') return '商品'
  if (role === 'scene') return '场景'
  if (role === 'input' || role === 'reference') return '旧记录未标注'
  if (role === 'output') return '结果'
  return role
}

function platformLabel(platform: string) {
  if (platform === 'douyin') return '抖音'
  if (platform === 'xiaohongshu') return '小红书'
  return platform || '—'
}

function copyStatusLabel(status: string) {
  if (status === 'processing') return '生成中'
  if (status === 'succeeded') return '成功'
  if (status === 'failed') return '失败'
  return status || '—'
}

function copyStatusClass(status: string) {
  if (status === 'processing') return 'running'
  return status
}

function copyUserLabel(row: AdminCopywriting) {
  if (row.username) return row.username
  return `#${row.user_id}`
}

function copyTitle(row: AdminCopywriting) {
  const edited = row.edited_content?.title?.trim()
  if (edited) return edited
  const generated = row.generated_content?.title?.trim()
  if (generated) return generated
  const facts = row.product_facts
  if (facts && typeof facts.product_name === 'string' && facts.product_name.trim()) {
    return facts.product_name.trim()
  }
  return '—'
}

function openCopyDetail(row: AdminCopywriting) {
  copyDetail.value = row
  copyDetailOpen.value = true
}

function formatParamValue(value: unknown) {
  if (value == null || value === '') return '不可用'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return '不可用'
  }
}

function formatTime(iso: string) {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function previewUrl(asset: AdminAsset) {
  return adminAssetPreview(asset)
}

function isVideoMime(mime?: string | null) {
  return (mime || '').startsWith('video/')
}

function isVideoItem(row: AdminGenerationRow) {
  if (row.mode === 'i2v') return true
  const out = (row.assets || []).find((a) => a.role === 'output')
  return isVideoMime(out?.mime)
}

/** 列表缩略：优先结果图，否则商品/场景/输入，最多 3 张。软删不挡管理员预览。 */
function thumbAssets(row: AdminGenerationRow): AdminAsset[] {
  const assets = row.assets || []
  const outputs = assets.filter((a) => a.role === 'output')
  if (outputs.length) return outputs.slice(0, 3)
  const labeled = assets.filter((a) => a.role === 'product' || a.role === 'scene')
  if (labeled.length) return labeled.slice(0, 3)
  const inputs = assets.filter((a) => a.role === 'input' || a.role === 'reference')
  return inputs.slice(0, 3)
}

function replaceItem(fresh: AdminGenerationRow) {
  const idx = items.value.findIndex((g) => g.id === fresh.id)
  if (idx >= 0) items.value.splice(idx, 1, fresh)
  if (detail.value?.id === fresh.id) detail.value = fresh
}

async function copyTaskId(id: number) {
  const text = String(id)
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(`已复制任务 ID ${text}`)
  } catch {
    ElMessage.info(`任务 ID ${text}`)
  }
}

async function openDetail(row: AdminGenerationRow) {
  detailOpen.value = true
  detail.value = row
  detailLoading.value = true
  reviewDraft.value = (row.review_status as ReviewStatus) || ''
  reviewReason.value = row.review_reason || ''
  try {
    // 详情再拉一次，保证资产最新；失败则沿用列表行。管理员预览忽略软删。
    detail.value = (await adminApi.getGeneration(row.id)) as AdminGenerationRow
    reviewDraft.value = (detail.value.review_status as ReviewStatus) || ''
    reviewReason.value = detail.value.review_reason || ''
  } catch (err) {
    if (!detail.value) {
      ElMessage.error(err instanceof Error ? err.message : '加载详情失败')
      detailOpen.value = false
    }
  } finally {
    detailLoading.value = false
  }
}

function prepareNeedsRevision() {
  reviewDraft.value = 'needs_revision'
  if (!reviewReason.value.trim()) {
    ElMessage.info('请填写需重做原因后确认')
  }
}

async function saveReview(status: ReviewStatus) {
  if (!detail.value) return
  if (detail.value.status !== 'succeeded') {
    ElMessage.warning('仅成功任务可修改人工决定')
    return
  }
  if (status === 'needs_revision' && !reviewReason.value.trim()) {
    reviewDraft.value = 'needs_revision'
    ElMessage.warning('管理员打回必须填写原因')
    return
  }
  reviewDraft.value = status
  reviewSaving.value = true
  try {
    const fresh = (await adminApi.patchReview(detail.value.id, {
      review_status: status,
      review_reason: status === 'needs_revision' ? reviewReason.value.trim() : undefined,
    })) as AdminGenerationRow
    replaceItem(fresh)
    ElMessage.success(status === 'usable' ? '已改为可用' : '已改为需重做')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '修改人工决定失败')
  } finally {
    reviewSaving.value = false
  }
}

function unwrapItems<T>(data: T[] | { items?: T[] } | null | undefined): T[] {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.items)) return data.items
  return []
}

async function fetchAdminCopywriting(): Promise<AdminCopywriting[]> {
  const res = await fetch('/api/admin/copywriting', { credentials: 'include' })
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (res.status === 401) {
    throw new Error('未登录')
  }
  if (!res.ok) {
    const detailText =
      typeof data === 'object' && data && 'detail' in data
        ? String((data as { detail: unknown }).detail)
        : res.statusText
    throw new Error(detailText || '加载文案记录失败')
  }
  return unwrapItems(data as AdminCopywriting[] | { items?: AdminCopywriting[] })
}

async function loadCopy(force = false) {
  if (copyLoading.value) return
  if (copyLoaded.value && !force) return
  copyLoading.value = true
  try {
    copyItems.value = await fetchAdminCopywriting()
    copyLoaded.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载文案记录失败')
  } finally {
    copyLoading.value = false
  }
}

function onMainTabChange() {
  if (mainTab.value === 'copy') loadCopy()
}

async function load() {
  if (mainTab.value === 'copy') {
    await loadCopy(true)
    return
  }
  loading.value = true
  try {
    const data = await adminApi.listGenerations()
    items.value = data.items as AdminGenerationRow[]
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.admin-page {
  padding: 20px;
}

.record-tabs {
  margin-bottom: 16px;
}

.tab-note {
  margin: 0 0 12px;
  color: var(--muted);
  font-size: 12px;
}

.clickable {
  cursor: pointer;
}

.is-deleted td {
  opacity: 0.82;
}

.err {
  color: var(--danger);
  font-size: 12px;
  max-width: 280px;
  word-break: break-word;
}

.muted {
  color: var(--muted);
  font-size: 13px;
}

.reason-inline {
  font-size: 12px;
}

.deleted-tag {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  border: 1px solid rgba(196, 69, 56, 0.28);
  background: rgba(196, 69, 56, 0.1);
  color: var(--danger);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.title-clip {
  display: inline-block;
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.legacy-note {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 12px;
}

.thumb-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.thumb {
  display: block;
  width: 40px;
  height: 40px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--border, #e5e7eb);
  background: var(--panel-2, #f8fafc);
  flex-shrink: 0;
}

.thumb img,
.thumb video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.detail {
  margin: 0;
  display: grid;
  gap: 14px;
}

.detail > div {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 8px;
  align-items: start;
}

.detail dt {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.detail dd {
  margin: 0;
  color: var(--text);
  font-size: 14px;
  word-break: break-word;
}

.detail .prompt {
  white-space: pre-wrap;
  line-height: 1.55;
  color: var(--muted);
  font-size: 13px;
}

.param-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.param-list li {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13px;
}

.param-key {
  color: var(--muted);
  min-width: 88px;
}

.param-val {
  word-break: break-all;
}

.asset-grid,
.compare-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.asset-card {
  display: block;
  width: 120px;
  text-decoration: none;
  color: inherit;
}

.asset-card img,
.asset-card video {
  width: 120px;
  height: 120px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--border, #e5e7eb);
  background: var(--panel-2, #f8fafc);
  display: block;
}

.asset-meta {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.compare-card {
  margin: 0;
  width: 140px;
}

.compare-card img,
.compare-card video,
.compare-empty {
  width: 140px;
  height: 140px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--border, #e5e7eb);
  background: var(--panel-2, #f8fafc);
  display: grid;
  place-items: center;
}

.compare-card figcaption {
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
}

.review-box {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--line, #e5e7eb);
}

.review-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
}

.review-hint {
  margin: 0 0 10px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}

.review-required {
  margin: 6px 0 0;
  color: var(--danger);
  font-size: 12px;
}

.review-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
