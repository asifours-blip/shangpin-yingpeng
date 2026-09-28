<template>
  <AppLayout>
    <div class="panel admin-page">
      <div class="page-bar">
        <h2>用户管理</h2>
        <div class="actions">
          <el-button size="small" @click="openAppeals">申诉</el-button>
          <el-button type="primary" size="small" @click="openCreate">创建</el-button>
        </div>
      </div>

      <div v-if="loading && !items.length" class="state-inline">加载中…</div>
      <div v-else-if="!items.length" class="state-block">
        <p>还没有用户</p>
      </div>
      <div v-else class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>用户</th>
              <th>角色</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.id">
              <td>{{ row.username }}</td>
              <td>{{ row.role === 'admin' ? '管理员' : '普通用户' }}</td>
              <td>
                <div class="status-cell">
                  <span class="status-pill" :class="row.is_active ? 'succeeded' : 'queued'">
                    {{ row.is_active ? '启用' : '禁用' }}
                  </span>
                  <span class="status-pill" :class="row.is_frozen ? 'failed' : 'succeeded'">
                    {{ row.is_frozen ? '已冻结' : '未冻结' }}
                  </span>
                </div>
              </td>
              <td class="ops">
                <el-button
                  v-if="row.is_active"
                  size="small"
                  :disabled="row.id === meId"
                  @click="onDisable(row)"
                >
                  禁用
                </el-button>
                <el-button v-else size="small" @click="onEnable(row)">启用</el-button>
                <el-button
                  v-if="!row.is_frozen"
                  size="small"
                  type="danger"
                  plain
                  :disabled="isFreezeProtected(row)"
                  @click="openFreeze(row)"
                >
                  冻结
                </el-button>
                <el-button v-else size="small" @click="onUnfreeze(row)">解冻</el-button>
                <el-button size="small" @click="onReset(row)">重置密码</el-button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <el-dialog v-model="createOpen" title="创建用户" width="360px" :close-on-click-modal="false">
      <el-form @submit.prevent="onCreate">
        <el-form-item :error="createUserError">
          <el-input v-model="newUsername" placeholder="账号" autocomplete="off" />
        </el-form-item>
        <el-form-item :error="createPassError">
          <el-input
            v-model="newPassword"
            type="password"
            placeholder="密码"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item>
          <el-select v-model="newRole" style="width: 100%">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-button type="primary" style="width: 100%" :loading="creating" native-type="submit">
          创建
        </el-button>
      </el-form>
    </el-dialog>

    <el-dialog v-model="freezeOpen" title="冻结用户" width="400px" :close-on-click-modal="false">
      <p class="freeze-hint">冻结 {{ freezeTarget?.username }} 需填写原因。不会踢下线，账号将进入受限页。</p>
      <el-form @submit.prevent="onFreeze">
        <el-form-item :error="freezeReasonError">
          <el-input
            v-model="freezeReason"
            type="textarea"
            :rows="4"
            maxlength="500"
            show-word-limit
            placeholder="冻结原因（必填）"
          />
        </el-form-item>
        <el-button type="danger" style="width: 100%" :loading="freezing" native-type="submit">
          确认冻结
        </el-button>
      </el-form>
    </el-dialog>

    <el-drawer v-model="appealsOpen" title="申诉" size="420px" :destroy-on-close="false">
      <div class="appeal-toolbar">
        <el-select v-model="appealStatus" style="width: 140px" @change="loadAppeals">
          <el-option label="待处理" value="pending" />
          <el-option label="已关闭" value="closed" />
        </el-select>
        <el-button size="small" :loading="appealsLoading" @click="loadAppeals">刷新</el-button>
      </div>
      <div v-if="appealsError" class="state-inline fail-msg">{{ appealsError }}</div>
      <div v-else-if="appealsLoading && !appeals.length" class="state-inline">加载申诉…</div>
      <div v-else-if="!appeals.length" class="state-block">
        <p>{{ appealStatus === 'closed' ? '没有已关闭申诉' : '没有待处理申诉' }}</p>
      </div>
      <ul v-else class="appeal-list">
        <li
          v-for="item in appeals"
          :key="item.id"
          class="appeal-item"
          :class="{ active: selectedAppeal?.id === item.id }"
          @click="selectAppeal(item)"
        >
          <div class="appeal-row">
            <span class="status-pill" :class="item.status === 'pending' ? 'running' : 'succeeded'">
              {{ item.status === 'pending' ? '待处理' : '已关闭' }}
            </span>
            <span class="muted">{{ usernameOf(item.user_id) }}</span>
          </div>
          <p class="appeal-preview">{{ item.content }}</p>
        </li>
      </ul>

      <div v-if="selectedAppeal" class="appeal-detail">
        <h3>申诉详情</h3>
        <p class="muted">{{ usernameOf(selectedAppeal.user_id) }} · {{ formatTime(selectedAppeal.created_at) }}</p>
        <p class="appeal-content">{{ selectedAppeal.content }}</p>
        <p v-if="selectedAppeal.admin_reply" class="appeal-reply">管理员回复：{{ selectedAppeal.admin_reply }}</p>
        <template v-if="selectedAppeal.status === 'pending'">
          <el-form-item :error="replyError">
            <el-input
              v-model="adminReply"
              type="textarea"
              :rows="4"
              maxlength="2000"
              show-word-limit
              placeholder="回复内容（必填）。关闭申诉不会解冻。"
            />
          </el-form-item>
          <el-button type="primary" :loading="closing" :disabled="!adminReply.trim()" @click="onCloseAppeal">
            关闭申诉
          </el-button>
        </template>
      </div>
    </el-drawer>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import { api } from '../api'
import { adminApi, type AdminUser } from '../adminApi'

type UserRow = AdminUser & { is_frozen?: boolean }

type AdminAppeal = {
  id: number
  user_id: number
  freeze_event_id: number
  content: string
  status: string
  admin_reply?: string | null
  handled_by_id?: number | null
  handled_at?: string | null
  created_at?: string
}

const items = ref<UserRow[]>([])
const loading = ref(false)
const meId = ref<number | null>(null)

const createOpen = ref(false)
const creating = ref(false)
const newUsername = ref('')
const newPassword = ref('')
const newRole = ref('user')
const createUserError = ref('')
const createPassError = ref('')

const freezeOpen = ref(false)
const freezing = ref(false)
const freezeTarget = ref<UserRow | null>(null)
const freezeReason = ref('')
const freezeReasonError = ref('')

const appealsOpen = ref(false)
const appealsLoading = ref(false)
const appealsError = ref('')
const appeals = ref<AdminAppeal[]>([])
const appealStatus = ref<'pending' | 'closed'>('pending')
const selectedAppeal = ref<AdminAppeal | null>(null)
const adminReply = ref('')
const replyError = ref('')
const closing = ref(false)

function isFreezeProtected(row: UserRow) {
  const name = String(row.username || '').toLowerCase()
  return name === 'demo' || name === 'admin' || row.role === 'admin'
}

function usernameOf(userId: number) {
  return items.value.find((u) => u.id === userId)?.username || `#${userId}`
}

function formatTime(iso?: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function detailOf(data: unknown, fallback: string) {
  if (typeof data === 'string' && data) return data
  if (!data || typeof data !== 'object' || !('detail' in data)) return fallback
  const detail = (data as { detail: unknown }).detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const message = (detail as { message: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  return fallback
}

async function adminFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
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
    throw Object.assign(new Error(detailOf(data, res.statusText || '请求失败')), { status: res.status })
  }
  return data as T
}

async function load() {
  loading.value = true
  try {
    const data = await adminApi.listUsers()
    items.value = data.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  newUsername.value = ''
  newPassword.value = ''
  newRole.value = 'user'
  createUserError.value = ''
  createPassError.value = ''
  createOpen.value = true
}

async function onCreate() {
  createUserError.value = ''
  createPassError.value = ''
  if (!newUsername.value.trim()) {
    createUserError.value = '请输入账号'
    return
  }
  if (!newPassword.value) {
    createPassError.value = '请输入密码'
    return
  }
  creating.value = true
  try {
    await adminApi.createUser({
      username: newUsername.value.trim(),
      password: newPassword.value,
      role: newRole.value,
    })
    createOpen.value = false
    ElMessage.success('已创建，可用该账号登录')
    await load()
  } catch (err) {
    createUserError.value = err instanceof Error ? err.message : '创建失败'
  } finally {
    creating.value = false
  }
}

async function onDisable(row: UserRow) {
  try {
    await ElMessageBox.confirm(`禁用 ${row.username} 后会立即踢下线，确认？`, '禁用用户', {
      confirmButtonText: '禁用',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await adminApi.setUserStatus(row.id, false)
    ElMessage.success('已禁用')
    await load()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : '操作失败')
  }
}

async function onEnable(row: UserRow) {
  try {
    await adminApi.setUserStatus(row.id, true)
    ElMessage.success('已启用')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '操作失败')
  }
}

function openFreeze(row: UserRow) {
  if (isFreezeProtected(row)) {
    ElMessage.error('不能冻结管理员或演示账号')
    return
  }
  freezeTarget.value = row
  freezeReason.value = ''
  freezeReasonError.value = ''
  freezeOpen.value = true
}

async function onFreeze() {
  freezeReasonError.value = ''
  const reason = freezeReason.value.trim()
  if (!reason) {
    freezeReasonError.value = '请填写冻结原因'
    return
  }
  const row = freezeTarget.value
  if (!row) return
  if (isFreezeProtected(row)) {
    freezeReasonError.value = '不能冻结管理员或演示账号'
    return
  }
  freezing.value = true
  try {
    await adminFetch(`/api/admin/users/${row.id}/freeze`, {
      method: 'PATCH',
      body: JSON.stringify({ action: 'freeze', reason }),
    })
    freezeOpen.value = false
    ElMessage.success('已冻结')
    await load()
  } catch (err) {
    freezeReasonError.value = err instanceof Error ? err.message : '冻结失败'
  } finally {
    freezing.value = false
  }
}

async function onUnfreeze(row: UserRow) {
  try {
    await ElMessageBox.confirm(`确认解冻 ${row.username}？关闭申诉不会解冻，需单独解冻。`, '解冻用户', {
      confirmButtonText: '解冻',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await adminFetch(`/api/admin/users/${row.id}/freeze`, {
      method: 'PATCH',
      body: JSON.stringify({ action: 'unfreeze' }),
    })
    ElMessage.success('已解冻')
    await load()
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : '解冻失败')
  }
}

async function onReset(row: UserRow) {
  try {
    const { value } = await ElMessageBox.prompt(`为 ${row.username} 设置新密码`, '重置密码', {
      confirmButtonText: '重置',
      cancelButtonText: '取消',
      inputType: 'password',
      inputPlaceholder: '新密码',
      inputValidator: (v) => (v && String(v).length > 0 ? true : '请输入新密码'),
    })
    await adminApi.resetPassword(row.id, String(value))
    ElMessage.success('密码已重置，旧登录已失效')
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : '操作失败')
  }
}

async function loadAppeals() {
  appealsLoading.value = true
  appealsError.value = ''
  selectedAppeal.value = null
  adminReply.value = ''
  replyError.value = ''
  try {
    const data = await adminFetch<{ items?: AdminAppeal[] } | AdminAppeal[]>(
      `/api/admin/appeals?status=${encodeURIComponent(appealStatus.value)}`,
    )
    appeals.value = Array.isArray(data) ? data : data.items || []
  } catch (err) {
    appeals.value = []
    appealsError.value = err instanceof Error ? err.message : '加载申诉失败'
  } finally {
    appealsLoading.value = false
  }
}

function openAppeals() {
  appealsOpen.value = true
  appealStatus.value = 'pending'
  void loadAppeals()
}

function selectAppeal(item: AdminAppeal) {
  selectedAppeal.value = item
  adminReply.value = item.admin_reply || ''
  replyError.value = ''
}

async function onCloseAppeal() {
  const item = selectedAppeal.value
  if (!item) return
  const reply = adminReply.value.trim()
  if (!reply) {
    replyError.value = '请填写回复'
    return
  }
  closing.value = true
  replyError.value = ''
  try {
    await adminFetch(`/api/admin/appeals/${item.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'closed', admin_reply: reply }),
    })
    ElMessage.success('申诉已关闭（未解冻）')
    await loadAppeals()
  } catch (err) {
    replyError.value = err instanceof Error ? err.message : '关闭失败'
  } finally {
    closing.value = false
  }
}

onMounted(async () => {
  try {
    const me = await api.me()
    meId.value = me.id
  } catch {
    meId.value = null
  }
  await load()
})
</script>

<style scoped>
.admin-page {
  padding: 20px;
}

.status-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.freeze-hint {
  margin: 0 0 12px;
  color: var(--muted);
  font-size: 13px;
}

.appeal-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.appeal-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.appeal-item {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
  padding: 10px 12px;
  cursor: pointer;
}

.appeal-item.active {
  border-color: var(--accent);
}

.appeal-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.appeal-preview,
.appeal-content,
.appeal-reply,
.muted,
.fail-msg {
  margin: 6px 0 0;
  font-size: 13px;
}

.appeal-preview {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.appeal-content,
.appeal-reply {
  white-space: pre-wrap;
  word-break: break-word;
}

.muted {
  color: var(--muted);
  font-size: 12px;
}

.fail-msg {
  color: var(--danger);
}

.appeal-detail {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.appeal-detail h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
</style>
