// 6R21F-F3 AFTER 视觉证据采集：Report 战术工作台（概览/个人/团队）。
// 三地图 demo19/21/22 × 五视口（desktop1280/1081/900 + mobile390/360）。
// 产出 docs/visual-evidence/6r21f-f3-{map}-{view}-{viewport}-after.png
// 运行：npx playwright test -c playwright.evidence.config.js phase6r21f-f3-after.spec.js
import { test, expect } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { setTier, demoPlayerSteam, seedIdentity, grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173'
const OUT = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(OUT, { recursive: true })
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DEMOS = [
  { id: 19, map: 'mirage', mapName: 'de_mirage' },
  { id: 21, map: 'dust2', mapName: 'de_dust2' },
  { id: 22, map: 'inferno', mapName: 'de_inferno' },
]
const VIEWS = [
  { label: 'desktop1280', width: 1280, height: 800 },
  { label: 'desktop1081', width: 1081, height: 800 },
  { label: 'desktop900', width: 900, height: 800 },
  { label: 'mobile390', width: 390, height: 844 },
  { label: 'mobile360', width: 360, height: 740 },
]
test.setTimeout(600000)

function S(map, view, vp) { return resolve(OUT, `6r21f-f3-${map}-${view}-${vp}-after.png`) }

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })
  await page.waitForSelector('.user', { timeout: 15000 })
}

for (const d of DEMOS) {
  test(`AFTER capture ${d.map} 报告三视图`, async ({ browser }) => {
    setTier(ACCOUNT.email, 'max', 7)
    const steam = demoPlayerSteam(ACCOUNT.email, d.id, 0)
    if (steam) seedIdentity(ACCOUNT.email, steam)
    grantTeamEntitlementForDemo(ACCOUNT.email, d.id)

    for (const v of VIEWS) {
      const ctx = await browser.newContext({ viewport: { width: v.width, height: v.height } })
      const page = await ctx.newPage()
      await login(page)
      await page.goto(`${BASE}/demo/${d.id}`, { waitUntil: 'networkidle' })
      await page.waitForSelector('.map-name', { timeout: 40000 })
      await page.waitForTimeout(900)

      // 概览
      await page.screenshot({ path: S(d.map, 'overview', v.label), fullPage: true })
      const ow = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
      expect(ow, `${d.map}/${v.label} overview 无横滚`).toBe(true)

      // 个人
      await page.click('.wb-tabs button:has-text("个人复盘")')
      await page.waitForTimeout(1000)
      await page.waitForSelector('.my, .form-error-box, .locked-box', { timeout: 20000 })
      await page.screenshot({ path: S(d.map, 'personal', v.label), fullPage: true })
      const pw = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
      expect(pw, `${d.map}/${v.label} personal 无横滚`).toBe(true)

      // 团队
      await page.click('.wb-tabs button:has-text("团队复盘")')
      await page.waitForTimeout(1200)
      await page.waitForSelector('.team-gate, .team-summary, .tr-card, .locked-box', { timeout: 30000 })
      await page.screenshot({ path: S(d.map, 'team', v.label), fullPage: true })
      const tw = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
      expect(tw, `${d.map}/${v.label} team 无横滚`).toBe(true)

      await ctx.close()
    }
  })
}
