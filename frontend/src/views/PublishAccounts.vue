<template>
  <AppLayout>
    <div class="panel accounts-page">
      <div class="page-bar">
        <h2>发布账号</h2>
        <el-button size="small" :loading="loading" @click="load">刷新状态</el-button>
      </div>
      <p class="intro">这里管理真实发布授权；<router-link to="/social">社交账号</router-link>中的样例联系人不提供发布权限。</p>

      <p v-if="resultNotice" class="notice" role="status">{{ resultNotice }}</p>
      <p v-if="error" class="fail-msg" role="alert">{{ error }}</p>
      <div v-if="loading && !payload" class="state-inline">正在加载发布账号…</div>

      <template v-if="payload">
        <section class="block">
          <div class="section-head">
            <h3>抖音</h3>
            <el-button type="primary" size="small" :loading="busy === 'connect'" :disabled="!canAuthorize || !!busy" @click="authorize()">连接抖音</el-button>
          </div>
          <p v-if="!payload.douyin.oauth_enabled" class="notice">授权入口未启用，暂不能连接。</p>
          <p v-if="payload.douyin.configuration_missing.length" class="notice">部署待配置：{{ payload.douyin.configuration_missing.join('、') }}</p>
          <p class="muted">用户授权与应用发布能力是两道校验；应用能力仍待平台验收。授权成功也不代表已公开发布。</p>

          <p v-if="!douyinAccounts.length" class="state-inline">尚无抖音发布连接。</p>
          <div v-else class="account-grid">
            <article v-for="account in douyinAccounts" :key="account.id" class="card account-card">
              <div class="account-head">
                <strong>抖音账号 {{ account.external_account_id }}</strong>
                <span class="status-pill" :class="account.status === 'connected' ? 'succeeded' : 'queued'">{{ statusLabel(account.status) }}</span>
              </div>
              <p>用途：发布 · 凭证：{{ account.credential_kind === 'managed' ? '已加密托管' : '部署配置' }}</p>
              <p>已授予权限：{{ account.scopes.length ? account.scopes.join('、') : '未确认' }}</p>
              <p>访问凭证到期：{{ formatExpiry(account.expires_at) }}</p>
              <p>应用发布能力：待平台验收</p>
              <ul v-if="account.missing.length" class="missing">
                <li v-for="(reason, index) in account.missing" :key="index">{{ reason }}</li>
              </ul>
              <div class="actions">
                <el-button v-if="account.credential_kind === 'managed'" size="small" :loading="busy === `reauth-${account.id}`" :disabled="!canAuthorize || !!busy" @click="authorize(account.id)">重新授权</el-button>
                <el-button v-else size="small" :loading="busy === 'connect'" :disabled="!canAuthorize || !!busy" @click="authorize()">通过 OAuth 新建连接</el-button>
                <el-button v-if="account.credential_kind === 'managed'" size="small" :loading="busy === `refresh-${account.id}`" :disabled="account.status !== 'connected' || !!busy" @click="refresh(account.id)">刷新凭证</el-button>
                <el-button size="small" type="danger" plain :loading="busy === `disconnect-${account.id}`" :disabled="!!busy || account.status === 'revoked'" @click="disconnect(account.id)">本地断开</el-button>
              </div>
              <p v-if="account.credential_kind !== 'managed'" class="muted">此连接使用部署凭证，不能在此刷新。通过 OAuth 会新建托管连接，不会替换旧任务的账号绑定。</p>
            </article>
          </div>
          <p class="muted">本地断开会清除本系统保存的凭证，但无法撤回已经发出的请求；如需在平台撤销授权，请在抖音 App 的授权管理中操作。</p>
        </section>

        <section class="block">
          <h3>小红书</h3>
          <p class="state-inline">发布授权待接入，当前不能连接或发布。</p>
        </section>
      </template>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import { errorLabel, publishAccountApi, type PublishAccountsResponse } from '../publishAccountApi'

const payload = ref<PublishAccountsResponse | null>(null)
const loading = ref(false)
const busy = ref('')
const error = ref('')
const resultNotice = ref('')
const douyinAccounts = computed(() => payload.value?.items.filter((item) => item.platform === 'douyin' && item.purpose === 'publish') || [])
const canAuthorize = computed(() => !!payload.value?.douyin.oauth_enabled && !payload.value.douyin.configuration_missing.length)

function statusLabel(status: string): string {
  return ({ connected: '已连接', expired: '已过期', revoked: '本地已断开', pending: '待连接' } as Record<string, string>)[status] || '状态待确认'
}

function formatExpiry(value: string | null): string {
  if (!value) return '未确认'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '未确认' : date.toLocaleString('zh-CN')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    payload.value = await publishAccountApi.list()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '加载发布账号失败'
  } finally {
    loading.value = false
  }
}

async function authorize(connectionId?: number) {
  if (!canAuthorize.value || busy.value) return
  busy.value = connectionId == null ? 'connect' : `reauth-${connectionId}`
  error.value = ''
  try {
    const { authorization_url: url } = await publishAccountApi.start(connectionId)
    const target = new URL(url)
    if (target.origin !== 'https://open.douyin.com' || target.pathname !== '/platform/oauth/connect/') {
      throw new Error('授权入口地址不符合预期，请联系管理员')
    }
    window.location.assign(target.toString())
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '无法发起授权'
    busy.value = ''
  }
}

async function refresh(id: number) {
  if (busy.value) return
  busy.value = `refresh-${id}`
  error.value = ''
  try {
    const response = await publishAccountApi.refresh(id)
    resultNotice.value = response.status === 'refreshed' ? '凭证已刷新' : response.status === 'current' ? '凭证仍有效' : response.status === 'busy' ? '已有刷新正在进行，请稍后查看状态' : response.status === 'stale' ? '账号状态已变化，请重新加载' : '刷新结果未确认，请核查账号状态'
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '刷新失败'
  } finally {
    busy.value = ''
  }
}

async function disconnect(id: number) {
  if (busy.value) return
  try {
    await ElMessageBox.confirm('仅断开本系统连接并清除本地凭证；已发出的请求无法撤回。平台撤权需到抖音 App 授权管理操作。', '本地断开抖音账号', { confirmButtonText: '本地断开', cancelButtonText: '取消', type: 'warning' })
  } catch {
    return
  }
  busy.value = `disconnect-${id}`
  error.value = ''
  try {
    await publishAccountApi.disconnect(id)
    resultNotice.value = '已在本系统断开；平台授权尚未由本系统撤销'
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '断开失败'
  } finally {
    busy.value = ''
  }
}

onMounted(() => {
  const url = new URL(window.location.href)
  const result = url.searchParams.get('result') || ''
  for (const key of ['result', 'code', 'state', 'scopes', 'error']) url.searchParams.delete(key)
  if (url.toString() !== window.location.href) window.history.replaceState(window.history.state, '', url.toString())
  if (result) resultNotice.value = result === 'connected' ? '抖音授权已保存；应用发布能力仍待平台验收' : errorLabel(result) || '授权未完成，请刷新状态或重新发起授权'
  void load()
})
</script>

<style scoped>
.accounts-page { display: grid; gap: 20px; }
.page-bar, .section-head, .account-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.page-bar h2, .section-head h3 { margin: 0; }
.intro, .muted { color: var(--muted); line-height: 1.6; }
.notice { padding: 12px 14px; border-radius: var(--radius); background: var(--glass-strong); border: 1px solid var(--line); }
.block { display: grid; gap: 14px; }
.account-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.account-card { padding: 18px; }
.account-card p { margin: 10px 0; overflow-wrap: anywhere; }
.missing { margin: 12px 0; padding-left: 20px; color: var(--muted); line-height: 1.6; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.actions :deep(.el-button + .el-button) { margin-left: 0; }
@media (max-width: 600px) { .page-bar, .section-head { align-items: flex-start; flex-direction: column; } .account-grid { grid-template-columns: 1fr; } }
</style>
