<template>
  <AppLayout>
    <div class="panel social-page">
      <div class="page-bar">
        <h2>社交账号</h2>
        <div class="actions">
          <el-button size="small" :loading="loadingAccounts" @click="loadAccounts">刷新</el-button>
        </div>
      </div>

      <p class="sample-banner">演示接入 / 样例联系人</p>
      <p class="sample-note">以下为演示样例，连接后才会出现在已有账号里，不会预填已连接状态。</p>

      <div class="sample-grid">
        <article v-for="sample in samples" :key="sampleKey(sample)" class="card sample-card">
          <div class="info">
            <div class="row">
              <strong>{{ sample.label }}</strong>
              <span class="status-pill queued">{{ platformLabel(sample.platform) }}</span>
            </div>
            <p class="meta-line">
              <span class="mono">{{ sample.external_account_id }}</span>
              <span class="muted">{{ sampleStatus(sample) }}</span>
            </p>
            <div class="card-ops">
              <el-button
                size="small"
                type="primary"
                :loading="connectingKey === sampleKey(sample)"
                :disabled="sampleLinked(sample)?.status === 'connected'"
                @click="onConnectSample(sample)"
              >
                {{ sampleLinked(sample)?.status === 'connected' ? '已连接' : '连接' }}
              </el-button>
            </div>
          </div>
        </article>
      </div>

      <section class="block">
        <h3>已有账号</h3>
        <div v-if="accountsError" class="state-block">
          <p class="fail-msg">{{ accountsError }}</p>
          <el-button size="small" :loading="loadingAccounts" @click="loadAccounts">重试</el-button>
        </div>
        <div v-else-if="loadingAccounts && !accounts.length" class="state-inline">加载账号…</div>
        <div v-else-if="!accounts.length" class="state-block">
          <p>还没有账号。可先连接上方样例，不要把空列表当成已接入。</p>
        </div>
        <div v-else class="account-grid">
          <article
            v-for="item in accounts"
            :key="item.id"
            class="card"
            :class="{ selected: selectedId === item.id }"
            @click="selectAccount(item)"
          >
            <div class="info">
              <div class="row">
                <strong>{{ item.display_name || item.external_account_id }}</strong>
                <span class="status-pill" :class="item.status === 'connected' ? 'succeeded' : 'queued'">
                  {{ statusLabel(item.status) }}
                </span>
              </div>
              <p class="meta-line">
                <span>{{ platformLabel(item.platform) }}</span>
                <span class="mono">{{ item.external_account_id }}</span>
              </p>
              <p class="meta-line">
                <span>最近同步</span>
                <span class="mono time">{{ formatTimeOrEmpty(item.last_synced_at) }}</span>
              </p>
              <div class="card-ops" @click.stop>
                <el-button
                  v-if="item.status === 'connected'"
                  size="small"
                  :loading="busyId === item.id && busyAction === 'disconnect'"
                  @click="onDisconnect(item)"
                >
                  断开
                </el-button>
                <el-button
                  v-else
                  size="small"
                  :loading="busyId === item.id && busyAction === 'reconnect'"
                  @click="onReconnect(item)"
                >
                  重连
                </el-button>
                <el-button
                  size="small"
                  :disabled="item.status !== 'connected'"
                  :loading="busyId === item.id && busyAction === 'sync'"
                  @click="onSync(item)"
                >
                  同步
                </el-button>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section class="block">
        <h3>联系人</h3>
        <div v-if="!selectedId" class="state-block">
          <p>选中上方账号后加载联系人</p>
        </div>
        <div v-else-if="contactsError" class="state-block">
          <p class="fail-msg">{{ contactsError }}</p>
        </div>
        <div v-else-if="loadingContacts" class="state-inline">加载联系人…</div>
        <div v-else-if="!contacts.length" class="state-block">
          <p>该账号暂无联系人</p>
        </div>
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>昵称</th>
                <th>备注</th>
                <th>标签</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in contacts" :key="row.id">
                <td>{{ row.nickname }}</td>
                <td>{{ row.remark || '—' }}</td>
                <td>{{ tagText(row.tags) }}</td>
                <td>{{ sourceLabel(row.data_source) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import {
  socialApi,
  type SocialAccount,
  type SocialContact,
  type SocialPlatform,
} from '../socialApi'

type Sample = {
  platform: SocialPlatform
  external_account_id: string
  label: string
}

const samples: Sample[] = [
  { platform: 'douyin', external_account_id: 'sample_dy_01', label: '抖音样例 01' },
  { platform: 'douyin', external_account_id: 'sample_dy_02', label: '抖音样例 02' },
  { platform: 'xiaohongshu', external_account_id: 'sample_xhs_01', label: '小红书样例 01' },
]

const accounts = ref<SocialAccount[]>([])
const loadingAccounts = ref(false)
const accountsError = ref('')
const connectingKey = ref('')
const busyId = ref<number | null>(null)
const busyAction = ref<'disconnect' | 'reconnect' | 'sync' | ''>('')

const selectedId = ref<number | null>(null)
const contacts = ref<SocialContact[]>([])
const loadingContacts = ref(false)
const contactsError = ref('')

let contactsSeq = 0
let contactsAbort: AbortController | null = null

function sampleKey(sample: Sample) {
  return `${sample.platform}:${sample.external_account_id}`
}

function sampleLinked(sample: Sample) {
  return accounts.value.find(
    (item) => item.platform === sample.platform && item.external_account_id === sample.external_account_id,
  )
}

function sampleStatus(sample: Sample) {
  const found = sampleLinked(sample)
  if (!found) return '未接入'
  return found.status === 'connected' ? '已在列表中' : '列表中未连接'
}

function platformLabel(platform: string) {
  if (platform === 'douyin') return '抖音'
  if (platform === 'xiaohongshu') return '小红书'
  return platform || '未知'
}

function statusLabel(status: string) {
  if (status === 'connected') return '已连接'
  if (status === 'disconnected') return '未连接'
  return status || '未知'
}

function sourceLabel(source?: string) {
  if (source === 'sample') return '样例'
  return source || '—'
}

function tagText(tags: unknown) {
  if (!Array.isArray(tags) || !tags.length) return '—'
  return tags.map((t) => String(t)).join('、')
}

function formatTimeOrEmpty(iso?: string | null) {
  if (!iso) return '尚未同步'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '尚未同步'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function httpStatus(err: unknown): number | undefined {
  if (typeof err === 'object' && err && 'status' in err) {
    const s = (err as { status: unknown }).status
    if (typeof s === 'number') return s
  }
  return undefined
}

function isAbort(err: unknown) {
  return (
    (typeof err === 'object' && err && 'name' in err && (err as { name: string }).name === 'AbortError') ||
    (err instanceof DOMException && err.name === 'AbortError')
  )
}

function failText(err: unknown, fallback: string) {
  if (httpStatus(err) === 404) return '接口尚未就绪，请稍后刷新'
  return err instanceof Error ? err.message : fallback
}

async function loadAccounts() {
  loadingAccounts.value = true
  accountsError.value = ''
  try {
    const data = await socialApi.listAccounts()
    accounts.value = data.items
  } catch (err) {
    accounts.value = []
    accountsError.value = failText(err, '加载账号失败')
  } finally {
    loadingAccounts.value = false
  }
}

async function loadContacts(accountId: number) {
  contactsSeq += 1
  const seq = contactsSeq
  contactsAbort?.abort()
  const ac = new AbortController()
  contactsAbort = ac
  contacts.value = []
  contactsError.value = ''
  loadingContacts.value = true
  try {
    const data = await socialApi.listContacts({ account_id: accountId }, { signal: ac.signal })
    if (seq !== contactsSeq) return
    contacts.value = data.items
  } catch (err) {
    if (isAbort(err) || seq !== contactsSeq) return
    contacts.value = []
    contactsError.value = failText(err, '加载联系人失败')
  } finally {
    if (seq === contactsSeq) loadingContacts.value = false
  }
}

function selectAccount(item: SocialAccount) {
  selectedId.value = item.id
  void loadContacts(item.id)
}

async function onConnectSample(sample: Sample) {
  connectingKey.value = sampleKey(sample)
  try {
    const existing = sampleLinked(sample)
    if (existing && existing.status !== 'connected') {
      try {
        await socialApi.reconnectAccount(existing.id)
      } catch (err) {
        if (httpStatus(err) !== 404) throw err
        await socialApi.connectAccount({
          platform: sample.platform,
          external_account_id: sample.external_account_id,
        })
      }
    } else {
      await socialApi.connectAccount({
        platform: sample.platform,
        external_account_id: sample.external_account_id,
      })
    }
    ElMessage.success('已提交连接')
    await loadAccounts()
    const linked = sampleLinked(sample)
    if (linked) selectAccount(linked)
  } catch (err) {
    ElMessage.error(failText(err, '连接失败'))
  } finally {
    connectingKey.value = ''
  }
}

async function onDisconnect(item: SocialAccount) {
  busyId.value = item.id
  busyAction.value = 'disconnect'
  try {
    await socialApi.disconnectAccount(item.id)
    ElMessage.success('已断开')
    await loadAccounts()
    if (selectedId.value === item.id) {
      const fresh = accounts.value.find((row) => row.id === item.id)
      if (fresh) void loadContacts(fresh.id)
    }
  } catch (err) {
    ElMessage.error(failText(err, '断开失败'))
  } finally {
    busyId.value = null
    busyAction.value = ''
  }
}

async function onReconnect(item: SocialAccount) {
  busyId.value = item.id
  busyAction.value = 'reconnect'
  try {
    try {
      await socialApi.reconnectAccount(item.id)
    } catch (err) {
      if (httpStatus(err) !== 404) throw err
      await socialApi.connectAccount({
        platform: item.platform as SocialPlatform,
        external_account_id: item.external_account_id,
      })
    }
    ElMessage.success('已重连')
    await loadAccounts()
    if (selectedId.value === item.id) void loadContacts(item.id)
  } catch (err) {
    ElMessage.error(failText(err, '重连失败'))
  } finally {
    busyId.value = null
    busyAction.value = ''
  }
}

async function onSync(item: SocialAccount) {
  busyId.value = item.id
  busyAction.value = 'sync'
  try {
    await socialApi.syncAccount(item.id)
    ElMessage.success('已同步')
    await loadAccounts()
    if (selectedId.value === item.id) void loadContacts(item.id)
  } catch (err) {
    ElMessage.error(failText(err, '同步失败'))
  } finally {
    busyId.value = null
    busyAction.value = ''
  }
}

onMounted(() => {
  void loadAccounts()
})

onUnmounted(() => {
  contactsSeq += 1
  contactsAbort?.abort()
})
</script>

<style scoped>
.social-page {
  padding: 20px;
}

.sample-banner {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 650;
  color: var(--text);
}

.sample-note {
  margin: 0 0 16px;
  color: var(--muted);
  font-size: 13px;
}

.sample-grid,
.account-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
}

.card {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
  overflow: hidden;
}

.account-grid .card {
  cursor: pointer;
}

.account-grid .card.selected {
  border-color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent);
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

.time,
.muted {
  color: var(--muted);
}

.card-ops {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}

.block {
  margin-top: 24px;
}

.block h3 {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: var(--muted);
}

.fail-msg {
  color: var(--danger);
}
</style>
