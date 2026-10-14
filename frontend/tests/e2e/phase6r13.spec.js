// 6R13 严格 E2E：击杀播报 / 投掷者归属 / 烟火烧真实生命周期 / 真实 player_blind / HE 受伤 / 确定性 seek。
// 用 mvr-acceptance@test.com（demo 19 = de_mirage，已回填 tactical artifact v3.0）。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, demoPlayerSteam, grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
let TOKEN = ''

// 6R14 身份优先：个人回放需先绑定 demo19 首位玩家作「我」
function seedDemo19() { seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, 19)) }

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
// 在演示 19 上找一个满足谓词（对某回合 API 数据为真）的回合
async function findRound(demoId, pred) {
  for (let r = 1; r <= 6; r++) {
    const d = await apiGet(`/api/demos/${demoId}/team/replay?round_no=${r}`)
    if (d.status === 200 && pred(d.body)) return r
  }
  return null
}

// 从 API body 内插某玩家在 timeSec 时刻的雷达坐标 [0,1]（复现前端插值；只读，不用隐藏钩子）
function interpRxRy(body, playerId, timeSec) {
  const player = (body.players || []).find((p) => p.player_id === playerId)
  if (!player || !player.points || !player.points.length) return null
  const freeze = body.round.freeze_end_tick ?? body.round.start_tick ?? 0
  const rate = Number(body.tick_rate)
  if (!Number.isFinite(rate) || rate <= 0) return null
  const target = freeze + timeSec * rate
  const pts = player.points
  let lo = 0, hi = pts.length - 1
  while (lo < hi) { const mid = (lo + hi) >> 1; if (pts[mid].tick < target) lo = mid + 1; else hi = mid }
  const b = pts[lo], a = pts[Math.max(0, lo - 1)]
  if (target >= a.tick && target <= b.tick && a.tick !== b.tick) {
    const f = (target - a.tick) / (b.tick - a.tick)
    return { rx: a.rx + (b.rx - a.rx) * f, ry: a.ry + (b.ry - a.ry) * f }
  }
  return { rx: b.rx, ry: b.ry }
}

// 整张 canvas 像素校验和（确定性，不依赖 data-* 钩子）
async function canvasChecksum(page) {
  return page.locator('.tm-canvas').evaluate((c) => {
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data
    let h = 0
    for (let i = 0; i < d.length; i += 101) h = (h * 31 + d[i]) % 1000000007
    return h
  })
}

// 受害者位置附近（crop box）内、命中致盲环色族的像素数（自/队/敌闪 分开匹配）
async function ringColorPixels(page, cx, cy, family) {
  return page.locator('.tm-canvas').evaluate((c, arg) => {
    const { cx, cy, family } = arg
    const ctx = c.getContext('2d')
    const box = 64
    const x0 = Math.max(0, Math.floor(cx - box / 2)), y0 = Math.max(0, Math.floor(cy - box / 2))
    const w = Math.min(c.width - x0, box), h = Math.min(c.height - y0, box)
    const d = ctx.getImageData(x0, y0, w, h).data
    let n = 0
    for (let i = 0; i < d.length; i += 4) {
      const r = d[i], g = d[i + 1], b = d[i + 2]
      if (family === 'enemy' && r > 150 && g < 140 && b < 140) n += 1
      else if (family === 'team' && r < 150 && g > 110 && b > 200) n += 1
      else if (family === 'self' && r > 180 && g > 180 && b > 180) n += 1
    }
    return n
  }, { cx, cy, family })
}

test('6R13a：击杀播报契约——无"真实枪线"过声称 + 图层开关存在（gate 1）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)   // 6R15 收口：按目标 demo 播种团队权益门禁
  await login(page)
  await enterTeam(page, 1)
  // 图层开关含"击杀播报"与"真实致盲"
  await expect(page.locator('.tm-toggles .tm-toggle:has-text("击杀播报")')).toHaveCount(1)
  // 团队回放有烟/火/闪/手雷独立图层开关
  for (const label of ['烟', '火', '闪', '手雷']) {
    await expect(page.locator(`.tm-toggles .tm-toggle:has-text("${label}")`)).toHaveCount(1)
  }
  // 不出现"真实枪线"/"真实朝向·枪线"（6R12 语义拆分保持）
  const toggles = await page.locator('.tm-toggles').innerText()
  expect(toggles).not.toContain('真实枪线')
  expect(toggles).not.toContain('真实朝向·枪线')
  await page.locator('.tm-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r13-killfeed-labels.png` })
})

test('6R13b：击杀播报在真实时刻出现且随时间淡出（确定性，gate 2）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const d = await apiGet('/api/demos/19/team/replay?round_no=1')
  const kf = (d.body.killfeed || []).filter((k) => k.time_sec != null)
  expect(kf.length).toBeGreaterThan(0)
  const first = kf[0]
  await enterTeam(page, 1)
  // 在击杀时刻 → data-killfeed=1
  await scrub(page, first.time_sec)
  expect(await canvAttr(page, 'data-killfeed')).toBe('1')
  // 在击杀之前 5s（超过 KILLFEED_SEC 窗口）→ data-killfeed=''
  await scrub(page, Math.max(0, first.time_sec - 5))
  expect(await canvAttr(page, 'data-killfeed')).toBe('')
  // 拖动回击杀时刻 → 又出现（seek 确定性重建，不残留）
  await scrub(page, first.time_sec)
  expect(await canvAttr(page, 'data-killfeed')).toBe('1')
})

test('6R13c：真实致盲来自 player_blind——active frame 截图 + data-blind 切换（gate 3）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const round = await findRound(19, (b) => (b.blinds || []).some((x) => x.start_sec != null && x.end_sec != null && x.victim_player_id != null))
  expect(round).toBeTruthy()
  const d = await apiGet(`/api/demos/19/team/replay?round_no=${round}`)
  const bl = (d.body.blinds || []).filter((b) => b.start_sec != null && b.end_sec != null && b.victim_player_id != null)
  const one = bl.reduce((a, b) => ((b.end_sec - b.start_sec) > (a.end_sec - a.start_sec) ? b : a))
  await enterTeam(page, round)
  // 进入致盲窗口 → data-blind=1，并在 active frame 立即截图（此时环/标签/倒计时可见）
  await scrub(page, one.start_sec + 0.05)
  expect(await canvAttr(page, 'data-blind')).toBe('1')
  await page.locator('.tm-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r13-blind-active.png` })
  // 只在结束 frame 保留 data-blind='' 的状态断言（不再截"已结束"图）
  await scrub(page, one.end_sec + 0.1)
  expect(await canvAttr(page, 'data-blind')).toBe('')
})

test('6R13c-vis：致盲可见性门禁（不依赖隐藏钩子）——active 帧与结束帧像素差异 + 受害者处环色像素（gate 3b）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const round = await findRound(19, (b) => (b.blinds || []).some((x) => x.start_sec != null && x.end_sec != null && x.victim_player_id != null))
  expect(round).toBeTruthy()
  const d = await apiGet(`/api/demos/19/team/replay?round_no=${round}`)
  const bl = (d.body.blinds || []).filter((b) => b.start_sec != null && b.end_sec != null && b.victim_player_id != null)
  const one = bl.reduce((a, b) => ((b.end_sec - b.start_sec) > (a.end_sec - a.start_sec) ? b : a))
  await enterTeam(page, round)
  // active frame（致盲环渲染中）
  await scrub(page, one.start_sec + 0.05)
  const activeChecksum = await canvasChecksum(page)
  // ended frame（致盲已结束，环消失）
  await scrub(page, one.end_sec + 0.1)
  const endedChecksum = await canvasChecksum(page)
  expect(activeChecksum).not.toBe(endedChecksum)   // 确定性像素差异：结束时状态确已改变（环消失）
  // 回到 active frame，校验受害者位置的环色像素（环/标签/倒计时在画面内可辨）
  await scrub(page, one.start_sec + 0.05)
  const pos = interpRxRy(d.body, one.victim_player_id, one.start_sec + 0.05)
  expect(pos).toBeTruthy()
  const family = one.flash_class === 'enemy' ? 'enemy' : (one.flash_class === 'team' ? 'team' : 'self')
  const cnt = await ringColorPixels(page, pos.rx * 720, pos.ry * 720, family)
  expect(cnt).toBeGreaterThanOrEqual(8)   // 环/标签/倒计时应以环色出现在受害者附近
  await page.locator('.tm-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r13-blind-active.png` })
})

test('6R13c-mobile：移动端 390px 致盲可见性——active frame 环色像素 + 截图（gate 3c）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page)
  const round = await findRound(19, (b) => (b.blinds || []).some((x) => x.start_sec != null && x.end_sec != null && x.victim_player_id != null))
  expect(round).toBeTruthy()
  const d = await apiGet(`/api/demos/19/team/replay?round_no=${round}`)
  const bl = (d.body.blinds || []).filter((b) => b.start_sec != null && b.end_sec != null && b.victim_player_id != null)
  const one = bl.reduce((a, b) => ((b.end_sec - b.start_sec) > (a.end_sec - a.start_sec) ? b : a))
  await enterTeam(page, round)
  await scrub(page, one.start_sec + 0.05)
  const pos = interpRxRy(d.body, one.victim_player_id, one.start_sec + 0.05)
  expect(pos).toBeTruthy()
  const family = one.flash_class === 'enemy' ? 'enemy' : (one.flash_class === 'team' ? 'team' : 'self')
  const cnt = await ringColorPixels(page, pos.rx * 720, pos.ry * 720, family)
  expect(cnt).toBeGreaterThanOrEqual(8)
  await page.locator('.tm-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r13-blind-mobile.png` })
})

test('6R13d：投掷者归属——诊断显示投掷者名（gate 4）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const round = await findRound(19, (b) => (b.props || []).some((p) => p.thrower_name != null))
  expect(round).toBeTruthy()
  const d = await apiGet(`/api/demos/19/team/replay?round_no=${round}`)
  const props = (d.body.props || []).filter((p) => p.thrower_name != null)
  await enterTeam(page, round)
  await page.locator('.tm-diag summary').click()
  const diag = await page.locator('.tm-diag-body').innerText()
  expect(diag).toContain('投掷者=')
  const hasName = props.some((p) => diag.includes(p.thrower_name))
  expect(hasName).toBe(true)
})

test('6R13e：HE 受伤——若存在则 data-hedamage 在受伤窗口内出现（gate 5）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const round = await findRound(19, (b) => (b.he_damage || []).some((h) => h.time_sec != null && h.victim_player_id != null))
  if (!round) { test.skip('demo19 无 HE 受伤样本') }
  const d = await apiGet(`/api/demos/19/team/replay?round_no=${round}`)
  const he = (d.body.he_damage || []).filter((h) => h.time_sec != null && h.victim_player_id != null)
  await enterTeam(page, round)
  await scrub(page, he[0].time_sec + 0.05)
  expect(await canvAttr(page, 'data-hedamage')).toBe('1')
})

test('6R13f：个人回放——只显示所选玩家相关击杀 + 我的道具开关（gate 6）', async ({ page }) => {
  setTier(ACCOUNT.email, 'pro', 7)
  seedDemo19()
  await login(page)
  // 选焦点玩家（第一个有个人数据的）
  const players = (await apiGet('/api/demos/19/players')).body
  expect(players.length).toBeGreaterThan(0)
  const me = players[0]
  await page.goto(ACCOUNT.demo)
  await page.waitForSelector('.map-name', { timeout: 15000 })
  await page.click('button.tab:has-text("个人")')
  await page.waitForSelector('.pt-canvas', { timeout: 25000 })
  // 拉取个人回合数据并断言只含焦点玩家相关内容
  const pd = await apiGet('/api/demos/19/personal/replay?round_no=1')
  expect(pd.status === 200 || pd.status === 409).toBe(true) // 可能有回合无轨迹
  if (pd.status === 200 && pd.body.killfeed) {
    for (const k of pd.body.killfeed) {
      expect(k.attacker_player_id === me.player_id || k.victim_player_id === me.player_id).toBe(true)
    }
  }
  // 个人图层开关含"我的道具"
  await expect(page.locator('.pt-toggles .pt-toggle:has-text("我的道具")')).toHaveCount(1)
  await expect(page.locator('.pt-toggles .pt-toggle:has-text("击杀播报")')).toHaveCount(1)
  await expect(page.locator('.pt-toggles .pt-toggle:has-text("真实致盲")')).toHaveCount(1)
})

test('6R13g：reduced-motion + 移动端密度（不遮驾驶舱/控制条，gate 7）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page)
  await enterTeam(page, 1)
  // 移动端：killfeed 开关与时间轴仍可操作
  await expect(page.locator('.tm-toggles')).toBeVisible()
  await expect(page.locator('.tm-scrub')).toBeVisible()
  await page.locator('.tm-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r13-mobile.png` })
})
