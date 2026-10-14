// 6R16 截图矩阵：区域控制图层（desktop 1440 + mobile 390/360）+ 多地图。
// 产出 PNG 供 DeepSeek 原生 Native Vision（read_image）审图。只截图，不做数值断言。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo, seedIdentity, demoPlayerSteam } from './_helpers6r14.js'

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const SHOTS = 'tests/e2e/screenshots'
let TOKEN = ''

// 5 个验收 demo 的地图（同一账号拥有 demo 19/21/22）
const DEMOS = [
  { id: 19, map: 'de_mirage', account: 'mvr-acceptance@test.com' },
  { id: 21, map: 'de_dust2', account: 'mvr-acceptance@test.com' },
  { id: 22, map: 'de_inferno', account: 'mvr-acceptance@test.com' },
]
const ACCOUNT = 'mvr-acceptance@test.com'

function seed(demoId) {
  const a = DEMOS.find((d) => d.id === demoId)
  seedIdentity(ACCOUNT, demoPlayerSteam(ACCOUNT, demoId))
}
function setTier(email, tier, days) {
  const exp = days ? `, subscription_expire = now() + interval '${days} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function getToken() {
  if (TOKEN) return TOKEN
  const r = await fetch('http://localhost/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: ACCOUNT, password: 'password123' }) })
  TOKEN = (await r.json()).access_token
  return TOKEN
}
async function login(page, account) {
  await page.goto('/login')
  await page.fill('input[type="email"]', account)
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

test('截图：三地图桌面 1440 区域控制图层', async ({ browser }) => {
  for (const d of DEMOS) {
    seed(d.id)
    setTier(ACCOUNT, 'max', 30)
    grantTeamEntitlementForDemo(ACCOUNT, d.id)
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const page = await ctx.newPage()
    await login(page, ACCOUNT)
    await enterTeamRound(page, d.id, 1)
    await page.screenshot({ path: `${SHOTS}/6r16_${d.map}_desk1440_r1.png`, fullPage: true })
    await ctx.close()
  }
})

test('截图：移动端 390 地图', async ({ browser }) => {
  const d = DEMOS[0]
  seed(d.id); setTier(ACCOUNT, 'max', 30); grantTeamEntitlementForDemo(ACCOUNT, d.id)
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await ctx.newPage()
  await login(page, ACCOUNT)
  await enterTeamRound(page, d.id, 1)
  await page.screenshot({ path: `${SHOTS}/6r16_${d.map}_mob390_r1.png`, fullPage: true })
  await ctx.close()
})

test('截图：移动端 360 地图', async ({ browser }) => {
  const d = DEMOS[1]
  seed(d.id); setTier(ACCOUNT, 'max', 30); grantTeamEntitlementForDemo(ACCOUNT, d.id)
  const ctx = await browser.newContext({ viewport: { width: 360, height: 740 } })
  const page = await ctx.newPage()
  await login(page, ACCOUNT)
  await enterTeamRound(page, d.id, 1)
  await page.screenshot({ path: `${SHOTS}/6r16_${d.map}_mob360_r1.png`, fullPage: true })
  await ctx.close()
})
