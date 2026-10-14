// 6R8：团队同步回放 + 真实朝向/枪线 + 道具生命周期 + 可解释深度配合原语 E2E 门禁
// 用 mvr-acceptance@test.com（demo 19 = de_mirage，已回填 tactical artifact v2.0）
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = process.env.TEAM_SHOTS_DIR || 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

// demo 19 round 3：smoke=[8.83,30.89] fire=[6.05,11.55] he/flash 瞬时，max_duration≈61.92
const ROUND = 3
const SMOKE_WINDOW = [8.83, 30.89]
const FIRE_WINDOW = [6.05, 11.55]
let TOKEN = ''

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}

async function getToken() {
  if (TOKEN) return TOKEN
  const r = await fetch('http://localhost/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: ACCOUNT.email, password: ACCOUNT.password }),
  })
  const j = await r.json()
  TOKEN = j.access_token
  return TOKEN
}

async function replayData(demo, round) {
  const tok = await getToken()
  const r = await fetch(`http://localhost/api/demos/${demo}/team/replay?round_no=${round}`, { headers: { Authorization: `Bearer ${tok}` } })
  return r.json()
}

async function login(page, account) {
  await page.goto('/login')
  await page.fill('input[type="email"]', account.email)
  await page.fill('input[type="password"]', account.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

async function enterTeam(page, round = ROUND) {
  await page.goto(ACCOUNT.demo)
  await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 15000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.locator('.tm-sel').selectOption(String(round))
  await page.waitForFunction((r) => document.querySelector('.tm-sel')?.value === String(r), round, { timeout: 20000 })
  await page.waitForTimeout(700)
  await waitCanvasPainted(page)
}

// 6R16 确定性加固：等待画布真正绘制出可见像素（防 headless 高负载下渲染时序抖动，不削弱语义）。
async function waitCanvasPainted(page, timeout = 20000) {
  await page.waitForFunction(() => {
    const cv = document.querySelector('.tm-canvas'); if (!cv) return false
    const ctx = cv.getContext('2d'); const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
    let p = 0; for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 10) p++
    return p / (cv.width * cv.height) > 0.01
  }, { timeout })
}

async function scrub(page, sec) {
  await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, sec)
  await page.waitForTimeout(160)
}

async function canvAttr(page, attr) {
  return page.locator('.tm-canvas').getAttribute(attr)
}

test.beforeEach(() => { setTier(ACCOUNT.email, 'max', 7); grantTeamEntitlementForDemo(ACCOUNT.email, 19) })   // 6R15 收口：按目标 demo 播种团队权益门禁

test('Max：枪线由真实 yaw 计算且与移动方向不等同（gate b）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page, ACCOUNT)
  const data = await replayData(19, ROUND)
  // 前置：请求成功且带 yaw
  expect(data.players.length).toBe(10)
  const anyYaw = data.players.some((p) => p.points?.some((q) => q.yaw != null))
  expect(anyYaw).toBe(true)

  await enterTeam(page, ROUND)
  // data-gun 触发（当前有枪线绘制）
  expect(await canvAttr(page, 'data-gun')).toBe('1')

  // 计算：相邻采样点移动方向 vs 视角朝向（雷达屏幕方向 =(cos yaw,-sin yaw)），至少一个样本显著不同
  const differAcrossPlayers = await page.evaluate(({ data }) => {
    const toVec = (yaw) => { const r = yaw * Math.PI / 180; return [Math.cos(r), -Math.sin(r)] }
    let differCount = 0
    for (const p of data.players) {
      const pts = p.points || []
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1], b = pts[i]
        if (a.yaw == null || b.yaw == null || a.rx == null || b.rx == null) continue
        const dx = b.rx - a.rx, dy = b.ry - a.ry
        const mv = Math.hypot(dx, dy); if (mv < 0.003) continue
        const v = toVec(a.yaw)
        const dot = (v[0] * dx / mv) + (v[1] * dy / mv)
        // 移动方向（雷达）与 yaw 朝向夹角 > 20°
        const ang = Math.acos(Math.min(1, Math.max(-1, dot))) * 180 / Math.PI
        if (ang > 20) differCount++
      }
    }
    return differCount
  }, { data })
  expect(differAcrossPlayers).toBeGreaterThan(0)   // 枪线≠移动方向
})

test('Max：烟/火只在真实有效区间出现并消失（gate a）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page, ACCOUNT)
  await enterTeam(page, ROUND)

  // round3 实际窗口：fire=[6.05-11.55, 13.03-18.53, 25.97-31.47, 27.73-33.23]
  //                 smoke=[8.83-30.89, 14.47-36.53]
  // 都未开始（t=2 < 6.05 / < 8.83）
  await scrub(page, 2)
  expect(await canvAttr(page, 'data-smoke')).toBe('')
  expect(await canvAttr(page, 'data-fire')).toBe('')

  // 只有 fire 出现（t=7 ∈ [6.05,11.55]；7 < 8.83 smoke 未开始）
  await scrub(page, 7)
  expect(await canvAttr(page, 'data-fire')).toBe('1')
  expect(await canvAttr(page, 'data-smoke')).toBe('')

  // smoke 出现而 fire 消失（t=23：smoke 8.83-30.89 有效；fire 处 18.53-25.97 空档）
  await scrub(page, 23)
  expect(await canvAttr(page, 'data-smoke')).toBe('1')
  expect(await canvAttr(page, 'data-fire')).toBe('')

  // 两者都结束（t=40 > 36.53 / > 33.23）
  await scrub(page, 40)
  expect(await canvAttr(page, 'data-smoke')).toBe('')
  expect(await canvAttr(page, 'data-fire')).toBe('')

  // 关闭烟开关后，即使处于有效区间也不画烟
  await scrub(page, 23)
  await page.locator('.tm-toggle:has-text("烟")').click()
  await page.waitForTimeout(160)
  expect(await canvAttr(page, 'data-smoke')).toBe('')
  await page.locator('.tm-toggle:has-text("烟")').click()   // 恢复
})

test('Max：同步 10 人共享时钟、阵亡态（gate c）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page, ACCOUNT)
  await enterTeam(page, ROUND)

  // 队伍计数（队A+队B=10）
  const summaryA = Number(await page.locator('.tm-summary').innerText().then((t) => (t.match(/(\d+) 队A/) || [])[1]))
  const summaryB = Number(await page.locator('.tm-summary').innerText().then((t) => (t.match(/(\d+) 队B/) || [])[1]))
  expect(summaryA + summaryB).toBe(10)
  // 玩家 chips（全部 + 10）
  expect(await page.locator('.tm-chip').count()).toBeGreaterThanOrEqual(10)

  // 至少一个玩家阵亡态（chip.dead 或 outcome=dead 时不显示当前球）
  const data = await replayData(19, ROUND)
  const deadCount = data.players.filter((p) => p.outcome === 'dead').length
  expect(deadCount).toBeGreaterThan(0)

  // 共享时钟：scrub max === 后端 max_duration（同一回放时钟）
  const max = Number(await page.locator('.tm-scrub').getAttribute('max'))
  expect(Math.abs(max - data.max_duration)).toBeLessThan(0.6)
})

test('Max：freeze/buy time 被跳过（00:00 = freeze_end_tick）（gate d）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page, ACCOUNT)
  await enterTeam(page, ROUND)
  const data = await replayData(19, ROUND)
  const max = Number(await page.locator('.tm-scrub').getAttribute('max'))
  // 时基 = (end_tick - freeze_end_tick)/tick_rate；若含 start_tick~freeze_end 的买枪/冻结时间则会更长
  const fromFreeze = (data.round.end_tick - data.round.freeze_end_tick) / data.tick_rate
  expect(Math.abs(max - fromFreeze)).toBeLessThan(0.6)
  expect(Math.abs(max - data.max_duration)).toBeLessThan(0.6)
})

test('Max：主界面无裸 tick / 世界坐标（gate e）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page, ACCOUNT)
  await enterTeam(page, ROUND)

  // F3P 后时间轴位于地图上方，当前主视图容器是 `.tm-main`（不再是仅含 canvas 的 `.tm-stage`）。
  // 主界面可见文本不应含裸 tick/坐标；seek 后文本时钟必须与画布权威时间一致。
  const mainVisible = await page.locator('.tm-main').innerText()
  expect(mainVisible).not.toMatch(/start_tick=|end_tick=|tick_rate=|world|pos=\(|scale=/)
  await scrub(page, 12.5)
  const canvasTime = Number(await canvAttr(page, 'data-time'))
  expect(Math.abs(canvasTime - 12.5)).toBeLessThan(0.05)
  await expect(page.locator('.tm-time')).toHaveText(/^当前 00:12 \/ \d{2}:\d{2}$/)
  // 技术诊断默认折叠（其内容不可见）
  const diagOpen = await page.locator('.tm-diag').evaluate((el) => el.open)
  expect(diagOpen).toBe(false)
})

test('Max：可解释深度配合原语定位到回放（gate f）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page, ACCOUNT)
  await enterTeam(page, ROUND)
  await page.locator('.prim-card').scrollIntoViewIfNeeded()

  // 原语列表存在（等待异步 `/tactical/primitives` 返回渲染），且 crossfire 只命名候选并带 reason
  await page.waitForSelector('.cp-item', { timeout: 15000 })
  const items = page.locator('.cp-item')
  expect(await items.count()).toBeGreaterThan(0)

  // 找一个带 time 的条目（可定位），从条目及其所属回合组取 round / time
  const locateBtn = page.locator('.cp-locate:not([disabled])').first()
  const itemLi = locateBtn.locator('xpath=ancestor::li')
  const roundEl = page.locator('.cp-round').filter({ has: locateBtn }).first()
  const roundText = await roundEl.locator('.cp-round-no').innerText()
  const roundMatch = roundText.match(/R(\d+)/)
  expect(roundMatch).not.toBeNull()
  const targetRound = Number(roundMatch[1])
  expect(targetRound).toBeGreaterThan(0)
  // 该条目的 mm:ss（cp-time）
  const itemTimeText = await itemLi.locator('.cp-time').innerText()
  const mTime = itemTimeText.match(/(\d{2}):(\d{2})/)
  await locateBtn.click()
  // 6R16 确定性加固：locate 可能切换到不同回合（会重载回放），等待回合选择器重新出现并完成绘制再断言，
  // 避免 90s 超时的渲染/重载时序抖动（不削弱语义）。
  await page.waitForSelector('.tm-sel', { timeout: 25000 })
  await waitCanvasPainted(page)
  await page.waitForFunction((r) => document.querySelector('.tm-sel')?.value === String(r), targetRound, { timeout: 20000 })

  // 回放定位：回合选择器切换到该回合，且当前时间被 scrub 到 mm:ss 目标
  const sel = await page.locator('.tm-sel').inputValue()
  expect(Number(sel)).toBe(targetRound)
  let expectSec = 0
  if (mTime) {
    const mm = Number(mTime[1]); const ss = Number(mTime[2]); expectSec = mm * 60 + ss
    const t = Number(await canvAttr(page, 'data-time'))
    // 定位到该原语的时刻（显示取整到 mm:ss，容差 ±3s）
    expect(Math.abs(t - expectSec)).toBeLessThan(3)
  }

  // crossfire 候选带 reason（独立查证）
  const cf = page.locator('.cp-item', { hasText: '交叉火力候选' }).first()
  if (await cf.count()) {
    await expect(cf.locator('.cp-reason')).toContainText(/墙体|navmesh|候选/)
  }
})

test('Max：reduced-motion 提供当前时刻静态态势，不强制动画', async ({ browser }) => {
  setTier(ACCOUNT.email, 'max', 7)
  const ctx = await browser.newContext({ reducedMotion: 'reduce' })
  const page = await ctx.newPage()
  try {
    await login(page, ACCOUNT)
    await enterTeam(page, ROUND)
    // 播放键应禁用（不自动动画），画布已绘制静态快照。6R16：等待画布真正绘制并校验 data-reduced-motion。
    await waitCanvasPainted(page)
    const disabled = await page.locator('.tm-play').isDisabled()
    expect(disabled).toBe(true)
    const ready = await canvAttr(page, 'data-ready')
    expect(ready).toBe('ready')
    await page.waitForFunction(() => document.querySelector('.tm-canvas')?.getAttribute('data-gun') === '1', { timeout: 10000 })
    expect(await page.locator('.tm-canvas').getAttribute('data-gun')).toBe('1')
    await page.screenshot({ path: `${SHOTS}/6r8-reduced-motion.png` })
  } finally { await ctx.close() }
})

test('Max：移动视口 390×844 有效像素 + 无横向溢出', async ({ browser }) => {
  setTier(ACCOUNT.email, 'max', 7)
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await ctx.newPage()
  try {
    await login(page, ACCOUNT)
    await enterTeam(page, ROUND)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    // 6R16 确定性加固：移动视口下等待画布绘制完成再量有效像素，避免渲染时序抖动
    await waitCanvasPainted(page)
    const painted = await page.locator('.tm-canvas').evaluate((cv) => {
      const ctx = cv.getContext('2d'); const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
      let p = 0; for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 10) p++
      return p / (cv.width * cv.height)
    })
    expect(painted).toBeGreaterThan(0.01)
    await page.screenshot({ path: `${SHOTS}/6r8-mobile.png`, fullPage: true })
  } finally { await ctx.close() }
})

