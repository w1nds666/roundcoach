// 6R5A → 6R8：个人战术回放可用性打磨 + 报告页视觉基座 的浏览器验收门禁
// 说明：旧 RouteReplay（/personal/route schema 1.2）已由 6R8 PersonalTacticalReplay
// （/personal/replay schema 2.0）替换。本文件按新组件 DOM/数据源迁移。
// 死亡语义：点中 alive===false 的那一点即死亡点；死亡时刻 = (deathTick - freeze_end_tick)/tick_rate。
// 用 mvr-acceptance@test.com（max，demo 19 = de_mirage，已回填 6R8 artifact）。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, DEMO19 } from './_helpers6r14.js'

const SHOTS = process.env.ROUTE_SHOTS_DIR || 'tests/e2e/screenshots'
const PRO = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const MIN_PAINTED = 0.10   // 画布被绘制（非透明 alpha>10）像素占比下限；雷达底图覆盖远超此值
const CANVAS = 720          // 与 PersonalTacticalReplay SIZE 一致（内部坐标系）
const PLAYER_ID = 374       // demo 19 中回合 1 有真实死亡点（alive===false）的 focus 玩家
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
let TOKEN = ''

// 6R14 身份优先：个人回放恒为绑定本人；先播种到 demo19 回合1 有真实死亡点的稳定玩家
function seedDeathPlayer() { seedIdentity(PRO.email, DEMO19.ROUND1_DEATH) }

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
async function apiGet(path) {
  const t = await getToken()
  const r = await fetch(`http://localhost${path}`, { headers: { Authorization: `Bearer ${t}` } })
  return { status: r.status, body: await r.json() }
}
async function apiPut(path, body) {
  const t = await getToken()
  const r = await fetch(`http://localhost${path}`, { method: 'PUT', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  return r.json()
}

// demo19 重解析后 player_id 会变化；6R14 身份优先：个人回放恒为绑定本人（require_pro_focus），
// 手动 focus 不改数据主体。直接取 is_you（绑定本人）玩家；该玩家回合1 有真实死亡点（alive===false）。
async function resolveDeathPlayer() {
  const players = (await apiGet('/api/demos/19/players')).body
  return (players.find((p) => p.is_you) || players[0]).player_id
}

async function login(page, account) {
  await page.goto('/login')
  await page.fill('input[type="email"]', account.email)
  await page.fill('input[type="password"]', account.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

// 进入「个人复盘」并把 focus 玩家设定为 playerId（用于取 /personal/replay）
async function gotoMyTab(page, playerId) {
  await apiPut('/api/demos/19/focus-player', { player_id: playerId })
  await page.goto(PRO.demo)
  await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 15000 })
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.picker select', { timeout: 15000 })
  const val = await page.locator('.picker select').inputValue()
  if (String(val) !== String(playerId)) {
    await page.locator('.picker select').selectOption(String(playerId))
  }
  await page.waitForSelector('.pt-canvas', { timeout: 25000 })
  await page.waitForFunction((pid) => document.querySelector('.pt')?.getAttribute('data-player') === String(pid), playerId, { timeout: 20000 })
  await page.waitForFunction(() => document.querySelector('.pt-canvas')?.getAttribute('data-ready') === 'ready', { timeout: 20000 })
}

async function canvasPaintedRatio(page) {
  return page.locator('.pt-canvas').evaluate((cv) => {
    const ctx = cv.getContext('2d')
    const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
    let painted = 0, nonBg = 0
    for (let i = 0; i < data.length; i += 4) {
      const a = data[i + 3]
      if (a > 10) painted++
      if (a > 10 && (Math.abs(data[i] - 11) + Math.abs(data[i + 1] - 15) + Math.abs(data[i + 2] - 20) > 12)) nonBg++
    }
    return { ratio: painted / (cv.width * cv.height), nonBg }
  })
}

// 计数死亡点附近红色 X 像素（#f85149 近似；橙色路线/击杀菱形不命中）
async function countRedNearDeath(page, box) {
  return page.locator('.pt-canvas').evaluate((cv, box) => {
    const ctx = cv.getContext('2d')
    const x = Math.max(0, Math.min(cv.width - 44, box.x - 22))
    const y = Math.max(0, Math.min(cv.height - 44, box.y - 22))
    const { data } = ctx.getImageData(x, y, 44, 44)
    let red = 0
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] > 100 && data[i] > 190 && data[i + 1] < 110 && data[i + 2] < 110) red++
    }
    return red
  }, box)
}

async function scrubTo(page, sec) {
  await page.locator('.pt-scrub').evaluate((el, v) => {
    el.value = String(v)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }, sec)
  await page.waitForTimeout(200)   // 等 nextTick 渲染
}

// 6R8 数据源：/personal/replay（focus 玩家；round_no 可空=回合列表）
async function getReplayData(roundNo) {
  const q = roundNo != null ? `?round_no=${roundNo}` : ''
  return (await apiGet(`/api/demos/19/personal/replay${q}`)).body
}

test('pro：桌面首帧像素 + 死亡 X 前后出现 + 结局真实时间（死亡点 tick 计算）+ 顶栏不遮挡', async ({ page }) => {
  setTier(PRO.email, 'max', 7)
  seedDeathPlayer()
  await login(page, PRO)
  const PLAYER_ID = await resolveDeathPlayer()
  await gotoMyTab(page, PLAYER_ID)

  // 回合选择器 + 路线开关（新语义：默认开，显示已探索路线）
  const sel = page.locator('.pt-sel')
  await expect(sel).toBeVisible()
  expect(await sel.locator('option').count()).toBeGreaterThan(1)
  const routeToggle = page.locator('.pt-toggle:has-text("路线")')
  await expect(routeToggle).toBeVisible()
  await expect(routeToggle).toHaveClass(/on/)   // 默认开（旧「显示完整路线默认关」已不存在）

  // 首帧（未点播放）即绘制雷达底图（非纯背景）
  const first = await canvasPaintedRatio(page)
  expect(first.ratio).toBeGreaterThan(MIN_PAINTED)
  expect(first.nonBg).toBeGreaterThan(1000)

  // 选中回合 1（focus 玩家 374 回合 1 有真实死亡点 alive===false）
  await sel.selectOption('1')
  await page.waitForTimeout(700)
  await page.waitForFunction(() => document.querySelector('.pt-canvas')?.getAttribute('data-ready') === 'ready', { timeout: 20000 })

  // 取当前回合数据（死亡点/时间轴）
  const rd = await getReplayData(1)
  const round = rd.round
  expect(round.number).toBe(1)
  const death = (rd.points || []).find((p) => p.alive === false)
  expect(death).toBeTruthy()   // 动态选中回合确有真实死亡点（alive===false）
  const tickRate = round.tick_rate
  expect(tickRate).toBe(64)
  const freezeTick = round.freeze_end_tick
  const deathTick = death.tick
  const deathSec = (deathTick - freezeTick) / tickRate
  const roundDur = (round.end_tick - freezeTick) / tickRate

  // 结局真实时间：死亡点 tick 在 freeze 之后，且在回合时长内（死亡点位置的死亡时刻）
  expect(deathTick).toBeGreaterThan(freezeTick)
  expect(deathSec).toBeGreaterThan(0)
  expect(deathSec).toBeLessThan(roundDur)
  expect(Number.isFinite(deathSec)).toBe(true)
  const deathBox = { x: death.rx * CANVAS, y: death.ry * CANVAS }

  // 到达死亡时刻前：死亡点附近无红 X
  await scrubTo(page, Math.max(0, deathSec - 2))
  expect(await countRedNearDeath(page, deathBox)).toBeLessThan(5)

  // 到达死亡时刻后：死亡 X 出现
  await scrubTo(page, Math.min(roundDur, deathSec + 2))
  expect(await countRedNearDeath(page, deathBox)).toBeGreaterThan(12)

  // 滚动到路线卡片：当前应用顶栏与 canvas 可见矩形不相交。
  // 旧 `.nav` 壳层已由 F2 的 `header.app-topbar` 取代，保护意图保持不变。
  await page.locator('.pt-card').scrollIntoViewIfNeeded()
  await page.waitForTimeout(200)
  const nav = await page.locator('header.app-topbar').boundingBox()
  const canvas = await page.locator('.pt-canvas').boundingBox()
  const navR = { left: nav.x, top: nav.y, right: nav.x + nav.width, bottom: nav.y + nav.height }
  const cvR = { left: canvas.x, top: canvas.y, right: canvas.x + canvas.width, bottom: canvas.y + canvas.height }
  const overlapX = navR.right > cvR.left && navR.left < cvR.right
  const overlapY = navR.bottom > cvR.top && navR.top < cvR.bottom
  expect(overlapX && overlapY, `顶栏与路线 canvas 矩形不应相交；nav=${JSON.stringify(navR)}, canvas=${JSON.stringify(cvR)}`).toBe(false)
  await page.screenshot({ path: `${SHOTS}/route-desktop-scroll.png` })   // 路线区滚动态 viewport

  // 顶部截图（viewport，非 fullPage）
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.waitForTimeout(200)
  await page.screenshot({ path: `${SHOTS}/route-desktop-top.png` })
})

test('pro：个人战术回放 默认绘制路线 + 路线开关真实影响画布（切 Tab 焦点持久）', async ({ page }) => {
  setTier(PRO.email, 'max', 7)
  seedDeathPlayer()
  await login(page, PRO)
  const PLAYER_ID = await resolveDeathPlayer()
  await gotoMyTab(page, PLAYER_ID)

  // 新语义：路线 toggle 默认开（显示已探索路线）
  const routeToggle = page.locator('.pt-toggle:has-text("路线")')
  await expect(routeToggle).toBeVisible()
  await expect(routeToggle).toHaveClass(/on/)

  // 首帧绘制（雷达底图 + 已探索路线）
  await scrubTo(page, 15)
  const painted = await canvasPaintedRatio(page)
  expect(painted.ratio).toBeGreaterThan(MIN_PAINTED)

  // 路线开关真实影响画布：关 → 路线被移除（画布变化，但当前球仍在）
  const bufOn = await page.locator('.pt-canvas').screenshot()
  await routeToggle.click()
  await page.waitForTimeout(180)
  const bufOff = await page.locator('.pt-canvas').screenshot()
  expect(Buffer.compare(bufOn, bufOff)).not.toBe(0)
  await routeToggle.click()
  await page.waitForTimeout(180)
  await expect(routeToggle).toHaveClass(/on/)

  // 切 Tab 后 focus 玩家持久（服务端持久化）；重挂载后仍绘制路线
  await page.click('button.tab:has-text("概览")')
  await page.waitForTimeout(300)
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.pt-canvas', { timeout: 15000 })
  await page.waitForFunction(() => document.querySelector('.pt-canvas')?.getAttribute('data-ready') === 'ready', { timeout: 20000 })
  await expect(page.locator('.pt')).toHaveAttribute('data-player', String(PLAYER_ID))
  await expect(page.locator('.pt-toggle:has-text("路线")')).toHaveClass(/on/)
  const painted2 = await canvasPaintedRatio(page)
  expect(painted2.ratio).toBeGreaterThan(MIN_PAINTED)
})

test('pro：个人战术回放 移动视口 390x844 有效像素 + 无横向溢出', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await ctx.newPage()
  try {
    setTier(PRO.email, 'max', 7)
    seedDeathPlayer()
    await login(page, PRO)
    const PLAYER_ID = await resolveDeathPlayer()
    await gotoMyTab(page, PLAYER_ID)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    const painted = await canvasPaintedRatio(page)
    expect(painted.ratio).toBeGreaterThan(MIN_PAINTED)
    await page.screenshot({ path: `${SHOTS}/route-mobile.png`, fullPage: true })
  } finally {
    await ctx.close()
  }
})
