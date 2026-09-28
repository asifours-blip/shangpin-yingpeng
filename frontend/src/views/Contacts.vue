<template>
  <AppLayout>
    <div class="panel contacts-page">
      <div class="page-bar">
        <h2>联系人中心</h2>
        <div class="actions">
          <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        </div>
      </div>

      <div class="page-filters">
        <el-input
          v-model="keyword"
          placeholder="搜索昵称 / 备注"
          clearable
          style="width: 220px"
          @keyup.enter="load"
          @clear="load"
        />
        <el-select v-model="platform" placeholder="全部平台" clearable style="width: 140px" @change="load">
          <el-option label="抖音" value="douyin" />
          <el-option label="小红书" value="xiaohongshu" />
        </el-select>
        <el-select
          v-model="accountId"
          placeholder="全部账号"
          clearable
          style="width: 200px"
          @change="load"
        >
          <el-option
            v-for="acc in accountOptions"
            :key="acc.id"
            :label="accountOptionLabel(acc)"
            :value="acc.id"
          />
        </el-select>
        <el-button size="small" :loading="loading" @click="load">筛选</el-button>
      </div>

      <div v-if="listError" class="state-block">
        <p class="fail-msg">{{ listError }}</p>
        <el-button size="small" :loading="loading" @click="load">重试</el-button>
      </div>
      <div v-else-if="loading && !rows.length" class="state-inline">加载中…</div>
      <div v-else-if="!rows.length" class="state-block">
        <p>没有联系人。断开账号的人不会出现在默认聚合里；刷新后仍在的是已连接账号的备注。</p>
      </div>
      <div v-else class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>平台</th>
              <th>来源</th>
              <th>昵称</th>
              <th>备注</th>
              <th>标签</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id">
              <td>{{ platformLabel(row.platform) }}</td>
              <td>{{ sourceLabel(row.data_source) }}</td>
              <td>{{ row.nickname }}</td>
              <td>
                <el-input v-model="row.draftRemark" size="small" placeholder="备注" />
              </td>
              <td>
                <el-select
                  v-model="row.draftTags"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  collapse-tags
                  collapse-tags-tooltip
                  placeholder="选择标签"
                  size="small"
                  style="min-width: 160px"
                >
                  <el-option v-for="tag in TAG_OPTIONS" :key="tag" :label="tag" :value="tag" />
                </el-select>
              </td>
              <td class="ops">
                <el-button size="small" :loading="row.saving" @click="onSave(row)">保存</el-button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import AppLayout from '../layouts/AppLayout.vue'
import {
  socialApi,
  type SocialAccount,
  type SocialContact,
  type SocialPlatform,
} from '../socialApi'

type ContactRow = SocialContact & {
  draftRemark: string
  draftTags: string[]
  saving: boolean
}

const TAG_OPTIONS = ['待跟进', '意向客户', '已询价', '高意向', '橱窗关注', '直播间', '样品', '复购']

const keyword = ref('')
const platform = ref<SocialPlatform | ''>('')
const accountId = ref<number | ''>('')
const accountOptions = ref<SocialAccount[]>([])
const rows = ref<ContactRow[]>([])
const loading = ref(false)
const listError = ref('')

function asTags(tags: unknown): string[] {
  if (!Array.isArray(tags)) return []
  return tags.map((t) => String(t)).filter((t) => t.length > 0)
}

function toRow(item: SocialContact): ContactRow {
  return {
    ...item,
    draftRemark: item.remark || '',
    draftTags: asTags(item.tags),
    saving: false,
  }
}

function platformLabel(value?: string) {
  if (value === 'douyin') return '抖音'
  if (value === 'xiaohongshu') return '小红书'
  return value || '—'
}

function sourceLabel(source?: string) {
  if (source === 'sample') return '样例'
  return source || '—'
}

function accountOptionLabel(acc: SocialAccount) {
  const name = acc.display_name || acc.external_account_id
  return `${platformLabel(acc.platform)} · ${name}`
}

function httpStatus(err: unknown): number | undefined {
  if (typeof err === 'object' && err && 'status' in err) {
    const s = (err as { status: unknown }).status
    if (typeof s === 'number') return s
  }
  return undefined
}

function failText(err: unknown, fallback: string) {
  if (httpStatus(err) === 404) return '接口尚未就绪，请稍后刷新'
  return err instanceof Error ? err.message : fallback
}

async function loadAccounts() {
  try {
    const data = await socialApi.listAccounts()
    accountOptions.value = data.items.filter((item) => item.status === 'connected')
  } catch {
    accountOptions.value = []
  }
}

async function load() {
  loading.value = true
  listError.value = ''
  try {
    const data = await socialApi.listContacts({
      q: keyword.value.trim() || undefined,
      platform: platform.value || undefined,
      account_id: accountId.value === '' ? undefined : accountId.value,
    })
    rows.value = data.items.map(toRow)
  } catch (err) {
    rows.value = []
    listError.value = failText(err, '加载联系人失败')
  } finally {
    loading.value = false
  }
}

async function onSave(row: ContactRow) {
  row.saving = true
  try {
    const fresh = await socialApi.patchContact(row.id, {
      remark: row.draftRemark.trim() || null,
      tags: row.draftTags,
    })
    const next = toRow(fresh)
    const idx = rows.value.findIndex((item) => item.id === row.id)
    if (idx >= 0) rows.value.splice(idx, 1, next)
    ElMessage.success('已保存')
  } catch (err) {
    ElMessage.error(failText(err, '保存失败'))
  } finally {
    row.saving = false
  }
}

onMounted(async () => {
  await loadAccounts()
  await load()
})
</script>

<style scoped>
.contacts-page {
  padding: 20px;
}

.fail-msg {
  color: var(--danger);
}
</style>
