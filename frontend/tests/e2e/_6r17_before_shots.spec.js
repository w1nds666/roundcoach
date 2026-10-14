// 6R17 before 证据：在重构前（基线 a9eb799）对 8 类页面 + 三地图核心页生成 1440/390/360 截图。
// 只用于保存 before 证据；重构完成后此脚本不再保留（after 由 phase6r17-shots.spec.js 承担）。
// 运行：npx playwright test -c playwright.headless.config.js _6r17_before_shots
import { test } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, demoPlayerSteam, grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots/6r17-before'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DEMOS = { mirage: 19, anubis: 20, dust2: 21 }

function setTier(email, tier, days) {
  const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
  const exp = days ? `, subscription_expire = now() + interval '${days} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}

async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

async function shotPage(page, url, waitSel, name) {
  await page.goto(url)
  if (waitSel) await page.waitForSelector(waitSel, { timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${SHOTS}/${name}.png` })
}

test('before 截图矩阵：Login/Dashboard/Upload/Profile/Coach/Overview/Personal/Team/Share + 三地图', async ({ page }) => {
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

  await login(page)

  // --- Dashboard ---
  await page.setViewportSize({ width: 1440, height: 900 })
  await shotPage(page, '/', '.page-wrap', 'dashboard-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await shotPage(page, '/', '.page-wrap', 'dashboard-390')
  await page.setViewportSize({ width: 360, height: 780 })
  await shotPage(page, '/', '.page-wrap', 'dashboard-360')

  // --- Upload ---
  await page.setViewportSize({ width: 1440, height: 900 })
  await shotPage(page, '/upload', '.dropzone, form', 'upload-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await shotPage(page, '/upload', '.dropzone, form', 'upload-390')
  await page.setViewportSize({ width: 360, height: 780 })
  await shotPage(page, '/upload', '.dropzone, form', 'upload-360')

  // --- Profile ---
  await page.setViewportSize({ width: 1440, height: 900 })
  await shotPage(page, '/profile', '.page-wrap', 'profile-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await shotPage(page, '/profile', '.page-wrap', 'profile-390')
  await page.setViewportSize({ width: 360, height: 780 })
  await shotPage(page, '/profile', '.page-wrap', 'profile-360')

  // --- Coach ---
  await page.setViewportSize({ width: 1440, height: 900 })
  await shotPage(page, '/coach', '.page-wrap', 'coach-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await shotPage(page, '/coach', '.page-wrap', 'coach-390')
  await page.setViewportSize({ width: 360, height: 780 })
  await shotPage(page, '/coach', '.page-wrap', 'coach-360')

  // --- 概览（默认 tab）---
  await page.setViewportSize({ width: 1440, height: 900 })
  await shotPage(page, '/demo/19', '.head', 'overview-desktop')

  // --- 个人/团队 三地图 ---
  for (const [map, id] of Object.entries(DEMOS)) {
    grantTeamEntitlementForDemo(ACCOUNT.email, id)
    const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
    const playersResp = await page.request.get(`/api/demos/${id}/players`, { headers: { Authorization: `Bearer ${token}` } })
    const players = await playersResp.json()
    const fp = (players[0] || {}).player_id
    if (fp) await page.request.put(`/api/demos/${id}/focus-player`, { headers: { Authorization: `Bearer ${token}` }, data: { player_id: fp } })

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`/demo/${id}`)
    await page.waitForSelector('.head', { timeout: 20000 })
    await page.click('button.tab:has-text("个人复盘")')
    await page.waitForSelector('.picker select', { timeout: 20000 }).catch(() => {})
    await page.waitForTimeout(600)
    await page.screenshot({ path: `${SHOTS}/personal-${map}-desktop.png` })
    await page.click('button.tab:has-text("团队复盘")')
    await page.waitForSelector('.team-summary, .tr-card, .locked-box', { timeout: 20000 }).catch(() => {})
    await page.waitForTimeout(600)
    await page.screenshot({ path: `${SHOTS}/team-${map}-desktop.png` })
  }

  // --- 个人/团队移动（mirage）---
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/demo/19')
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.picker select', { timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${SHOTS}/personal-390.png` })
  await page.click('button.tab:has-text("团队复盘")')
  await page.waitForSelector('.team-summary, .tr-card, .locked-box', { timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${SHOTS}/team-390.png` })
  await page.setViewportSize({ width: 360, height: 780 })
  await page.reload()
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.picker select', { timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${SHOTS}/personal-360.png` })
  await page.click('button.tab:has-text("团队复盘")')
  await page.waitForSelector('.team-summary, .tr-card, .locked-box', { timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${SHOTS}/team-360.png` })

  // --- Share（匿名）---
  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const shareResp = await page.request.post('/api/demos/19/share', { headers: { Authorization: `Bearer ${token}` }, data: { expires_in_hours: 24 } })
  const shareToken = (await shareResp.json()).token
  await page.setViewportSize({ width: 1440, height: 900 })
  await shotPage(page, `/share#${shareToken}`, '.lb', 'share-desktop')
  await page.setViewportSize({ width: 390, height: 844 })
  await shotPage(page, `/share#${shareToken}`, '.lb', 'share-390')
  await page.setViewportSize({ width: 360, height: 780 })
  await shotPage(page, `/share#${shareToken}`, '.lb', 'share-360')
})
