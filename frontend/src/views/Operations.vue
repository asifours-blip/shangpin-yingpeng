<template>
  <AppLayout wide>
    <section class="ops-hero">
      <div class="ops-hero-wash" aria-hidden="true"></div>
      <div class="ops-hero-content">
        <p class="ops-kicker">运营总览</p>
        <h1><span>把事实理清。</span><span>把内容交付。</span></h1>
        <p>自家商品、生成、人工审核与交付，都从这里继续。</p>
        <div class="head-actions"><router-link class="primary-link" to="/campaigns/new">创建活动</router-link><router-link class="secondary-link" to="/operation-plans">运营计划与执行记录</router-link></div>
      </div>
    </section>
    <section v-if="error" class="panel state-block"><p class="fail-msg">{{ error }}</p><el-button @click="load">重试</el-button></section>
    <template v-else>
      <div class="bucket-intro"><h2>当前待办</h2><p>按活动的平台变体统计，点开后可继续处理。</p></div>
      <div class="bucket-grid">
        <router-link v-for="bucket in buckets" :key="bucket.key" class="panel bucket" :to="{ path: '/operations', query: { bucket: bucket.key } }">
          <span>{{ bucket.label }}</span><strong>{{ overview?.counts[bucket.key] ?? '—' }}</strong>
        </router-link>
      </div>
      <section class="panel ops-list">
        <div class="list-head"><h2>{{ selectedLabel }}</h2><router-link v-if="selected" to="/operations">全部</router-link></div>
        <div v-if="loading" class="state-block">正在读取活动…</div>
        <div v-else-if="!visibleItems.length" class="state-block"><p>{{ overview?.items.length ? '这一类暂时没有活动。' : '还没有活动，先从自家商品创建。' }}</p><router-link class="primary-link" to="/campaigns/new">创建活动</router-link></div>
        <div v-else class="table-wrap"><table class="data-table"><thead><tr><th>活动 / 平台</th><th>状态</th><th>当前阻塞</th><th>下一步</th></tr></thead><tbody>
          <tr v-for="item in visibleItems" :key="`${item.campaign_id}-${item.platform}`">
            <td><router-link :to="detailLink(item)">活动 {{ item.campaign_id }} · {{ platformLabel(item.platform) }} · v{{ item.version }}</router-link></td>
            <td>{{ bucketLabel(item.bucket) }}</td>
            <td>{{ item.blockers?.map((part) => part.message).join('；') || '无' }}</td>
            <td><router-link :to="detailLink(item)">{{ item.next_action || '查看详情' }} →</router-link></td>
          </tr>
        </tbody></table></div>
      </section>
    </template>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import { campaignApi, type Overview, type OverviewBucket, type OverviewItem } from '../campaignApi'

const route = useRoute()
const overview = ref<Overview | null>(null)
const loading = ref(true)
const error = ref('')
const buckets: { key: OverviewBucket; label: string }[] = [
  { key: 'needs_info', label: '待补资料' }, { key: 'pending_generation', label: '待生成' },
  { key: 'generating', label: '生成中' }, { key: 'needs_review', label: '待审核' },
  { key: 'approved_ready', label: '已审待交付' }, { key: 'publication_exception', label: '发布任务异常' },
]
const selected = computed(() => buckets.find((row) => row.key === route.query.bucket)?.key)
const selectedLabel = computed(() => buckets.find((row) => row.key === selected.value)?.label || '所有活动')
const visibleItems = computed(() => (overview.value?.items || []).filter((item) => !selected.value || item.bucket === selected.value))
function bucketLabel(value: OverviewBucket) { return buckets.find((row) => row.key === value)?.label || value }
function platformLabel(value: string) { return value === 'douyin' ? '抖音' : value === 'xiaohongshu' ? '小红书' : value }
function detailLink(item: OverviewItem) { return { path: `/campaigns/${item.campaign_id}`, query: { platform: item.platform, version: String(item.version) } } }
async function load() {
  loading.value = true; error.value = ''
  try { overview.value = await campaignApi.overview() }
  catch (err) { error.value = err instanceof Error ? err.message : '读取失败' }
  finally { loading.value = false }
}
onMounted(() => { void load() })
</script>

<style scoped>
.ops-hero {
  min-height: 390px;
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  margin-bottom: 36px;
  border-radius: 24px;
  background: #e5e7eb radial-gradient(circle at 72% 38%, rgba(184, 195, 207, .76), transparent 38%), radial-gradient(circle at 22% 78%, rgba(219, 208, 195, .58), transparent 42%), linear-gradient(135deg, #e8edf2 0%, #cdd5dc 56%, #f5f7f9 100%);
}
.ops-hero-wash { position: absolute; inset: 0; width: 100%; height: 100%; }
.ops-hero-wash { background: linear-gradient(90deg, rgba(249, 250, 251, .84), rgba(249, 250, 251, .3) 68%, rgba(249, 250, 251, .05)); }
.ops-hero-content { position: relative; width: min(100%, 860px); padding: 46px 60px; }
.ops-kicker { margin: 0 0 12px; color: var(--muted); font-weight: 600; letter-spacing: .12em; }
.ops-hero h1 { margin: 0; font-size: clamp(44px, 5.4vw, 78px); font-weight: 400; letter-spacing: -.05em; line-height: 1.1; }
.ops-hero h1 span { display: block; }
.ops-hero h1 span:first-child { color: #6b7280; }
.ops-hero h1 span:last-child { color: #202A36; margin-top: -3px; }
.ops-hero-content > p:not(.ops-kicker) { max-width: 560px; margin: 22px 0 20px; color: #374151; font-size: 16px; }
.head-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.secondary-link { display: inline-flex; align-items: center; min-height: 42px; padding: 0 19px; border: 1px solid var(--glass-edge); border-radius: 999px; background: linear-gradient(145deg, rgba(255, 255, 255, .72), rgba(205, 213, 223, .48)); box-shadow: var(--glass-shadow); color: #202A36; font-weight: 600; backdrop-filter: blur(16px) saturate(170%); transition: transform .2s ease, background-color .2s ease, box-shadow .2s ease; }
.secondary-link:hover { background-color: var(--glass-light-hover); box-shadow: 0 10px 22px rgba(32, 42, 54, .14), inset 0 1px 0 rgba(255, 255, 255, .96); transform: translateY(-2px); }
.secondary-link:active { transform: translateY(1px); }
.bucket-intro { display: flex; align-items: baseline; gap: 16px; width: max-content; max-width: 100%; margin-bottom: 14px; padding: 9px 14px; border: 1px solid rgba(255, 255, 255, .72); border-radius: 14px; background: rgba(255, 255, 255, .62); box-shadow: 0 5px 18px rgba(32, 42, 54, .09); backdrop-filter: blur(14px) saturate(135%); }
.bucket-intro h2 { margin: 0; font-size: 22px; letter-spacing: -.025em; }
.bucket-intro p { margin: 0; color: #374151; }
.bucket-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 25px; }
.bucket { display: flex; align-items: flex-end; justify-content: space-between; gap: 8px; min-height: 88px; padding: 16px 20px; color: var(--text); }
.bucket:hover { border-color: #9ca3af; }
.bucket strong { font-size: 33px; line-height: 1; font-weight: 500; letter-spacing: -.05em; color: var(--text); }
.ops-list { padding: 0; overflow: hidden; }
.list-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 22px 24px; border-bottom: 1px solid var(--line); }
.list-head h2 { margin: 0; font-size: 20px; }
.list-head a, .data-table a { color: var(--accent); }
.data-table td { min-width: 120px; }
@media (max-width: 700px) {
  .ops-hero { min-height: 400px; margin-bottom: 24px; }
  .ops-hero-wash { background: rgba(249, 250, 251, .78); }
  .ops-hero-content { padding: 32px 24px 74px; }
  .ops-hero h1 { font-size: clamp(39px, 10vw, 58px); }
  .ops-hero h1 span:last-child { margin-top: -2px; }
  .ops-hero-content > p:not(.ops-kicker) { font-size: 14px; }
  .bucket-intro { display: block; width: 100%; }
  .bucket-intro p { margin-top: 4px; font-size: 13px; }
  .bucket-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .bucket { min-height: 78px; flex-direction: column; align-items: flex-start; padding: 13px 16px; }
  .bucket strong { font-size: 28px; }
  .list-head { padding: 18px 16px; }
  .ops-list .table-wrap { overflow: visible; }
  .data-table, .data-table tbody, .data-table tr, .data-table td { display: block; width: 100%; }
  .data-table thead { display: none; }
  .data-table tr { padding: 16px; border-bottom: 1px solid var(--line); }
  .data-table tr:last-child { border-bottom: 0; }
  .data-table td { min-width: 0; padding: 3px 0; border: 0; overflow-wrap: anywhere; }
  .data-table td:first-child { font-weight: 600; margin-bottom: 7px; }
  .data-table td:nth-child(n+2)::before { display: inline-block; min-width: 72px; color: var(--muted); font-weight: 400; }
  .data-table td:nth-child(2)::before { content: '状态'; }
  .data-table td:nth-child(3)::before { content: '当前阻塞'; }
  .data-table td:nth-child(4)::before { content: '下一步'; }
}
</style>
