<template>
  <AppLayout wide>
    <section class="panel page-head">
      <div>
        <h1>运营计划</h1>
        <p>到点创建一轮活动；自动推进最多停在待人工审核。计划不会自动批准或发布。</p>
      </div>
      <div class="head-actions"><router-link to="/operations">返回运营总览</router-link><el-button type="primary" @click="newPlan">新建计划</el-button></div>
    </section>

    <section v-if="error" class="panel error" role="alert">{{ error }} <el-button size="small" @click="load">重试</el-button></section>
    <section class="panel plans-panel">
      <h2>计划列表</h2>
      <div v-if="loading" class="state-block">正在读取计划…</div>
      <div v-else-if="!plans.length" class="state-block"><p>还没有运营计划。</p><el-button type="primary" @click="newPlan">新建计划</el-button></div>
      <p v-else class="scroll-hint">左右滑动表格可查看操作 →</p>
      <div v-if="!loading && plans.length" class="table-wrap" tabindex="0"><table class="data-table"><thead><tr><th>计划</th><th>执行时间</th><th>来源与商品</th><th>目标与上限</th><th>下一窗口</th><th>操作</th></tr></thead><tbody>
        <tr v-for="plan in plans" :key="plan.id">
          <td><strong>计划 {{ plan.id }}</strong><br />{{ plan.enabled ? '已启用' : '已暂停' }}</td>
          <td>{{ plan.timezone }} · {{ plan.local_time }}</td>
          <td>{{ sourceName(plan.source_config_id) }}<br />{{ plan.product_ids.map(productName).join('、') || '未选商品' }}</td>
          <td>{{ plan.target_platforms.map(platformLabel).join('、') }}<br />每日最多 {{ plan.daily_campaign_limit }} 个活动 · 每日 {{ plan.daily_budget_limit }} 次模型调用</td>
          <td>{{ plan.next_due_at ? formatTime(plan.next_due_at) : '暂无' }}</td>
          <td class="row-actions"><el-button text @click="editPlan(plan)">编辑</el-button><el-button text :loading="changingId === plan.id" @click="toggle(plan)">{{ plan.enabled ? '暂停' : '启用' }}</el-button><router-link :to="{ path: '/operation-plans', query: { plan_id: String(plan.id) } }">执行记录</router-link></td>
        </tr>
      </tbody></table></div>
    </section>

    <section v-if="selectedPlan" class="panel runs-panel">
      <div class="section-head"><h2>计划 {{ selectedPlan.id }} 的执行记录</h2><el-button @click="loadRuns(selectedPlan.id)">刷新记录</el-button></div>
      <p class="hint">错过的窗口只记录，不自动补跑；进入具体记录可明确补跑。来源待连接时不会创建空活动。</p>
      <div v-if="runsLoading" class="state-block">正在读取执行记录…</div>
      <div v-else-if="!runs.length" class="state-block">暂无执行记录。</div>
      <p v-else class="scroll-hint">左右滑动表格可打开执行记录 →</p>
      <div v-if="!runsLoading && runs.length" class="table-wrap" tabindex="0"><table class="data-table"><thead><tr><th>调度窗口</th><th>状态</th><th>实际来源</th><th>阻塞与下一步</th><th></th></tr></thead><tbody>
        <tr v-for="run in runs" :key="run.id">
          <td>{{ formatTime(run.scheduled_for) }}</td><td>{{ runStatus(run.status) }}</td>
          <td>{{ runSourceName(run) }} · {{ run.actual_count }} 条</td>
          <td>{{ run.blocker_message || '无' }}<br /><span class="hint">{{ run.next_action || '查看详情' }}</span></td>
          <td><router-link :to="`/operation-runs/${run.id}`">打开并处理 →</router-link></td>
        </tr>
      </tbody></table></div>
    </section>

    <el-dialog v-model="formOpen" :title="editingId ? `编辑计划 ${editingId}` : '新建运营计划'" width="min(700px, 96vw)">
      <el-form label-position="top" class="plan-form">
        <el-form-item label="启用状态"><el-switch v-model="draft.enabled" active-text="启用" inactive-text="暂停" /></el-form-item>
        <el-form-item label="时区"><el-input v-model="draft.timezone" placeholder="Asia/Shanghai" /></el-form-item>
        <el-form-item label="每日执行时间（计划时区）"><input v-model="draft.local_time" type="time" class="time-input" /></el-form-item>
        <el-form-item label="来源参考（可空）"><el-select v-model="draft.source_config_id" clearable placeholder="不使用外部来源" @clear="draft.source_config_id = null"><el-option v-for="config in availableSources" :key="config.id" :value="config.id" :label="`${config.provider} · ${config.category} · ${config.sort_metric}`" /></el-select></el-form-item>
        <el-form-item label="自家商品范围"><el-select v-model="draft.product_ids" multiple filterable placeholder="选择已录入商品"><el-option v-for="product in activeProducts" :key="product.id" :value="product.id" :label="`${product.name} · ${product.sku}`" /></el-select></el-form-item>
        <el-form-item label="目标平台"><el-checkbox-group v-model="draft.target_platforms"><el-checkbox value="douyin">抖音</el-checkbox><el-checkbox value="xiaohongshu">小红书</el-checkbox></el-checkbox-group></el-form-item>
        <el-form-item label="每日活动上限"><el-input-number v-model="draft.daily_campaign_limit" :min="1" :max="100" /></el-form-item>
        <el-form-item label="每日模型调用次数上限"><el-input-number v-model="draft.daily_budget_limit" :min="0" :max="10000" /></el-form-item>
        <el-form-item label="每个活动的模型调用次数上限"><el-input-number v-model="draft.generation_budget" :min="0" :max="100" /></el-form-item>
        <el-form-item label="自动推进"><el-switch v-model="draft.auto_advance_to_review" active-text="生成后停在待人工审核" inactive-text="只创建活动，人工启动" /></el-form-item>
      </el-form>
      <p class="hint">有来源但关联规则不明确时，执行记录会等待你选择来源条目与自家商品；无来源时按所选商品范围创建。未启用自动推进只创建活动草稿，之后手动启动仍需要模型连接。</p>
      <template #footer><el-button @click="formOpen = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存计划</el-button></template>
    </el-dialog>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import { operationPlanApi, type OperationPlan, type PlanDraft, type PlanRun } from '../operationPlanApi'
import { productApi, type OwnedProduct } from '../productApi'
import { sourceApi, type CollectionConfig } from '../sourceApi'

const route = useRoute()
const plans = ref<OperationPlan[]>([])
const runs = ref<PlanRun[]>([])
const products = ref<OwnedProduct[]>([])
const sources = ref<CollectionConfig[]>([])
const loading = ref(false)
const runsLoading = ref(false)
const saving = ref(false)
const changingId = ref<number | null>(null)
const error = ref('')
const formOpen = ref(false)
let runsSerial = 0
let plansSerial = 0
const editingId = ref<number | null>(null)
const editingVersion = ref(0)
const draft = reactive<PlanDraft>({
  enabled: false, timezone: 'Asia/Shanghai', local_time: '09:00', source_config_id: null,
  product_ids: [], target_platforms: ['douyin', 'xiaohongshu'], daily_campaign_limit: 1,
  daily_budget_limit: 12, generation_budget: 12, auto_advance_to_review: true,
})
const selectedPlan = computed(() => plans.value.find((plan) => plan.id === Number(route.query.plan_id)))
const activeProducts = computed(() => products.value.filter((product) => product.active))
const availableSources = computed(() => sources.value.filter((source) => source.provider !== 'fixture' || import.meta.env.VITE_ENABLE_FIXTURE_SOURCE === '1'))
function sourceName(id: number | null) { return id == null ? '无外部来源' : sources.value.find((row) => row.id === id)?.provider || `来源配置 ${id}` }
function runSourceName(run: PlanRun) {
  const sourceId = run.config_snapshot?.source_config_id
  return sourceId == null ? '不使用外部来源' : run.source_provider || `来源配置 ${sourceId}（当前不可用）`
}
function productName(id: number) { return products.value.find((row) => row.id === id)?.name || `商品 ${id}` }
function platformLabel(value: string) { return value === 'douyin' ? '抖音' : value === 'xiaohongshu' ? '小红书' : value }
function formatTime(value: string) { return new Date(value).toLocaleString() }
function runStatus(value: string) { return ({ collecting: '采集中', awaiting_selection: '待人工选择', pending_connection: '待连接', source_empty: '来源无结果', interrupted: '执行中断', needs_info: '待补资料', budget_blocked: '预算触顶', paused: '已暂停', created: '已创建活动', missed: '错过窗口', failed: '异常' } as Record<string, string>)[value] || value }
function setDraft(plan?: OperationPlan) {
  Object.assign(draft, plan ? {
    enabled: plan.enabled, timezone: plan.timezone, local_time: plan.local_time,
    source_config_id: plan.source_config_id, product_ids: [...plan.product_ids],
    target_platforms: [...plan.target_platforms], daily_campaign_limit: plan.daily_campaign_limit,
    daily_budget_limit: plan.daily_budget_limit, generation_budget: plan.generation_budget,
    auto_advance_to_review: plan.auto_advance_to_review,
  } : {
    enabled: false, timezone: 'Asia/Shanghai', local_time: '09:00', source_config_id: null,
    product_ids: [], target_platforms: ['douyin', 'xiaohongshu'], daily_campaign_limit: 1,
    daily_budget_limit: 12, generation_budget: 12, auto_advance_to_review: true,
  })
}
function newPlan() { editingId.value = null; editingVersion.value = 0; setDraft(); formOpen.value = true }
function editPlan(plan: OperationPlan) { editingId.value = plan.id; editingVersion.value = plan.version; setDraft(plan); formOpen.value = true }
async function loadRuns(id: number) {
  const serial = ++runsSerial
  runsLoading.value = true
  runs.value = []
  try { const page = await operationPlanApi.runs(id); if (serial === runsSerial) runs.value = page.items }
  catch (err) { if (serial === runsSerial) ElMessage.error(err instanceof Error ? err.message : '执行记录读取失败') }
  finally { if (serial === runsSerial) runsLoading.value = false }
}
async function load() {
  const serial = ++plansSerial
  loading.value = true; error.value = ''
  plans.value = []
  products.value = []
  sources.value = []
  runsSerial++
  runs.value = []
  runsLoading.value = false
  try {
    const [planPage, productPage, sourcePage] = await Promise.all([
      operationPlanApi.list(), productApi.list(), sourceApi.listConfigs().catch(() => ({ items: [] })),
    ])
    if (serial !== plansSerial) return
    plans.value = planPage.items
    products.value = productPage.items
    sources.value = sourcePage.items
    if (selectedPlan.value) await loadRuns(selectedPlan.value.id)
  } catch (err) { if (serial === plansSerial) error.value = err instanceof Error ? err.message : '计划读取失败' }
  finally { if (serial === plansSerial) loading.value = false }
}
async function save() {
  if (!draft.product_ids.length || !draft.target_platforms.length) { ElMessage.warning('请选择自家商品范围和至少一个目标平台'); return }
  if (!draft.timezone.trim() || !draft.local_time) { ElMessage.warning('请填写时区与执行时间'); return }
  saving.value = true
  try {
    if (editingId.value) await operationPlanApi.update(editingId.value, editingVersion.value, { ...draft })
    else await operationPlanApi.create({ ...draft })
    formOpen.value = false
    await load()
    ElMessage.success('运营计划已保存')
  } catch (err) { ElMessage.error(err instanceof Error ? err.message : '计划保存失败') }
  finally { saving.value = false }
}
async function toggle(plan: OperationPlan) {
  changingId.value = plan.id
  try {
    await operationPlanApi.update(plan.id, plan.version, { enabled: !plan.enabled })
    await load()
    ElMessage.success(plan.enabled ? '计划已暂停；尚未创建的活动不会继续产生' : '计划已启用')
  } catch (err) { ElMessage.error(err instanceof Error ? err.message : '状态更新失败') }
  finally { changingId.value = null }
}
onMounted(load)
watch(() => route.query.plan_id, (id) => { if (id) void loadRuns(Number(id)); else { runsSerial++; runs.value = []; runsLoading.value = false } })
</script>

<style scoped>
.panel { padding: 26px; margin-bottom: 18px; }
.page-head { border: 0; box-shadow: none; background: transparent; padding: 8px 0 20px; }
.page-head, .section-head, .head-actions { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.page-head h1, .plans-panel h2, .runs-panel h2 { margin: 0; }
.page-head h1 { font-size: clamp(32px, 4vw, 48px); font-weight: 500; letter-spacing: -.04em; }
.plans-panel h2, .runs-panel h2 { font-size: 23px; letter-spacing: -.035em; }
.page-head p, .hint { color: var(--muted); line-height: 1.6; }
.head-actions, .row-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.head-actions a, .row-actions a, .runs-panel a { color: var(--accent); text-decoration: underline; text-decoration-color: #9ca3af; text-underline-offset: 3px; }
.plans-panel, .runs-panel { overflow: hidden; }
.plans-panel .data-table { min-width: 900px; }
.runs-panel .data-table { min-width: 760px; }
.scroll-hint { display: none; }
.row-actions { min-width: 180px; }
.error { color: var(--danger); }
.plan-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.plan-form :deep(.el-select) { width: 100%; }
.time-input { min-height: 36px; border: 1px solid #d1d5db; border-radius: var(--radius); color: var(--text); background: #fff; padding: 4px 10px; }
@media (max-width: 800px) {
  .panel { padding: 16px; }
  .page-head, .section-head { align-items: flex-start; flex-direction: column; }
  .plan-form { grid-template-columns: 1fr; }
  .scroll-hint { display: block; margin: 8px 0; color: var(--muted); font-size: 13px; }
}
</style>
