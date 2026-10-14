// 6R21F-E6B-SCALAR-DETERMINISM 回归：同一 t 多次 seek 后画布校验和必须确定一致（含标量场层开启）。
// Red（修复前）：GPU 光栅化（scaled drawImage / 抗锯齿矢量）在复用画布上于两个固定 checksum 间翻转；
// Green（修复后）：MapControlReplay 主画布以 willReadFrequently:true 强制 CPU 软件光栅化，readback 确定。
// 运行：PLAYWRIGHT_BASE_URL=http://localhost  (真实 nginx:80 产品链路)
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
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
async function enterTeam(page) {
  await page.goto(ACCOUNT.demo)
  await page.waitForSelector('.map-name', { timeout: 20000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.e6a-view-switch', { timeout: 30000 })
  await page.click('button.e6a-vtab:has-text("区域控制")')
  await page.waitForSelector('[data-testid="mcr-canvas"]', { timeout: 30000 })
  await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt && window.__mcReplay.toggle), undefined, { timeout: 30000 })
}
async function seekSec(page, t) { await page.locator('.mcr-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, t); await page.waitForTimeout(180) }
async function canvasChecksum(page) { return page.locator('[data-testid="mcr-canvas"]').evaluate((c) => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let s = 0; for (let i = 0; i < d.length; i++) s = (s + d[i]) % 1000000007; return String(s) }) }

test.beforeEach(async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page)
})

test('E6B-SCALAR-DET: 同 t 多次 seek 画布校验和确定一致（scalar 层开 + 默认层）', async ({ page }) => {
  await enterTeam(page)
  await page.evaluate(() => window.__mcReplay.awaitExactAt(20.0, 12000))
  // 场景 A：标量场层开启（受影响的核心路径）
  await page.evaluate(() => window.__mcReplay.toggle('scalar', true))
  await page.waitForTimeout(200)
  let prev = null
  for (let k = 0; k < 6; k++) { await seekSec(page, 20.0); const cs = await canvasChecksum(page); if (k > 0) expect(cs).toBe(prev); prev = cs }
  // 场景 B：默认层（用户产品默认：scalar off）在 t=30 同 t 三次
  await page.evaluate(() => window.__mcReplay.toggle('scalar', false))
  await page.evaluate(() => window.__mcReplay.awaitExactAt(30.0, 12000))
  prev = null
  for (let k = 0; k < 3; k++) { await seekSec(page, 30.0); const cs = await canvasChecksum(page); if (k > 0) expect(cs).toBe(prev); prev = cs }
})
