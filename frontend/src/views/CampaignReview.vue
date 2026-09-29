<template>
  <AppLayout wide>
    <section class="panel review-head">
      <div>
        <h1>内容审核</h1>
        <p class="lead">
          抖音和小红书分开审。通过只绑定当前这一版。下方排期保存已审核快照；仅在授权有效且启用真实调用后执行发布，创建受理不等于公开。
        </p>
        <p v-if="payload" class="ark-note">方舟联调：{{ payload.ark_live }}。还没验收，不能当成已经生成完成。</p>
      </div>
      <label class="pick">
        活动
        <el-select v-if="campaigns.length" v-model="campaignId" placeholder="选择活动" @change="selectCampaign">
          <el-option
            v-for="item in campaigns"
            :key="item.id"
            :label="`活动 ${item.id} · ${payload?.campaign_id === item.id ? payload.status : item.status}`"
            :value="item.id"
          />
        </el-select>
        <span v-else>还没有可审核的活动。</span>
        <a v-if="payload" href="#publish">安排发布</a>
      </label>
    </section>
    <section v-if="versionWarning" class="panel warn" role="alert">{{ versionWarning }} <el-button size="small" @click="useCurrentVersion">核对并切到当前版本</el-button></section>
    <section v-if="campaignError" class="panel warn" role="alert">{{ campaignError }}</section>
    <section v-if="loadError" class="panel warn" role="alert">{{ loadError }} <el-button size="small" @click="load">重试</el-button></section>

    <section v-if="payload" class="panel fact-strip">
      <div>
        <h2>锁定事实</h2>
        <p>商品 {{ payload.product_name || '未命名' }} · 事实版本 v{{ payload.fact_version ?? '—' }}</p>
        <p>{{ factText }}</p>
      </div>
      <figure v-if="payload.primary_asset_id" class="origin">
        <img :src="`/api/assets/${payload.primary_asset_id}/file`" alt="原商品图" />
        <figcaption>原商品图</figcaption>
      </figure>
    </section>

    <div v-if="payload" class="review-grid" :class="{ single: payload.platforms.length === 1 }">
      <article v-for="side in payload.platforms" :key="side.platform" :id="`review-${side.platform}`" class="panel review-side">
        <header class="side-head">
          <h2>{{ side.platform === 'douyin' ? '抖音' : '小红书' }}</h2>
          <span>当前版本 {{ side.current.version }} · {{ side.current.status }}</span>
        </header>

        <label>标题</label>
        <el-input v-model="drafts[side.platform].title" />
        <label>正文</label>
        <el-input v-model="drafts[side.platform].body" type="textarea" :rows="4" />
        <label>话题</label>
        <el-input v-model="drafts[side.platform].tags" placeholder="用空格分隔" />
        <p v-if="humanCopy(side)" class="hint">这一版文案是人工修订，媒体依赖这次输入，不会把失败的旧文案改成成功。</p>

        <section v-if="side.platform === 'douyin'" class="storyboard-panel">
          <h3>三镜头审核</h3>
          <p class="hint">三个镜头各自保留首帧和 5 秒视频；导出为分镜素材，不是拼接成片。旧版 FFmpeg 成片仍可沿原流程交付。</p>
          <p class="hint">活动生成预算：{{ payload.generation_budget }} 次，已占用 {{ payload.budget_reserved }} 次，剩余 {{ payload.budget_remaining }} 次。每次提交图片或视频各占 1 次；不会自动连续提交。</p>
          <p class="hint">方舟图片：{{ payload.provider_ready.image ? '已配置' : '未配置' }} · 视频：{{ payload.provider_ready.video ? '已配置' : '未配置' }}；配置存在不代表模型可调用或免费。</p>
          <el-button v-if="side.current.storyboard?.mode !== 'reviewed_shots_v1'" :disabled="isStale(side.platform) || !!storyBusy" @click="onStartStoryboard(side)">开启三镜头版本</el-button>
          <template v-else>
            <p>当前审核版本 v{{ side.current.version }} · 三镜头事实版本 #{{ side.current.storyboard.fact_version_id }}</p>
            <div class="story-shots">
              <article v-for="(shot, index) in side.current.storyboard.shots" :key="index" class="story-shot">
                <h4>镜头 {{ index + 1 }} · {{ shot.locked ? '已锁定' : '未锁定' }}</h4>
                <el-input v-model="shotPrompts[index]" type="textarea" :rows="2" :disabled="shot.locked" aria-label="镜头描述" />
                <p class="hint">修改描述后需点“重做首帧”才会保存；锁定镜头先显式解锁。</p>
                <p>首帧：{{ shot.first_frame_review === 'approved' ? '审核通过' : shot.first_frame_review === 'rejected' ? '已退回' : taskStatus(side, shot.first_frame_task_id) }}</p>
                <img v-if="shot.first_frame_asset_id" class="shot-media" :src="`/api/assets/${shot.first_frame_asset_id}/file`" :alt="`镜头 ${index + 1} 首帧`" />
                <div class="actions">
                  <el-button :disabled="isStale(side.platform) || !!storyBusy || shot.locked || !!shot.first_frame_task_id || !payload.provider_ready.image || payload.budget_remaining < 1" @click="onShotAction(side, index, 'submit_frame')">生成首帧</el-button>
                  <el-button :disabled="isStale(side.platform) || !!storyBusy || shot.locked || taskStatus(side, shot.first_frame_task_id) !== 'succeeded' || !!shot.first_frame_review" @click="onShotAction(side, index, 'review_frame', { accepted: true })">首帧通过</el-button>
                  <el-button :disabled="isStale(side.platform) || !!storyBusy || shot.locked || taskStatus(side, shot.first_frame_task_id) !== 'succeeded' || !!shot.first_frame_review" @click="onShotAction(side, index, 'review_frame', { accepted: false })">首帧退回</el-button>
                </div>
                <p>视频：{{ shot.video_review === 'approved' ? '审核通过' : shot.video_review === 'rejected' ? '已退回' : taskStatus(side, shot.video_task_id) }}</p>
                <video v-if="shot.video_asset_id" class="shot-media" controls :src="`/api/assets/${shot.video_asset_id}/file`" :aria-label="`镜头 ${index + 1} 视频`"></video>
                <div class="actions">
                  <el-button :disabled="isStale(side.platform) || !!storyBusy || shot.locked || shot.first_frame_review !== 'approved' || !!shot.video_task_id || !payload.provider_ready.video || payload.budget_remaining < 1" @click="onShotAction(side, index, 'submit_video')">生成本镜头视频</el-button>
                  <el-button :disabled="isStale(side.platform) || !!storyBusy || shot.locked || taskStatus(side, shot.video_task_id) !== 'succeeded' || !!shot.video_review" @click="onShotAction(side, index, 'review_video', { accepted: true })">视频通过</el-button>
                  <el-button :disabled="isStale(side.platform) || !!storyBusy || shot.locked || taskStatus(side, shot.video_task_id) !== 'succeeded' || !!shot.video_review" @click="onShotAction(side, index, 'review_video', { accepted: false })">视频退回</el-button>
                </div>
                <div class="actions">
                  <el-button v-if="!shot.locked" :disabled="isStale(side.platform) || !!storyBusy || shot.video_review !== 'approved'" @click="onShotAction(side, index, 'lock')">锁定镜头</el-button>
                  <el-button v-else :disabled="isStale(side.platform) || !!storyBusy" @click="onShotAction(side, index, 'unlock')">解锁镜头</el-button>
                  <el-button :disabled="isStale(side.platform) || !!storyBusy || shot.locked" @click="onShotAction(side, index, 'redo', { stage: 'first_frame', prompt: shotPrompts[index] })">只重做本镜头首帧</el-button>
                  <el-button :disabled="isStale(side.platform) || !!storyBusy || shot.locked || shot.first_frame_review !== 'approved'" @click="onShotAction(side, index, 'redo', { stage: 'video' })">只重做本镜头视频</el-button>
                </div>
              </article>
            </div>
            <el-button :disabled="!!storyBusy" @click="load">刷新生成状态</el-button>
          </template>
        </section>

        <h3>媒体预览</h3>
        <p class="hint">图片和视频从当前配置的对象存储读取。对象不存在或读不出来时，不能批准。</p>
        <ul class="asset-list">
          <li v-for="asset in side.current.assets" :key="`${asset.role}-${asset.position}-${asset.asset_id}`">
            <span>{{ asset.position }}. {{ roleLabel(asset.role) }} · 资产 {{ asset.asset_id }}</span>
            <img v-if="asset.role !== 'final_video' && asset.role !== 'shot_video'" :src="`/api/assets/${asset.asset_id}/file`" alt="" />
            <video
              v-else
              controls
              :src="`/api/assets/${asset.asset_id}/file`"
              :aria-label="`${roleLabel(asset.role)} ${asset.asset_id}`"
            ></video>
          </li>
          <li v-if="!side.current.assets.length">这一版还没有媒体。印进画面的文案改过之后，封面、组图或视频要重新生成。</li>
        </ul>

        <h3>事实引用</h3>
        <p v-if="side.current.fact_version_id">这一版锁定事实 #{{ side.current.fact_version_id }}</p>
        <ul>
          <li v-for="(line, index) in citations(side)" :key="index">{{ line }}</li>
          <li v-if="!citations(side).length">这一版还没有写进画面的事实句。</li>
        </ul>

        <h3>质检问题</h3>
        <ul>
          <li v-for="item in side.blockers" :key="item.code">{{ item.message }}（{{ item.code }}）</li>
          <li v-if="!side.blockers.length">这一版没有挡住通过的质检问题。</li>
        </ul>

        <h3>内部备注</h3>
        <ul>
          <li v-for="(note, index) in notes(side)" :key="index">{{ note }}</li>
          <li v-if="!notes(side).length">没有内部备注。</li>
        </ul>

        <label>意见</label>
        <el-input v-model="comments[side.platform]" type="textarea" :rows="2" placeholder="通过、退回或重做时一起记下" />

        <div class="actions">
          <el-button :disabled="isStale(side.platform)" @click="onEdit(side)">保存为新版本</el-button>
          <el-button type="primary" :disabled="isStale(side.platform)" @click="onApprove(side)">通过</el-button>
          <el-button :disabled="isStale(side.platform) || !hasCurrentApproval(side)" :loading="exporting === side.platform" @click="onExport(side)">导出当前已审核版本</el-button>
          <el-button :disabled="isStale(side.platform)" @click="onReject(side)">退回</el-button>
          <el-button v-if="side.current.status === 'approved'" :disabled="isStale(side.platform)" @click="onRevoke(side)">撤回批准</el-button>
        </div>
        <p v-if="hasCurrentApproval(side)" class="hint">导出锁定这一版的审核快照；缺少发布账号也能交付素材。下载成功不代表已发布。</p>
        <p v-else class="hint">当前版本还没有有效批准。媒体或文案变化后，需重新审核才能导出。</p>

        <h3 v-if="side.current.storyboard?.mode !== 'reviewed_shots_v1'">局部重做</h3>
        <el-checkbox-group v-if="side.current.storyboard?.mode !== 'reviewed_shots_v1'" v-model="redos[side.platform]">
          <el-checkbox v-for="step in side.steps" :key="step.step_key" :value="step.step_key">
            重做 {{ stepLabel(step.step_key) }}（v{{ step.version }} {{ step.status }}）
          </el-checkbox>
        </el-checkbox-group>
        <el-button v-if="side.current.storyboard?.mode !== 'reviewed_shots_v1'" :disabled="isStale(side.platform) || side.steps.some((item) => item.status === 'unknown')" @click="onRedo(side)">按所选步骤重做</el-button>
        <p v-if="side.steps.some((item) => item.status === 'unknown')" class="warn">结果未知，先人工核对，不能盲目重提。</p>

        <h3>版本</h3>
        <ul class="versions">
          <li v-for="item in side.versions" :key="item.id">
            v{{ item.version }} · {{ item.status }} · {{ item.title || '还没有标题' }}
          </li>
        </ul>

        <h3>审核记录</h3>
        <ul class="versions">
          <li v-for="item in side.reviews" :key="item.id">
            变体 {{ item.variant_id }} · v{{ item.version }} · {{ item.decision === 'approved' ? '通过' : '退回' }}
            · 审核人 {{ item.reviewer_id }} · {{ item.reviewed_at }}
            <span v-if="item.comment"> · {{ item.comment }}</span>
            <div>文案快照：{{ item.copy_snapshot.title || '无标题' }} / {{ item.copy_snapshot.body || '无正文' }}</div>
            <div>事实版本 #{{ item.fact_version_id }} · 资产 {{ item.asset_order.length }} 个</div>
          </li>
          <li v-if="!side.reviews.length">还没有审核记录。</li>
        </ul>
      </article>
    </div>

    <section v-if="desk" id="publish" class="panel publish-desk">
      <header>
        <h2>发布安排</h2>
        <p class="hint">
          目标账号只来自发布连接，不是样例联系人。时间按界面时区 {{ zone }} 填写，库存 {{ desk.timezone_storage }}。
          活动状态 {{ desk.campaign_status }}。一侧失败不会改另一侧已经记下的任务。
        </p>
      </header>
      <div class="review-grid" :class="{ single: desk.platforms.length === 1 }">
        <article v-for="side in desk.platforms" :key="side.platform">
          <h3>{{ side.platform === 'douyin' ? '抖音' : '小红书' }}</h3>
          <p>{{ side.content_ready ? '已审版本' : '待审核版本' }} v{{ side.version }} · {{ side.variant_status }} · 审核记录 {{ side.review_id ?? '无' }}</p>
          <p>连接状态：{{ selectedConnection(side) && !visibleMissing(side).length ? '已连接' : '待连接' }} · 资格 {{ readinessLabel(visibleReadiness(side)) }}</p>
          <p class="hint">{{ side.catalog.account_type }}</p>
          <p v-if="side.platform === 'douyin'" class="hint">
            HTTP 适配器：{{ side.catalog.implemented ? '已实现' : '待接入' }} ·
            真实调用：{{ side.catalog.live_enabled ? '开' : '关' }} ·
            真实联调：{{ side.catalog.live_verified === 'passed' ? '已验收' : '待验收' }}
          </p>
          <ul>
            <li v-for="item in visibleMissing(side)" :key="item">{{ item }}</li>
            <li v-if="!visibleMissing(side).length">没有待连接的缺项。</li>
          </ul>
          <ul>
            <li v-for="item in side.content_blockers" :key="item.code">{{ item.message }}</li>
          </ul>
          <p v-for="line in side.catalog.limits || []" :key="line" class="hint">{{ line }}</p>
          <label>发布账号</label>
          <el-select v-model="accounts[side.platform]" clearable placeholder="选择已连接的发布账号">
            <el-option
              v-for="conn in side.connections"
              :key="conn.id"
              :label="`${conn.external_account_id} · ${conn.status}`"
              :value="conn.id"
            />
          </el-select>
          <label>排期（{{ zone }}）</label>
          <input v-model="when[side.platform]" class="when" type="datetime-local" />
          <div class="actions">
            <el-button :disabled="!side.content_ready || isStale(side.platform)" @click="onSchedule(side)">安排这一版</el-button>
          </div>
        </article>
      </div>
      <h3>任务</h3>
      <ul class="versions">
        <li v-for="job in desk.jobs" :key="job.id">
          <strong>{{ job.platform === 'douyin' ? '抖音' : '小红书' }}</strong>
          · {{ accountLabel(job) }}
          · v{{ job.version }}
          · {{ job.status }}
          · {{ phaseLabel(job.phase) }}
          · {{ formatWhen(job.scheduled_at) }}
          <span v-if="job.readiness"> · {{ readinessLabel(job.readiness) }}</span>
          <div v-if="job.notice" class="warn">{{ job.notice }}</div>
          <div v-if="job.error_message">{{ job.error_message }}</div>
          <div v-if="job.next_action" class="hint">下一步：{{ job.next_action }}</div>
          <div v-if="job.missing.length">缺项：{{ job.missing.join('；') }}</div>
          <div>快照标题：{{ job.copy_snapshot.title || '无标题' }}</div>
          <el-button v-if="job.cancelable" size="small" @click="onCancel(job)">取消尚未提交的任务</el-button>
          <el-button v-if="job.status === 'needs_reconfirm'" size="small" @click="onReconfirm(job)">重新确认</el-button>
        </li>
        <li v-if="!desk.jobs.length">还没有发布任务。</li>
      </ul>
    </section>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import { campaignApi, type PublishDesk, type PublishJob, type PublishSide, type ReviewPayload, type ReviewPlatform } from '../campaignApi'
import { downloadApprovedAssets, hasCurrentApproval } from '../exportDelivery'

const campaigns = ref<{ id: number; status: string; product_id: number }[]>([])
const campaignError = ref('')
const loadError = ref('')
const loading = ref(false)
const exporting = ref<string | null>(null)
let loadSerial = 0
const route = useRoute()
const router = useRouter()
const campaignId = ref<number | null>(null)
const payload = ref<ReviewPayload | null>(null)
const drafts = reactive<Record<string, { title: string; body: string; tags: string }>>({
  douyin: { title: '', body: '', tags: '' },
  xiaohongshu: { title: '', body: '', tags: '' },
})
const comments = reactive<Record<string, string>>({ douyin: '', xiaohongshu: '' })
const redos = reactive<Record<string, string[]>>({ douyin: [], xiaohongshu: [] })
const shotPrompts = ref<string[]>(['', '', ''])
const storyBusy = ref(false)
const desk = ref<PublishDesk | null>(null)
const accounts = reactive<Record<string, number | undefined>>({ douyin: undefined, xiaohongshu: undefined })
const when = reactive<Record<string, string>>({ douyin: '', xiaohongshu: '' })
const scheduleKeys = reactive<Record<string, string>>({ douyin: '', xiaohongshu: '' })
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const versionWarning = computed(() => {
  const expectedPlatform = String(route.query.platform || '')
  const expectedVersion = Number(route.query.version)
  if (!expectedPlatform) return ''
  if (route.query.version != null && (!Number.isInteger(expectedVersion) || expectedVersion < 1)) return '链接中的版本号无效。'
  if (!expectedVersion) return ''
  const side = payload.value?.platforms.find((item) => item.platform === expectedPlatform)
  if (!side) return `活动中没有 ${expectedPlatform} 平台内容。`
  return side.current.version === expectedVersion ? '' : `链接指向 ${expectedPlatform} v${expectedVersion}，当前已是 v${side.current.version}。旧链接不会自动改为新版执行操作。`
})
function isStale(platform: string) { return loading.value || (!!versionWarning.value && String(route.query.platform || '') === platform) }
async function useCurrentVersion() {
  const platform = String(route.query.platform || '')
  const side = payload.value?.platforms.find((item) => item.platform === platform)
  if (!side) return
  await router.replace({ path: '/review', query: { campaign_id: String(campaignId.value), platform, version: String(side.current.version) } })
}

const factText = computed(() => {
  const facts = payload.value?.facts || {}
  const parts = Object.entries(facts).map(([key, value]) => `${key}: ${String(value)}`)
  return parts.length ? parts.join('；') : '没有锁定事实'
})

function roleLabel(role: string) {
  if (role === 'cover') return '封面'
  if (role === 'card') return '内容卡'
  if (role === 'final_video') return '成片'
  if (role === 'clip') return '镜头'
  if (role === 'shot_first_frame') return '分镜首帧'
  if (role === 'shot_video') return '分镜视频'
  return role
}

function stepLabel(key: string) {
  const names: Record<string, string> = {
    'copy:douyin': '抖音文案',
    'image:douyin': '抖音封面',
    'video:douyin': '抖音成片',
    'copy:xiaohongshu': '小红书文案',
    'cards:xiaohongshu': '小红书组图',
  }
  return names[key] || key
}

function taskStatus(side: ReviewPlatform, taskId: number | null) {
  if (!taskId) return '待生成'
  const task = side.storyboard_tasks[String(taskId)]
  if (!task) return '任务不可见'
  if (task.status === 'succeeded') return '待人工审核'
  if (task.status === 'queued') return '排队中'
  if (task.status === 'running') return '生成中'
  if (task.status === 'unknown') return '结果未知，禁止重提'
  return task.error_message ? `失败：${task.error_message}` : task.status
}

async function onStartStoryboard(side: ReviewPlatform) {
  if (!campaignId.value) return
  storyBusy.value = true
  try {
    await campaignApi.startStoryboard(campaignId.value, side.current.version)
    await refreshVersion('douyin')
    ElMessage.success('三镜头已开启，请逐镜生成与审核')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '开启失败')
    await load()
  } finally {
    storyBusy.value = false
  }
}

async function onShotAction(side: ReviewPlatform, index: number, action: string, extra: Record<string, unknown> = {}) {
  if (!campaignId.value) return
  storyBusy.value = true
  try {
    await campaignApi.shotAction(campaignId.value, index, action, side.current.version, extra)
    await refreshVersion('douyin')
    ElMessage.success('镜头状态已更新')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '镜头操作失败')
    await load()
  } finally {
    storyBusy.value = false
  }
}

function notes(side: ReviewPlatform) {
  const fromSteps = side.steps.flatMap((step) => step.review_notes || [])
  const qc = side.current.qc_result
  const fromQc = qc && Array.isArray(qc.review_notes) ? (qc.review_notes as string[]) : []
  return [...fromSteps, ...fromQc]
}

function humanCopy(side: ReviewPlatform) {
  return side.steps.some((step) => step.step_key.startsWith('copy:') && step.source === 'human_edit')
}

function citations(side: ReviewPlatform) {
  return [...new Set(side.steps.flatMap((step) => step.lines || []).filter(Boolean))]
}

function fillDrafts(data: ReviewPayload) {
  for (const side of data.platforms) {
    drafts[side.platform] = {
      title: side.current.title || '',
      body: side.current.body || '',
      tags: (side.current.hashtags || []).join(' '),
    }
  }
}

function commentFor(platform: string, fallback: string) {
  const text = comments[platform]?.trim()
  return text || fallback
}

async function load() {
  if (!campaignId.value) return
  const serial = ++loadSerial
  const requestedId = campaignId.value
  campaignError.value = ''
  loadError.value = ''
  loading.value = true
  payload.value = null
  desk.value = null
  try {
    const review = await campaignApi.review(requestedId)
    const publish = await campaignApi.publishDesk(requestedId)
    if (serial !== loadSerial) return
    payload.value = review
    desk.value = publish
    fillDrafts(review)
    shotPrompts.value = review.platforms.find((side) => side.platform === 'douyin')?.current.storyboard?.shots.map((shot) => shot.prompt) || ['', '', '']
    for (const side of publish.platforms) {
      if (!side.connections.some((row) => row.id === accounts[side.platform])) {
        accounts[side.platform] = undefined
      }
    }
    await nextTick()
    const platform = String(route.query.platform || '')
    const section = String(route.query.section || '')
    const target = section === 'publish' ? document.getElementById('publish') : document.getElementById(`review-${platform}`)
    target?.scrollIntoView({ block: 'start' })
  } catch (err) {
    if (serial === loadSerial) loadError.value = err instanceof Error ? err.message : '审核资料读取失败'
  } finally {
    if (serial === loadSerial) loading.value = false
  }
}

async function selectCampaign(value: number) {
  await router.push({ path: '/review', query: { campaign_id: String(value) } })
}

async function refreshVersion(platform: string) {
  await load()
  const side = payload.value?.platforms.find((item) => item.platform === platform)
  if (side) await router.replace({ path: '/review', query: { campaign_id: String(campaignId.value), platform, version: String(side.current.version) } })
}

function selectedConnection(side: PublishSide) {
  return side.connections.find((row) => row.id === accounts[side.platform])
}

function visibleMissing(side: PublishSide) {
  return selectedConnection(side)?.missing ?? side.missing
}

function visibleReadiness(side: PublishSide) {
  return selectedConnection(side)?.readiness ?? side.readiness
}

function readinessLabel(value: string) {
  const labels: Record<string, string> = {
    content_blocked: '内容未通过审核',
    pending_connection: '待接入',
    approved_ready_to_publish: '已审、待发布',
    ready: '具备提交条件',
  }
  return labels[value] || value
}

function phaseLabel(value: string) {
  const labels: Record<string, string> = {
    pending_cover: '待上传审核封面',
    cover_intent: '封面上传结果待确认',
    cover_uploaded: '封面已上传',
    video_intent: '视频上传结果待确认',
    video_uploaded: '视频已上传',
    create_intent: '创建结果待确认',
    done: '创建已受理',
    legacy_unknown: '旧任务，产物待核对',
  }
  return labels[value] || value
}

function note(result: unknown) {
  if (result && typeof result === 'object' && 'notice' in result) {
    const text = (result as { notice?: string | null }).notice
    if (text) ElMessage.warning(text)
  }
}

function formatWhen(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.toLocaleString()}（${zone}）`
}

function accountLabel(job: PublishJob) {
  const side = desk.value?.platforms.find((item) => item.platform === job.platform)
  const conn = side?.connections.find((item) => item.id === job.connection_id)
  return conn ? conn.external_account_id : '未绑定发布账号'
}

function toUtcIso(localValue: string) {
  if (!localValue) throw new Error('先选一个还没到的时间')
  const date = new Date(localValue)
  if (Number.isNaN(date.getTime())) throw new Error('时间无效')
  return date.toISOString()
}

async function onSchedule(side: PublishSide) {
  try {
    if (!scheduleKeys[side.platform]) scheduleKeys[side.platform] = crypto.randomUUID()
    await campaignApi.schedule(payload.value!.campaign_id, {
      platform: side.platform,
      expected_version: side.version,
      scheduled_at: toUtcIso(when[side.platform]),
      connection_id: accounts[side.platform] ?? null,
      idempotency_key: scheduleKeys[side.platform],
    })
    scheduleKeys[side.platform] = ''
    ElMessage.success('已记下这一版的发布安排')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '不能安排')
  }
}

async function onCancel(job: PublishJob) {
  try {
    await campaignApi.cancelPublish(payload.value!.campaign_id, job.id)
    ElMessage.success('未提交的任务已取消')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '不能取消')
  }
}

async function onReconfirm(job: PublishJob) {
  try {
    await campaignApi.reconfirmPublish(payload.value!.campaign_id, job.id, toUtcIso(when[job.platform]))
    ElMessage.success('已重新确认排期')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '不能重新确认')
  }
}

async function onRevoke(side: ReviewPlatform) {
  try {
    const result = await campaignApi.revoke(
      payload.value!.campaign_id,
      side.platform,
      side.current.version,
      commentFor(side.platform, '撤回批准'),
    )
    note(result)
    ElMessage.success(result.notice || '已撤回这一版的本地批准')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '不能撤回')
  }
}

async function onEdit(side: ReviewPlatform) {
  try {
    const result = await campaignApi.edit(payload.value!.campaign_id, side.platform, side.current.version, {
      title: drafts[side.platform].title,
      body: drafts[side.platform].body,
      hashtags: drafts[side.platform].tags.split(/\s+/).filter(Boolean),
    })
    note(result)
    ElMessage.success('已写成新版本，旧批准留在旧版本上')
    await refreshVersion(side.platform)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  }
}

async function onApprove(side: ReviewPlatform) {
  try {
    await campaignApi.approve(
      payload.value!.campaign_id,
      side.platform,
      side.current.version,
      commentFor(side.platform, '通过当前版本'),
    )
    ElMessage.success('已通过这一版')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '不能通过')
  }
}

async function onReject(side: ReviewPlatform) {
  try {
    const result = await campaignApi.reject(
      payload.value!.campaign_id,
      side.platform,
      side.current.version,
      commentFor(side.platform, '退回'),
    )
    note(result)
    ElMessage.success('已退回这一版')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '退回失败')
  }
}

async function onRedo(side: ReviewPlatform) {
  try {
    const result = await campaignApi.redo(
      payload.value!.campaign_id,
      side.platform,
      side.current.version,
      redos[side.platform],
      commentFor(side.platform, '局部重做'),
    )
    note(result)
    redos[side.platform] = []
    ElMessage.success('已按所选步骤重做，其余成功结果还在')
    await refreshVersion(side.platform)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '重做失败')
  }
}

async function onExport(side: ReviewPlatform) {
  if (!payload.value || exporting.value) return
  exporting.value = side.platform
  try {
    await downloadApprovedAssets(payload.value.campaign_id, side, (message) => ElMessage.error(message))
    ElMessage.success('已开始下载这一版已审素材；导出不等于发布')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '无法导出素材')
    if (err && typeof err === 'object' && 'status' in err) await load()
  } finally {
    exporting.value = null
  }
}

onMounted(async () => {
  let listed: { items: { id: number; status: string; product_id: number }[] }
  try { listed = await campaignApi.list() }
  catch (err) { loadError.value = err instanceof Error ? err.message : '活动列表读取失败'; return }
  campaigns.value = listed.items
  const queryId = route.query.campaign_id
  if (queryId != null && !listed.items.some((item) => item.id === Number(queryId))) {
    campaignError.value = `活动 ${String(queryId)} 不存在或无权限查看。`
    return
  }
  if (listed.items.length) {
    campaignId.value = queryId == null ? listed.items[0].id : Number(queryId)
    await load()
  }
})
watch(() => route.query.campaign_id, (value) => {
  const id = Number(value)
  if (value != null && !campaigns.value.some((item) => item.id === id)) {
    loadSerial++
    loading.value = false
    campaignError.value = `活动 ${String(value)} 不存在或无权限查看。`
    payload.value = null
    desk.value = null
    return
  }
  if (id && id !== campaignId.value) { campaignId.value = id; void load() }
})
watch(() => [route.query.platform, route.query.section], async () => {
  await nextTick()
  const section = String(route.query.section || '')
  const platform = String(route.query.platform || '')
  const target = section === 'publish' ? document.getElementById('publish') : document.getElementById(`review-${platform}`)
  target?.scrollIntoView({ block: 'start' })
})
</script>

<style scoped>
.review-head,
.fact-strip,
.side-head,
.actions {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}
.review-head {
  align-items: flex-end;
  border: 0;
  box-shadow: none;
  background: transparent;
  padding: 8px 0 24px;
}
.review-head h1 { margin: 0 0 9px; font-size: clamp(32px, 4vw, 48px); font-weight: 500; letter-spacing: -.04em; }
.review-head .lead { max-width: 75ch; color: var(--muted); line-height: 1.6; }
.fact-strip, .review-side, .publish-desk { margin-bottom: 18px; }
.review-side { min-width: 0; }
.side-head { padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.side-head h2 { margin: 0; font-size: 25px; letter-spacing: -.04em; }
.pick {
  display: grid;
  gap: 6px;
  min-width: 220px;
}
.fact-strip {
  align-items: center;
}
.origin {
  margin: 0;
}
.origin img,
.asset-list img,
.asset-list video {
  display: block;
  width: 160px;
  max-height: 200px;
  object-fit: cover;
  border-radius: var(--radius);
  background: #f3f4f6;
}
.hint {
  color: var(--muted);
  font-size: 13px;
}
.review-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.review-grid.single { grid-template-columns: 1fr; }
.review-side h3 {
  margin: 16px 0 6px;
}
.asset-list,
.versions {
  margin: 0;
  padding-left: 18px;
}
.actions {
  margin: 12px 0;
  justify-content: flex-start;
  flex-wrap: wrap;
}
.ark-note {
  color: var(--warn);
}
.warn {
  color: var(--danger);
}
.when {
  display: block;
  margin-top: 6px;
  min-height: 32px;
}
.publish-desk {
  margin-top: 16px;
}
label {
  display: block;
  margin-top: 10px;
}
@media (max-width: 900px) {
  .review-grid,
  .review-head,
  .fact-strip {
    grid-template-columns: 1fr;
    display: grid;
  }
}
</style>


<style scoped>
.storyboard-panel { margin: 1.5rem 0; padding: 1rem; border: 1px solid var(--el-border-color); border-radius: 12px; }
.story-shots { display: grid; gap: 1rem; margin: 1rem 0; }
.story-shot { padding: 1rem; border: 1px solid var(--el-border-color-light); border-radius: 8px; }
.shot-media { display: block; max-width: 240px; max-height: 320px; margin: .5rem 0; }
</style>
