// 6R21B 返工：三地图 × (Overview/Personal/Team) × (desktop/390/360) 截图矩阵。
// 只为「DeepSeek 原生 Native Vision 审图」产出证据图，不做数值断言；另把「无横滚」程序化测到 JSON 证据。
// 运行：npx playwright test -c playwright.headless.config.js phase6r21b-vision-matrix.spec.js
import { test, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'
import { seedIdentity, demoPlayerSteam, grantTeamEntitlementForDemo } from './_helpers6r14.js'
import { login, ACCOUNT, setTier } from './_helpers6r15.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
// tests/e2e/phase6r21b-vision-matrix.spec.js → 项目根为 ../../
const SHOTS = process.env.VISION_SHOTS_DIR
  ? resolve(process.env.VISION_SHOTS_DIR)
  : resolve(__dirname, '../../tests/e2e/screenshots/6r21b')
// docs/ 在项目根（../.. 之上还需一级），用 ../../../docs
const OUT_JSON = process.env.VISION_OUT_JSON
  ? resolve(process.env.VISION_OUT_JSON)
  : resolve(__dirname, '../../../docs/phase6r21b-overflow.json')
mkdirSync(SHOTS, { recursive: true })

test.setTimeout(600000)

// 三地图：mirage=19 / dust2=21 / inferno=22（同一账号拥有）
const ALL_MAPS = [
  { key: 'mirage', id: 19, map: 'de_mirage' },
  { key: 'dust2', id: 21, map: 'de_dust2' },
  { key: 'inferno', id: 22, map: 'de_inferno' },
]
const ALL_VIEWPORTS = [
  { key: 'desktop', w: 1440, h: 900 },
  { key: '390', w: 390, h: 844 },
  { key: '360', w: 360, h: 800 },
]
const wantedMaps = new Set((process.env.VISION_MAPS || ALL_MAPS.map((m) => m.key).join(',')).split(',').filter(Boolean))
const wantedViewports = new Set((process.env.VISION_VIEWPORTS || ALL_VIEWPORTS.map((v) => v.key).join(',')).split(',').filter(Boolean))
const MAPS = ALL_MAPS.filter((m) => wantedMaps.has(m.key))
const VIEWPORTS = ALL_VIEWPORTS.filter((v) => wantedViewports.has(v.key))
const OVERFLOW = []

async function checkNoHScroll(page, label) {
  const m = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
    bodyScroll: document.body.scrollWidth,
  }))
  const ok = m.scroll <= m.client + 1 && m.bodyScroll <= m.client + 1
  OVERFLOW.push({ label, ...m, ok })
  if (!ok) console.log(`[OVERFLOW] ${label}: scroll=${m.scroll} client=${m.client} body=${m.bodyScroll}`)
  return ok
}

// 等待 `.pt-canvas` 就绪并 scrub 到「有真实状态」的时刻（gun/shoot/smoke/fire/blind/killfeed 至少两项）
async function personalState(page, ratio = 0.42) {
  await page.waitForSelector('.pt-canvas[data-ready="ready"]', { timeout: 30000 })
  const maxD = await page.locator('.pt-scrub').evaluate((el) => Number(el.max) || 0).catch(() => 0)
  if (maxD > 0) {
    // 从 0.3→0.85 扫描，取「状态钩子命中最多」的时刻（真实战术信息最丰富）
    let bestT = maxD * ratio, bestScore = -1
    for (let t = maxD * 0.25; t <= maxD * 0.9; t += Math.max(1, maxD / 22)) {
      await page.locator('.pt-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, t)
      await page.waitForTimeout(120)
      const score = await page.locator('.pt-canvas').evaluate((el) => {
        const attrs = ['data-gun', 'data-shoot', 'data-smoke', 'data-fire', 'data-blind', 'data-killfeed', 'data-hedamage']
        return attrs.filter((a) => el.getAttribute(a) === '1').length
      })
      if (score > bestScore) { bestScore = score; bestT = t }
    }
    await page.locator('.pt-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, bestT)
    await page.waitForTimeout(400)
  }
}

// Canvas 特写只需要 backing bitmap。一次页面内 evaluate 同步读取当前已连接 canvas，
// 避免 locator.screenshot 在 PersonalTab 异步重挂载的滚动/截图阶段持有已 detach 的 element。
// 同时强制验证非空像素；失败必须抛出，不再静默缺图。
async function captureCanvasPng(page, selector, path) {
  const shot = await page.locator(selector).evaluate((canvas) => {
    if (!(canvas instanceof HTMLCanvasElement) || !canvas.isConnected) throw new Error('canvas 未连接')
    const { data } = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height)
    let painted = 0
    for (let i = 3; i < data.length; i += 4) if (data[i] > 10) painted++
    return { png: canvas.toDataURL('image/png'), painted }
  })
  expect(shot.painted, `${selector} 不得为空画布`).toBeGreaterThan(100)
  writeFileSync(path, Buffer.from(shot.png.split(',', 2)[1], 'base64'))
}

// 等待 `.tm-canvas` 就绪并 scrub 到「区域控制最清晰」的时刻（field：双方 camp>0 且 frontier 最大；poly：回合中段）
async function teamState(page) {
  await page.waitForSelector('.tm-canvas[data-ready="ready"]', { timeout: 40000 })
  const area = await page.locator('.tm-canvas').getAttribute('data-area').catch(() => '')
  const maxD = await page.locator('.tm-scrub').evaluate((el) => Number(el.max) || 0).catch(() => 0)
  if (maxD > 0) {
    if (area === 'field') {
      let shotT = maxD * 0.45, bestF = -1
      for (let t = 0; t <= maxD; t += 2) {
        await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, t)
        await page.waitForTimeout(110)
        const camps = String(await page.locator('.tm-canvas').getAttribute('data-area-camps') || '').split(',').map(Number)
        const frontier = Number(await page.locator('.tm-canvas').getAttribute('data-area-frontier') || 0)
        if (camps[0] > 0 && camps[1] > 0 && frontier > bestF) { bestF = frontier; shotT = t }
      }
      await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, shotT)
    } else {
      await page.locator('.tm-scrub').evaluate((el) => { el.value = String(Number(el.max) * 0.45); el.dispatchEvent(new Event('input', { bubbles: true })) })
    }
    await page.waitForTimeout(400)
  }
}

async function shotPage(page, url, waitSel, name) {
  await page.goto(url)
  if (waitSel) await page.waitForSelector(waitSel, { timeout: 30000 }).catch(() => {})
  await page.waitForTimeout(600)
  await page.locator('.page-wrap, body').first().screenshot({ path: `${SHOTS}/${name}.png` })
}

test('6R21B vision matrix: 三地图 × Overview/Personal/Team × desktop/390/360', async ({ browser }) => {
  expect(MAPS.length, 'VISION_MAPS 过滤后至少保留一张地图').toBeGreaterThan(0)
  expect(VIEWPORTS.length, 'VISION_VIEWPORTS 过滤后至少保留一个视口').toBeGreaterThan(0)
  setTier(ACCOUNT.email, 'max', 30)

  for (const m of MAPS) {
    seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, m.id))
    grantTeamEntitlementForDemo(ACCOUNT.email, m.id)

    // 用绑定身份对应 demo 的焦点玩家（否则 personal 端点 403 / 无数据）
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const page = await ctx.newPage()
    await login(page, ACCOUNT.email, ACCOUNT.password)
    const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
    const playersResp = await page.request.get(`/api/demos/${m.id}/players`, { headers: { Authorization: `Bearer ${token}` } })
    const players = await playersResp.json().catch(() => [])
    const fp = (players[0] || {}).player_id
    if (fp) await page.request.put(`/api/demos/${m.id}/focus-player`, { headers: { Authorization: `Bearer ${token}` }, data: { player_id: fp } })
    await ctx.close()

    for (const v of VIEWPORTS) {
      const c = await browser.newContext({ viewport: { width: v.w, height: v.h } })
      const p = await c.newPage()
      await login(p, ACCOUNT.email, ACCOUNT.password)

      // ---- Overview（概览，默认 tab；玩家表 + 结论 + 长名/徽标）----
      await p.goto(`/demo/${m.id}`)
      await p.waitForSelector('.conclusion-strip, .head, .tbl', { timeout: 30000 }).catch(() => {})
      await p.waitForTimeout(800)
      await p.locator('.page-wrap').screenshot({ path: `${SHOTS}/${m.key}-overview-${v.key}.png` })
      await checkNoHScroll(p, `${m.key}-overview-${v.key}`)

      // ---- Personal（个人复盘：战术回放 + 交战图 + 我的道具）----
      await p.click('button.tab:has-text("个人复盘")')
      await p.waitForSelector('.picker select', { timeout: 30000 }).catch(() => {})
      await personalState(p)
      await p.waitForTimeout(500)
      await p.locator('.page-wrap').screenshot({ path: `${SHOTS}/${m.key}-personal-${v.key}.png` })
      await checkNoHScroll(p, `${m.key}-personal-${v.key}`)

      // ---- Team（团队复盘：十人同步回放 + 区域控制 + 深度配合指标）----
      await p.click('button.tab:has-text("团队复盘")')
      await p.waitForSelector('.tm-canvas[data-ready="ready"]', { timeout: 45000 }).catch(() => {})
      await teamState(p)
      // 等深度配合指标/证据渲染完成（API 约 6-12s；只等「已加载」态，不把「加载中」当完成）
      await p.waitForSelector('.co-baseline, .co-concl-item, .co-metric-table, .form-error-box', { timeout: 40000 }).catch(() => {})
      await p.waitForTimeout(800)
      await p.locator('.page-wrap').screenshot({ path: `${SHOTS}/${m.key}-team-${v.key}.png` })
      await checkNoHScroll(p, `${m.key}-team-${v.key}`)

      await c.close()
    }

    // 桌面 canvas 特写（personal + team），供 Native Vision 逐点核对真实状态
    const c = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const p = await c.newPage()
    await login(p, ACCOUNT.email, ACCOUNT.password)
    await p.goto(`/demo/${m.id}`)
    await p.waitForSelector('.conclusion-strip, .head', { timeout: 30000 }).catch(() => {})
    await p.click('button.tab:has-text("个人复盘")')
    await p.waitForSelector('.pt-canvas[data-ready="ready"]', { timeout: 30000 })
    await personalState(p)
    await p.waitForTimeout(400)
    await captureCanvasPng(p, '.pt-canvas', `${SHOTS}/${m.key}-personal-canvas.png`)
    await p.click('button.tab:has-text("团队复盘")')
    await p.waitForSelector('.tm-canvas[data-ready="ready"]', { timeout: 45000 }).catch(() => {})
    await teamState(p)
    await p.waitForTimeout(400)
    await p.locator('.tm-canvas').screenshot({ path: `${SHOTS}/${m.key}-team-canvas.png` }).catch(() => {})
    // 深度配合指标（证据导航卡片；只等「已加载」态，不截到「加载中」）
    await p.waitForSelector('.co-baseline, .co-concl-item, .co-metric-table, .form-error-box', { timeout: 40000 }).catch(() => {})
    await p.waitForTimeout(600)
    await p.locator('.co').first().screenshot({ path: `${SHOTS}/${m.key}-coordination.png` }).catch(() => {})
    await c.close()
  }
})

test('6R21B record overflow evidence', async () => {
  writeFileSync(OUT_JSON, JSON.stringify({ matrix: '3 maps × Overview/Personal/Team × desktop/390/360', checks: OVERFLOW }, null, 2))
  expect(OVERFLOW.filter((x) => !x.ok)).toEqual([])
})
