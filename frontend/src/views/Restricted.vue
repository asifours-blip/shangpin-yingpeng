<template>
  <div class="studio-shell">
    <header class="studio-header">
      <span class="brand">账号受限</span>
      <div></div>
      <div class="user-slot">
        <span v-if="me?.username" class="name">{{ me.username }}</span>
        <el-button size="small" :loading="loggingOut" @click="onLogout">退出</el-button>
      </div>
    </header>
    <main class="studio-main">
      <div class="panel restricted">
        <div class="page-bar">
          <h2>账号已冻结</h2>
          <div class="actions">
            <el-button size="small" :loading="loadingMe" @click="refresh">刷新</el-button>
          </div>
        </div>

        <div v-if="meError" class="state-block">
          <p class="fail-msg">{{ meError }}</p>
          <el-button size="small" :loading="loadingMe" @click="refresh">重试</el-button>
        </div>
        <template v-else>
          <dl class="meta">
            <div>
              <dt>冻结状态</dt>
              <dd>
                <span class="status-pill" :class="me?.is_frozen ? 'failed' : 'succeeded'">
                  {{ freezeLabel }}
                </span>
              </dd>
            </div>
            <div>
              <dt>冻结原因</dt>
              <dd>{{ displayText(me?.freeze_reason) }}</dd>
            </div>
            <div>
              <dt>冻结时间</dt>
              <dd class="mono">{{ formatTimeOrEmpty(me?.frozen_at) }}</dd>
            </div>
          </dl>

          <section class="appeal-box">
            <h3>提交申诉</h3>
            <el-input
              v-model="appealContent"
              type="textarea"
              :rows="5"
              maxlength="2000"
              show-word-limit
              placeholder="说明情况，等待管理员处理"
            />
            <p v-if="appealHint" class="hint-warn">{{ appealHint }}</p>
            <div class="appeal-ops">
              <el-button
                type="primary"
                :loading="submitting"
                :disabled="!appealContent.trim()"
                @click="onSubmitAppeal"
              >
                提交申诉
              </el-button>
            </div>
          </section>

          <section class="appeal-list">
            <h3>申诉记录</h3>
            <div v-if="appealsError" class="state-inline fail-msg">{{ appealsError }}</div>
            <div v-else-if="loadingAppeals && !appeals.length" class="state-inline">加载申诉…</div>
            <div v-else-if="!appeals.length" class="state-block">
              <p>还没有申诉记录</p>
            </div>
            <ul v-else class="records">
              <li v-for="item in appeals" :key="item.id" class="record">
                <div class="row">
                  <span class="status-pill" :class="item.status === 'pending' ? 'running' : 'succeeded'">
                    {{ appealStatusLabel(item.status) }}
                  </span>
                  <span class="mono time">{{ formatTimeOrEmpty(item.created_at) }}</span>
                </div>
                <p class="content">{{ item.content }}</p>
                <p v-if="item.admin_reply" class="reply">管理员回复：{{ item.admin_reply }}</p>
                <p v-else class="muted">暂无管理员回复</p>
              </li>
            </ul>
          </section>
        </template>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

type Me = {
  id: number
  username: string
  role: string
  is_frozen?: boolean
  freeze_reason?: string | null
  frozen_at?: string | null
}

type Appeal = {
  id: number
  content: string
  status: string
  admin_reply?: string | null
  created_at?: string
}

type HttpError = Error & { status?: number }

const router = useRouter()
const me = ref<Me | null>(null)
const meError = ref('')
const loadingMe = ref(false)
const loggingOut = ref(false)

const appeals = ref<Appeal[]>([])
const appealsError = ref('')
const loadingAppeals = ref(false)
const appealContent = ref('')
const appealHint = ref('')
const submitting = ref(false)

const freezeLabel = computed(() => {
  if (!me.value) return '加载中'
  return me.value.is_frozen ? '已冻结' : '未冻结'
})

function httpStatus(err: unknown): number | undefined {
  if (typeof err === 'object' && err && 'status' in err) {
    const s = (err as { status: unknown }).status
    if (typeof s === 'number') return s
  }
  return undefined
}

function detailMessage(data: unknown, fallback: string): string {
  if (typeof data === 'string' && data) return data
  if (!data || typeof data !== 'object') return fallback
  if (!('detail' in data)) return fallback
  const detail = (data as { detail: unknown }).detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object') {
    const obj = detail as { message?: unknown }
    if (typeof obj.message === 'string' && obj.message) return obj.message
  }
  return fallback
}

async function rawRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
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
    throw Object.assign(new Error(detailMessage(data, res.statusText || '请求失败')), {
      status: res.status,
      detail: data,
    }) as HttpError
  }
  return data as T
}

function unwrapItems<T>(data: T[] | { items?: T[] } | null | undefined): T[] {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.items)) return data.items
  return []
}

function displayText(value: unknown) {
  if (value == null || value === '') return '暂无'
  return String(value)
}

function formatTimeOrEmpty(iso?: string | null) {
  if (!iso) return '暂无'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '暂无'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function appealStatusLabel(status: string) {
  if (status === 'pending') return '待处理'
  if (status === 'closed') return '已关闭'
  return status || '未知'
}

function failText(err: unknown, fallback: string) {
  const st = httpStatus(err)
  if (st === 404) return '接口尚未就绪，请稍后刷新'
  return err instanceof Error ? err.message : fallback
}

async function loadAppeals() {
  loadingAppeals.value = true
  appealsError.value = ''
  try {
    const data = await rawRequest<Appeal[] | { items: Appeal[] }>('/api/appeals')
    appeals.value = unwrapItems(data)
  } catch (err) {
    appeals.value = []
    appealsError.value = failText(err, '加载申诉失败')
  } finally {
    loadingAppeals.value = false
  }
}

async function loadMe() {
  loadingMe.value = true
  meError.value = ''
  try {
    const data = await rawRequest<Me>('/api/auth/me')
    me.value = data
    if (!data.is_frozen) {
      await router.replace('/workbench')
      return
    }
    await loadAppeals()
  } catch (err) {
    if (httpStatus(err) === 401) {
      window.location.href = '/login'
      return
    }
    meError.value = failText(err, '加载账号状态失败')
  } finally {
    loadingMe.value = false
  }
}

function onFocus() {
  void loadMe()
}

async function refresh() {
  await loadMe()
}

async function onSubmitAppeal() {
  const content = appealContent.value.trim()
  if (!content) return
  submitting.value = true
  appealHint.value = ''
  try {
    await rawRequest('/api/appeals', {
      method: 'POST',
      body: JSON.stringify({ content }),
    })
    appealContent.value = ''
    ElMessage.success('申诉已提交')
    await loadAppeals()
  } catch (err) {
    if (httpStatus(err) === 409) {
      appealHint.value = '已有待处理申诉'
      ElMessage.warning('已有待处理申诉')
      await loadAppeals()
      return
    }
    const msg = failText(err, '提交失败')
    appealHint.value = msg
    ElMessage.error(msg)
  } finally {
    submitting.value = false
  }
}

async function onLogout() {
  loggingOut.value = true
  try {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  } catch {
    // 仍跳登录页
  }
  window.location.href = '/login'
}

onMounted(() => {
  window.addEventListener('focus', onFocus)
  void loadMe()
})

onUnmounted(() => {
  window.removeEventListener('focus', onFocus)
})
</script>

<style scoped>
.restricted {
  padding: 20px;
}

.meta {
  margin: 0 0 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.meta > div {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 8px 12px;
}

.meta dt {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}

.meta dd {
  margin: 0;
  font-size: 13px;
  word-break: break-word;
}

.appeal-box,
.appeal-list {
  margin-top: 8px;
}

.appeal-box h3,
.appeal-list h3 {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: var(--muted);
}

.appeal-ops {
  margin-top: 12px;
}

.hint-warn,
.fail-msg {
  color: var(--danger);
  font-size: 13px;
}

.hint-warn {
  margin: 8px 0 0;
}

.records {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.record {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.time,
.muted {
  color: var(--muted);
  font-size: 12px;
}

.content,
.reply {
  margin: 0;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
}

.reply {
  color: var(--text);
}
</style>
