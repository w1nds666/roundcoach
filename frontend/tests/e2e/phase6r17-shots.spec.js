// 6R17 after 截图矩阵：重构后对 8 类页面 + 三地图核心页生成 1440/390/360 截图，供 DeepSeek Native Vision 审图。
// 运行：npx playwright test -c playwright.headless.config.js phase6r17-shots.spec.js
import { test } from '@playwright/test'
import { seedIdentity, demoPlayerSteam, grantTeamEntitlementForDemo } from './_helpers6r14.js'
import { login, ACCOUNT, setTier } from './_helpers6r15.js'

const SHOTS = 'tests/e2e/screenshots/6r17-after'

async function mapId(page, token, mapName, fallback) {
  const res = await page.request.get('/api/demos?limit=100', { headers: { Authorization: `Bearer ${token}` } }).catch(() => null)
  const list = (await res?.json?.()) || []
  const hit = list.find((d) => d.map_name === mapName && d.status === 'parsed')
  return hit ? hit.id : fallback
}

async function shotPage(page, url, waitSel, name) {
  await page.goto(url)
  if (waitSel) await page.waitForSelector(waitSel, { timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${SHOTS}/${name}.png` })
}

test('after 截图矩阵：Login/Dashboard/Upload/Profile/Coach/Overview/Personal/Team/Share + 三地图', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, 19))

  // --- Login（未登录）---
  await page.context().clearCookies()
  await page.setViewportSize({ width: 1440, height: 900 })
  await shotPage(page, '/login', '.auth-card, .login-wrap, form', 'login-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await shotPage(page, '/login', '.auth-card, .login-wrap, form', 'login-390')
  await page.setViewportSize({ width: 360, height: 780 })
  await shotPage(page, '/login', '.auth-card, .login-wrap, form', 'login-360')

  await login(page, ACCOUNT.email, ACCOUNT.password)

  // --- Dashboard ---
  for (const [w, tag] of [[1440, 'desktop'], [390, '390'], [360, '360']]) {
    await page.setViewportSize({ width: w, height: 900 })
    await shotPage(page, '/', '.page-wrap', `dashboard-${tag}`)
  }

  // --- Upload ---
  for (const [w, tag] of [[1440, 'desktop'], [390, '390'], [360, '360']]) {
    await page.setViewportSize({ width: w, height: 900 })
    await shotPage(page, '/upload', '.dropzone, form', `upload-${tag}`)
  }

  // --- Profile ---
  for (const [w, tag] of [[1440, 'desktop'], [390, '390'], [360, '360']]) {
    await page.setViewportSize({ width: w, height: 900 })
    await shotPage(page, '/profile', '.page-wrap', `profile-${tag}`)
  }

  // --- Coach ---
  for (const [w, tag] of [[1440, 'desktop'], [390, '390'], [360, '360']]) {
    await page.setViewportSize({ width: w, height: 900 })
    await shotPage(page, '/coach', '.page-wrap', `coach-${tag}`)
  }

  // --- 概览（默认 tab；desktop + 390 + 360）---
  await page.setViewportSize({ width: 1440, height: 900 })
  await shotPage(page, '/demo/19', '.conclusion-strip, .head', 'overview-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await shotPage(page, '/demo/19', '.conclusion-strip, .head', 'overview-390')
  await page.setViewportSize({ width: 360, height: 780 })
  await shotPage(page, '/demo/19', '.conclusion-strip, .head', 'overview-360')

  // --- 个人/团队 三地图（mirage/dust2/inferno，动态解析 id）---
  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const maps = [
    ['mirage', await mapId(page, token, 'de_mirage', 19)],
    ['dust2', await mapId(page, token, 'de_dust2', 21)],
    ['inferno', await mapId(page, token, 'de_inferno', 20)],
  ]
  for (const [map, id] of maps) {
    grantTeamEntitlementForDemo(ACCOUNT.email, id)
    const playersResp = await page.request.get(`/api/demos/${id}/players`, { headers: { Authorization: `Bearer ${token}` } })
    const players = await playersResp.json().catch(() => [])
    const fp = (players[0] || {}).player_id
    if (fp) await page.request.put(`/api/demos/${id}/focus-player`, { headers: { Authorization: `Bearer ${token}` }, data: { player_id: fp } })

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`/demo/${id}`)
    await page.waitForSelector('.head', { timeout: 20000 })
    await page.click('button.tab:has-text("个人复盘")')
    await page.waitForSelector('.picker select', { timeout: 20000 }).catch(() => {})
    await page.waitForTimeout(700)
    await page.screenshot({ path: `${SHOTS}/personal-${map}-desktop.png` })
    await page.click('button.tab:has-text("团队复盘")')
    await page.waitForSelector('.tm-canvas[data-ready="ready"]', { timeout: 25000 }).catch(() => {})
    await page.waitForTimeout(700)
    await page.screenshot({ path: `${SHOTS}/team-${map}-desktop.png` })
  }

  // --- 个人/团队移动（mirage）---
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  for (const [w, tag] of [[390, '390'], [360, '360']]) {
    await page.setViewportSize({ width: w, height: 844 })
    await page.goto('/demo/19')
    await page.click('button.tab:has-text("个人复盘")')
    await page.waitForSelector('.picker select', { timeout: 20000 }).catch(() => {})
    await page.waitForTimeout(700)
    await page.screenshot({ path: `${SHOTS}/personal-${tag}.png` })
    await page.click('button.tab:has-text("团队复盘")')
    await page.waitForSelector('.tm-canvas[data-ready="ready"]', { timeout: 25000 }).catch(() => {})
    await page.waitForTimeout(700)
    await page.screenshot({ path: `${SHOTS}/team-${tag}.png` })
  }

  // --- Share（匿名）---
  const shareResp = await page.request.post('/api/demos/19/share', { headers: { Authorization: `Bearer ${token}` }, data: { expires_in_hours: 24 } })
  const shareToken = (await shareResp.json()).token
  for (const [w, tag] of [[1440, 'desktop'], [390, '390'], [360, '360']]) {
    await page.setViewportSize({ width: w, height: 900 })
    await shotPage(page, `/share#${shareToken}`, '.lb', `share-${tag}`)
  }
})
