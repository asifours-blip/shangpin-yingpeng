<template>
  <AppLayout wide>
    <section class="panel">
      <h1>趋势样本</h1>
      <p class="lead">
        每次采集只保留当前口径下最多 100 条。数量不够就显示实际条数，没有授权时显示待连接，不会补造榜单。
      </p>
      <el-form class="grid" label-position="top" @submit.prevent="onCollect">
        <el-form-item label="来源">
          <el-select v-model="form.provider">
            <el-option v-if="showFixture" label="内部箱包样本（仅隔离测试）" value="fixture" />
            <el-option label="淘宝客商品" value="taobao" />
            <el-option label="抖音内容" value="douyin" />
            <el-option label="小红书内容" value="xiaohongshu" />
          </el-select>
        </el-form-item>
        <el-form-item label="数据类型">
          <el-select v-model="form.item_kind">
            <el-option label="商品" value="product" />
            <el-option label="内容" value="post" />
          </el-select>
        </el-form-item>
        <el-form-item label="类目">
          <el-input v-model="form.category" />
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="form.query" />
        </el-form-item>
        <el-form-item label="时间窗">
          <el-input v-model="form.window" />
        </el-form-item>
        <el-form-item label="排序口径">
          <el-input v-model="form.sort_metric" />
        </el-form-item>
        <el-form-item label="最多条数">
          <el-input-number v-model="form.max_items" :min="1" :max="100" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit" :loading="loading">开始采集</el-button>
        </el-form-item>
      </el-form>
    </section>

    <section v-if="run" class="panel">
      <h2>本轮结果</h2>
      <router-link v-if="run.actual_count > 0" :to="{ path: '/campaigns/new', query: { run_id: String(run.id) } }">用这一批作活动参考 →</router-link>
      <p>
        状态 {{ statusLabel }} · 实际 {{ run.actual_count }} 条 · 去掉重复 {{ run.dropped_duplicate }} ·
        去掉缺字段 {{ run.dropped_invalid }}
      </p>
      <p class="scope">{{ run.scope_description || '暂无数据集说明' }}</p>
      <p v-if="run.error_summary" class="warn">{{ run.error_summary }}</p>
      <p v-if="run.actual_count === 0" class="empty">待连接 / 暂无可用数据</p>
      <el-table v-else :data="items" size="small">
        <el-table-column prop="source_rank" label="序号" width="70" />
        <el-table-column prop="platform" label="平台" width="110" />
        <el-table-column prop="item_kind" label="类型" width="90" />
        <el-table-column prop="title" label="标题" min-width="180" />
        <el-table-column label="链接" min-width="160">
          <template #default="{ row }">
            <a v-if="row.url" :href="row.url" target="_blank" rel="noreferrer">来源</a>
            <span v-else>无链接</span>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import { sourceApi, type CollectionRun, type SourceItem } from '../sourceApi'

const loading = ref(false)
const showFixture = import.meta.env.VITE_ENABLE_FIXTURE_SOURCE === '1'
const run = ref<CollectionRun | null>(null)
const items = ref<SourceItem[]>([])
const form = reactive({
  provider: 'taobao',
  item_kind: 'product',
  category: '箱包',
  query: '箱包',
  window: '7d',
  sort_metric: 'sample_order',
  max_items: 100,
})

const statusLabel = computed(() => {
  if (!run.value) return ''
  if (run.value.status === 'succeeded') return '采集完成'
  if (run.value.status === 'partial') return '只完成了一部分'
  if (run.value.status === 'failed') return '未连上或失败'
  return run.value.status
})

async function onCollect() {
  loading.value = true
  try {
    const config = await sourceApi.createConfig({ ...form })
    const started = await sourceApi.startRun(config.id)
    run.value = started
    const page = await sourceApi.getItems(started.id)
    items.value = page.items
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '采集失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.panel {
  margin-bottom: 24px;
}
.lead,
.scope,
.empty,
.warn {
  line-height: 1.6;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px 16px;
  align-items: end;
}
.warn,
.empty {
  color: #8a5a00;
}
h1,
h2 {
  margin: 0 0 8px;
}
</style>
