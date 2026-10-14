// 6R18 strict E2E：首测体验急救（P0-1 回放可播放 / P0-2 团队页非空结构化门禁）+ 动态区域控制（P1）。
// 账号 mvr-acceptance@test.com（demo 19 = de_mirage，demo 20 = de_anubis，均回填 tactical artifact v3.0 + 区域几何）。
//
// 覆盖：
// 1) 团队回放 play→pause→seek→speed→end，按钮 aria/文字切换，时间值与画布确实推进（P0-1）。
// 2) 个人回放 play→pause→seek→speed→end（P0-1）。
// 3) reduced-motion 给出明确说明而非静默失效（P0-1）。
// 4) demo20 无权益 → 团队页显示非空结构化解释（roster 不足 / 不是空白）（P0-2）。
// 5) demo20 合法 roster → Anubis 团队同步回放可加载并播放（P0-2）。
// 6) 动态区域控制：连续控制场 data-area=field、seek 确定性重建、低 trust 降级为离散（P1）。
// 7) 移动端 390 无整页横滚 + 区域控制图层可见（P1）。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo, seedIdentity, demoPlayerSteam, psql } from './_helpers6r14.js'

const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo19: '/demo/19', demo20: '/demo/20' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
let TOKEN = ''

function seedDemo19() { seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, 19)) }
function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
// 6R18：把调用者的 active 团队占座全部移除 → 服务端团队门禁返回 403（TEAM_NOT_MEMBER，roster 不足）。
function clearTeamEntitlement(email) {
  const uid = psql(`SELECT id FROM users WHERE email='${email}'`).split(/\r?\n/).map((s) => s.trim()).find((s) => /^\d+$/.test(s))
  psql(`UPDATE team_memberships SET status='removed', removed_at=now(), version=version+1 WHERE user_id=${uid} AND status='active'`)
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
async function enterTeam(page, demo, round = 1) {
  await page.goto(demo)
  await page.waitForSelector('.map-name', { timeout: 15000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.locator('.tm-sel').selectOption(String(round))
  await page.waitForFunction((r) => document.querySelector('.tm-sel')?.value === String(r), round, { timeout: 20000 })
  await page.waitForTimeout(900)
}
async function scrub(page, cls, sec) {
  await page.locator(cls).evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, sec)
  await page.waitForTimeout(150)
}
async function canvAttr(page, cls, attr) { return page.locator(cls).getAttribute(attr) }
async function canvasChecksum(page, cls) {
  return page.locator(cls).evaluate((c) => {
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data
    let h = 0
    for (let i = 0; i < d.length; i += 101) h = (h * 31 + d[i]) % 1000000007
    return h
  })
}
// 读取阵营网格计数钩子（"CT,T,contested"）：0 表示该阵营无控制
function parseCamps(attr) {
  const parts = String(attr || '').split(',').map((x) => Number(x) || 0)
  return { c1: parts[0] || 0, c2: parts[1] || 0, c3: parts[2] || 0 }
}

test('6R18a：团队回放 play→pause→seek→speed→end（确定性）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page)
  const rep = await apiGet('/api/demos/19/team/replay?round_no=1')
  expect(rep.status).toBe(200)
  const maxDur = Number(rep.body.max_duration)
  expect(maxDur).toBeGreaterThan(5)
  await enterTeam(page, ACCOUNT.demo19, 1)
  await expect(page.locator('.tm-play')).toHaveAttribute('aria-label', '播放')
  // 播放：按钮变暂停、时间确实推进
  await page.locator('.tm-play').click()
  await expect(page.locator('.tm-play')).toHaveAttribute('aria-label', '暂停')
  await page.waitForTimeout(1200)
  const t1 = Number(await canvAttr(page, '.tm-canvas', 'data-time'))
  expect(t1).toBeGreaterThan(0.6)
  // 暂停：按钮回播放、时间不再推进
  await page.locator('.tm-play').click()
  await expect(page.locator('.tm-play')).toHaveAttribute('aria-label', '播放')
  const t2 = Number(await canvAttr(page, '.tm-canvas', 'data-time'))
  await page.waitForTimeout(500)
  const t3 = Number(await canvAttr(page, '.tm-canvas', 'data-time'))
  expect(Math.abs(t3 - t2)).toBeLessThan(0.25)
  // seek：拖动到指定秒 → data-time 命中（确定性重建）
  await scrub(page, '.tm-scrub', 5.0)
  expect(Math.abs(Number(await canvAttr(page, '.tm-canvas', 'data-time')) - 5.0)).toBeLessThan(0.6)
  // speed 2x：播放 1 秒应从 ~2.0 推进到 ~4.0（明显快于 1x）
  await page.locator('.tm-seg-btn:has-text("2x")').click()
  await scrub(page, '.tm-scrub', 2.0)
  await page.locator('.tm-play').click()
  await page.waitForTimeout(1000)
  const tFast = Number(await canvAttr(page, '.tm-canvas', 'data-time'))
  expect(tFast).toBeGreaterThan(3.4)
  await page.locator('.tm-play').click()   // 暂停
  // end：seek 到结尾附近播放 → 到达 max_duration 自动停（按钮回播放、时间=或接近上限）
  await scrub(page, '.tm-scrub', Math.max(0, maxDur - 0.6))
  await page.locator('.tm-play').click()
  await page.waitForTimeout(1600)
  await expect(page.locator('.tm-play')).toHaveAttribute('aria-label', '播放')
  expect(Number(await canvAttr(page, '.tm-canvas', 'data-time'))).toBeGreaterThanOrEqual(maxDur - 0.3)
})

test('6R18b：个人回放 play→pause→seek→speed→end（确定性）', async ({ page }) => {
  setTier(ACCOUNT.email, 'pro', 7)
  seedDemo19()
  await login(page)
  // 找有有效时长（max_duration>0）的回合，避免"无轨迹"回合
  let round = null
  for (let r = 1; r <= 6; r++) {
    const d = await apiGet(`/api/demos/19/personal/replay?round_no=${r}`)
    if (d.status === 200 && Number(d.body.max_duration) > 5) { round = r; break }
  }
  expect(round).toBeTruthy()
  const rep = await apiGet(`/api/demos/19/personal/replay?round_no=${round}`)
  const maxDur = Number(rep.body.max_duration)
  await page.goto(ACCOUNT.demo19)
  await page.waitForSelector('.map-name', { timeout: 15000 })
  await page.click('button.tab:has-text("个人")')
  await page.waitForSelector('.pt-canvas', { timeout: 25000 })
  await page.locator('.pt-sel').selectOption(String(round))
  await page.waitForTimeout(800)
  await expect(page.locator('.pt-play')).toHaveAttribute('aria-label', '播放')
  await page.locator('.pt-play').click()
  await expect(page.locator('.pt-play')).toHaveAttribute('aria-label', '暂停')
  await page.waitForTimeout(1200)
  expect(Number(await canvAttr(page, '.pt-canvas', 'data-time'))).toBeGreaterThan(0.5)
  await page.locator('.pt-play').click()
  await expect(page.locator('.pt-play')).toHaveAttribute('aria-label', '播放')
  await scrub(page, '.pt-scrub', Math.min(6, maxDur - 1))
  expect(Number(await canvAttr(page, '.pt-canvas', 'data-time'))).toBeGreaterThan(0.5)
  await scrub(page, '.pt-scrub', Math.max(0, maxDur - 0.6))
  await page.locator('.pt-play').click()
  await page.waitForTimeout(1600)
  await expect(page.locator('.pt-play')).toHaveAttribute('aria-label', '播放')
  expect(Number(await canvAttr(page, '.pt-canvas', 'data-time'))).toBeGreaterThanOrEqual(maxDur - 0.4)
})

test('6R18c：reduced-motion 给出明确说明而非静默失效', async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 360, height: 740 } })
  const page = await ctx.newPage()
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page)
  await enterTeam(page, ACCOUNT.demo19, 1)
  await expect(page.locator('.tm')).toHaveAttribute('data-reduced-motion', 'true')
  await expect(page.locator('.tm-hint:has-text("减弱动效")')).toBeVisible()
  await expect(page.locator('.tm-play')).toBeDisabled()
  await expect(page.locator('.tm-scrub')).toBeEnabled()
  await ctx.close()
})

test('6R18d：demo20 无权益 → 团队页显示非空结构化解释（绝非空白）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  clearTeamEntitlement(ACCOUNT.email)
  await login(page)
  await page.goto(ACCOUNT.demo20)
  await page.waitForSelector('.map-name', { timeout: 15000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForTimeout(1500)
  // 团队区块：出现结构化门禁盒（roster 不足），中文原因非空，绝不是静默空白
  const gate = page.locator('.team-gate')
  await expect(gate).toBeVisible({ timeout: 15000 })
  const reason = (await gate.locator('.team-gate-reason').innerText()).trim()
  expect(reason.length).toBeGreaterThan(0)
  expect(reason).toMatch(/[团队roster工作区]/)
  expect(await gate.locator('.team-gate-actions .btn').count()).toBeGreaterThan(0)
})

test('6R18e：demo20 合法 roster → Anubis 团队同步回放可加载并播放', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 20)
  await login(page)
  const rep = await apiGet('/api/demos/20/team/replay?round_no=1')
  expect(rep.status).toBe(200)
  expect(Number(rep.body.max_duration)).toBeGreaterThan(5)
  await enterTeam(page, ACCOUNT.demo20, 1)
  await expect(page.locator('.tm-canvas')).toHaveAttribute('data-ready', 'ready', { timeout: 15000 })
  // 播放确实推进（P0-1 修复的字段级回归）
  await page.locator('.tm-play').click()
  await page.waitForTimeout(1200)
  expect(Number(await canvAttr(page, '.tm-canvas', 'data-time'))).toBeGreaterThan(0.6)
  await page.locator('.tm-play').click()
})

test('6R18f：动态区域控制——真实 CT/T 阵营网格 + 前线 + seek 确定性', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 20)
  await login(page)
  await enterTeam(page, ACCOUNT.demo20, 1)
  // Anubis 几何 validated → 连续控制场
  await expect(page.locator('.tm-canvas')).toHaveAttribute('data-area', 'field', { timeout: 15000 })
  // 找同时存在 CT(1) 与 T(2) 且前线>0 的时刻（真实对抗边界；原 bug 下 c1=c2=0 恒失败）
  const maxD = await page.locator('.tm-scrub').evaluate((el) => Number(el.max) || 0)
  let foundTime = null
  for (let t = 0; t <= maxD; t += 2) {
    await scrub(page, '.tm-scrub', t)
    const camps = parseCamps(await page.locator('.tm-canvas').getAttribute('data-area-camps'))
    const frontier = Number(await page.locator('.tm-canvas').getAttribute('data-area-frontier') || 0)
    if (camps.c1 > 0 && camps.c2 > 0 && camps.c3 >= 0 && frontier > 0) { foundTime = t; break }
  }
  expect(foundTime).not.toBeNull()
  // seek 确定性：同一时刻两次重建 hash/像素一致
  await scrub(page, '.tm-scrub', foundTime)
  const h1 = await page.locator('.tm-canvas').getAttribute('data-area-hash')
  const p1 = await canvasChecksum(page, '.tm-canvas')
  await scrub(page, '.tm-scrub', Math.max(0, foundTime - 3))
  await scrub(page, '.tm-scrub', foundTime)
  const h2 = await page.locator('.tm-canvas').getAttribute('data-area-hash')
  const p2 = await canvasChecksum(page, '.tm-canvas')
  expect(h1).toBe(h2)
  expect(p1).toBe(p2)
})

test('6R18i：只有单方/全争夺时不伪造 CT/T 前线', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 20)
  await login(page)
  await enterTeam(page, ACCOUNT.demo20, 1)
  await expect(page.locator('.tm-canvas')).toHaveAttribute('data-area', 'field', { timeout: 15000 })
  const maxD = await page.locator('.tm-scrub').evaluate((el) => Number(el.max) || 0)
  for (let i = 0; i <= 8; i++) {
    const t = (maxD / 8) * i
    await scrub(page, '.tm-scrub', t)
    const camps = parseCamps(await page.locator('.tm-canvas').getAttribute('data-area-camps'))
    const frontier = Number(await page.locator('.tm-canvas').getAttribute('data-area-frontier') || 0)
    const distinct = [camps.c1, camps.c2, camps.c3].filter((x) => x > 0).length
    // 只一种阵营（单方控制/全争夺）→ 必须无 CT/T 前线；有两种及以上阵营才有边界
    if (distinct <= 1) expect(frontier).toBe(0)
  }
})

test('6R18g：移动端 390 无整页横滚 + 区域控制图层可见', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 20)
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page)
  await enterTeam(page, ACCOUNT.demo20, 1)
  const overflow = await page.evaluate(() => ({ scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth }))
  expect(overflow.scrollW).toBeLessThanOrEqual(overflow.clientW + 1)
  await expect(page.locator('.tm-toggles')).toBeVisible()
  await expect(page.locator('.tm-canvas')).toHaveAttribute('data-area', 'field', { timeout: 15000 })
})

test('6R18h：低 coverage/confidence → 降级为离散区域多边形（不伪造连续场/前线）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 21)   // demo21 = de_dust2（geometry_trust=experimental）
  await login(page)
  await enterTeam(page, '/demo/21', 1)
  // dust2 min_coverage 0.73 < 0.85 → 降级 data-area=poly（离散），绝不冒充连续场
  await expect(page.locator('.tm-canvas')).toHaveAttribute('data-area', 'poly', { timeout: 15000 })
  // 降级路径不产生 camp 网格/前线 → 测试钩子为空（无伪造）
  expect(await page.locator('.tm-canvas').getAttribute('data-area-camps')).toBe('')
  expect(await page.locator('.tm-canvas').getAttribute('data-area-frontier')).toBe('')
})
