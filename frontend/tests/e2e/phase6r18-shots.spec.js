// 6R18 截图矩阵：动态区域控制（连续控制场 field / 低信任降级 poly）· desktop 1440 + mobile 390/360 · 多地图。
// 产出 PNG 供 DeepSeek 原生 Native Vision（read_image）真实审图。只截图，不做数值断言。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo, seedIdentity, demoPlayerSteam } from './_helpers6r14.js'

test.setTimeout(600000)

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const SHOTS = 'tests/e2e/screenshots/6r18'
const ACCOUNT = 'mvr-acceptance@test.com'
let TOKEN = ''

const DEMOS = [
  { id: 19, map: 'de_mirage', field: 'field' },
  { id: 20, map: 'de_anubis', field: 'field' },
  { id: 21, map: 'de_dust2', field: 'poly' },
  { id: 22, map: 'de_inferno', field: 'poly' },
]
const VIEWPORTS = [
  { key: 'desk', w: 1440, h: 900 },
  { key: 'mob390', w: 390, h: 844 },
  { key: 'mob360', w: 360, h: 740 },
]

function seed(demoId) { seedIdentity(ACCOUNT, demoPlayerSteam(ACCOUNT, demoId)) }
function setTier(email, tier) {
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}', subscription_expire=now()+interval '30 days' WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT)
  await page.fill('input[type="password"]', 'password123')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterTeamRound(page, demoId, round = 1) {
  await page.goto(`/demo/${demoId}`)
  await expect(page.locator('.map-name')).toHaveText(/de_/, { timeout: 15000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.locator('.tm-sel').selectOption(String(round))
  await page.waitForFunction((r) => document.querySelector('.tm-sel')?.value === String(r), round, { timeout: 20000 })
  await page.waitForTimeout(900)
}

test('截图：动态区域控制 多地图 × desktop/mobile 矩阵', async ({ browser }) => {
  for (const d of DEMOS) {
    seed(d.id); setTier(ACCOUNT, 'max'); grantTeamEntitlementForDemo(ACCOUNT, d.id)
    for (const v of VIEWPORTS) {
      const ctx = await browser.newContext({ viewport: { width: v.w, height: v.h } })
      const page = await ctx.newPage()
      await login(page)
      await enterTeamRound(page, d.id, 1)
      // 等区域控制图层就绪（field 或 poly）
      await page.waitForFunction(() => { const a = document.querySelector('.tm-canvas')?.getAttribute('data-area'); return a === 'field' || a === 'poly' }, { timeout: 25000 })
      const area = await page.locator('.tm-canvas').getAttribute('data-area')
      if (area === 'field') {
        // validated 连续场：找「同时存在 CT(1) 与 T(2) 且前线最大」的真实争夺时刻，让双方控制场与前线最清晰
        const maxD = await page.locator('.tm-scrub').evaluate((el) => Number(el.max) || 0)
        let shotT = maxD * 0.45, bestF = -1
        for (let t = 0; t <= maxD; t += 2) {
          await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, t)
          await page.waitForTimeout(120)
          const camps = String(await page.locator('.tm-canvas').getAttribute('data-area-camps') || '').split(',').map(Number)
          const frontier = Number(await page.locator('.tm-canvas').getAttribute('data-area-frontier') || 0)
          if (camps[0] > 0 && camps[1] > 0 && frontier > bestF) { bestF = frontier; shotT = t }
        }
        await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, shotT)
        await page.waitForTimeout(400)
      } else {
        // 降级：直接取回合中段
        await page.locator('.tm-scrub').evaluate((el) => { el.value = String(Number(el.max) * 0.45); el.dispatchEvent(new Event('input', { bubbles: true })) })
        await page.waitForTimeout(400)
      }
      await page.locator('.tm-stage').scrollIntoViewIfNeeded()
      await page.screenshot({ path: `${SHOTS}/${d.map}_${v.key}_area${area}.png`, fullPage: true })
      await page.locator('.tm-canvas').screenshot({ path: `${SHOTS}/${d.map}_${v.key}_canvas.png` })
      await ctx.close()
    }
  }
})
