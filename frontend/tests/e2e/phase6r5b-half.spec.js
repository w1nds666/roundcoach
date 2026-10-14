// 6R5B → 6R8：半场多回合动态路线总览 E2E 门禁
// 用 mvr-acceptance@test.com（max，demo 19 = de_mirage，已回填 6R8 artifact）
// 门禁：一次聚合 API / CT-T 切换 / 首帧多路线 / 推进像素变化 / death X 到达才出现 /
//       单回合突出交互 / 切回单回合 / 390 无溢出 / 三张截图
// 迁移说明：模式按钮由 .rr-mode-btn → .pt-mode-btn；单回合数据源由 /personal/route
// → /personal/replay；半场仍复用 RouteHalfReplay（/personal/route?view=half，.hr-*不变）。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, DEMO19 } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const PRO = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const MIN_PAINTED = 0.10
const FOCUS_PLAYER = 371   // CT 玩家，半场总览（view=half&side=CT）有足够多回合
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
let TOKEN = ''

// 6R14 身份优先：半场总览恒为绑定本人；先播种到 demo19 有足够 CT 回合的稳定玩家
function seedCtPlayer() { seedIdentity(PRO.email, DEMO19.ROUND1_DEATH) }

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function getToken() {
  if (TOKEN) return TOKEN
  const r = await fetch('http://localhost/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(PRO) })
  TOKEN = (await r.json()).access_token
  return TOKEN
}
async function apiPut(path, body) {
  const t = await getToken()
  const r = await fetch(`http://localhost${path}`, { method: 'PUT', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  return r.json()
}
async function apiGet(path) {
  const t = await getToken()
  const r = await fetch(`http://localhost${path}`, { headers: { Authorization: `Bearer ${t}` } })
  return { status: r.status, body: await r.json() }
}

async function login(page, account) {
  await page.goto('/login')
  await page.fill('input[type="email"]', account.email)
  await page.fill('input[type="password"]', account.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

// demo19 重解析后 player_id 会变化；动态找一个 CT 侧玩家（队伍身份 A）做半场 CT 视图
async function resolveCtPlayer() {
  const players = (await apiGet('/api/demos/19/players')).body
  const ct = players.find((p) => p.team_identity === 'A' || p.team === 'CT') || players[0]
  return ct.player_id
}

async function gotoMyTab(page) {
  const FOCUS_PLAYER = await resolveCtPlayer()
  await apiPut('/api/demos/19/focus-player', { player_id: FOCUS_PLAYER })
  await page.goto(PRO.demo)
  await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 15000 })
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.picker select', { timeout: 15000 })
  const val = await page.locator('.picker select').inputValue()
  if (String(val) !== String(FOCUS_PLAYER)) {
    await page.locator('.picker select').selectOption(String(FOCUS_PLAYER))
  }
  await page.waitForSelector('.pt-mode-btn', { timeout: 20000 })
}

async function enterHalf(page) {
  await page.click('.pt-mode-btn:has-text("半场总览")')
  await page.waitForSelector('.hr-canvas', { timeout: 25000 })
  await page.waitForSelector('.hr-chips', { timeout: 15000 })
  // 等首帧绘制
  await page.waitForFunction(() => {
    const cv = document.querySelector('.hr-canvas')
    if (!cv) return false
    const ctx = cv.getContext('2d'); const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
    let p = 0; for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 10) p++
    return p / (cv.width * cv.height) > 0.01
  }, { timeout: 15000 })
}

async function scrubHalf(page, sec) {
  await page.locator('.hr-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, sec)
  await page.waitForTimeout(220)
}

async function canvasStats(page, sel) {
  return page.locator(sel).evaluate((cv) => {
    const ctx = cv.getContext('2d'); const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
    let painted = 0, red = 0
    for (let i = 0; i < data.length; i += 4) {
      const a = data[i + 3]
      if (a > 10) painted++
      if (a > 100 && data[i] > 190 && data[i + 1] < 110 && data[i + 2] < 110) red++
    }
    return { painted, red }
  })
}

test('pro：半场总览 聚合请求 + CT/T 切换 + 首帧多路线 + death X 到达才出现 + 单回合突出 + 切回单回合', async ({ page }) => {
  setTier(PRO.email, 'max', 7)
  seedCtPlayer()
  const routeReqs = []
  // 半场仍走 /personal/route?view=half；单回合已改走 /personal/replay（不会被本过滤器计入）
  page.on('request', (req) => { if (req.url().includes('/personal/route')) routeReqs.push(req.url()) })
  await login(page, PRO)
  await gotoMyTab(page)
  // 等单回合视图加载完成（round_no 请求结束，走 /personal/replay），再把统计清零，只统计进半场后的请求
  await page.waitForSelector('.pt-timeline', { timeout: 15000 })
  routeReqs.length = 0
  await enterHalf(page)

  // 一次聚合 API：进入半场只发一次 view=half（CT），无逐回合 round_no 请求
  const viewHalf = () => routeReqs.filter((u) => u.includes('view=half'))
  const roundNo = () => routeReqs.filter((u) => u.includes('round_no='))
  expect(viewHalf().length).toBe(1)   // 只允许一次聚合请求
  expect(roundNo().length).toBe(0)

  // 摘要 + 回合 chips（多回合，默认全部）
  await expect(page.locator('.hr-summary')).toBeVisible()
  const chipCount = await page.locator('.hr-chip').count()
  expect(chipCount).toBeGreaterThan(2)     // 全部 + 至少 2 个回合
  const sumText = await page.locator('.hr-summary').innerText()
  expect(sumText).toContain('回合')

  // 首帧：已绘制（雷达底图 + 出生点/小球），死亡 X 尚未出现（t=0 仅图例红）
  const first = await canvasStats(page, '.hr-canvas')
  expect(first.painted / (720 * 720)).toBeGreaterThan(MIN_PAINTED)
  const redBefore = first.red
  await page.locator('.hr-stage').scrollIntoViewIfNeeded()
  await page.waitForTimeout(200)
  await page.screenshot({ path: `${SHOTS}/half-desktop-first.png` })

  // 推进：路线增长（t=0 vs t=mid 截图不同）
  await scrubHalf(page, 0)
  const buf0 = await page.locator('.hr-canvas').screenshot()
  const maxDur = Number(await page.locator('.hr-scrub').getAttribute('max'))
  await scrubHalf(page, maxDur / 2)
  const bufMid = await page.locator('.hr-canvas').screenshot()
  expect(Buffer.compare(buf0, bufMid)).not.toBe(0)   // 多回合路线增长，像素变化
  await page.locator('.hr-stage').scrollIntoViewIfNeeded()
  await page.waitForTimeout(150)
  await page.screenshot({ path: `${SHOTS}/half-desktop-playing.png` })

  // 拖到最末：死亡 X 出现
  await scrubHalf(page, maxDur)
  const redAfter = await canvasStats(page, '.hr-canvas').then((s) => s.red)
  expect(redAfter).toBeGreaterThan(redBefore + 10)

  // 单回合突出交互：点击某 chip → on；再点恢复
  const tag = page.locator('.hr-chip').nth(1)
  await tag.click()
  await expect(tag).toHaveClass(/on/, { timeout: 5000 })
  await page.locator('.hr-stage').screenshot({ path: `${SHOTS}/half-desktop-highlight.png` })   // 突出状态证据
  const hi = await page.locator('.hr-canvas').screenshot()
  await tag.click()
  await expect(tag).not.toHaveClass(/on/, { timeout: 5000 })
  const unHi = await page.locator('.hr-canvas').screenshot()
  expect(Buffer.compare(hi, unHi)).not.toBe(0)   // 突出改变渲染（含球/标签/事件淡化）

  // CT/T 切换（T 按钮精确匹配）。每次切换只允许一次聚合请求
  const beforeSwitch = viewHalf().length
  await page.locator('.hr-side').filter({ hasText: /^T$/ }).click()
  await page.waitForSelector('.hr-chips', { timeout: 15000 })
  await page.waitForTimeout(500)
  expect(viewHalf().length).toBe(beforeSwitch + 1)

  // 切回单回合：单回合控件可见（单回合回放 / 数据源 /personal/replay）
  await page.click('.pt-mode-btn:has-text("单回合战术回放")')
  await page.waitForSelector('.pt-sel', { timeout: 15000 })
  await expect(page.locator('.pt-sel')).toBeVisible()
})

test('pro：半场总览 移动视口 390x844 有效像素 + 无横向溢出', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await ctx.newPage()
  try {
    setTier(PRO.email, 'max', 7)
    seedCtPlayer()
    await login(page, PRO)
    await gotoMyTab(page)
    await enterHalf(page)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    const stats = await canvasStats(page, '.hr-canvas')
    expect(stats.painted / (720 * 720)).toBeGreaterThan(MIN_PAINTED)
    await page.screenshot({ path: `${SHOTS}/half-mobile.png`, fullPage: true })
  } finally {
    await ctx.close()
  }
})
