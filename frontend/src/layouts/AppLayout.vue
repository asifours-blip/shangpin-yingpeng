<template>
  <div class="studio-shell">
    <header class="studio-header">
      <router-link class="brand" to="/operations"><span>商品影棚</span><small>运营工作台</small></router-link>
      <nav v-if="!isFrozen" id="studio-navigation" class="nav" :class="{ open: navOpen }" aria-label="主导航" @keydown.esc="navOpen = false">
        <router-link v-for="item in navigation" :key="item.to" :to="item.to" @click="navOpen = false">{{ item.label }}</router-link>
      </nav>
      <div v-else class="nav"></div>
      <div class="user-slot">
        <span class="name">{{ username || '…' }}</span>
        <button v-if="!isFrozen" class="menu-toggle" type="button" aria-controls="studio-navigation" :aria-expanded="navOpen" :aria-label="navOpen ? '关闭导航' : '打开导航'" @click="navOpen = !navOpen"><span>{{ navOpen ? '关闭' : '菜单' }}</span><span aria-hidden="true">{{ navOpen ? '×' : '☰' }}</span></button>
        <el-button size="small" @click="onLogout">退出</el-button>
      </div>
    </header>
    <div v-if="!isFrozen && pendingNotices.length" class="notice-banner" role="status">
      <p class="notice-summary">有 {{ pendingNotices.length }} 条待处理通知</p>
      <ul class="notice-list">
        <li v-for="item in pendingNotices" :key="item.id" class="notice-item">
          <span class="notice-reason">{{ item.reason || '（无原因）' }}</span>
          <el-button
            size="small"
            type="primary"
            :loading="handlingId === item.id"
            :disabled="handlingId != null && handlingId !== item.id"
            @click="onGoHandle(item)"
          >
            去处理
          </el-button>
        </li>
      </ul>
    </div>
    <main class="studio-main" :class="{ wide: wide }">
      <slot />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, setOnAccountFrozen, WORKBENCH_FILL_KEY, type RevisionNotice } from '../api'

withDefaults(
  defineProps<{
    wide?: boolean
  }>(),
  { wide: false },
)

const router = useRouter()
const route = useRoute()
const username = ref('')
const isAdmin = ref(false)
const isFrozen = ref(false)
const notices = ref<RevisionNotice[]>([])
const handlingId = ref<number | null>(null)
const navOpen = ref(false)
const navigation = computed(() => [
  { to: '/operations', label: '运营总览' },
  { to: '/trends', label: '来源' },
  { to: '/products', label: '商品库' },
  { to: '/review', label: '审核' },
  ...(!isAdmin.value ? [{ to: '/social', label: '社交账号' }, { to: '/publish-accounts', label: '发布账号' }, { to: '/contacts', label: '联系人' }] : []),
  { to: '/workbench', label: '原工作台' },
  { to: '/history', label: '历史' },
  ...(isAdmin.value ? [{ to: '/admin/users', label: '用户管理' }, { to: '/admin/records', label: '生成记录' }] : []),
])

const pendingNotices = computed(() => notices.value.filter((item) => item.status === 'pending'))
watch(() => route.fullPath, () => { navOpen.value = false })

function errorStatus(err: unknown): number | undefined {
  if (!err || typeof err !== 'object' || !('status' in err)) return undefined
  const status = (err as { status: unknown }).status
  return typeof status === 'number' ? status : undefined
}

function goRestricted() {
  isFrozen.value = true
  notices.value = []
  if (route.path !== '/restricted') {
    void router.replace('/restricted')
  }
}

function onAccountFrozen() {
  goRestricted()
}

async function loadNotifications() {
  if (isFrozen.value) return
  try {
    const res = await api.listNotifications()
    notices.value = Array.isArray(res?.items) ? res.items : []
  } catch {
    /* 焦点轮询失败不打断页面 */
  }
}

function onWindowFocus() {
  if (isFrozen.value) return
  void loadNotifications()
}

async function onGoHandle(notice: RevisionNotice) {
  if (handlingId.value != null) return
  handlingId.value = notice.id
  try {
    try {
      await api.handleNotification(notice.id)
      notices.value = notices.value.filter((item) => item.id !== notice.id)
    } catch (err) {
      ElMessage.error(err instanceof Error ? err.message : '标记已处理失败')
    }
    await router.push({ path: '/history', query: { review: 'needs_revision' } })
  } finally {
    handlingId.value = null
  }
}

onMounted(async () => {
  setOnAccountFrozen(() => goRestricted())
  window.addEventListener('account-frozen', onAccountFrozen)
  try {
    const me = await api.me()
    username.value = me.username
    isAdmin.value = me.role === 'admin'
    if (me.is_frozen) {
      goRestricted()
      return
    }
    window.addEventListener('focus', onWindowFocus)
    await loadNotifications()
  } catch (err) {
    if (isFrozen.value || errorStatus(err) === 403) {
      goRestricted()
      return
    }
    router.replace('/login')
  }
})

onUnmounted(() => {
  setOnAccountFrozen(null)
  window.removeEventListener('account-frozen', onAccountFrozen)
  window.removeEventListener('focus', onWindowFocus)
})

async function onLogout() {
  try {
    sessionStorage.removeItem(WORKBENCH_FILL_KEY)
  } catch {
    /* ignore */
  }
  await api.logout()
  router.replace('/login')
}
</script>

<style scoped>
.notice-banner {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 1280px;
  margin: 16px auto 0;
  padding: 12px 16px;
  border: 1px solid rgba(255, 255, 255, .68);
  border-radius: var(--radius-lg);
  background: var(--glass-strong);
  box-shadow: 0 10px 30px rgba(32, 42, 54, .08);
  backdrop-filter: blur(16px) saturate(145%);
  box-sizing: border-box;
}

.notice-summary {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.notice-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.notice-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.notice-reason {
  min-width: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}
</style>
