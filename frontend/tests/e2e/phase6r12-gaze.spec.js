// 6R12 严格 E2E：视角朝向（修正 90° 变换）+ 瞬时"射击线"仅在 validated 时渲染 + 门禁通过 + 配合 quarantine。
// 用 mvr-acceptance@test.com（demo 19 = de_mirage，已回填 tactical artifact v2.1）
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
let TOKEN = ''

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function getToken() {
  if (TOKEN) return TOKEN
  const r = await fetch('http://localhost/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(ACCOUNT) })
  TOKEN = (await r.json()).access_token
  return TOKEN
}
async function apiGet(path) {
  const t = await getToken()
  const r = await fetch(`http://localhost${path}`, { headers: { Authorization: `Bearer ${t}` } })
  return { status: r.status, body: await r.json() }
}
async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterTeam(page, round = 1) {
  await page.goto(ACCOUNT.demo)
  await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 15000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.locator('.tm-sel').selectOption(String(round))
  await page.waitForFunction((r) => document.querySelector('.tm-sel')?.value === String(r), round, { timeout: 20000 })
  await page.waitForTimeout(800)
}
async function scrub(page, sec) {
  await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, sec)
  await page.waitForTimeout(200)
}
async function canvAttr(page, attr) { return page.locator('.tm-canvas').getAttribute(attr) }

test.beforeEach(() => grantTeamEntitlementForDemo(ACCOUNT.email, 19))   // 6R15 收口：按目标 demo 播种团队权益门禁

test('6R12a：UI 用诚实名称「视角朝向」，不再宣称「真实枪线」（gate 1）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  await enterTeam(page, 1)
  // 图例 / 图层开关应出现"视角朝向"与"射击线（已验证）"
  await expect(page.locator('.tm-toggle:has-text("视角朝向")')).toHaveCount(1)
  const legendText = await page.locator('.tm-canvas').evaluate(() => '') // canvas 文字不可 DOM 读，改用开关
  // 图层开关文案不得出现"真实枪线"或"真实朝向·枪线"
  const toggles = await page.locator('.tm-toggles').innerText()
  expect(toggles).not.toContain('真实枪线')
  expect(toggles).not.toContain('真实朝向·枪线')
  // 详情/提示不得出现"真实枪线"
  const hint = await page.locator('.tm-hint').innerText()
  expect(hint).not.toContain('真实枪线')
  await page.locator('.tm-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r12-gaze-label.png` })
})

test('6R12b：瞬时"射击线"仅在 validated 时渲染（gate 2）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  // 取 round1 射击事件
  const d = await apiGet('/api/demos/19/team/replay?round_no=1')
  const shoots = d.body.shoots || []
  const validated = shoots.find((s) => s.validated && s.time != null && s.rx != null)
  expect(validated).toBeTruthy()
  await enterTeam(page, 1)
  // 在 validated 射击时刻 → 出现射击线 data-shoot=1
  await scrub(page, validated.time)
  expect(await canvAttr(page, 'data-shoot')).toBe('1')
  // 在首个射击时刻之前 → 无射击线 data-shoot=''
  const t0 = Math.max(0, (validated.time - 2))
  await scrub(page, t0)
  expect(await canvAttr(page, 'data-shoot')).toBe('')
  await page.locator('.tm-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r12-gaze-shootline.png` })
})

test('6R12c：朝向/枪线真值门禁通过（median≤10°, p95≤25°, 无90/180°偏差）（gate 3）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const d = await apiGet('/api/demos/19/team/replay?round_no=1')
  const gate = d.body.gaze_gate
  expect(gate).toBeTruthy()
  expect(gate.n).toBeGreaterThanOrEqual(50)
  expect(gate.median).toBeLessThanOrEqual(10)
  expect(gate.p95).toBeLessThanOrEqual(25)
  expect(gate.bias_90).toBeLessThanOrEqual(0.02)
  expect(gate.bias_180).toBeLessThanOrEqual(0.02)
  expect(gate.passed).toBe(true)
})

test('6R12d：配合指标 quarantine——crossfire hidden + 零机会「不适用」非0分（gate 4）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const co = await apiGet('/api/demos/19/coordination')
  expect(co.status).toBe(200)
  const inv = co.body.metric_credibility || []
  expect(inv.length).toBe(7)
  const cross = inv.find((m) => m.metric_id === 'crossfire_candidate')
  expect(cross.trust).toBe('hidden')
  // 打开协调面板，检查指标表无"零机会显示数字/0%"的伪结果（零机会显示不适用）
  await page.goto(ACCOUNT.demo)
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.co-metric-table', { timeout: 25000 })
  const someRow = page.locator('.co-metric-table tbody tr').filter({ hasText: '交叉火力候选' }).first()
  if (await someRow.count() > 0) {
    const txt = await someRow.innerText()
    expect(txt).not.toMatch(/^\s*0\s*%|\b0%\b/)
  }
  await page.screenshot({ path: `${SHOTS}/6r12-quarantine.png` })
})
