// Real Chromium clicks against the isolated public copy. Never stubs /api.
import { chromium } from 'playwright-core'
import fs from 'node:fs'
import path from 'node:path'

const phase = process.argv[2]
if (!['edit', 'finish', 'capture'].includes(phase)) throw new Error('usage: browser_flow.mjs edit|finish|capture')
const root = path.resolve(import.meta.dirname, '../../..')
const screenshots = path.join(root, 'docs', 'screenshots')
const runtime = path.join(root, 'deploy', 'runtime')
const campaignId = JSON.parse(fs.readFileSync(path.join(runtime, 'public-flow-state.json'), 'utf8')).campaign_id
fs.mkdirSync(screenshots, { recursive: true })
fs.mkdirSync(runtime, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
let externalRequests = 0
let pageErrors = 0
let apiErrors = 0

async function pageAt(width, height) {
  const context = await browser.newContext({ viewport: { width, height }, acceptDownloads: true })
  const page = await context.newPage()
  await page.route('**/*', async route => {
    const url = route.request().url()
    if (url.startsWith('http://127.0.0.1:18260/') || url.startsWith('blob:') || url.startsWith('data:')) {
      await route.continue()
    } else {
      externalRequests++
      await route.abort()
    }
  })
  page.on('pageerror', () => { pageErrors++ })
  page.on('response', response => {
    if (response.url().includes('/api/') && response.status() >= 400) apiErrors++
  })
  await page.goto('http://127.0.0.1:18260/login', { waitUntil: 'networkidle' })
  await page.getByPlaceholder('账号').fill('demo')
  await page.getByPlaceholder('密码').fill('demo123')
  await page.getByRole('button', { name: '登录并继续' }).click()
  await page.waitForURL('**/workbench')
  return { page, context }
}

async function checkOverflow(page, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  if (overflow > 1) throw new Error(`${label} horizontal overflow: ${overflow}px`)
  return overflow
}

try {
  if (phase === 'edit') {
    const { page, context } = await pageAt(1440, 900)
    await page.getByRole('link', { name: '运营总览' }).click()
    await page.waitForURL('**/operations')
    await page.getByRole('link', { name: new RegExp(`活动 ${campaignId} · 抖音 · v2`) }).first().click()
    await page.waitForURL(`**/campaigns/${campaignId}**`)
    await page.getByRole('link', { name: '编辑与审核' }).first().click()
    await page.waitForURL('**/review**')
    const side = page.locator('#review-douyin')
    await side.getByRole('textbox').first().fill('帆布手提袋 · 出门随行')
    await side.getByRole('button', { name: '保存为新版本' }).click()
    await side.getByText('当前版本 3').waitFor()
    console.log(`ui_edit_ok campaign=${campaignId} platform=douyin version=3`)
    await context.close()
  } else if (phase === 'finish') {
    const { page, context } = await pageAt(1440, 900)
    await page.goto(`http://127.0.0.1:18260/review?campaign_id=${campaignId}&platform=douyin&version=3`, { waitUntil: 'networkidle' })
    const side = page.locator('#review-douyin')
    await side.getByText('当前版本 3').waitFor()
    await side.getByRole('button', { name: '通过', exact: true }).click()
    await side.getByText('当前版本 3 · approved').waitFor()
    await page.getByRole('link', { name: '运营总览' }).click()
    await page.waitForURL('**/operations')
    await page.getByText('已审待交付').first().waitFor()
    await page.screenshot({ path: path.join(screenshots, 'public-flow-overview-desktop.png') })
    await page.getByRole('link', { name: new RegExp(`活动 ${campaignId} · 抖音 · v3`) }).first().click()
    await page.waitForURL(`**/campaigns/${campaignId}**`)
    await page.getByText('合成示例·帆布手提袋').first().waitFor()
    await page.screenshot({ path: path.join(screenshots, 'public-flow-detail-desktop.png') })
    await page.getByRole('link', { name: '编辑与审核' }).first().click()
    await page.waitForURL('**/review**')
    await page.getByText('连接状态：待连接').first().waitFor()
    const exportButton = page.locator('#review-douyin').getByRole('button', { name: '导出当前已审核版本' })
    await exportButton.scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(screenshots, 'public-flow-delivery-desktop.png') })
    const downloadPromise = page.waitForEvent('download')
    await exportButton.click()
    const download = await downloadPromise
    const filename = download.suggestedFilename()
    if (!filename.endsWith('douyin-v3.zip')) throw new Error(`unexpected download filename: ${filename}`)
    const target = path.join(runtime, filename)
    await download.saveAs(target)
    if (fs.statSync(target).size < 1024) throw new Error('download is unexpectedly small')
    console.log(`ui_delivery_ok filename=${filename} bytes=${fs.statSync(target).size}`)
    await context.close()

    const mobile = await pageAt(390, 844)
    const m = mobile.page
    await m.goto('http://127.0.0.1:18260/operations', { waitUntil: 'networkidle' })
    await m.getByText('已审待交付').first().waitFor()
    await m.screenshot({ path: path.join(screenshots, 'public-flow-overview-narrow.png') })
    await m.getByRole('link', { name: new RegExp(`活动 ${campaignId} · 抖音 · v3`) }).first().click()
    await m.waitForURL(`**/campaigns/${campaignId}**`)
    await m.screenshot({ path: path.join(screenshots, 'public-flow-detail-narrow.png') })
    await m.getByRole('link', { name: '编辑与审核' }).first().click()
    await m.waitForURL('**/review**')
    await m.locator('#review-douyin').getByRole('button', { name: '导出当前已审核版本' }).scrollIntoViewIfNeeded()
    await m.screenshot({ path: path.join(screenshots, 'public-flow-delivery-narrow.png') })
    const overflow = await m.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    if (overflow > 1) throw new Error(`mobile horizontal overflow: ${overflow}px`)
    console.log(`ui_mobile_ok overflow_px=${overflow}`)
    await mobile.context.close()
  } else {
    for (const [label, width, height] of [['desktop', 1440, 900], ['narrow', 390, 844]]) {
      const { page, context } = await pageAt(width, height)
      await page.goto('http://127.0.0.1:18260/operations', { waitUntil: 'networkidle' })
      await page.getByRole('link', { name: new RegExp(`活动 ${campaignId} · 抖音 · v3`) }).first().waitFor()
      await page.evaluate(() => window.scrollTo(0, 0))
      await page.locator('.el-message').first().waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {})
      await checkOverflow(page, `${label} overview`)
      await page.screenshot({ path: path.join(screenshots, `public-flow-overview-${label}.png`) })

      await page.goto(`http://127.0.0.1:18260/campaigns/${campaignId}?platform=douyin&version=3`, { waitUntil: 'networkidle' })
      await page.getByRole('heading', { name: `活动 ${campaignId}` }).waitFor()
      await page.getByText('合成示例·帆布手提袋').first().waitFor()
      await page.evaluate(() => window.scrollTo(0, 0))
      await checkOverflow(page, `${label} detail`)
      await page.screenshot({ path: path.join(screenshots, `public-flow-detail-${label}.png`) })

      await page.goto(`http://127.0.0.1:18260/review?campaign_id=${campaignId}&platform=douyin&version=3`, { waitUntil: 'networkidle' })
      const exportButton = page.locator('#review-douyin').getByRole('button', { name: '导出当前已审核版本' })
      await exportButton.scrollIntoViewIfNeeded()
      const overflow = await checkOverflow(page, `${label} delivery`)
      await page.screenshot({ path: path.join(screenshots, `public-flow-delivery-${label}.png`) })
      console.log(`ui_capture_ok viewport=${label} overflow_px=${overflow}`)
      await context.close()
    }
  }
  if (externalRequests || pageErrors || apiErrors) {
    throw new Error(`browser errors external=${externalRequests} page=${pageErrors} api=${apiErrors}`)
  }
  console.log(`ui_guard_ok external=${externalRequests} page_errors=${pageErrors} api_errors=${apiErrors}`)
} finally {
  await browser.close()
}
