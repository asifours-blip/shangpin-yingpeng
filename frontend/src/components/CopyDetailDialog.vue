<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="640px"
    :close-on-click-modal="true"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <dl v-if="record" class="detail">
      <div>
        <dt>记录 ID</dt>
        <dd>
          <button type="button" class="task-id" @click="copyId">#{{ record.id }}</button>
        </dd>
      </div>
      <div v-if="username">
        <dt>用户</dt>
        <dd>{{ username }}</dd>
      </div>
      <div>
        <dt>平台</dt>
        <dd>{{ platformLabel }}</dd>
      </div>
      <div>
        <dt>状态</dt>
        <dd>
          <span class="status-pill" :class="statusClass">{{ statusLabel }}</span>
          <span v-if="record.user_deleted_at" class="muted"> · 用户已删除</span>
        </dd>
      </div>
      <div>
        <dt>创建时间</dt>
        <dd class="mono">{{ formatTime(record.created_at) }}</dd>
      </div>
      <div v-if="record.updated_at">
        <dt>更新时间</dt>
        <dd class="mono">{{ formatTime(record.updated_at) }}</dd>
      </div>
      <div>
        <dt>商品资料</dt>
        <dd>
          <ul v-if="factEntries.length" class="fact-list">
            <li v-for="row in factEntries" :key="row.key">
              <span class="fact-key">{{ row.key }}</span>
              <span>{{ row.value }}</span>
            </li>
          </ul>
          <span v-else class="muted">未填写</span>
        </dd>
      </div>
      <div v-if="record.error_message">
        <dt>失败原因</dt>
        <dd class="err">{{ record.error_message }}</dd>
      </div>
      <div>
        <dt>标题</dt>
        <dd>{{ content?.title || '—' }}</dd>
      </div>
      <div>
        <dt>正文</dt>
        <dd class="body-full">{{ content?.body || '—' }}</dd>
      </div>
      <div>
        <dt>话题</dt>
        <dd>{{ hashtagText }}</dd>
      </div>
      <div>
        <dt>待确认</dt>
        <dd>
          <ul v-if="content?.facts_to_confirm?.length" class="fact-list">
            <li v-for="(item, idx) in content.facts_to_confirm" :key="idx">{{ item }}</li>
          </ul>
          <span v-else class="muted">无</span>
        </dd>
      </div>
      <div>
        <dt>风险核对</dt>
        <dd>
          <p v-if="riskSummary">{{ riskSummary }}</p>
          <ul v-if="riskHits.length" class="fact-list">
            <li v-for="(hit, idx) in riskHits" :key="idx">
              <strong>{{ hit.fragment }}</strong>
              <span v-if="hit.reason"> · {{ hit.reason }}</span>
            </li>
          </ul>
          <span v-else class="muted">未发现预设规则命中</span>
        </dd>
      </div>
    </dl>
    <div class="detail-ops">
      <el-button size="small" @click="copyBody">复制正文</el-button>
      <el-button size="small" @click="copyAll">复制全文</el-button>
      <slot name="actions" :record="record" />
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'

export type CopyDetailContent = {
  title?: string
  body?: string
  hashtags?: string[]
  facts_to_confirm?: string[]
}

export type CopyDetailRecord = {
  id: number
  platform: string
  status: string
  product_facts?: Record<string, unknown> | null
  generated_content?: CopyDetailContent | null
  edited_content?: CopyDetailContent | null
  risk_result?: unknown
  error_message?: string | null
  created_at: string
  updated_at?: string
  user_deleted_at?: string | null
}

const props = defineProps<{
  modelValue: boolean
  record: CopyDetailRecord | null
  username?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const dialogTitle = computed(() =>
  props.record ? `文案详情 #${props.record.id}` : '文案详情',
)

const content = computed(() => {
  if (!props.record) return null
  return props.record.edited_content || props.record.generated_content || null
})

const platformLabel = computed(() => {
  const platform = props.record?.platform
  if (platform === 'douyin') return '抖音'
  if (platform === 'xiaohongshu') return '小红书'
  return platform || '—'
})

const statusLabel = computed(() => {
  const status = props.record?.status
  if (status === 'processing') return '处理中'
  if (status === 'succeeded') return '成功'
  if (status === 'failed') return '失败'
  return status || '—'
})

const statusClass = computed(() => {
  const status = props.record?.status
  if (status === 'processing') return 'running'
  return status || ''
})

const factEntries = computed(() => {
  const facts = props.record?.product_facts
  if (!facts || typeof facts !== 'object') return []
  const labels: Record<string, string> = {
    product_name: '商品名称',
    selling_points: '卖点',
    campaign: '活动',
  }
  return Object.entries(facts)
    .map(([key, value]) => ({
      key: labels[key] || key,
      value: value == null || value === '' ? '' : String(value),
    }))
    .filter((row) => row.value)
})

const hashtagText = computed(() => {
  const tags = content.value?.hashtags || []
  if (!tags.length) return '—'
  return tags.map((tag) => (tag.startsWith('#') ? tag : `#${tag}`)).join(' ')
})

const riskHits = computed(() => {
  const raw = props.record?.risk_result
  if (!raw || typeof raw !== 'object') return []
  const hits = (raw as { hits?: unknown }).hits
  if (!Array.isArray(hits)) return []
  return hits
    .map((hit) => {
      const obj = hit && typeof hit === 'object' ? (hit as Record<string, unknown>) : {}
      return {
        fragment: typeof obj.fragment === 'string' ? obj.fragment : '',
        reason: typeof obj.reason === 'string' ? obj.reason : '',
      }
    })
    .filter((hit) => hit.fragment || hit.reason)
})

const riskSummary = computed(() => {
  const raw = props.record?.risk_result
  if (!raw || typeof raw !== 'object') return ''
  const summary = (raw as { summary?: unknown }).summary
  return typeof summary === 'string' ? summary : ''
})

function formatTime(iso?: string) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function writeClipboard(text: string, emptyMsg: string, okMsg: string) {
  if (!text) {
    ElMessage.warning(emptyMsg)
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(okMsg)
  } catch {
    ElMessage.info(text)
  }
}

function copyId() {
  if (!props.record) return
  void writeClipboard(String(props.record.id), '没有 ID', `已复制记录 ID ${props.record.id}`)
}

function copyBody() {
  void writeClipboard(content.value?.body?.trim() || '', '没有可复制的正文', '已复制正文')
}

function copyAll() {
  const title = content.value?.title?.trim() || ''
  const body = content.value?.body?.trim() || ''
  const text = [title, body, hashtagText.value === '—' ? '' : hashtagText.value]
    .filter(Boolean)
    .join('\n\n')
  void writeClipboard(text, '没有可复制的文案', '已复制')
}
</script>

<style scoped>
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

.body-full {
  white-space: pre-wrap;
}

.fact-list {
  margin: 0;
  padding-left: 18px;
}

.fact-key {
  color: var(--muted);
  margin-right: 8px;
}

.detail-ops {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.muted {
  color: var(--muted);
}

.err {
  color: var(--danger);
}

.mono {
  font-variant-numeric: tabular-nums;
}

.task-id {
  border: 0;
  padding: 0;
  background: none;
  color: var(--accent);
  cursor: pointer;
  font: inherit;
}
</style>
