<template>
  <AppLayout>
    <section class="panel">
      <h1>自家商品</h1>
      <p class="lead">只保存你确认过的规格。没写的卖点保持空白，后面的文案不能自己补。</p>
      <el-form class="product-form" label-position="top" @submit.prevent="onCreate">
        <el-form-item label="SKU">
          <el-input v-model="draft.sku" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="draft.name" />
        </el-form-item>
        <el-form-item label="材质（可空）">
          <el-input v-model="draft.material" placeholder="未确认就留空" />
        </el-form-item>
        <el-form-item label="防水">
          <el-select v-model="draft.waterproof">
            <el-option label="待确认" value="needs_confirmation" />
            <el-option label="不宣称" value="not_claimed" />
            <el-option label="已确认防水" value="confirmed" />
          </el-select>
        </el-form-item>
        <el-form-item label="商品主图"><input type="file" accept="image/png,image/jpeg,image/webp,image/bmp,image/gif" :disabled="uploading" @change="onNewImage" /><span v-if="uploading">上传中，请稍候</span><span v-else-if="draft.assetId">已上传资产 {{ draft.assetId }}</span></el-form-item>
        <el-button type="primary" native-type="submit" :loading="saving" :disabled="uploading">保存商品</el-button>
      </el-form>
    </section>
    <section class="panel">
      <h2>已录入</h2>
      <el-table :data="products" size="small">
        <el-table-column prop="sku" label="SKU" width="140" />
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column label="事实版本" width="100">
          <template #default="{ row }">v{{ row.latest_fact?.version || 0 }}</template>
        </el-table-column>
        <el-table-column label="已确认事实" min-width="220">
          <template #default="{ row }">{{ factText(row) }}</template>
        </el-table-column>
        <el-table-column label="商品主图" width="130"><template #default="{ row }"><img v-if="row.primary_asset_id" class="thumb" :src="`/api/assets/${row.primary_asset_id}/file`" alt="商品主图" /><span v-else>缺主图</span></template></el-table-column>
        <el-table-column label="操作" width="210"><template #default="{ row }"><label class="upload-label">{{ row.primary_asset_id ? '更换主图' : '补充主图' }}<input type="file" accept="image/png,image/jpeg,image/webp,image/bmp,image/gif" hidden @change="onExistingImage($event, row)" /></label><el-button text size="small" @click="beginEdit(row)">编辑事实</el-button><router-link :to="{ path: '/campaigns/new', query: { product_id: String(row.id) } }">创建活动</router-link></template></el-table-column>
      </el-table>
    </section>
    <el-dialog v-model="editing" title="编辑已确认事实" width="min(560px, 92vw)">
      <p>商品 {{ editingProduct?.name }} · 当前 v{{ editingProduct?.latest_fact?.version }}。保存后新增事实版本，已创建活动仍引用旧版本。</p>
      <p class="lead">只修改当前核实的属性；其他已存事实保持原样。</p>
      <el-form label-position="top"><el-form-item label="材质（可空）"><el-input v-model="editMaterial" placeholder="未确认就留空" /></el-form-item><el-form-item label="防水"><el-select v-model="editWaterproof"><el-option label="待确认" value="needs_confirmation" /><el-option label="不宣称" value="not_claimed" /><el-option label="已确认防水" value="confirmed" /></el-select></el-form-item></el-form>
      <template #footer><el-button @click="editing = false">取消</el-button><el-button type="primary" :loading="savingFacts" @click="saveFacts">保存新版本</el-button></template>
    </el-dialog>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import { productApi, type OwnedProduct } from '../productApi'
import { api } from '../api'

const products = ref<OwnedProduct[]>([])
const saving = ref(false)
const uploading = ref(false)
const editing = ref(false)
const editingProduct = ref<OwnedProduct | null>(null)
const editMaterial = ref('')
const editWaterproof = ref('needs_confirmation')
const savingFacts = ref(false)
const draft = reactive({
  sku: '',
  name: '',
  material: '',
  waterproof: 'needs_confirmation',
  assetId: null as number | null,
})

function factsFromDraft() {
  const facts: Record<string, unknown> = {
    waterproof: draft.waterproof,
  }
  if (draft.material.trim()) facts.material = draft.material.trim()
  return facts
}

function factText(row: OwnedProduct) {
  const facts = row.latest_fact?.facts || {}
  const parts = Object.entries(facts).map(([key, value]) => `${key}: ${String(value)}`)
  return parts.length ? parts.join('；') : '没有已确认事实'
}

async function load() {
  const res = await productApi.list()
  products.value = res.items
}

async function onCreate() {
  if (uploading.value) { ElMessage.warning('商品主图仍在上传，请稍候'); return }
  if (!draft.sku.trim() || !draft.name.trim()) {
    ElMessage.warning('先填写 SKU 和名称')
    return
  }
  saving.value = true
  try {
    await productApi.create({
      sku: draft.sku.trim(),
      name: draft.name.trim(),
      facts: factsFromDraft(),
      primary_asset_id: draft.assetId,
    })
    draft.sku = ''
    draft.name = ''
    draft.material = ''
    draft.waterproof = 'needs_confirmation'
    draft.assetId = null
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function onNewImage(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true
  try { draft.assetId = (await api.uploadAsset(file)).id }
  catch (err) { ElMessage.error(err instanceof Error ? err.message : '上传失败') }
  finally { input.value = ''; uploading.value = false }
}

async function onExistingImage(event: Event, row: OwnedProduct) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const asset = await api.uploadAsset(file)
    await productApi.patchFacts(row.id, { primary_asset_id: asset.id, expected_version: row.latest_fact?.version || 0 })
    await load()
    ElMessage.success('商品主图已更新')
  } catch (err) { ElMessage.error(err instanceof Error ? err.message : '主图保存失败') }
  finally { input.value = '' }
}

function beginEdit(row: OwnedProduct) {
  editingProduct.value = row
  editMaterial.value = String(row.latest_fact?.facts?.material || '')
  editWaterproof.value = String(row.latest_fact?.facts?.waterproof || 'needs_confirmation')
  editing.value = true
}

async function saveFacts() {
  if (!editingProduct.value?.latest_fact) return
  const facts: Record<string, unknown> = { ...editingProduct.value.latest_fact.facts, waterproof: editWaterproof.value }
  if (editMaterial.value.trim()) facts.material = editMaterial.value.trim()
  else delete facts.material
  savingFacts.value = true
  try {
    await productApi.patchFacts(editingProduct.value.id, { facts, expected_version: editingProduct.value.latest_fact.version })
    editing.value = false
    await load()
    ElMessage.success('已保存新事实版本')
  } catch (err) { ElMessage.error(err instanceof Error ? err.message : '保存失败') }
  finally { savingFacts.value = false }
}

onMounted(() => {
  void load().catch((err: unknown) => {
    ElMessage.error(err instanceof Error ? err.message : '商品加载失败')
  })
})
</script>

<style scoped>
.panel {
  margin-bottom: 20px;
  padding: 30px;
}
.lead {
  line-height: 1.6;
  color: var(--muted);
  max-width: 70ch;
}
h1,
h2 {
  margin: 0 0 10px;
  letter-spacing: -.04em;
}
h1 { font-size: clamp(30px, 3vw, 42px); font-weight: 500; }
h2 { font-size: 23px; }
.product-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 20px; max-width: 820px; margin-top: 24px; }
.product-form :deep(.el-select) { width: 100%; }
.product-form :deep(.el-button) { justify-self: start; padding-inline: 24px; }
.thumb { width: 72px; height: 72px; object-fit: contain; border: 1px solid var(--line); border-radius: var(--radius); background: #f9fafb; }
.upload-label { color: var(--accent); cursor: pointer; font-weight: 600; }
.panel :deep(.el-table) { border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.panel :deep(.el-table th.el-table__cell) { background: rgba(255, 255, 255, .42); color: var(--muted); }
@media (max-width: 700px) {
  .panel { padding: 20px; }
  .product-form { grid-template-columns: 1fr; }
}
</style>
