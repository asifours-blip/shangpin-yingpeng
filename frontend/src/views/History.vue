<template>
  <AppLayout>
    <div class="panel history">
      <div class="page-bar">
        <h2>我的历史</h2>
        <div class="actions">
          <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        </div>
      </div>

      <div class="page-filters">
        <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 140px">
          <el-option label="排队中" value="queued" />
          <el-option label="生成中" value="running" />
          <el-option label="成功" value="succeeded" />
          <el-option label="失败" value="failed" />
        </el-select>
        <el-select v-model="modeFilter" placeholder="全部类型" clearable style="width: 160px">
          <el-option label="文生图" value="t2i" />
          <el-option label="参考图生图" value="i2i" />
          <el-option label="图生视频" value="i2v" />
          <el-option label="Prompt 优化" value="optimize" />
        </el-select>
        <el-select v-model="reviewFilter" placeholder="全部决定" clearable style="width: 140px">
          <el-option label="未审核" value="unreviewed" />
          <el-option label="可用" value="usable" />
          <el-option label="需重做" value="needs_revision" />
        </el-select>
      </div>

      <div v-if="loading && !genItems.length && !promptItems.length" class="state-inline">加载中…</div>
      <div v-else-if="!filteredGens.length && !filteredPrompts.length" class="state-block">
        <p v-if="genItems.length || promptItems.length">没有符合筛选的记录</p>
        <template v-else>
          <p>还没有记录，去工作台</p>
          <el-button type="primary" @click="$router.push('/workbench')">去工作台</el-button>
        </template>
      </div>

      <template v-else>
        <section v-if="filteredGens.length" class="history-section">
          <h3>生成</h3>
          <div class="grid">
            <article v-for="item in filteredGens" :key="'g-' + item.id" class="card">
              <button type="button" class="thumb" :disabled="!cover(item)" @click="openImage(item)">
                <video
                  v-if="isVideoItem(item) && cover(item)"
                  :src="cover(item)"
                  muted
                  playsinline
                  preload="metadata"
                />
                <img
                  v-else-if="cover(item)"
                  :src="cover(item)"
                  alt=""
                  @error="onCoverError(item)"
                />
                <span v-else class="muted">无图</span>
              </button>
              <div class="info">
                <div class="row">
                  <span class="status-pill" :class="item.status">{{ statusLabel(item.status) }}</span>
                  <button
                    type="button"
                    class="task-id"
                    title="点击复制任务 ID"
                    @click="copyId(item.id, '任务')"
                  >
                    #{{ item.id }}
                  </button>
                </div>
                <p class="meta-line">
                  <span>{{ modeLabel(item.mode) }}</span>
                  <span class="mono time">{{ formatTime(item.created_at) }}</span>
                </p>
                <p class="meta-line">
                  <span>人工决定 · {{ reviewStatusLabel(item.review_status) }}</span>
                  <span v-if="item.review_reason" class="muted">{{ item.review_reason }}</span>
                </p>
                <p class="prompt">{{ displayText(item.prompt) }}</p>
                <p v-if="item.error_message" class="err">{{ item.error_message }}</p>
                <div class="card-ops">
                  <el-button size="small" @click="openDetail(item)">详情</el-button>
                  <el-button
                    v-if="cover(item)"
                    size="small"
                    @click="openImage(item)"
                  >
                    打开
                  </el-button>
                  <el-button
                    v-if="hasOutputAsset(item)"
                    size="small"
                    :loading="downloadingId === item.id"
                    @click="downloadImage(item)"
                  >
                    下载
                  </el-button>
                  <el-button size="small" @click="fillWorkbench(item)">填回工作台</el-button>
                  <el-button
                    v-if="item.status === 'failed' || item.status === 'succeeded'"
                    size="small"
                    :loading="retryingId === item.id"
                    @click="onRetry(item)"
                  >
                    重试
                  </el-button>
                  <el-button
                    v-if="canDeleteGeneration(item)"
                    size="small"
                    :loading="deletingId === item.id"
                    @click="onDelete(item)"
                  >
                    删除
                  </el-button>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section v-if="filteredPrompts.length" class="history-section">
          <h3>反推 / Prompt 优化</h3>
          <div class="grid">
            <article v-for="item in filteredPrompts" :key="'p-' + item.id" class="card">
              <button
                v-if="item.op_type === 'reverse'"
                type="button"
                class="thumb"
                :disabled="!promptCover(item)"
                @click="openPromptImage(item)"
              >
                <img v-if="promptCover(item)" :src="promptCover(item)" alt="" />
                <span v-else class="muted">无图</span>
              </button>
              <div class="info">
                <div class="row">
                  <span class="status-pill" :class="item.status">{{ statusLabel(item.status) }}</span>
                  <button
                    type="button"
                    class="task-id"
                    title="点击复制记录 ID"
                    @click="copyId(item.id, '记录')"
                  >
                    #{{ item.id }}
                  </button>
                </div>
                <p class="meta-line">
                  <span>{{ modeLabel(item.op_type) }}</span>
                  <span class="mono time">{{ formatTime(item.created_at) }}</span>
                </p>
                <p v-if="item.op_type === 'optimize' && item.input_text" class="prompt before">
                  优化前：{{ item.input_text }}
                </p>
                <p v-if="item.output_prompt" class="prompt">
                  {{ item.op_type === 'optimize' ? '优化后：' : '' }}{{ item.output_prompt }}
                </p>
                <p v-if="item.input_asset_id" class="meta-line">
                  <span>参考图 #{{ item.input_asset_id }}</span>
                </p>
                <p v-if="item.input_object_key" class="mono object-key" :title="item.input_object_key">
                  {{ item.input_object_key }}
                </p>
                <p v-if="item.error_message" class="err">{{ item.error_message }}</p>
                <div class="card-ops">
                  <el-button
                    v-if="promptCover(item)"
                    size="small"
                    @click="openPromptImage(item)"
                  >
                    打开参考图
                  </el-button>
                </div>
              </div>
            </article>
          </div>
        </section>
      </template>
    </div>

    <el-dialog
      v-model="detailOpen"
      :title="detailTitle"
      width="640px"
      :close-on-click-modal="true"
      destroy-on-close
      @closed="onDetailClosed"
    >
      <div v-if="detailLoading" class="state-inline">加载详情…</div>
      <div v-else-if="detailError" class="state-block detail-fail">
        <p class="err">{{ detailError }}</p>
        <el-button size="small" :loading="detailLoading" @click="reloadDetail">重试加载</el-button>
      </div>
      <template v-else-if="detail">
        <dl class="detail">
          <div>
            <dt>任务 ID</dt>
            <dd>
              <button type="button" class="task-id" @click="copyId(detail.id, '任务')">
                #{{ detail.id }}
              </button>
            </dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>
              <span class="status-pill" :class="detail.status">{{ statusLabel(detail.status) }}</span>
            </dd>
          </div>
          <div>
            <dt>类型</dt>
            <dd>{{ modeLabel(detail.mode) }}</dd>
          </div>
          <div>
            <dt>创建时间</dt>
            <dd class="mono">{{ formatTime(detail.created_at) }}</dd>
          </div>
          <div>
            <dt>开始时间</dt>
            <dd class="mono">{{ formatTimeOrUnavailable(detail.started_at) }}</dd>
          </div>
          <div>
            <dt>结束时间</dt>
            <dd class="mono">{{ formatTimeOrUnavailable(detail.finished_at) }}</dd>
          </div>
          <div v-if="detail.retry_of_id != null">
            <dt>重试自</dt>
            <dd>#{{ detail.retry_of_id }}</dd>
          </div>
          <div>
            <dt>最终 Prompt</dt>
            <dd class="prompt-full">{{ displayText(detail.prompt) }}</dd>
          </div>
          <div>
            <dt>Prompt 来源</dt>
            <dd>{{ sourceLabel(detail) }}</dd>
          </div>
          <div>
            <dt>已保存 params</dt>
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
          <div v-if="detail.error_message">
            <dt>失败原因</dt>
            <dd class="err">{{ detail.error_message }}</dd>
          </div>
          <div>
            <dt>输入图</dt>
            <dd>
              <p v-if="isLegacyUnlabeled(detail)" class="legacy-note">旧记录未标注</p>
              <div v-if="labeledInputAssets.length || legacyInputAssets.length" class="asset-row">
                <figure
                  v-for="asset in labeledInputAssets"
                  :key="'in-l-' + asset.id"
                  class="asset-thumb"
                >
                  <img
                    v-if="asset.url"
                    :src="asset.url"
                    alt=""
                    @error="onDetailAssetError(asset.id)"
                  />
                  <div v-else class="asset-fallback muted">不可用</div>
                  <figcaption class="mono">
                    #{{ asset.id }} · {{ assetRoleLabel(asset.role) }}
                  </figcaption>
                </figure>
                <figure
                  v-for="(asset, idx) in legacyInputAssets"
                  :key="'in-old-' + asset.id"
                  class="asset-thumb"
                >
                  <img
                    v-if="asset.url"
                    :src="asset.url"
                    alt=""
                    @error="onDetailAssetError(asset.id)"
                  />
                  <div v-else class="asset-fallback muted">不可用</div>
                  <figcaption class="mono">
                    #{{ asset.id }} · 旧记录未标注
                    <span v-if="legacySuspectHint(idx)">（{{ legacySuspectHint(idx) }}）</span>
                  </figcaption>
                </figure>
              </div>
              <span v-else class="muted">不可用</span>
            </dd>
          </div>
          <div>
            <dt>生成结果</dt>
            <dd>
              <div v-if="outputAssets.length" class="asset-row">
                <figure v-for="asset in outputAssets" :key="'out-' + asset.id" class="asset-thumb result">
                  <video
                    v-if="isVideoMime(asset.mime) && asset.url"
                    :src="asset.url"
                    controls
                    playsinline
                    preload="metadata"
                  />
                  <img
                    v-else-if="asset.url"
                    :src="asset.url"
                    alt=""
                    @error="onDetailAssetError(asset.id)"
                  />
                  <div v-else class="asset-fallback muted">
                    <span>不可用</span>
                    <el-button
                      v-if="autoResignTried.has(detail.id)"
                      size="small"
                      text
                      :loading="resigningId === detail.id"
                      @click="manualResign"
                    >
                      重新取址
                    </el-button>
                  </div>
                  <figcaption class="mono">#{{ asset.id }} · {{ assetRoleLabel(asset.role) }}</figcaption>
                </figure>
              </div>
              <span v-else class="muted">不可用</span>
            </dd>
          </div>
          <div v-if="compareProductUrl || cover(detail)">
            <dt>对照</dt>
            <dd>
              <div class="compare-row">
                <figure class="asset-thumb">
                  <img v-if="compareProductUrl" :src="compareProductUrl" alt="原商品" />
                  <div v-else class="asset-fallback muted">无原商品</div>
                  <figcaption>原商品</figcaption>
                </figure>
                <figure class="asset-thumb result">
                  <video
                    v-if="isVideoItem(detail) && cover(detail)"
                    :src="cover(detail)"
                    controls
                    playsinline
                    preload="metadata"
                  />
                  <img v-else-if="cover(detail)" :src="cover(detail)" alt="生成结果" />
                  <div v-else class="asset-fallback muted">无结果</div>
                  <figcaption>生成结果</figcaption>
                </figure>
              </div>
            </dd>
          </div>
        </dl>
        <div class="detail-ops">
          <el-button size="small" type="primary" @click="fillWorkbench(detail)">填回工作台</el-button>
          <el-button
            v-if="hasOutputAsset(detail)"
            size="small"
            :loading="downloadingId === detail.id"
            @click="downloadImage(detail)"
          >
            下载结果
          </el-button>
          <el-button
            v-if="cover(detail)"
            size="small"
            @click="openImage(detail)"
          >
            新标签打开
          </el-button>
          <el-button
            v-if="autoResignTried.has(detail.id)"
            size="small"
            :loading="resigningId === detail.id"
            @click="manualResign"
          >
            重新取址
          </el-button>
          <el-button
            v-if="detail.status === 'failed' || detail.status === 'succeeded'"
            size="small"
            :loading="retryingId === detail.id"
            @click="onRetry(detail)"
          >
            重试
          </el-button>
          <el-button
            v-if="canDeleteGeneration(detail)"
            size="small"
            :loading="deletingId === detail.id"
            @click="onDelete(detail)"
          >
            删除
          </el-button>
        </div>
      </template>
    </el-dialog>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import {
  api,
  WORKBENCH_FILL_KEY,
  type CreateGenerationBody,
  type Generation,
  type PromptOperation,
  type WorkbenchFillPayload,
} from '../api'

const router = useRouter()
const route = useRoute()

const genItems = ref<Generation[]>([])
const promptItems = ref<PromptOperation[]>([])
const loading = ref(false)
const statusFilter = ref<string>('')
const modeFilter = ref<string>('')
const reviewFilter = ref<string>('')

function applyReviewQuery() {
  const raw = route.query.review
  const value = Array.isArray(raw) ? raw[0] : raw
  if (value === 'needs_revision' || value === 'usable' || value === 'unreviewed') {
    reviewFilter.value = value
  }
}
const retryingId = ref<number | null>(null)
const deletingId = ref<number | null>(null)
const downloadingId = ref<number | null>(null)

const detailOpen = ref(false)
const detail = ref<Generation | null>(null)
const detailTargetId = ref<number | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const resigningId = ref<number | null>(null)
/** 每个任务最多自动换签一次，避免死循环 */
const autoResignTried = ref<Set<number>>(new Set())

const isPromptMode = computed(
  () => modeFilter.value === 'reverse' || modeFilter.value === 'optimize',
)
const isGenMode = computed(() => modeFilter.value === 't2i' || modeFilter.value === 'i2i' || modeFilter.value === 'i2v')

const filteredGens = computed(() => {
  if (isPromptMode.value) return []
  return genItems.value.filter((item) => {
    if (statusFilter.value && item.status !== statusFilter.value) return false
    if (modeFilter.value && item.mode !== modeFilter.value) return false
    if (reviewFilter.value) {
      const st = item.review_status || 'unreviewed'
      if (st !== reviewFilter.value) return false
    }
    return true
  })
})

const filteredPrompts = computed(() => {
  if (isGenMode.value) return []
  return promptItems.value.filter((item) => {
    if (statusFilter.value && item.status !== statusFilter.value) return false
    if (modeFilter.value && item.op_type !== modeFilter.value) return false
    return true
  })
})

const detailTitle = computed(() =>
  detail.value ? `任务详情 #${detail.value.id}` : '任务详情',
)

const paramEntries = computed(() => {
  const params = detail.value?.params
  if (!params || typeof params !== 'object' || Array.isArray(params)) return []
  return Object.entries(params).map(([key, value]) => ({
    key,
    value: formatParamValue(value),
  }))
})

const labeledInputAssets = computed(() => {
  if (!detail.value?.assets) return []
  return detail.value.assets.filter((a) => a.role === 'product' || a.role === 'scene')
})

const legacyInputAssets = computed(() => {
  if (!detail.value?.assets) return []
  return detail.value.assets.filter((a) => a.role === 'input' || a.role === 'reference')
})

const outputAssets = computed(() => {
  if (!detail.value?.assets) return []
  return detail.value.assets.filter((a) => a.role === 'output')
})

const compareProductUrl = computed(() => {
  if (!detail.value?.assets) return ''
  const product = detail.value.assets.find((a) => a.role === 'product' && a.url)
  if (product?.url) return product.url
  const legacy = detail.value.assets.find((a) => (a.role === 'input' || a.role === 'reference') && a.url)
  return legacy?.url || ''
})

function cover(item: Generation) {
  if (!item?.assets?.length) return ''
  return item.assets.find((a) => a.role === 'output' && a.url)?.url || ''
}

function isVideoMime(mime?: string | null) {
  return (mime || '').startsWith('video/')
}

function isVideoItem(item: Generation) {
  if (item.mode === 'i2v') return true
  const out = item.assets?.find((a) => a.role === 'output')
  return isVideoMime(out?.mime)
}

function hasOutputAsset(item: Generation) {
  if (!item?.assets?.length) return false
  return item.assets.some((a) => a.role === 'output' && a.id != null)
}

function promptCover(item: PromptOperation) {
  return item.asset?.url || ''
}

function displayText(value: unknown) {
  if (value == null || value === '') return '不可用'
  return String(value)
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

function statusLabel(status: string) {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '生成中',
    succeeded: '成功',
    failed: '失败',
    unknown: '未知',
  }
  return map[status] || status || '不可用'
}

function reviewStatusLabel(status?: string | null) {
  if (status === 'usable') return '可用'
  if (status === 'needs_revision') return '需重做'
  if (!status || status === 'unreviewed') return '未审核'
  return status
}

function modeLabel(mode: string) {
  if (mode === 't2i') return '文生图'
  if (mode === 'i2i') return '参考图生图'
  if (mode === 'i2v') return '图生视频'
  if (mode === 'reverse') return '图片反推'
  if (mode === 'optimize') return 'Prompt 优化'
  return mode || '不可用'
}

function assetRoleLabel(role: string) {
  if (role === 'product') return '商品'
  if (role === 'scene') return '场景'
  if (role === 'output') return '结果'
  if (role === 'input' || role === 'reference') return '旧记录未标注'
  return role || '不可用'
}

function isLegacyUnlabeled(item: Generation) {
  if (item.legacy_unlabeled) return true
  return (item.assets || []).some((a) => a.role === 'input' || a.role === 'reference')
}

/** 仅作 position 提示，不得写成已标注 */
function legacySuspectHint(idx: number) {
  if (idx === 0) return '疑似商品'
  if (idx === 1) return '疑似场景'
  return ''
}

function sourceLabel(item: Generation) {
  const parts: string[] = []
  if (item.reverse_op_id) parts.push(`反推 #${item.reverse_op_id}`)
  if (item.optimize_op_id) parts.push(`优化 #${item.optimize_op_id}`)
  if (!parts.length) return '未关联来源'
  return parts.join(' · ')
}

function formatTime(iso: string) {
  if (!iso) return '不可用'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '不可用'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function formatTimeOrUnavailable(iso?: string | null) {
  if (!iso) return '不可用'
  return formatTime(iso)
}

async function copyId(id: number, kind: string) {
  const text = String(id)
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(`已复制${kind} ID ${text}`)
  } catch {
    ElMessage.info(`${kind} ID ${text}`)
  }
}

function errorStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object' && 'status' in err) {
    const status = (err as { status: unknown }).status
    if (typeof status === 'number') return status
  }
  return undefined
}

function canDeleteGeneration(item: Generation) {
  return item.status === 'succeeded' || item.status === 'failed' || item.status === 'unknown'
}

function replaceGeneration(fresh: Generation) {
  const idx = genItems.value.findIndex((g) => g.id === fresh.id)
  if (idx >= 0) {
    genItems.value.splice(idx, 1, fresh)
  }
  if (detail.value?.id === fresh.id) {
    detail.value = fresh
  }
}

function removeGeneration(id: number) {
  genItems.value = genItems.value.filter((g) => g.id !== id)
  if (detail.value?.id === id || detailTargetId.value === id) {
    detailOpen.value = false
    detail.value = null
    detailTargetId.value = null
    detailError.value = ''
  }
}

/** 通过 GET /api/generations/{id} 重新取预签名，每个任务最多自动一次 */
async function resignGeneration(item: Generation, manual = false): Promise<boolean> {
  if (!manual && autoResignTried.value.has(item.id)) return false
  if (resigningId.value === item.id) return false
  if (!manual) {
    const next = new Set(autoResignTried.value)
    next.add(item.id)
    autoResignTried.value = next
  }
  resigningId.value = item.id
  try {
    const fresh = await api.getGeneration(item.id)
    replaceGeneration(fresh)
    return true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '重新取址失败，请稍后手动重试')
    return false
  } finally {
    resigningId.value = null
  }
}

async function onCoverError(item: Generation) {
  await resignGeneration(item, false)
}

async function onDetailAssetError(_assetId: number) {
  if (!detail.value) return
  await resignGeneration(detail.value, false)
}

async function manualResign() {
  if (!detail.value) return
  const ok = await resignGeneration(detail.value, true)
  if (ok) ElMessage.success('已重新取址')
}

function openImage(item: Generation) {
  const url = cover(item)
  if (!url) {
    ElMessage.warning('暂无可打开的结果地址，可点「重新取址」或刷新')
    return
  }
  window.open(url, '_blank', 'noopener')
}

function openPromptImage(item: PromptOperation) {
  const url = promptCover(item)
  if (!url) return
  window.open(url, '_blank', 'noopener')
}

async function downloadImage(item: Generation) {
  const asset = item.assets?.find((a) => a.role === 'output')
  if (!asset?.id) {
    ElMessage.error('没有可下载的结果资产')
    return
  }
  if (downloadingId.value === item.id) return
  downloadingId.value = item.id
  try {
    await api.downloadAsset(asset.id)
    ElMessage.success('已开始下载')
  } catch (err) {
    const msg = err instanceof Error ? err.message : '下载失败'
    ElMessage.error(`${msg}。可刷新后重试，或确认已登录`)
  } finally {
    downloadingId.value = null
  }
}

function keepFeaturesOf(item: Generation) {
  const raw = item.params?.keep_features
  return typeof raw === 'string' ? raw : ''
}

function sizeOf(item: Generation) {
  return typeof item.params?.size === 'string' && item.params.size
    ? item.params.size
    : '2048x2048'
}

function buildFillPayload(item: Generation): WorkbenchFillPayload {
  const assets = item.assets || []
  const product = assets.find((a) => a.role === 'product')
  const scene = assets.find((a) => a.role === 'scene')
  const legacyInputs = assets.filter((a) => a.role === 'input' || a.role === 'reference')

  const payload: WorkbenchFillPayload = {
    mode: item.mode === 'i2i' ? 'i2i' : item.mode === 'i2v' ? 'i2v' : 't2i',
    prompt: item.prompt || '',
    size: sizeOf(item),
    keep_features: keepFeaturesOf(item),
    retry_of_id: item.id,
  }

  if (product?.id) {
    payload.product_asset_id = product.id
    if (product.url) payload.product_preview_url = product.url
    if (scene?.id) {
      payload.scene_asset_id = scene.id
      if (scene.url) payload.scene_preview_url = scene.url
    }
  } else if (legacyInputs.length) {
    payload.input_asset_ids = legacyInputs.map((a) => a.id)
    payload.input_preview_urls = legacyInputs.map((a) => a.url || '')
  }

  return payload
}

/** 填回工作台：先 GET 详情，写 sessionStorage 后跳转；提交必须是新任务 */
async function fillWorkbench(item: Generation) {
  let fresh: Generation
  try {
    fresh = await api.getGeneration(item.id)
  } catch (err) {
    if (errorStatus(err) === 404) {
      ElMessage.warning('原记录不可用或已从历史移除')
    } else {
      ElMessage.error(err instanceof Error ? err.message : '无法加载记录详情')
    }
    return
  }

  replaceGeneration(fresh)
  const payload = buildFillPayload(fresh)
  payload.retry_of_id = item.id

  try {
    sessionStorage.setItem(WORKBENCH_FILL_KEY, JSON.stringify(payload))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '无法写入填回数据')
    return
  }
  detailOpen.value = false
  void router.push('/workbench')
}

async function openDetail(item: Generation) {
  detailOpen.value = true
  detailError.value = ''
  detailTargetId.value = item.id
  detail.value = null
  detailLoading.value = true
  try {
    const fresh = await api.getGeneration(item.id)
    replaceGeneration(fresh)
    detail.value = fresh
  } catch (err) {
    if (errorStatus(err) === 404) {
      detail.value = null
      detailError.value = '原记录不可用或已从历史移除'
      ElMessage.warning('原记录不可用或已从历史移除')
    } else {
      detail.value = null
      detailError.value = err instanceof Error ? err.message : '加载详情失败'
    }
  } finally {
    detailLoading.value = false
  }
}

async function reloadDetail() {
  const id = detail.value?.id ?? detailTargetId.value
  if (id == null) return
  detailLoading.value = true
  detailError.value = ''
  try {
    const fresh = await api.getGeneration(id)
    replaceGeneration(fresh)
    detail.value = fresh
  } catch (err) {
    if (errorStatus(err) === 404) {
      detail.value = null
      detailError.value = '原记录不可用或已从历史移除'
      ElMessage.warning('原记录不可用或已从历史移除')
    } else {
      detailError.value = err instanceof Error ? err.message : '加载详情失败'
    }
  } finally {
    detailLoading.value = false
  }
}

function onDetailClosed() {
  detail.value = null
  detailTargetId.value = null
  detailError.value = ''
  detailLoading.value = false
}

function collectRetryAssets(item: Generation): Pick<
  CreateGenerationBody,
  'product_asset_id' | 'scene_asset_id' | 'input_asset_ids' | 'keep_features'
> {
  const assets = item.assets || []
  const product = assets.find((a) => a.role === 'product')
  const scene = assets.find((a) => a.role === 'scene')
  const legacy = assets.filter((a) => a.role === 'input' || a.role === 'reference')
  const body: Pick<
    CreateGenerationBody,
    'product_asset_id' | 'scene_asset_id' | 'input_asset_ids' | 'keep_features'
  > = {}
  if (product?.id) {
    body.product_asset_id = product.id
    if (scene?.id) body.scene_asset_id = scene.id
    body.input_asset_ids = [product.id, ...(scene?.id ? [scene.id] : [])]
  } else if (legacy.length) {
    body.input_asset_ids = legacy.map((a) => a.id)
  } else {
    body.input_asset_ids = []
  }
  const kf = keepFeaturesOf(item)
  if (kf) body.keep_features = kf
  return body
}

async function onRetry(item: Generation) {
  retryingId.value = item.id
  try {
    const extra =
      item.mode === 'i2i' || item.mode === 'i2v'
        ? collectRetryAssets(item)
        : { input_asset_ids: [] as number[] }
    if (item.mode === 'i2v') {
      extra.scene_asset_id = undefined
      if (extra.product_asset_id) extra.input_asset_ids = [extra.product_asset_id]
    }
    await api.createGeneration({
      prompt: item.prompt || '',
      size: sizeOf(item),
      mode: item.mode || 't2i',
      retry_of_id: item.id,
      ...extra,
    })
    ElMessage.success('已提交重试（新任务），可在工作台或本页刷新查看')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '重试失败')
  } finally {
    retryingId.value = null
  }
}

async function onDelete(item: Generation) {
  if (!canDeleteGeneration(item)) return
  try {
    await ElMessageBox.confirm(
      '将从你的历史中移除，管理员仍保留记录用于审核。',
      '删除记录',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : '操作失败')
    return
  }
  if (deletingId.value === item.id) return
  deletingId.value = item.id
  try {
    await api.deleteGeneration(item.id)
    removeGeneration(item.id)
    ElMessage.success('已从历史中移除')
  } catch (err) {
    if (errorStatus(err) === 404) {
      removeGeneration(item.id)
      ElMessage.success('已从历史中移除')
    } else {
      ElMessage.error(err instanceof Error ? err.message : '删除失败')
    }
  } finally {
    deletingId.value = null
  }
}

async function load() {
  loading.value = true
  try {
    const [gens, ops] = await Promise.all([api.listGenerations(), api.listPromptOperations()])
    genItems.value = Array.isArray(gens?.items) ? gens.items : []
    promptItems.value = Array.isArray(ops?.items) ? ops.items : []
    // 列表刷新会重新签 url，重置自动换签标记
    autoResignTried.value = new Set()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  applyReviewQuery()
  load()
})

watch(
  () => route.query.review,
  () => {
    applyReviewQuery()
  },
)
</script>

<style scoped>
.history {
  padding: 20px;
}

.history-section {
  margin-bottom: 24px;
}

.history-section h3 {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: var(--muted);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}

.card {
  border: 1px solid var(--line);
  border-radius: 12px;
  overflow: hidden;
  background: var(--surface-2);
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.thumb {
  height: 180px;
  display: grid;
  place-items: center;
  background: var(--canvas);
  border: 0;
  padding: 0;
  width: 100%;
  cursor: default;
  color: inherit;
}

.thumb:not(:disabled) {
  cursor: pointer;
}

.thumb:not(:disabled):hover img,
.thumb:not(:disabled):hover video {
  opacity: 0.92;
}

.thumb img,
.thumb video {
  width: 100%;
  height: 180px;
  object-fit: cover;
  display: block;
}

.info {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.meta-line {
  margin: 0;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: var(--muted);
  font-size: 12px;
}

.time {
  color: var(--muted);
}

.prompt {
  margin: 0;
  font-size: 13px;
  color: var(--text);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.prompt.before {
  color: var(--muted);
}

.prompt-full {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.5;
}

.object-key {
  margin: 0;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.err {
  margin: 0;
  color: var(--danger);
  font-size: 12px;
  word-break: break-word;
}

.legacy-note {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 12px;
}

.card-ops,
.detail-ops {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}

.detail-ops {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.muted {
  color: var(--muted);
}

.detail {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail > div {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 8px 12px;
  align-items: start;
}

.detail dt {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.detail dd {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  min-width: 0;
  word-break: break-word;
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
}

.param-key {
  color: var(--muted);
  min-width: 88px;
}

.param-val {
  word-break: break-all;
}

.asset-row,
.compare-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.asset-thumb {
  margin: 0;
  width: 120px;
}

.asset-thumb.result {
  width: 180px;
}

.asset-thumb img,
.asset-thumb video,
.asset-fallback {
  width: 100%;
  height: 120px;
  object-fit: cover;
  border-radius: 8px;
  background: var(--canvas);
  border: 1px solid var(--line);
  display: grid;
  place-items: center;
  gap: 4px;
}

.asset-thumb.result img,
.asset-thumb.result video,
.asset-thumb.result .asset-fallback {
  height: 160px;
}

.asset-thumb figcaption {
  margin-top: 4px;
  font-size: 11px;
  color: var(--muted);
}

.detail-fail {
  padding: 12px 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-start;
}
</style>
