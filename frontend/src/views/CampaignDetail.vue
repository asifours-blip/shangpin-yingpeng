<template>
  <AppLayout wide>
    <section v-if="error" class="panel state-block"><p class="fail-msg">{{ error }}</p><el-button @click="load">重试读取</el-button></section>
    <template v-else-if="detail">
      <section class="panel detail-head"><div><h1>活动 {{ detail.id }}</h1><p>商品 {{ product?.name || detail.product_id }} · 事实版本 #{{ detail.fact_version_id }} · {{ detail.status }}</p></div><div class="head-actions"><router-link to="/operations">返回运营总览</router-link><el-button @click="load">刷新进度</el-button><el-button v-if="detail.status === 'draft'" type="primary" :loading="starting" @click="start">启动生成</el-button></div></section>
      <section v-if="versionWarning" class="panel warn" role="alert">{{ versionWarning }}</section>
      <section class="panel"><h2>执行与预算</h2><p>模型调用次数上限 {{ detail.run?.generation_budget ?? '—' }} · 已预留 {{ detail.run?.budget_reserved ?? '—' }} · {{ detail.run?.started_at ? '已启动' : '待启动' }}</p><p class="hint">本轮使用来源参考 {{ detail.selected_source_item_ids.length }} 条；创作方向 {{ String(detail.brief?.generation_requirements || '未指定') }}</p><ul v-if="sourceCandidates.length"><li v-for="item in sourceCandidates" :key="item.source_item_id">来源 {{ item.source_item_id }} · {{ item.cite || '无标题' }}{{ detail.selected_source_item_ids.includes(item.source_item_id) ? ' · 本轮使用' : ' · 候选' }}</li></ul></section>
      <div class="side-grid" :class="{ single: sides.length === 1 }"><article v-for="side in sides" :key="side.platform" class="panel side" :class="{ selected: side.platform === selectedPlatform }"><header><h2>{{ platformLabel(side.platform) }}</h2><span>v{{ side.current.version }} · {{ side.current.status }}</span></header>
        <p v-if="operations[side.platform]?.generation_connection === 'pending_connection' && side.steps.some((item) => item.status === 'pending')" class="warn">生成模型待连接。当前步骤尚未提交；连接后可继续现有任务。</p>
        <p v-if="side.steps.some((item) => item.status === 'unknown')" class="warn">结果未知：请联系运维按下方请求编号核对上游结果与资产，不能盲目重提。</p>
        <p v-else-if="side.steps.some((item) => item.status === 'failed')" class="warn">部分步骤失败，可在审核页核对后局部重做。</p>
        <p v-else-if="side.blockers.length" class="warn">{{ side.blockers.map((item) => item.message).join('；') }}</p>
        <p v-else-if="!side.current.assets.length" class="hint">媒体尚未就绪。</p>
        <p v-if="operations[side.platform]?.next_action" class="hint">下一步：{{ operations[side.platform].next_action }}</p>
        <h3>文案</h3><p><strong>{{ side.current.title || '暂无标题' }}</strong></p><p class="copy">{{ side.current.body || '暂无正文' }}</p><p class="hint">{{ side.current.hashtags?.map((tag) => `#${tag}`).join(' ') || '暂无话题' }}</p>
        <h3>媒体</h3><div class="media"><div v-for="asset in side.current.assets" :key="`${asset.asset_id}-${asset.position}`"><video v-if="asset.role === 'final_video'" controls :src="`/api/assets/${asset.asset_id}/file`" /><img v-else :src="`/api/assets/${asset.asset_id}/file`" :alt="`${asset.role} ${asset.position}`" /><small>{{ asset.position }} · {{ asset.role }}</small></div><p v-if="!side.current.assets.length">暂无</p></div>
        <h3>步骤</h3><ul><li v-for="item in side.steps" :key="`${item.step_key}-${item.version}`">{{ item.step_key }} · {{ item.status }}<span v-if="item.error_code"> · {{ item.error_code }}</span><details v-if="stepRequest(item.step_key, item.version)"><summary>执行信息</summary>{{ stepRequest(item.step_key, item.version) }}</details></li></ul>
        <h3>事实与质检</h3><p>引用事实版本 #{{ side.current.fact_version_id ?? detail.fact_version_id }}</p><p>{{ factText }}</p><p>质检：{{ side.current.qc_result ? (side.blockers.length ? '有待处理问题' : '当前无阻塞问题') : '尚无质检结果' }}</p><ul><li v-for="item in side.blockers" :key="item.code">{{ item.message }}（{{ item.code }}）</li><li v-if="!side.blockers.length && side.current.qc_result">当前没有审核阻塞项。</li></ul>
        <div class="actions"><router-link :to="reviewLink(side.platform, side.current.version)">编辑与审核</router-link><router-link v-if="!side.steps.some((item) => item.status === 'unknown')" :to="reviewLink(side.platform, side.current.version, 'redo')">局部重做</router-link><router-link :to="reviewLink(side.platform, side.current.version, 'publish')">安排发布</router-link><el-button :disabled="isStale(side.platform) || !hasCurrentApproval(side)" :loading="exporting === side.platform" @click="onExport(side)">导出素材</el-button></div>
        <p v-if="hasCurrentApproval(side)" class="hint">导出锁定当前审核版本。发布账号待连接仍可交付；导出不等于发布。</p>
        <p v-else class="hint">当前版本尚未有效审核；请先完成审核后再导出。</p>
      </article></div>
    </template>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import { campaignApi, type CampaignDetail, type OverviewItem, type ReviewPlatform } from '../campaignApi'
import { downloadApprovedAssets, hasCurrentApproval } from '../exportDelivery'
import { ElMessage } from 'element-plus'
import { productApi, type OwnedProduct } from '../productApi'
import { factLabel, factValue } from '../productFacts'

const route = useRoute(); const detail = ref<CampaignDetail | null>(null); const sides = ref<ReviewPlatform[]>([])
const product = ref<OwnedProduct | undefined>(); const error = ref(''); const starting = ref(false)
const exporting = ref<string | null>(null)
const facts = ref<Record<string, unknown>>({})
const operations = ref<Record<string, OverviewItem>>({})
const selectedPlatform = computed(() => String(route.query.platform || ''))
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let refreshCount = 0
const factText = computed(() => Object.entries(facts.value).map(([key, value]) => `${factLabel(key)}：${factValue(key, value)}`).join('；') || '没有已确认事实')
const sourceCandidates = computed(() => (Array.isArray(detail.value?.brief?.candidates) ? detail.value!.brief.candidates : []) as { source_item_id: number; cite?: string }[])
const versionWarning = computed(() => {
  const platform = String(route.query.platform || '')
  const expected = Number(route.query.version)
  if (!platform) return ''
  if (route.query.version != null && (!Number.isInteger(expected) || expected < 1)) return '链接中的版本号无效。'
  if (!expected) return ''
  const side = sides.value.find((item) => item.platform === platform)
  if (!side) return `活动没有 ${platform} 平台内容。`
  return side.current.version === expected ? '' : `链接指向 ${platform} v${expected}，当前为 v${side.current.version}。请核对后再操作。`
})
function isStale(platform: string) { return !!versionWarning.value && String(route.query.platform || '') === platform }
function platformLabel(platform: string) { return platform === 'douyin' ? '抖音' : '小红书' }
function stepRequest(key: string, version: number) {
  const step = detail.value?.run?.steps.find((item) => item.step_key === key && item.version === version)
  if (!step) return ''
  return [step.provider_request_id && `上游请求号 ${step.provider_request_id}`, step.local_request_id && `本地请求号 ${step.local_request_id}`].filter(Boolean).join(' · ')
}
function reviewLink(platform: string, version: number, section?: string) { return { path: '/review', query: { campaign_id: String(detail.value!.id), platform, version: String(version), ...(section ? { section } : {}) } } }
async function load() {
  const id = Number(route.params.id); if (!Number.isInteger(id) || id < 1) { error.value = '活动编号无效'; return }
  error.value = ''
  try {
    const [campaign, review, products] = await Promise.all([campaignApi.detail(id), campaignApi.review(id), productApi.list()])
    detail.value = campaign; sides.value = review.platforms; facts.value = review.facts; product.value = products.items.find((row) => row.id === campaign.product_id)
    const overview = await campaignApi.overview().catch(() => null)
    operations.value = Object.fromEntries((overview?.items || []).filter((item) => item.campaign_id === id).map((item) => [item.platform, item]))
    if (refreshTimer) clearTimeout(refreshTimer)
    if (campaign.status === 'generating' && refreshCount < 12) { refreshCount++; refreshTimer = setTimeout(() => { void load() }, 5000) }
  } catch (err) { error.value = err instanceof Error ? err.message : '活动读取失败' }
}
async function start() {
  if (!detail.value || starting.value) return
  starting.value = true
  const keyName = `campaign-start-${detail.value.id}`
  const key = sessionStorage.getItem(keyName) || crypto.randomUUID()
  sessionStorage.setItem(keyName, key)
  try { await campaignApi.start(detail.value.id, key); sessionStorage.removeItem(keyName); await load() }
  catch (err) { error.value = err instanceof Error ? err.message : '启动失败' }
  finally { starting.value = false }
}
async function onExport(side: ReviewPlatform) {
  if (!detail.value || exporting.value) return
  exporting.value = side.platform
  try {
    await downloadApprovedAssets(detail.value.id, side, (message) => ElMessage.error(message))
    ElMessage.success('已开始下载已审素材；导出不等于发布')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '无法导出素材')
    if (err && typeof err === 'object' && 'status' in err && err.status === 409) await load()
  } finally { exporting.value = null }
}
onMounted(load); watch(() => route.params.id, () => { refreshCount = 0; void load() })
onUnmounted(() => { if (refreshTimer) clearTimeout(refreshTimer) })
</script>

<style scoped>
.panel { padding: 28px; margin-bottom: 18px; }
.detail-head { border: 0; box-shadow: none; background: transparent; padding: 8px 0 18px; }
.detail-head, .detail-head .head-actions, .side header, .actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.detail-head h1, .side h2 { margin: 0; letter-spacing: -.04em; }
.detail-head h1 { font-size: clamp(32px, 4vw, 48px); font-weight: 500; }
.side h2 { font-size: 24px; }
.detail-head p, .hint { color: var(--muted); }
.side-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.side-grid.single { grid-template-columns: 1fr; }
.side.selected { border-color: #9ca3af; box-shadow: inset 0 0 0 1px #9ca3af; }
.side h3 { margin: 20px 0 8px; }
.copy { white-space: pre-wrap; }
.media { display: flex; flex-wrap: wrap; gap: 8px; }
.media img, .media video {
  width: 150px;
  height: 160px;
  object-fit: contain;
  border-radius: var(--radius);
  background: var(--canvas);
  border: 1px solid var(--line);
}
.media small { display: block; }
.actions { justify-content: flex-start; flex-wrap: wrap; margin-top: 20px; }
.actions a { color: var(--accent); }
.warn { color: var(--danger); }
@media (max-width: 800px) {
  .panel { padding: 16px; }
  .side-grid { grid-template-columns: 1fr; }
  .detail-head { align-items: flex-start; flex-direction: column; }
}
</style>
