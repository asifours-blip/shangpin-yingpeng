<template>
  <AppLayout wide>
    <section v-if="loading" class="panel">正在读取计划执行记录…</section>
    <section v-if="error" class="panel error" role="alert">{{ error }} <el-button size="small" @click="load">重试</el-button></section>
    <template v-if="run">
      <section class="panel run-head">
        <div><h1>计划执行 {{ run.id }}</h1><p>计划 {{ run.plan_id }} · {{ statusLabel(run.status) }} · 窗口 {{ formatTime(run.scheduled_for) }}</p></div>
        <div class="head-actions"><router-link :to="{ path: '/operation-plans', query: { plan_id: String(run.plan_id) } }">返回执行记录</router-link><el-button @click="load">刷新</el-button></div>
      </section>
      <section class="panel">
        <h2>这轮进度与下一步</h2>
        <p v-if="run.blocker_message" class="warn">{{ run.blocker_message }}<span v-if="run.blocker_code">（{{ run.blocker_code }}）</span></p>
        <p v-else>当前没有阻塞原因。</p>
        <p>下一步：{{ run.next_action || '查看活动状态' }}</p>
        <p class="hint">计划暂停会阻止尚未创建的新活动；已经开始的活动继续沿原有状态机处理。自动推进最多到待人工审核。</p>
      </section>
      <div class="run-grid">
        <section class="panel"><h2>来源与商品</h2>
          <p>来源：{{ runSourceName(run) }}</p>
          <p>实际取得 {{ run.actual_count }} 条 · {{ run.source_observed_at ? `采集于 ${formatTime(run.source_observed_at)}` : '无采集时间' }}</p>
          <p>排序依据：{{ run.source_sort_metric || '未提供热度依据' }}</p>
          <p class="hint">{{ run.source_scope_description || '无来源范围说明' }}</p>
          <p v-if="run.source_run_id">采集批次 {{ run.source_run_id }}</p>
          <ul v-if="run.source_items.length"><li v-for="item in run.source_items" :key="item.id">{{ item.source_rank ?? '—' }} · {{ item.title || `来源条目 ${item.id}` }} · {{ formatTime(item.observed_at) }}</li></ul>
          <p v-else class="hint">本轮没有来源条目，不会生成虚构榜单。</p>
        </section>
        <section class="panel"><h2>活动与预算</h2>
          <p>本轮创建时配置：每日最多 {{ run.config_snapshot?.daily_campaign_limit ?? '—' }} 个活动 · 每日最多 {{ run.config_snapshot?.daily_budget_limit ?? '—' }} 次模型调用 · 单活动最多 {{ run.config_snapshot?.generation_budget ?? '—' }} 次</p>
          <p v-if="run.current_daily_campaign_limit != null && run.current_daily_budget_limit != null">当前计划每日额度：最多 {{ run.current_daily_campaign_limit }} 个活动 · {{ run.current_daily_budget_limit }} 次模型调用。手动继续时按当前每日额度核验。</p>
          <p>目标平台：{{ run.config_snapshot?.target_platforms?.map(platformLabel).join('、') || '—' }} · {{ run.config_snapshot?.auto_advance_to_review ? '自动推进到待人工审核' : '只创建草稿' }}</p>
          <ul v-if="run.campaign_ids.length"><li v-for="id in run.campaign_ids" :key="id"><router-link :to="`/campaigns/${id}`">活动 {{ id }}</router-link> · 活动当前状态 {{ campaignStatus[id] ? campaignStatusLabel(campaignStatus[id]) : '待刷新' }}</li></ul>
          <p v-else class="hint">尚未创建活动。</p>
          <p class="hint">这里显示计划执行状态；活动审核状态以活动详情为准。</p>
        </section>
      </div>
      <section v-if="run.status === 'awaiting_selection'" class="panel action-panel">
        <h2>人工选择来源与自家商品</h2>
        <p class="hint">来源条目仅作为创作参考；商品事实来自所选自家商品。可继续选择不同配对；上方已创建的活动可独立处理。</p>
        <el-form label-position="top"><el-form-item label="来源条目"><el-select v-model="sourceItemId" filterable placeholder="选择这一轮实际采集的条目"><el-option v-for="item in run.source_items" :key="item.id" :value="item.id" :label="`${item.source_rank ?? '—'} · ${item.title || `条目 ${item.id}`}`" /></el-select></el-form-item>
          <el-form-item label="自家商品"><el-select v-model="productId" filterable placeholder="选择计划范围内的自家商品"><el-option v-for="item in run.product_candidates" :key="item.id" :value="item.id" :label="item.name" /></el-select></el-form-item></el-form>
        <el-button type="primary" :loading="acting" :disabled="!sourceItemId || !productId" @click="resolve">确认关联并继续</el-button>
      </section>
      <section v-if="run.status === 'missed'" class="panel action-panel"><h2>补跑错过的窗口</h2><p>记录了 {{ run.missed_count || 1 }} 个错过的调度窗口，本次可明确补跑 {{ formatTime(run.scheduled_for) }} 这一轮；系统不会自动补跑。</p><p v-if="!plan" class="hint">当前计划暂不可读，请刷新后再补跑。</p><el-button type="primary" :loading="acting" :disabled="!plan" @click="backfill">补跑这一轮</el-button></section>
      <section v-if="canResume" class="panel action-panel"><h2>继续这一轮</h2><p>先处理上方阻塞原因，再继续已有执行记录；不会另建一轮。</p><p v-if="run.status === 'needs_info'" class="hint">请先到 <router-link to="/products">商品库补充真实主图或事实</router-link>，再返回继续。</p><p v-if="run.status === 'source_empty'" class="hint">本轮实际没有取得来源条目。请核查来源配置与采集结果后再继续；不会创建空活动。</p><p v-if="run.status === 'interrupted'" class="warn">调度中断时，外部采集结果可能仍在途。请先人工核查对应采集批次，再明确继续，避免盲目重提。</p><p v-if="run.status === 'paused' && !plan?.enabled" class="hint">计划仍已暂停。请先到 <router-link :to="{ path: '/operation-plans', query: { plan_id: String(run.plan_id) } }">计划页启用</router-link>。</p><el-button type="primary" :loading="acting" :disabled="run.status === 'paused' && !plan?.enabled" @click="resume">处理后继续</el-button></section>
      <section v-if="run.status === 'failed'" class="panel action-panel"><h2>人工核查</h2><p>这轮失败，需先核查具体错误与外部结果；这里不提供盲目重试。</p></section>
    </template>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import { campaignApi } from '../campaignApi'
import { operationPlanApi, type OperationPlan, type PlanRun } from '../operationPlanApi'

const route = useRoute()
const run = ref<PlanRun | null>(null)
const plan = ref<OperationPlan | null>(null)
const campaignStatus = ref<Record<number, string>>({})
const error = ref('')
const loading = ref(false)
const acting = ref(false)
const sourceItemId = ref<number | undefined>()
const productId = ref<number | undefined>()
let loadSerial = 0
const canResume = computed(() => !!run.value && ['pending_connection', 'source_empty', 'interrupted', 'needs_info', 'budget_blocked', 'paused'].includes(run.value.status))
function statusLabel(value: string) { return ({ collecting: '采集中', awaiting_selection: '待人工选择', pending_connection: '待连接', source_empty: '来源无结果', interrupted: '执行中断', needs_info: '待补资料', budget_blocked: '预算触顶', paused: '已暂停', created: '已创建活动', missed: '错过窗口', failed: '异常' } as Record<string, string>)[value] || value }
function campaignStatusLabel(value: string) { return ({ draft: '草稿', generating: '生成中', needs_review: '待审核', approved: '已审核', failed: '失败' } as Record<string, string>)[value] || value }
function platformLabel(value: string) { return value === 'douyin' ? '抖音' : value === 'xiaohongshu' ? '小红书' : value }
function runSourceName(record: PlanRun) {
  const sourceId = record.config_snapshot?.source_config_id
  return sourceId == null ? '本轮不使用外部来源' : record.source_provider || `来源配置 ${sourceId}（当前不可用）`
}
function formatTime(value: string) { return new Date(value).toLocaleString() }
async function load() {
  const serial = ++loadSerial
  loading.value = true
  run.value = null
  plan.value = null
  campaignStatus.value = {}
  sourceItemId.value = undefined
  productId.value = undefined
  const id = Number(route.params.id)
  if (!Number.isInteger(id) || id < 1) { error.value = '执行编号无效'; loading.value = false; return }
  error.value = ''
  try {
    const record = await operationPlanApi.run(id)
    const [planResult, campaignResult] = await Promise.allSettled([operationPlanApi.list(), campaignApi.list()])
    if (serial !== loadSerial) return
    run.value = record
    if (planResult.status === 'fulfilled') plan.value = planResult.value.items.find((item) => item.id === record.plan_id) || null
    if (campaignResult.status === 'fulfilled') campaignStatus.value = Object.fromEntries(campaignResult.value.items.map((item) => [item.id, item.status]))
  } catch (err) { if (serial === loadSerial) error.value = err instanceof Error ? err.message : '执行记录读取失败' }
  finally { if (serial === loadSerial) loading.value = false }
}
async function resolve() {
  if (!run.value || !sourceItemId.value || !productId.value || acting.value) return
  acting.value = true
  try { await operationPlanApi.resolve(run.value.id, run.value.version, sourceItemId.value, productId.value); await load(); ElMessage.success('关联已确认，这轮继续执行') }
  catch (err) { ElMessage.error(err instanceof Error ? err.message : '关联失败'); await load() }
  finally { acting.value = false }
}
async function resume() {
  if (!run.value || acting.value) return
  const record = run.value
  if (record.status === 'interrupted') {
    try {
      await ElMessageBox.confirm('请先核查外部采集结果是否仍在途。确认已核查，继续这一轮？', '执行中断需人工核查', { confirmButtonText: '已核查，继续', cancelButtonText: '取消', type: 'warning' })
    } catch { return }
    if (run.value?.id !== record.id || run.value.version !== record.version) return
  }
  acting.value = true
  try { await operationPlanApi.resume(record.id, record.version); await load(); ElMessage.success('已继续这一轮') }
  catch (err) { ElMessage.error(err instanceof Error ? err.message : '暂不能继续'); await load() }
  finally { acting.value = false }
}
async function backfill() {
  if (!run.value || !plan.value || acting.value) return
  acting.value = true
  try { await operationPlanApi.backfill(plan.value.id, plan.value.version, run.value.scheduled_for); await load(); ElMessage.success('已明确补跑这一窗口') }
  catch (err) { ElMessage.error(err instanceof Error ? err.message : '补跑失败'); await load() }
  finally { acting.value = false }
}
onMounted(load)
watch(() => route.params.id, load)
</script>

<style scoped>
.panel { padding: 26px; margin-bottom: 18px; }
.run-head { border: 0; box-shadow: none; background: transparent; padding: 8px 0 20px; }
.run-head, .head-actions { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.run-head h1, .panel h2 { margin: 0 0 8px; }
.run-head h1 { font-size: clamp(32px, 4vw, 48px); font-weight: 500; letter-spacing: -.04em; }
.panel h2 { font-size: 23px; letter-spacing: -.035em; }
.run-head p, .hint { color: var(--muted); line-height: 1.6; }
.head-actions { flex-wrap: wrap; }
.head-actions a, .panel a { color: var(--accent); text-decoration: underline; text-decoration-color: #9ca3af; text-underline-offset: 3px; }
.warn, .error { color: var(--danger); }
.run-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.run-grid .panel { min-width: 0; }
.action-panel :deep(.el-select) { width: min(100%, 520px); }
@media (max-width: 800px) {
  .panel { padding: 16px; }
  .run-grid { grid-template-columns: 1fr; }
  .run-head { align-items: flex-start; flex-direction: column; }
}
</style>
