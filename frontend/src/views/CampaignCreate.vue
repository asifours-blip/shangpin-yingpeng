<template>
  <AppLayout wide>
    <section class="panel create-head"><h1>创建活动</h1><p>先确认自家商品事实，再决定生成方向。来源内容只供参考。</p></section>
    <ol class="flow"><li v-for="(name, index) in steps" :key="name" :class="{ active: index === step }">{{ index + 1 }}. {{ name }}</li></ol>
    <section v-if="error" class="panel error-box">{{ error }}</section>
    <section v-if="step === 0" class="panel create-panel">
      <h2>选择来源参考（可跳过）</h2><p class="hint">来源没有连接时，直接用自家商品继续。可选多个参考，本轮只取排序最高的一条作为创作参考；采集内容不会写入商品事实。</p>
      <el-select v-model="runId" clearable filterable placeholder="选择已有采集批次" @change="loadItems"><el-option v-for="run in runs" :key="run.id" :value="run.id" :label="`批次 ${run.id} · ${run.provider} · 实际 ${run.actual_count} 条 · ${run.sort_metric}`" /></el-select>
      <p v-if="runs.length === 0" class="hint">没有可用采集批次。<router-link to="/trends">查看趋势采集</router-link></p>
      <p v-if="currentRun" class="hint">来源 {{ currentRun.provider }} · {{ currentRun.started_at }} · 排序依据 {{ currentRun.sort_metric }} · 实际 {{ sourceItems.length }} 条</p>
      <div v-if="sourceItems.length" class="source-items"><label v-for="item in sourceItems" :key="item.id"><input v-model="sourceIds" type="checkbox" :value="item.id" /> {{ item.source_rank ?? '—' }} · {{ item.title || item.text_excerpt || `条目 ${item.id}` }}</label></div>
      <p v-if="runId && !sourceItems.length" class="hint">这一批没有可用条目，可继续使用自家商品。</p>
    </section>
    <section v-else-if="step === 1" class="panel create-panel"><h2>选择自家商品</h2>
      <p class="hint">只展示已录入商品。没有商品时先去商品库录入，随后返回这里。</p>
      <el-radio-group v-model="productId" class="product-pick"><el-radio v-for="product in activeProducts" :key="product.id" :value="product.id"><span>{{ product.name }} · {{ product.sku }}</span><span v-if="!product.primary_asset_id" class="warn"> · 缺商品主图</span></el-radio></el-radio-group>
      <p v-if="!activeProducts.length"><router-link to="/products">去商品库录入商品 →</router-link></p>
    </section>
    <section v-else-if="step === 2" class="panel create-panel"><h2>确认事实版本</h2>
      <template v-if="chosenProduct"><p>{{ chosenProduct.name }} · 版本 v{{ chosenProduct.latest_fact?.version ?? '—' }}</p>
        <p v-if="chosenProduct.primary_asset_id"><img class="product-image" :src="`/api/assets/${chosenProduct.primary_asset_id}/file`" alt="自家商品主图" /></p>
        <p v-else class="warn">缺少商品主图，媒体生成可能被阻塞。<router-link to="/products">去商品库补充</router-link></p>
        <dl class="facts"><template v-for="[key, value] in factRows" :key="key"><dt>{{ factLabel(key) }}</dt><dd>{{ factValue(key, value) }}</dd></template></dl>
        <p v-if="!confirmedFacts.length" class="warn">没有已确认的商品属性。请核对商品资料，未确认属性不会当成卖点。</p>
        <p class="hint">待确认、不宣称的属性保持原状态；来源标题不等于自家商品事实。</p>
        <el-checkbox v-model="factsAccepted">我已核对以上事实版本与缺项</el-checkbox>
      </template>
    </section>
    <section v-else-if="step === 3" class="panel create-panel"><h2>目标平台</h2><el-checkbox-group v-model="platforms"><el-checkbox value="douyin">抖音 · 视频、封面、文案</el-checkbox><el-checkbox value="xiaohongshu">小红书 · 封面、有序内容卡、文案</el-checkbox></el-checkbox-group></section>
    <section v-else-if="step === 4" class="panel create-panel"><h2>生成内容和预算</h2><p class="hint">产物按目标平台的现有配方生成。预算由现有执行机制控制。</p>
      <el-form label-position="top"><el-form-item label="创作方向（可选；不是商品事实）"><el-input v-model="requirements" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="例如：偏向通勤场景；商品属性仍以已确认事实为准" /></el-form-item><el-form-item label="本轮模型调用次数上限"><el-input-number v-model="budget" :min="0" :max="100" /></el-form-item></el-form>
    </section>
    <section v-else class="panel create-panel"><h2>确认并启动</h2><p>商品：{{ chosenProduct?.name }} · 事实版本 v{{ chosenProduct?.latest_fact?.version }}</p><p>参考来源：选中 {{ sourceIds.length }} 条，本轮最多使用排序最高的一条</p><p>目标平台：{{ platforms.map(platformLabel).join('、') }}</p><p>生成内容：{{ platforms.map((value) => value === 'douyin' ? '视频、封面、文案' : '封面、内容卡、文案').join('；') }}</p><p>模型调用次数上限：{{ budget }}</p><p>创作方向：{{ requirements || '未指定' }}</p><p class="hint">确认后创建活动并启动。生成结果还需人工审核。</p></section>
    <div class="flow-actions"><el-button v-if="step > 0" :disabled="saving" @click="step--">上一步</el-button><el-button v-if="step < steps.length - 1" type="primary" @click="next">下一步</el-button><el-button v-else type="primary" :loading="saving" @click="createAndStart">创建并启动</el-button></div>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import { productApi, type OwnedProduct } from '../productApi'
import { sourceApi, type CollectionRunSummary, type SourceItem } from '../sourceApi'
import { campaignApi } from '../campaignApi'
import { factLabel, factValue } from '../productFacts'

const router = useRouter(); const route = useRoute()
const steps = ['来源参考', '自家商品', '事实版本', '目标平台', '内容与预算', '确认启动']
const step = ref(0); const runs = ref<CollectionRunSummary[]>([]); const sourceItems = ref<SourceItem[]>([])
const runId = ref<number | undefined>(); const sourceIds = ref<number[]>([])
const products = ref<OwnedProduct[]>([]); const productId = ref<number | undefined>()
const factsAccepted = ref(false); const platforms = ref<string[]>(['douyin', 'xiaohongshu'])
const requirements = ref(''); const budget = ref(12); const saving = ref(false); const error = ref('')
const currentRun = computed(() => runs.value.find((item) => item.id === runId.value))
const activeProducts = computed(() => products.value.filter((item) => item.active))
const chosenProduct = computed(() => products.value.find((item) => item.id === productId.value))
watch(productId, () => { factsAccepted.value = false })
const confirmedFacts = computed(() => Object.entries(chosenProduct.value?.latest_fact?.facts || {}).filter(([, value]) => !['needs_confirmation', 'unknown', 'not_claimed', ''].includes(String(value)) && value != null))
const factRows = computed(() => Object.entries(chosenProduct.value?.latest_fact?.facts || {}))
function platformLabel(value: string) { return value === 'douyin' ? '抖音' : '小红书' }
async function loadItems() { sourceIds.value = []; sourceItems.value = []; if (!runId.value) return; try { sourceItems.value = (await sourceApi.getItems(runId.value)).items } catch (err) { error.value = err instanceof Error ? err.message : '来源读取失败' } }
function next() {
  error.value = ''
  if (step.value === 1 && !chosenProduct.value) error.value = '先选一个自家商品'
  if (step.value === 2 && !factsAccepted.value) error.value = '请先核对事实版本'
  if (step.value === 3 && !platforms.value.length) error.value = '至少选择一个平台'
  if (!error.value) step.value++
}
async function createAndStart() {
  if (saving.value || !chosenProduct.value?.latest_fact) return
  saving.value = true; error.value = ''
  let campaignId: number | undefined
  try {
    const latestProducts = (await productApi.list()).items
    const latestProduct = latestProducts.find((item) => item.id === chosenProduct.value?.id)
    if (!latestProduct?.latest_fact || latestProduct.latest_fact.id !== chosenProduct.value.latest_fact.id) {
      products.value = latestProducts
      factsAccepted.value = false
      step.value = 2
      throw new Error('商品事实版本已变化，请重新核对后再启动')
    }
    const created = await campaignApi.create({ product_id: chosenProduct.value.id, fact_version_id: chosenProduct.value.latest_fact.id, source_item_ids: sourceIds.value, target_platforms: platforms.value, generation_budget: budget.value, generation_requirements: requirements.value.trim() })
    campaignId = created.id
    const key = crypto.randomUUID()
    sessionStorage.setItem(`campaign-start-${campaignId}`, key)
    await campaignApi.start(campaignId, key)
    sessionStorage.removeItem(`campaign-start-${campaignId}`)
    await router.push(`/campaigns/${campaignId}`)
  } catch (err) {
    error.value = `${err instanceof Error ? err.message : '创建失败'}${campaignId ? `。活动 ${campaignId} 已创建，可在详情继续启动。` : ''}`
    if (campaignId) await router.push(`/campaigns/${campaignId}`)
  } finally { saving.value = false }
}
onMounted(async () => {
  try { products.value = (await productApi.list()).items }
  catch (err) { error.value = err instanceof Error ? err.message : '商品读取失败' }
  const requestedProduct = Number(route.query.product_id)
  if (requestedProduct && products.value.some((item) => item.id === requestedProduct && item.active)) productId.value = requestedProduct
  try {
    runs.value = (await sourceApi.listRuns()).items
    const requested = Number(route.query.run_id)
    if (requested && runs.value.some((item) => item.id === requested)) { runId.value = requested; await loadItems() }
  } catch { runs.value = [] }
})
</script>

<style scoped>
.panel { padding: 28px; }
.create-head, .create-panel { margin-bottom: 18px; }
.create-head { border: 0; box-shadow: none; background: transparent; padding: 10px 0 12px; }
.create-head h1, .create-panel h2 { margin: 0 0 8px; letter-spacing: -.04em; }
.create-head h1 { font-size: clamp(32px, 4vw, 48px); font-weight: 500; }
.create-panel h2 { font-size: 23px; }
.create-head p, .hint { color: var(--muted); line-height: 1.6; }
.flow {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 0;
  margin: 0 0 16px;
  list-style: none;
}
.flow li {
  background: var(--glass-strong);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 8px 12px;
  color: var(--muted);
}
.flow li.active { color: #fff; background: var(--accent); border-color: var(--accent); box-shadow: none; }
.create-panel { min-height: 300px; }
.create-panel :deep(.el-select) { width: min(100%, 520px); }
.source-items { display: grid; gap: 8px; max-height: 280px; overflow: auto; margin-top: 16px; padding: 14px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface-2); }
.source-items label { display: block; }
.product-pick { display: grid; gap: 10px; }
.facts { display: grid; grid-template-columns: minmax(110px, 180px) 1fr; gap: 8px; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; }
.product-image { max-width: 180px; max-height: 180px; object-fit: contain; border-radius: var(--radius); }
.warn, .error-box { color: var(--danger); }
.flow-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 24px; }
@media (max-width: 600px) {
  .panel { padding: 16px; }
  .flow li { font-size: 12px; padding: 6px 8px; }
  .create-panel { min-height: 240px; }
}
</style>
