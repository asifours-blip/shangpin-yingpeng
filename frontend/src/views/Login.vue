<template>
  <div class="login-wrap">
    <div class="login-wash" aria-hidden="true"></div>
    <div class="login-stage">
      <div class="login-brand">商品影棚 <span>运营工作台</span></div>
      <div class="login-copy">
        <p class="login-eyebrow">商品内容运营</p>
        <h1 class="login-title"><span>从商品出发。</span><span>让内容落地。</span></h1>
        <p class="sub">把真实商品资料串成内容、审核与交付。每一步都能看见依据和下一步。</p>
        <div class="login-actions"><a class="hero-cta hero-cta-dark" href="#login-form">登录工作台</a><button ref="workflowButton" class="hero-cta hero-cta-light" type="button" aria-haspopup="dialog" :aria-expanded="workflowOpen" @click="workflowOpen = true">查看流程</button></div>
        <p class="workflow-note">来源参考 → 自家商品 → 生成 → 人工审核 → 交付</p>
      </div>
      <div id="login-form" class="login-card">
        <div class="login-card-head"><h2>进入工作台</h2><p>使用已有账号继续处理活动。</p></div>
        <el-form class="login-form" @submit.prevent="onSubmit">
          <el-form-item label="账号" :error="userError">
            <el-input
              v-model="username"
              placeholder="账号"
              autocomplete="username"
              size="large"
            />
          </el-form-item>
          <el-form-item label="密码" :error="passError">
            <el-input
              v-model="password"
              type="password"
              placeholder="密码"
              autocomplete="current-password"
              show-password
              size="large"
            />
          </el-form-item>
          <el-button
            type="primary"
            class="login-submit"
            style="width: 100%"
            size="large"
            :loading="loading"
            native-type="submit"
          >
            登录并继续
          </el-button>
        </el-form>
      </div>
    </div>
    <el-dialog v-model="workflowOpen" class="login-workflow-dialog" width="560px" title="从资料到交付" :close-on-press-escape="true" @closed="restoreWorkflowFocus">
      <template #header><h2 class="workflow-dialog-title">从资料到交付</h2></template>
      <p class="workflow-intro">五步把一次活动推进到可交付的内容。</p>
      <ol class="workflow-list">
        <li><strong>选择来源参考</strong><span>可选。来源未连接时，也能直接用自家商品创建活动。</span></li>
        <li><strong>确认自家商品与事实</strong><span>选择商品，只用已确认的资料；未确认属性不会写成确定卖点。</span></li>
        <li><strong>设置并启动生成</strong><span>选择目标平台、创作要求与预算，查看各平台的生成进度。</span></li>
        <li><strong>人工编辑与审核</strong><span>核对文案、媒体和事实；修改后的新版本需要重新审核。</span></li>
        <li><strong>导出或安排发布</strong><span>已审核素材可以导出。发布需要平台连接与权限，不会自动发布。</span></li>
      </ol>
      <template #footer><el-button type="primary" @click="workflowOpen = false">关闭流程</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const userError = ref('')
const passError = ref('')
const loading = ref(false)
const workflowOpen = ref(false)
const workflowButton = ref<HTMLButtonElement | null>(null)

function restoreWorkflowFocus() { workflowButton.value?.focus() }

async function onSubmit() {
  userError.value = ''
  passError.value = ''
  if (!username.value.trim()) {
    userError.value = '请输入账号'
    return
  }
  if (!password.value) {
    passError.value = '请输入密码'
    return
  }
  loading.value = true
  try {
    const me = await api.login(username.value.trim(), password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/workbench'
    if (me.is_frozen) {
      router.replace('/restricted')
      return
    }
    router.replace(redirect)
  } catch (err) {
    passError.value = err instanceof Error ? err.message : '登录失败'
  } finally {
    loading.value = false
  }
}
</script>
