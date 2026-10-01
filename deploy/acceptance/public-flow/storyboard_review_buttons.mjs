// Render the real Vue review page in Chrome with fixture API responses only.
// Run: node deploy/acceptance/public-flow/storyboard_review_buttons.mjs
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import path from 'node:path'
import { chromium } from 'playwright-core'

const root = path.resolve(import.meta.dirname, '../../..')
const origin = 'http://127.0.0.1:18473'
const vite = spawn(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '18473', '--strictPort'], {
  cwd: path.join(root, 'frontend'), stdio: 'ignore', windowsHide: true,
})
let browser
let page
const unexpectedRequests = []
const pageErrors = []
const submitted = []
let holdPost = false
let releasePost
let notifyHeldPost

const shot = (frameId, videoId, overrides = {}) => ({
  prompt: '镜头描述', first_frame_task_id: frameId, first_frame_asset_id: null,
  first_frame_review: null, video_task_id: videoId, video_asset_id: null,
  video_review: null, locked: false, ...overrides,
})
const review = {
  campaign_id: 7, status: 'needs_review', fact_version_id: 1, fact_version: 1,
  facts: {}, product_name: '测试商品', primary_asset_id: null, generation_budget: 10,
  budget_reserved: 0, budget_remaining: 10, provider_ready: { image: true, video: true },
  ark_live: '未启用', platforms: [{
    platform: 'douyin', current: {
      id: 8, platform: 'douyin', version: 3, title: '测试标题', body: '测试正文',
      hashtags: [], status: 'needs_review', fact_version_id: 1, qc_result: null, assets: [],
      storyboard: { mode: 'reviewed_shots_v1', product_asset_id: 1, fact_version_id: 1,
        shots: [shot(11, null)] },
    },
    versions: [], reviews: [], blockers: [], steps: [],
    storyboard_tasks: { '11': { status: 'succeeded' } },
  }],
}
const buttonNames = ['首帧通过', '首帧退回', '视频通过', '视频退回']
const side = () => review.platforms[0]
const buttons = index => page.locator('#review-douyin .story-shot').nth(index)

async function waitForServer() {
  for (let attempt = 0; attempt < 80; attempt++) {
    if (vite.exitCode !== null) throw new Error(`Vite exited: ${vite.exitCode}`)
    try { if ((await fetch(origin)).ok) return } catch { /* starting */ }
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  throw new Error('Vite did not start')
}
async function reload() {
  await page.reload()
  await buttons(0).getByRole('button', { name: '首帧通过' }).waitFor()
}
async function expectButtons(frameEnabled, videoEnabled) {
  for (const [index, name] of buttonNames.entries()) {
    const button = buttons(0).getByRole('button', { name, exact: true })
    const enabled = index < 2 ? frameEnabled : videoEnabled
    assert.equal(await button.isEnabled(), enabled, `${name} enabled=${enabled}`)
  }
}
async function clickAndCheck(name, action, accepted) {
  const response = page.waitForResponse(reply => reply.url().endsWith(`/storyboard/shots/0/${action}`) && reply.request().method() === 'POST')
  await buttons(0).getByRole('button', { name, exact: true }).click()
  await response
  assert.deepEqual(submitted.at(-1), {
    path: `/api/campaigns/7/storyboard/shots/0/${action}`, method: 'POST',
    ifMatch: '3', body: { expected_version: 3, accepted },
  })
}

try {
  await waitForServer()
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  page = await browser.newPage()
  page.on('pageerror', error => pageErrors.push(error.message))
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) {
      unexpectedRequests.push(url.href)
      return route.abort()
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const json = body => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (url.pathname === '/api/auth/me') return json({ id: 1, username: 'fixture', role: 'user' })
    if (url.pathname === '/api/notifications') return json({ items: [] })
    if (url.pathname === '/api/campaigns') return json({ items: [{ id: 7, status: 'needs_review', product_id: 1 }] })
    if (url.pathname === '/api/campaigns/7/review') return json(review)
    if (url.pathname === '/api/campaigns/7/publish') return json({ platforms: [], jobs: [], timezone_storage: 'UTC', campaign_status: 'needs_review' })
    if (/^\/api\/campaigns\/7\/storyboard\/shots\/0\/(review_frame|review_video)$/.test(url.pathname)) {
      submitted.push({ path: url.pathname, method: route.request().method(),
        ifMatch: route.request().headers()['if-match'], body: route.request().postDataJSON() })
      if (holdPost) await new Promise(resolve => { releasePost = resolve; notifyHeldPost() })
      return json({ id: 8, version: 3 })
    }
    unexpectedRequests.push(url.href)
    return route.abort()
  })
  await page.goto(`${origin}/review?campaign_id=7&platform=douyin&version=3`)
  await buttons(0).getByText('首帧：待人工审核').waitFor()
  await expectButtons(true, false)
  await clickAndCheck('首帧通过', 'review_frame', true)
  await clickAndCheck('首帧退回', 'review_frame', false)

  side().current.storyboard.shots[0].first_frame_review = 'approved'
  side().current.storyboard.shots[0].video_task_id = 21
  side().storyboard_tasks['21'] = { status: 'succeeded' }
  await reload()
  await buttons(0).getByText('视频：待人工审核').waitFor()
  await expectButtons(false, true)
  await clickAndCheck('视频通过', 'review_video', true)
  await clickAndCheck('视频退回', 'review_video', false)
  side().current.storyboard.shots[0].locked = true
  await reload()
  await expectButtons(false, false)
  side().current.storyboard.shots[0].locked = false
  await page.goto(`${origin}/review?campaign_id=7&platform=douyin&version=2`)
  await buttons(0).getByRole('button', { name: '视频通过' }).waitFor()
  await expectButtons(false, false)
  await page.goto(`${origin}/review?campaign_id=7&platform=douyin&version=3`)
  await buttons(0).getByRole('button', { name: '视频通过' }).waitFor()
  holdPost = true
  let heldPost = new Promise(resolve => { notifyHeldPost = resolve })
  let click = buttons(0).getByRole('button', { name: '视频通过' }).click()
  await Promise.race([heldPost, new Promise((_, reject) => setTimeout(() => reject(new Error('video POST was not held')), 5000))])
  await expectButtons(false, false)
  releasePost()
  holdPost = false
  await click
  releasePost = null

  for (const status of ['queued', 'running', 'unknown', 'failed', 'cancelled']) {
    side().current.storyboard.shots[0].first_frame_review = null
    side().current.storyboard.shots[0].video_task_id = null
    side().storyboard_tasks['11'] = { status }
    await reload()
    await expectButtons(false, false)
    side().current.storyboard.shots[0].first_frame_review = 'approved'
    side().current.storyboard.shots[0].video_task_id = 21
    side().storyboard_tasks['11'] = { status: 'succeeded' }
    side().storyboard_tasks['21'] = { status }
    await reload()
    await expectButtons(false, false)
  }
  side().current.storyboard.shots[0].first_frame_review = null
  side().current.storyboard.shots[0].video_task_id = null
  delete side().storyboard_tasks['11']
  await reload()
  await expectButtons(false, false) // task id present but task invisible
  side().current.storyboard.shots[0].first_frame_task_id = null
  await reload()
  await expectButtons(false, false) // task id absent
  side().current.storyboard.shots[0].first_frame_task_id = 11
  side().storyboard_tasks['11'] = { status: 'succeeded' }
  side().current.storyboard.shots[0].first_frame_review = 'approved'
  side().current.storyboard.shots[0].video_task_id = 21
  delete side().storyboard_tasks['21']
  await reload()
  await expectButtons(false, false) // video task invisible
  side().current.storyboard.shots[0].video_task_id = null
  await reload()
  await expectButtons(false, false) // video task id absent
  side().current.storyboard.shots[0].first_frame_review = null
  side().current.storyboard.shots[0].locked = true
  await reload()
  await expectButtons(false, false)
  side().current.storyboard.shots[0].locked = false
  side().current.storyboard.shots[0].first_frame_review = 'approved'
  side().current.storyboard.shots[0].video_task_id = 21
  side().storyboard_tasks['21'] = { status: 'succeeded' }
  side().current.storyboard.shots[0].video_review = 'rejected'
  await reload()
  await expectButtons(false, false)
  side().current.storyboard.shots[0].first_frame_review = 'rejected'
  side().current.storyboard.shots[0].video_task_id = null
  side().current.storyboard.shots[0].video_review = null
  await reload()
  await expectButtons(false, false)
  side().current.storyboard.shots[0].first_frame_review = 'approved'
  side().current.storyboard.shots[0].video_task_id = 21
  side().current.storyboard.shots[0].video_review = 'approved'
  await reload()
  await expectButtons(false, false)
  side().current.storyboard.shots[0].first_frame_review = null
  side().current.storyboard.shots[0].video_review = null
  side().current.storyboard.shots[0].video_task_id = null
  await page.goto(`${origin}/review?campaign_id=7&platform=douyin&version=2`)
  await buttons(0).getByRole('button', { name: '首帧通过' }).waitFor()
  await expectButtons(false, false) // stale version
  await page.goto(`${origin}/review?campaign_id=7&platform=douyin&version=3`)
  await buttons(0).getByRole('button', { name: '首帧通过' }).waitFor()
  holdPost = true
  heldPost = new Promise(resolve => { notifyHeldPost = resolve })
  click = buttons(0).getByRole('button', { name: '首帧通过' }).click()
  await Promise.race([heldPost, new Promise((_, reject) => setTimeout(() => reject(new Error('POST was not held')), 5000))])
  await expectButtons(false, false) // action in flight
  releasePost()
  holdPost = false
  await click
  releasePost = null
  assert.deepEqual(unexpectedRequests, [])
  assert.deepEqual(pageErrors, [])
  console.log('storyboard_review_buttons: PASS (rendered Vue, mocked /api, no backend)')
} finally {
  if (releasePost) releasePost()
  if (browser) await browser.close()
  vite.kill()
  if (vite.exitCode === null) await Promise.race([once(vite, 'exit'), new Promise(resolve => setTimeout(resolve, 3000))])
}
