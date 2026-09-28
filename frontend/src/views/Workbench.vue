<template>
  <AppLayout wide>
    <div class="workbench">
      <aside class="panel form-col">
        <h2>{{ titleText }}</h2>
        <p class="hint">{{ subtitleText }}</p>
        <el-radio-group v-model="mode" class="mode-switch" @change="onModeChange">
          <el-radio-button label="t2i">文生图</el-radio-button>
          <el-radio-button label="i2i">参考图生图</el-radio-button>
          <el-radio-button label="i2v">图生视频</el-radio-button>
          <el-radio-button label="optimize">Prompt 优化</el-radio-button>
          <el-radio-button label="copywriting">营销文案</el-radio-button>
        </el-radio-group>

        <ol v-if="mode === 'i2i'" class="flow-steps">
          <li>选商品</li>
          <li>选场景 / 描述</li>
          <li>确认保留特征</li>
          <li>生成</li>
          <li>对照</li>
          <li>标记</li>
        </ol>

        <el-form label-position="top">
          <el-form-item v-if="mode === 'i2i' || mode === 'i2v' || mode === 'reverse' || isCopyMode" :label="refsLabel">
            <div class="refs">
              <div v-for="(item, idx) in refs" :key="item.key" class="ref-card">
                <img :src="item.preview" :alt="refRoleLabel(idx)" />
                <span class="ref-badge" :class="refRoleClass(idx)">{{ refRoleLabel(idx) }}</span>
                <button type="button" class="remove" @click="removeRef(idx)">移除</button>
              </div>
              <label v-if="refs.length < refLimit" class="ref-add" :class="{ busy: uploading }">
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/bmp,image/gif"
                  :disabled="uploading || promptBusy || copyBusy"
                  hidden
                  @change="onPickRef"
                />
                {{ uploadSlotText }}
              </label>
            </div>
            <p v-if="mode === 'i2i'" class="slot-note">第 1 张为商品，第 2 张为场景（可选）。</p>
            <p v-else-if="mode === 'i2v'" class="slot-note">仅 1 张商品图。演示固定 5 秒、480p，按方舟用量计费。</p>
            <p v-else-if="isCopyMode" class="slot-note">仅 1 张商品图，不使用场景图。</p>
          </el-form-item>

          <el-form-item v-if="mode === 'i2i'" label="保留特征">
            <el-input
              v-model="keepFeatures"
              type="textarea"
              :rows="3"
              placeholder="例如：包型、颜色、肩带、五金位置与商品图一致"
              :disabled="promptBusy"
            />
            <p class="slot-note">
              这是生成<strong>意图</strong>，会写入任务参数；不是结构或颜色的保真保证，最终仍需人工对照。
            </p>
          </el-form-item>

          <el-form-item v-if="isCopyMode" label="平台">
            <el-radio-group v-model="copyPlatform">
              <el-radio-button label="douyin">抖音</el-radio-button>
              <el-radio-button label="xiaohongshu">小红书</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="isCopyMode" label="商品名">
            <el-input v-model="copyProductName" placeholder="例如：磨砂玻璃护肤精华" :disabled="copyBusy" />
          </el-form-item>
          <el-form-item v-if="isCopyMode" label="卖点">
            <el-input
              v-model="copySellingPoints"
              type="textarea"
              :rows="3"
              placeholder="例如：轻便、防水、可调节肩带"
              :disabled="copyBusy"
            />
          </el-form-item>
          <el-form-item v-if="isCopyMode" label="活动">
            <el-input v-model="copyCampaign" placeholder="例如：春季上新" :disabled="copyBusy" />
          </el-form-item>

          <el-form-item v-if="!isCopyMode" label="Prompt">
            <el-input
              v-model="prompt"
              type="textarea"
              :rows="8"
              :placeholder="promptPlaceholder"
              :disabled="promptBusy"
            />
          </el-form-item>
          <el-form-item v-if="isGenerateMode && mode !== 'i2v'" label="尺寸">
            <el-select v-model="size" style="width: 100%">
              <el-option label="2048×2048" value="2048x2048" />
              <el-option label="2K" value="2K" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="mode === 'i2v'" label="规格">
            <p class="slot-note">5 秒 · 480p · 16:9（演示档，不开放更长或更高清）</p>
          </el-form-item>
          <el-button
            v-if="isGenerateMode"
            type="primary"
            :loading="submitting"
            :disabled="polling || uploading"
            @click="submit()"
          >
            生成
          </el-button>
          <el-button
            v-else-if="isCopyMode"
            type="primary"
            :loading="copyBusy"
            :disabled="uploading"
            @click="submitCopy"
          >
            生成文案
          </el-button>
          <el-button
            v-else-if="mode === 'reverse'"
            type="primary"
            :loading="promptBusy"
            :disabled="uploading"
            @click="runReverse"
          >
            反推 Prompt
          </el-button>
          <el-button v-else type="primary" :loading="promptBusy" @click="runOptimize">
            优化 Prompt
          </el-button>
        </el-form>
      </aside>

      <section class="canvas-col">
        <div class="meta">
          <button
            v-if="isGenerateMode && task"
            type="button"
            class="task-id"
            title="点击复制任务 ID"
            @click="copyTaskId(task.id)"
          >
            任务 ID {{ task.id }}
          </button>
          <span v-if="isCopyMode && copyError" class="status-pill failed">失败</span>
          <span v-else-if="isCopyMode && copyBusy" class="status-pill running">生成中</span>
          <span v-else-if="isCopyMode && copyOp?.status === 'processing'" class="status-pill running">处理中</span>
          <span v-else-if="isCopyMode && copyOp?.status === 'failed'" class="status-pill failed">失败</span>
          <span v-else-if="isCopyMode && copyOp?.status === 'succeeded'" class="status-pill written">已生成</span>
          <span v-else-if="isPromptMode && promptError" class="status-pill failed">失败</span>
          <span v-else-if="isPromptMode && promptBusy" class="status-pill running">调用中</span>
          <span v-else-if="isPromptMode && promptResult" class="status-pill written">已写入</span>
          <span v-else-if="isGenerateMode && task" class="status-pill" :class="task.status">{{ statusText }}</span>
          <el-button
            v-if="isGenerateMode && outputAssetId"
            size="small"
            :loading="downloading"
            @click="downloadResult"
          >
            下载
          </el-button>
        </div>

        <div
          class="canvas"
          :class="{
            'is-prompt-done': isPromptMode && promptResult,
            'is-compare': showCompare,
            'is-copy-done': isCopyMode && copyShowEditor,
          }"
        >
          <div v-if="isCopyMode && (copyError || copyOp?.status === 'failed')" class="state-block">
            <p class="fail-msg">{{ copyError || copyOp?.error_message || '生成失败' }}</p>
          </div>
          <div v-else-if="isCopyMode && (copyBusy || copyOp?.status === 'processing')" class="state-block">
            <div class="canvas-skeleton" aria-hidden="true" />
            <p>{{ copyOp?.status === 'processing' ? '处理中…' : '正在生成文案…' }}</p>
          </div>
          <div v-else-if="isCopyMode && copyShowEditor" class="copy-result">
            <el-form label-position="top">
              <el-form-item label="标题">
                <el-input v-model="copyTitle" :disabled="copySaving" />
              </el-form-item>
              <el-form-item>
                <template #label>
                  <span class="copy-field-label">
                    正文
                    <el-button size="small" text type="primary" @click.stop="copyCopyBody">一键复制</el-button>
                  </span>
                </template>
                <el-input v-model="copyBody" type="textarea" :rows="8" :disabled="copySaving" />
              </el-form-item>
              <el-form-item label="话题">
                <el-input
                  v-model="copyHashtags"
                  placeholder="空格或逗号分隔"
                  :disabled="copySaving"
                />
              </el-form-item>
            </el-form>
            <div class="copy-risk">
              <p class="review-title">待人工核对</p>
              <ul v-if="copyHits.length" class="copy-hits">
                <li v-for="(hit, idx) in copyHits" :key="idx">
                  <strong>{{ hit.fragment }}</strong>
                  <span v-if="hit.reason"> · {{ hit.reason }}</span>
                  <span v-if="hit.suggestion">（{{ hit.suggestion }}）</span>
                </li>
              </ul>
              <p v-else class="slot-note">未发现预设规则命中，仍需人工核对后再发布。</p>
            </div>
            <div class="review-actions">
              <el-button type="primary" size="small" :loading="copySaving" @click="saveCopy">
                保存
              </el-button>
              <el-button size="small" @click="copyCopyText">复制</el-button>
            </div>
          </div>
          <div v-else-if="isCopyMode" class="state-block">
            <p>文案会出现在这里</p>
          </div>

          <div v-else-if="isPromptMode && promptError" class="state-block">
            <p class="fail-msg">{{ promptError }}</p>
          </div>
          <div v-else-if="isPromptMode && promptBusy" class="state-block">
            <div class="canvas-skeleton" aria-hidden="true" />
            <p>正在调用…</p>
          </div>
          <div v-else-if="isPromptMode && promptResult" class="prompt-done">
            <figure v-if="mode === 'reverse' && reversePreview" class="prompt-done-figure">
              <img class="prompt-done-preview" :src="reversePreview" alt="场景图" />
              <figcaption>场景图</figcaption>
            </figure>
            <pre v-if="mode === 'reverse'" class="prompt-done-scroll">{{ promptResult }}</pre>
          </div>

          <div v-else-if="showCompare" class="compare-wrap">
            <div class="compare-grid">
              <figure class="compare-card">
                <img v-if="productPreviewUrl" :src="productPreviewUrl" alt="原商品" />
                <div v-else class="compare-empty muted">无原商品图</div>
                <figcaption>原商品</figcaption>
              </figure>
              <figure class="compare-card">
                <video
                  v-if="outputIsVideo && imageUrl"
                  class="result-video"
                  :src="imageUrl"
                  controls
                  playsinline
                />
                <img v-else-if="imageUrl" :src="imageUrl" alt="生成结果" />
                <div v-else class="compare-empty muted">无结果图</div>
                <figcaption>生成结果</figcaption>
              </figure>
            </div>

            <div class="review-panel">
              <p class="review-title">人工判断</p>
              <p class="slot-note">对照原商品后标记。仅表示当前决定，需人工确认；不是自动通过。</p>
              <div class="review-actions">
                <el-button
                  type="success"
                  size="small"
                  :loading="reviewSaving && reviewDraft === 'usable'"
                  :disabled="reviewBusy || task?.status !== 'succeeded'"
                  @click="saveReview('usable')"
                >
                  可用
                </el-button>
                <el-button
                  type="warning"
                  size="small"
                  :loading="reviewSaving && reviewDraft === 'needs_revision'"
                  :disabled="reviewBusy || task?.status !== 'succeeded'"
                  @click="saveReview('needs_revision')"
                >
                  需重做
                </el-button>
              </div>
              <el-form-item v-if="reviewDraft === 'needs_revision' || taskReviewStatus === 'needs_revision'" label="原因" class="reason-item">
                <el-select v-model="reviewReason" placeholder="选择原因" clearable style="width: 100%">
                  <el-option v-for="opt in REVIEW_REASONS" :key="opt" :label="opt" :value="opt" />
                </el-select>
              </el-form-item>
              <p v-if="taskReviewStatus && taskReviewStatus !== 'unreviewed'" class="review-current">
                当前决定：{{ reviewStatusLabel(taskReviewStatus) }}
                <span v-if="task?.review_reason"> · {{ task.review_reason }}</span>
              </p>
            </div>
          </div>

          <video
            v-else-if="outputIsVideo && imageUrl"
            class="result-video"
            :src="imageUrl"
            controls
            playsinline
          />
          <img v-else-if="imageUrl" :src="imageUrl" alt="生成结果" />
          <div v-else-if="isGenerateMode && (polling || submitting)" class="state-block">
            <div class="canvas-skeleton" aria-hidden="true" />
            <p>正在生成…</p>
          </div>
          <div v-else-if="isGenerateMode && task?.status === 'failed'" class="state-block">
            <p class="fail-msg">{{ task.error_message || '生成失败' }}</p>
            <el-button type="primary" @click="retry">重试</el-button>
          </div>
          <div v-else class="state-block">
            <p>结果会出现在这里</p>
          </div>
        </div>
      </section>
    </div>

    <section v-if="isCopyMode" class="panel copy-mine">
      <div class="copy-mine-bar">
        <h3>我的文案</h3>
        <el-button size="small" :loading="copyListLoading" @click="loadCopyList">刷新</el-button>
      </div>
      <p v-if="copyListLoading && !copyList.length" class="slot-note">加载中…</p>
      <p v-else-if="!copyList.length" class="slot-note">还没有文案</p>
      <ul v-else class="copy-mine-list">
        <li
          v-for="item in copyList"
          :key="item.id"
          class="copy-mine-item is-clickable"
          @click="openCopyDetail(item)"
        >
          <span
            class="status-pill"
            :class="item.status === 'processing' ? 'running' : item.status"
          >
            {{ copyStatusLabel(item.status) }}
          </span>
          <span class="copy-mine-platform">{{ platformLabel(item.platform) }}</span>
          <span class="copy-mine-title">{{ copyListTitle(item) }}</span>
          <el-button size="small" @click.stop="openCopyDetail(item)">详情</el-button>
          <el-button
            size="small"
            :disabled="item.status === 'processing'"
            :loading="copyDeletingId === item.id"
            @click.stop="deleteCopy(item)"
          >
            删除
          </el-button>
        </li>
      </ul>
    </section>

    <CopyDetailDialog v-model="copyDetailOpen" :record="copyDetail">
      <template #actions>
        <el-button
          v-if="copyDetail && copyDetail.status === 'succeeded'"
          size="small"
          type="primary"
          @click="fillCopyFromDetail"
        >
          填回编辑区
        </el-button>
      </template>
    </CopyDetailDialog>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import CopyDetailDialog from '../components/CopyDetailDialog.vue'
import {
  api,
  WORKBENCH_FILL_KEY,
  type CreateGenerationBody,
  type Generation,
  type ReviewStatus,
  type WorkbenchFillPayload,
} from '../api'
import {
  copyApi,
  type CopyPlatform,
  type CopywritingOperation,
  type GeneratedContent,
} from '../copyApi'

const POLL_MS = 2000
const MAX_WAIT_MS = 180_000
const DEFAULT_KEEP_FEATURES = '包型、颜色、肩带、五金位置与商品图一致'
const REVIEW_REASONS = ['结构变化', '颜色偏差', '细节错误', '融合不自然', '原商品残留', '其他'] as const

type WorkbenchMode = 't2i' | 'i2i' | 'i2v' | 'reverse' | 'optimize' | 'copywriting'
type RefItem = { key: string; id: number; preview: string }

const mode = ref<WorkbenchMode>('t2i')
const prompt = ref('商品静物摄影：一瓶磨砂玻璃护肤精华，浅灰背景，柔和棚灯，留白构图')
const size = ref('2048x2048')
const keepFeatures = ref(DEFAULT_KEEP_FEATURES)
const submitting = ref(false)
const uploading = ref(false)
const polling = ref(false)
const downloading = ref(false)
const promptBusy = ref(false)
const promptResult = ref('')
const promptError = ref('')
const task = ref<Generation | null>(null)
const refs = ref<RefItem[]>([])
const reviewDraft = ref<ReviewStatus | ''>('')
const reviewReason = ref('')
const reviewSaving = ref(false)
/** 填回带来的 retry_of_id；仅用户手动点生成时带上，填回本身不 submit */
const pendingRetryOfId = ref<number | undefined>(undefined)
const copyPlatform = ref<CopyPlatform>('douyin')
const copyProductName = ref('')
const copySellingPoints = ref('')
const copyCampaign = ref('')
const copyBusy = ref(false)
const copySaving = ref(false)
const copyError = ref('')
const copyOp = ref<CopywritingOperation | null>(null)
const copyTitle = ref('')
const copyBody = ref('')
const copyHashtags = ref('')
const copyList = ref<CopywritingOperation[]>([])
const copyListLoading = ref(false)
const copyDeletingId = ref<number | null>(null)
const copyDetailOpen = ref(false)
const copyDetail = ref<CopywritingOperation | null>(null)
let timer: number | null = null

const isGenerateMode = computed(() => mode.value === 't2i' || mode.value === 'i2i' || mode.value === 'i2v')
const isPromptMode = computed(() => mode.value === 'reverse' || mode.value === 'optimize')
const isCopyMode = computed(() => mode.value === 'copywriting')
const reversePreview = computed(() => refs.value[0]?.preview || '')

const refLimit = computed(() =>
  mode.value === 'reverse' || mode.value === 'i2v' || isCopyMode.value ? 1 : 2,
)

const titleText = computed(() => {
  const map: Record<WorkbenchMode, string> = {
    t2i: '文生图',
    i2i: '参考图生图',
    i2v: '图生视频',
    reverse: '图片反推',
    optimize: 'Prompt 优化',
    copywriting: '营销文案',
  }
  return map[mode.value]
})

const subtitleText = computed(() => {
  if (mode.value === 'i2i') return '箱包场景图制作与审核'
  if (mode.value === 't2i') return '按文案生成，结果需人工确认'
  if (mode.value === 'i2v') return '商品图生成 5 秒短视频，结果需人工确认'
  if (mode.value === 'reverse') return '从场景图反推 Prompt 草稿'
  if (mode.value === 'copywriting') return '根据商品图生成平台文案，结果需人工核对'
  return '润色 Prompt 草稿，再去生图'
})

const refsLabel = computed(() => {
  if (mode.value === 'reverse') return '场景图'
  if (mode.value === 'copywriting' || mode.value === 'i2v') return '商品图'
  if (mode.value === 'i2i') return '商品 / 场景'
  return '参考图'
})

const uploadSlotText = computed(() => {
  if (uploading.value) return '上传中…'
  if (mode.value === 'reverse') return '上传场景图'
  if (mode.value === 'copywriting' || mode.value === 'i2v') return '上传商品图'
  if (refs.value.length === 0) return '上传商品图'
  return '上传场景图'
})

const promptPlaceholder = computed(() => {
  if (mode.value === 'optimize') {
    return '粘贴口语或草稿，例如：帮我拍一瓶精华液，高级一点'
  }
  if (mode.value === 'reverse') {
    return '反推结果会填到这里'
  }
  if (mode.value === 't2i') {
    return '例如：商品静物摄影，一瓶磨砂玻璃护肤精华，浅灰背景，柔和棚灯'
  }
  if (mode.value === 'i2v') {
    return '例如：商品在影棚灯光下缓慢展示，镜头轻微推进，主体清晰'
  }
  return '例如：保留商品主体外观与材质，参考场景光影与背景，柔和棚灯'
})

const outputAsset = computed(() => {
  if (isPromptMode.value) return undefined
  return task.value?.assets.find((a) => a.role === 'output' && a.url)
})

const outputAssetId = computed(() => outputAsset.value?.id || 0)

const imageUrl = computed(() => (isPromptMode.value ? '' : outputAsset.value?.url || ''))
const outputIsVideo = computed(() => (outputAsset.value?.mime || '').startsWith('video/'))

/** 对照用原商品：任务返回的 product，其次本地第 1 张 ref，再兼容旧 input */
const productPreviewUrl = computed(() => {
  const assets = task.value?.assets || []
  const product = assets.find((a) => a.role === 'product' && a.url)
  if (product?.url) return product.url
  if (refs.value[0]?.preview) return refs.value[0].preview
  const input = assets.find((a) => (a.role === 'input' || a.role === 'reference') && a.url)
  return input?.url || ''
})

const showCompare = computed(
  () =>
    isGenerateMode.value &&
    task.value?.status === 'succeeded' &&
    Boolean(imageUrl.value),
)

const taskReviewStatus = computed(() => task.value?.review_status || 'unreviewed')

const reviewBusy = computed(() => reviewSaving.value || submitting.value || polling.value)

const statusText = computed(() => {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '生成中',
    succeeded: '成功',
    failed: '失败',
    unknown: '未知',
  }
  return map[task.value?.status || ''] || task.value?.status || ''
})

const copyShowEditor = computed(() => {
  if (!isCopyMode.value || !copyOp.value) return false
  if (copyOp.value.status === 'failed' || copyOp.value.status === 'processing') return false
  if (copyError.value) return false
  return Boolean(copyTitle.value || copyBody.value || copyOp.value.generated_content || copyOp.value.edited_content)
})

const copyHits = computed(() => {
  const raw = copyOp.value?.risk_result
  if (!raw || typeof raw !== 'object') return [] as { fragment: string; reason: string; suggestion: string }[]
  const hits = (raw as { hits?: unknown }).hits
  if (!Array.isArray(hits)) return []
  return hits
    .map((hit) => {
      const obj = hit && typeof hit === 'object' ? (hit as Record<string, unknown>) : {}
      return {
        fragment: typeof obj.fragment === 'string' ? obj.fragment : '',
        reason: typeof obj.reason === 'string' ? obj.reason : '',
        suggestion: typeof obj.suggestion === 'string' ? obj.suggestion : '',
      }
    })
    .filter((hit) => hit.fragment || hit.reason)
})

function reviewStatusLabel(status: string) {
  if (status === 'usable') return '可用'
  if (status === 'needs_revision') return '需重做'
  if (status === 'unreviewed') return '未审核'
  return status || '未审核'
}

/** i2i：idx0=商品；reverse：唯一槽=场景；i2i 的 idx1=场景 */
function refRoleLabel(idx: number): string {
  if (mode.value === 'reverse') return '场景'
  if (mode.value === 'copywriting' || mode.value === 'i2v') return '商品'
  if (idx === 0) return '第1张·商品'
  return '第2张·场景'
}

function refRoleClass(idx: number): string {
  if (mode.value === 'reverse') return 'is-scene'
  if (mode.value === 'copywriting' || mode.value === 'i2v') return 'is-product'
  return idx === 0 ? 'is-product' : 'is-scene'
}

/**
 * 切模式：只清状态提示，不改 prompt 正文（避免覆盖用户已编辑内容）。
 * 进反推时若已有双图，保留第2张场景、丢掉第1张商品，避免把商品图误当反推素材。
 */
function onModeChange() {
  promptError.value = ''
  reviewDraft.value = ''
  // 右侧「已写入」是上次操作回显；切模式后收起，避免误以为又写了一遍。不碰 prompt。
  if (mode.value === 't2i' || mode.value === 'i2i' || mode.value === 'i2v') {
    promptResult.value = ''
  }
  if (mode.value === 'i2i' && !keepFeatures.value.trim()) {
    keepFeatures.value = DEFAULT_KEEP_FEATURES
  }
  if (mode.value === 'reverse' && refs.value.length > 1) {
    const product = refs.value.shift()
    if (product) URL.revokeObjectURL(product.preview)
    refs.value.splice(1).forEach((item) => URL.revokeObjectURL(item.preview))
    ElMessage.info('反推仅用场景图：已保留原第2张场景，移除了商品图')
  } else if (mode.value === 'reverse' && refs.value.length === 1) {
    // 单图路径：沿用当前图作为场景槽，不静默清空
  } else if ((mode.value === 'copywriting' || mode.value === 'i2v') && refs.value.length > 1) {
    refs.value.splice(1).forEach((item) => {
      if (item.preview.startsWith('blob:')) URL.revokeObjectURL(item.preview)
    })
    ElMessage.info(mode.value === 'i2v' ? '图生视频仅用 1 张商品图：已移除多余参考图' : '文案仅用 1 张商品图：已移除多余参考图')
  }
  if (mode.value === 'copywriting') {
    void loadCopyList()
  }
}

async function downloadResult() {
  const id = outputAssetId.value
  if (!id || downloading.value) return
  downloading.value = true
  try {
    await api.downloadAsset(id)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '下载失败')
  } finally {
    downloading.value = false
  }
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

function stopPoll() {
  if (timer !== null) {
    window.clearTimeout(timer)
    timer = null
  }
  polling.value = false
}

async function pollUntilDone(id: number, startedAt: number) {
  polling.value = true
  const tick = async () => {
    try {
      const current = await api.getGeneration(id)
      task.value = current
      if (current.status === 'succeeded' || current.status === 'failed' || current.status === 'unknown') {
        stopPoll()
        if (current.status === 'succeeded') {
          reviewDraft.value = (current.review_status as ReviewStatus) || ''
          reviewReason.value = current.review_reason || ''
        }
        return
      }
      if (Date.now() - startedAt > MAX_WAIT_MS && mode.value !== 'i2v') {
        stopPoll()
        ElMessage.warning('等待超过 3 分钟，可到历史页刷新查看。任务不会因此被标失败。')
        return
      }
      if (Date.now() - startedAt > 480_000) {
        stopPoll()
        ElMessage.warning('等待超过 8 分钟，可到历史页刷新查看。任务不会因此被标失败。')
        return
      }
      timer = window.setTimeout(tick, POLL_MS)
    } catch (err) {
      stopPoll()
      ElMessage.error(err instanceof Error ? err.message : '轮询失败')
    }
  }
  await tick()
}

async function onPickRef(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (refs.value.length >= refLimit.value) {
    ElMessage.warning(
      mode.value === 'reverse'
        ? '反推仅支持 1 张场景图'
        : mode.value === 'copywriting' || mode.value === 'i2v'
          ? '仅支持 1 张商品图'
          : '最多 2 张：第1商品、第2场景',
    )
    return
  }
  uploading.value = true
  try {
    const created = await api.uploadAsset(file)
    const nextIdx = refs.value.length
    refs.value.push({
      key: `${created.id}-${Date.now()}`,
      id: created.id,
      preview: URL.createObjectURL(file),
    })
    if (mode.value === 'reverse') {
      ElMessage.success('已上传场景图')
    } else if (mode.value === 'copywriting' || mode.value === 'i2v' || nextIdx === 0) {
      ElMessage.success(
        mode.value === 'copywriting' || mode.value === 'i2v' ? '已上传商品图' : '已上传第1张·商品图',
      )
    } else {
      ElMessage.success('已上传第2张·场景图')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '上传失败')
  } finally {
    uploading.value = false
  }
}

function removeRef(idx: number) {
  const item = refs.value[idx]
  if (item) URL.revokeObjectURL(item.preview)
  refs.value.splice(idx, 1)
}

async function runReverse() {
  if (refs.value.length !== 1) {
    ElMessage.warning('请先上传场景图')
    return
  }
  promptBusy.value = true
  promptError.value = ''
  promptResult.value = ''
  try {
    const out = await api.reversePrompt(refs.value[0].id)
    const text = (out.prompt || '').trim()
    if (!text) {
      promptError.value = '模型未返回 Prompt 文本'
      ElMessage.error(promptError.value)
      return
    }
    // 仅在用户主动点「反推」时写入；切模式不会触发这里
    prompt.value = text
    promptResult.value = text
    ElMessage.success('反推完成')
  } catch (err) {
    promptError.value = err instanceof Error ? err.message : '反推失败'
    ElMessage.error(promptError.value)
  } finally {
    promptBusy.value = false
  }
}

async function runOptimize() {
  const draft = prompt.value.trim()
  if (!draft) {
    ElMessage.warning('请填写要优化的 Prompt')
    return
  }
  promptBusy.value = true
  promptError.value = ''
  promptResult.value = ''
  try {
    const out = await api.optimizePrompt(draft)
    const text = (out.prompt || '').trim()
    if (!text) {
      promptError.value = '模型未返回 Prompt 文本'
      ElMessage.error(promptError.value)
      return
    }
    prompt.value = text
    promptResult.value = text
    ElMessage.success('优化完成，已填入 Prompt，可再编辑后去生图')
  } catch (err) {
    promptError.value = err instanceof Error ? err.message : '优化失败'
    ElMessage.error(promptError.value)
  } finally {
    promptBusy.value = false
  }
}

async function submit(retryOf?: number) {
  if (!isGenerateMode.value) return
  if (!prompt.value.trim()) {
    ElMessage.warning('请填写 Prompt')
    return
  }
  if (mode.value === 'i2i') {
    if (refs.value.length < 1 || refs.value.length > 2) {
      ElMessage.warning('参考图生图需要商品图（第1张），场景图可选（第2张）')
      return
    }
  }
  if (mode.value === 'i2v' && refs.value.length !== 1) {
    ElMessage.warning('图生视频需要 1 张商品图')
    return
  }
  submitting.value = true
  stopPoll()
  promptError.value = ''
  reviewDraft.value = ''
  try {
    const genMode = mode.value === 'i2i' ? 'i2i' : mode.value === 'i2v' ? 'i2v' : 't2i'
    const retryId = retryOf ?? pendingRetryOfId.value
    const body: CreateGenerationBody = {
      prompt: prompt.value.trim(),
      size: size.value,
      mode: genMode,
      retry_of_id: retryId,
    }
    if (genMode === 'i2i' || genMode === 'i2v') {
      body.product_asset_id = refs.value[0]?.id
      if (genMode === 'i2i' && refs.value[1]?.id) body.scene_asset_id = refs.value[1].id
      body.input_asset_ids = genMode === 'i2v' ? refs.value.slice(0, 1).map((item) => item.id) : refs.value.map((item) => item.id)
      const kf = keepFeatures.value.trim()
      if (genMode === 'i2i' && kf) body.keep_features = kf
    } else {
      body.input_asset_ids = []
    }
    const created = await api.createGeneration(body)
    task.value = created
    pendingRetryOfId.value = undefined
    if (genMode === 'i2i' || genMode === 'i2v') {
      ElMessage.success('已提交')
    }
    await pollUntilDone(created.id, Date.now())
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

function retry() {
  if (!task.value || !isGenerateMode.value) return
  void submit(task.value.id)
}

async function saveReview(status: ReviewStatus) {
  if (!task.value || task.value.status !== 'succeeded') {
    ElMessage.warning('仅成功任务可标记人工决定')
    return
  }
  if (status === 'needs_revision' && !reviewReason.value.trim()) {
    reviewDraft.value = status
    ElMessage.warning('请选择需重做的原因')
    return
  }
  reviewDraft.value = status
  reviewSaving.value = true
  try {
    const fresh = await api.patchReview(task.value.id, {
      review_status: status,
      review_reason: status === 'needs_revision' ? reviewReason.value.trim() : undefined,
    })
    task.value = fresh
    ElMessage.success(status === 'usable' ? '已标记为可用' : '已标记为需重做')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '提交人工决定失败')
  } finally {
    reviewSaving.value = false
  }
}

function platformLabel(platform: string) {
  if (platform === 'douyin') return '抖音'
  if (platform === 'xiaohongshu') return '小红书'
  return platform
}

function copyStatusLabel(status: string) {
  if (status === 'processing') return '处理中'
  if (status === 'succeeded') return '成功'
  if (status === 'failed') return '失败'
  return status || ''
}

function copyContentOf(op: CopywritingOperation | null): GeneratedContent | null {
  if (!op) return null
  return op.edited_content || op.generated_content || null
}

function copyListTitle(item: CopywritingOperation) {
  if (item.status === 'processing') return '处理中'
  if (item.status === 'failed') return item.error_message || '失败'
  const content = copyContentOf(item)
  return content?.title || '无标题'
}

function openCopyDetail(item: CopywritingOperation) {
  copyDetail.value = item
  copyDetailOpen.value = true
}

function fillCopyFromDetail() {
  const item = copyDetail.value
  if (!item || item.status !== 'succeeded') {
    ElMessage.warning('仅成功的文案可填回')
    return
  }
  copyOp.value = item
  copyError.value = ''
  applyCopyEditor(item)
  copyDetailOpen.value = false
  ElMessage.success('已填回编辑区')
}

function parseHashtags(raw: string): string[] {
  return raw
    .split(/[\s,，]+/)
    .map((item) => item.replace(/^#/, '').trim())
    .filter(Boolean)
}

function applyCopyEditor(op: CopywritingOperation) {
  const content = copyContentOf(op)
  copyTitle.value = content?.title || ''
  copyBody.value = content?.body || ''
  copyHashtags.value = (content?.hashtags || []).join(' ')
}

function resetCopyEditor() {
  copyTitle.value = ''
  copyBody.value = ''
  copyHashtags.value = ''
}

async function loadCopyList() {
  copyListLoading.value = true
  try {
    const data = await copyApi.list()
    copyList.value = data.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载文案列表失败')
  } finally {
    copyListLoading.value = false
  }
}

async function submitCopy() {
  if (!isCopyMode.value) return
  if (refs.value.length !== 1) {
    ElMessage.warning('请上传 1 张商品图')
    return
  }
  copyBusy.value = true
  copyError.value = ''
  copyOp.value = null
  resetCopyEditor()
  try {
    const name = copyProductName.value.trim()
    const points = copySellingPoints.value.trim()
    const campaign = copyCampaign.value.trim()
    const created = await copyApi.create({
      asset_id: refs.value[0].id,
      platform: copyPlatform.value,
      product_name: name || undefined,
      selling_points: points || undefined,
      campaign: campaign || undefined,
    })
    copyOp.value = created
    if (created.status === 'failed') {
      copyError.value = created.error_message || '生成失败'
      ElMessage.error(copyError.value)
      resetCopyEditor()
      await loadCopyList()
      return
    }
    if (created.status === 'processing') {
      ElMessage.info('处理中')
      await loadCopyList()
      return
    }
    const content = copyContentOf(created)
    if (!content) {
      copyError.value = '未返回文案'
      ElMessage.error(copyError.value)
      resetCopyEditor()
      await loadCopyList()
      return
    }
    applyCopyEditor(created)
    ElMessage.success('文案已生成，请人工核对')
    await loadCopyList()
  } catch (err) {
    copyError.value = err instanceof Error ? err.message : '生成失败'
    copyOp.value = null
    resetCopyEditor()
    ElMessage.error(copyError.value)
    await loadCopyList()
  } finally {
    copyBusy.value = false
  }
}

async function saveCopy() {
  if (!copyOp.value || copyOp.value.status !== 'succeeded') {
    ElMessage.warning('仅成功的文案可保存')
    return
  }
  const title = copyTitle.value.trim()
  const body = copyBody.value.trim()
  if (!title || !body) {
    ElMessage.warning('标题和正文不能为空')
    return
  }
  copySaving.value = true
  try {
    const prev = copyContentOf(copyOp.value)
    const updated = await copyApi.patch(copyOp.value.id, {
      edited_content: {
        title,
        body,
        hashtags: parseHashtags(copyHashtags.value),
        facts_to_confirm: prev?.facts_to_confirm || [],
      },
    })
    copyOp.value = updated
    applyCopyEditor(updated)
    ElMessage.success('已保存')
    await loadCopyList()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    copySaving.value = false
  }
}

async function copyCopyBody() {
  const text = copyBody.value.trim()
  if (!text) {
    ElMessage.warning('没有可复制的正文')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制正文')
  } catch {
    ElMessage.info(text)
  }
}

async function copyCopyText() {
  const tags = parseHashtags(copyHashtags.value)
    .map((tag) => (tag.startsWith('#') ? tag : `#${tag}`))
    .join(' ')
  const text = [copyTitle.value.trim(), copyBody.value.trim(), tags].filter(Boolean).join('\n\n')
  if (!text) {
    ElMessage.warning('没有可复制的文案')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.info(text)
  }
}

async function deleteCopy(item: CopywritingOperation) {
  if (item.status === 'processing') {
    ElMessage.warning('处理中的记录不可删除')
    return
  }
  copyDeletingId.value = item.id
  try {
    await copyApi.delete(item.id)
    if (copyOp.value?.id === item.id) {
      copyOp.value = null
      copyError.value = ''
      resetCopyEditor()
    }
    if (copyDetail.value?.id === item.id) {
      copyDetailOpen.value = false
      copyDetail.value = null
    }
    ElMessage.success('已删除')
    await loadCopyList()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  } finally {
    copyDeletingId.value = null
  }
}

/** 从历史「填回工作台」读取一次；提交仍是新任务。填回成功后再 handle 通知，绝不自动 submit。 */
async function applyFillFromHistory() {
  let raw = ''
  try {
    raw = sessionStorage.getItem(WORKBENCH_FILL_KEY) || ''
  } catch {
    return
  }
  if (!raw) return
  try {
    sessionStorage.removeItem(WORKBENCH_FILL_KEY)
  } catch {
    /* ignore */
  }
  let payload: WorkbenchFillPayload
  try {
    payload = JSON.parse(raw) as WorkbenchFillPayload
  } catch {
    return
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return
  }

  try {
    if (payload.mode === 't2i' || payload.mode === 'i2i' || payload.mode === 'i2v') {
      mode.value = payload.mode
    }
    if (typeof payload.prompt === 'string') prompt.value = payload.prompt
    if (typeof payload.size === 'string' && payload.size) size.value = payload.size
    if (typeof payload.keep_features === 'string') {
      keepFeatures.value = payload.keep_features || DEFAULT_KEEP_FEATURES
    } else if (mode.value === 'i2i') {
      keepFeatures.value = DEFAULT_KEEP_FEATURES
    }

    refs.value.forEach((item) => {
      if (item.preview.startsWith('blob:')) URL.revokeObjectURL(item.preview)
    })
    refs.value = []

    const pushRef = (id: number | undefined, preview: string | undefined, fallbackLabel: string) => {
      if (!id) return
      refs.value.push({
        key: `fill-${id}-${Date.now()}-${fallbackLabel}`,
        id,
        preview: preview || `/api/assets/${id}/file`,
      })
    }

    if (mode.value === 'i2i' || mode.value === 'i2v') {
      if (payload.product_asset_id) {
        pushRef(payload.product_asset_id, payload.product_preview_url, 'product')
        if (mode.value === 'i2i' && payload.scene_asset_id) {
          pushRef(payload.scene_asset_id, payload.scene_preview_url, 'scene')
        }
      } else if (payload.input_asset_ids?.length) {
        const ids = mode.value === 'i2v' ? payload.input_asset_ids.slice(0, 1) : payload.input_asset_ids.slice(0, 2)
        ids.forEach((id, idx) => {
          pushRef(id, payload.input_preview_urls?.[idx], `input-${idx}`)
        })
      }
    }

    task.value = null
    reviewDraft.value = ''
    reviewReason.value = ''
    pendingRetryOfId.value =
      typeof payload.retry_of_id === 'number' && Number.isFinite(payload.retry_of_id) && payload.retry_of_id > 0
        ? payload.retry_of_id
        : undefined
  } catch {
    return
  }

  ElMessage.success('已填回工作台，提交将创建新任务')

  const nid = payload.notification_id
  if (typeof nid === 'number' && Number.isFinite(nid) && nid > 0) {
    try {
      await api.handleNotification(nid)
    } catch (err) {
      ElMessage.warning(err instanceof Error ? err.message : '通知未能标记已处理，可稍后再次打开')
    }
  }
}

onMounted(() => {
  void applyFillFromHistory()
})

onUnmounted(() => {
  stopPoll()
  refs.value.forEach((item) => {
    if (item.preview.startsWith('blob:')) URL.revokeObjectURL(item.preview)
  })
})
</script>

<style scoped>
.workbench {
  display: grid;
  grid-template-columns: minmax(300px, 370px) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.form-col {
  padding: 28px 24px;
}

.form-col h2 {
  margin: 0 0 8px;
  font-size: 25px;
  font-weight: 600;
  letter-spacing: -.04em;
}

.hint {
  margin: 0 0 16px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}

.flow-steps {
  margin: 0 0 16px;
  padding: 10px 10px 10px 28px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface-2);
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.mode-switch {
  display: flex;
  flex-wrap: nowrap;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  overflow-x: auto;
  margin-bottom: 16px;
}

.mode-switch :deep(.el-radio-button) {
  flex: 1 1 auto;
}

.mode-switch :deep(.el-radio-button__inner) {
  width: 100%;
  padding: 8px 6px;
  font-size: 12px;
  white-space: nowrap;
}

.refs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.ref-card {
  width: 88px;
  height: 88px;
  position: relative;
  border-radius: var(--radius);
  overflow: hidden;
  border: 1px solid var(--line);
  background: var(--canvas);
}

.ref-card img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.ref-badge {
  position: absolute;
  left: 4px;
  bottom: 4px;
  max-width: calc(100% - 8px);
  border-radius: 999px;
  padding: 1px 6px;
  font-size: 10px;
  line-height: 1.4;
  color: #fff;
  background: rgba(32, 42, 54, .88);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
}

.ref-badge.is-product {
  background: rgba(55, 65, 81, .88);
}

.ref-badge.is-scene {
  background: rgba(107, 114, 128, .9);
}

.ref-card .remove {
  position: absolute;
  right: 4px;
  top: 4px;
  border: 0;
  border-radius: 999px;
  background: rgba(32, 42, 54, .88);
  color: #fff;
  font-size: 11px;
  padding: 2px 6px;
  cursor: pointer;
  z-index: 1;
}

.ref-add {
  width: 88px;
  height: 88px;
  display: grid;
  place-items: center;
  border: 1px dashed var(--line);
  border-radius: 8px;
  color: var(--muted);
  cursor: pointer;
  font-size: 12px;
  text-align: center;
  padding: 4px;
  line-height: 1.35;
  background: var(--surface-2);
  box-sizing: border-box;
}

.ref-add:hover {
  color: var(--text);
  border-color: var(--muted);
}

.ref-add.busy {
  opacity: 0.6;
  cursor: wait;
}

.slot-note {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}

.meta {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
  min-height: 24px;
  flex-wrap: wrap;
}

.canvas :deep(.state-block) {
  width: 100%;
  min-height: 420px;
}

.canvas :deep(.canvas-skeleton) {
  width: min(72%, 360px);
  margin: 0 auto 8px;
}

.status-pill.written {
  color: var(--text);
  background: #f3f4f6;
  border-color: var(--line);
}

.canvas.is-prompt-done {
  display: flex;
  align-items: stretch;
  justify-content: flex-start;
  overflow: auto;
  place-items: unset;
}

.canvas.is-compare {
  display: block;
  overflow: auto;
  place-items: unset;
  padding: 16px;
}

.compare-wrap {
  width: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.compare-card {
  margin: 0;
  min-width: 0;
}

.compare-card img,
.compare-card video,
.compare-empty {
  width: 100%;
  aspect-ratio: 1 / 1;
  max-height: 360px;
  object-fit: contain;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: #f9fafb;
  display: grid;
  place-items: center;
}

.compare-card video {
  display: block;
  background: #000;
}

.result-video {
  width: min(100%, 720px);
  max-height: 70vh;
  border-radius: 8px;
  background: #000;
}

.compare-card figcaption {
  margin-top: 6px;
  font-size: 12px;
  color: var(--muted);
}

.review-panel {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface-2);
  padding: 12px 14px;
}

.review-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
}

.review-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.reason-item {
  margin: 12px 0 0;
}

.review-current {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.prompt-done {
  width: 100%;
  box-sizing: border-box;
  padding: 20px 24px 24px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 12px;
}

.prompt-done-figure {
  margin: 0;
  width: 100%;
}

.canvas.is-prompt-done .prompt-done-preview {
  display: block;
  width: 100%;
  height: auto;
  max-height: 360px;
  object-fit: contain;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: #f9fafb;
}

.prompt-done-figure figcaption {
  margin-top: 6px;
  font-size: 12px;
  color: var(--muted);
}

.prompt-done-note {
  margin: 0;
  width: 100%;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.prompt-done-scroll {
  margin: 0;
  width: 100%;
  max-width: none;
  box-sizing: border-box;
  max-height: 12em;
  overflow: auto;
  padding: 10px 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  line-height: 1.6;
  font-size: 13px;
  color: var(--text);
  background: #f9fafb;
  border: 1px solid var(--line);
  border-radius: 8px;
}

.canvas.is-copy-done {
  display: block;
  overflow: auto;
  place-items: unset;
  padding: 16px;
}

.copy-result {
  width: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.copy-field-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.copy-risk {
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-2);
  padding: 12px 14px;
}

.copy-hits {
  margin: 8px 0 0;
  padding-left: 18px;
  color: var(--text);
  font-size: 13px;
  line-height: 1.55;
}

.copy-mine {
  margin-top: 24px;
  padding: 16px 20px 20px;
}

.copy-mine-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.copy-mine-bar h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.copy-mine-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.copy-mine-item {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-2);
}

.copy-mine-item.is-clickable {
  cursor: pointer;
}

.copy-mine-platform {
  font-size: 12px;
  color: var(--muted);
}

.copy-mine-title {
  flex: 1 1 160px;
  min-width: 0;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 900px) {
  .workbench {
    grid-template-columns: minmax(0, 1fr);
  }

  .form-col, .canvas-col { min-width: 0; }

  .compare-grid {
    grid-template-columns: 1fr;
  }
}
</style>
