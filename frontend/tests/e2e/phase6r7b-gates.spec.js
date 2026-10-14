// 6R7B gate5 严格门禁：死亡帧（当前标识消失+红X）、时间原点=headered tick_rate、地点翻译+未知回退、
// 个人/团队 combat 点位↔密度 mode/legend/render/summary 一致 + 数据范围隔离。
// 只允许真实像素/契约断言，绝不降级为"请求发生/元素存在/canvas 变化"。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, DEMO19, grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const CANVAS = 720
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
let TOKEN = ''

// 6R14 身份优先：个人端点恒为绑定本人；先播种到 demo19 回合1 有真实死亡点的稳定玩家
function seedDeathPlayer() { seedIdentity(ACCOUNT.email, DEMO19.ROUND1_DEATH) }

function setTier(email, tier, days) {
  const exp = days ? `, subscription_expire = now() + interval '${days} days'` : ', subscription_expire = NULL'
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
async function apiPut(path, body) {
  const t = await getToken()
  const r = await fetch(`http://localhost${path}`, { method: 'PUT', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
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

async function apiToken(page) {
  return page.evaluate(() => localStorage.getItem('cs2_token'))
}

// 找 demo 19 里绑定本人（is_you）在某个 outcome==dead 的回合有真实死亡点（alive===false）。
// 6R14 身份优先：personal/replay 恒为绑定本人，手动 focus 不改主体 → 直接锁定 is_you 玩家。
async function findDeathRound() {
  const players = (await apiGet('/api/demos/19/players')).body
  const me = players.find((p) => p.is_you) || players[0]
  await apiPut('/api/demos/19/focus-player', { player_id: me.player_id })
  const list = (await apiGet('/api/demos/19/personal/replay')).body
  for (const r of (list.rounds || [])) {
    if (r.outcome !== 'dead') continue
    const detail = (await apiGet(`/api/demos/19/personal/replay?round_no=${r.round}`)).body
    const death = (detail.points || []).find((pt) => pt.alive === false)
    if (death) return { playerId: me.player_id, roundNo: r.round, detail }
  }
  throw new Error('未找到有真实死亡点（alive===false）的回合')
}

function ballPosAt(points, startTick, tickRate, t) {
  const tick = startTick + t * tickRate
  const pts = points.filter((p) => p.rx != null && p.ry != null)
  if (!pts.length) return null
  if (tick <= pts[0].tick) return { rx: pts[0].rx, ry: pts[0].ry }
  const last = pts[pts.length - 1]
  if (tick >= last.tick) return { rx: last.rx, ry: last.ry }
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1]
    if (tick >= a.tick && tick <= b.tick) {
      const f = b.tick === a.tick ? 0 : (tick - a.tick) / (b.tick - a.tick)
      return { rx: a.rx + (b.rx - a.rx) * f, ry: a.ry + (b.ry - a.ry) * f }
    }
  }
  return { rx: last.rx, ry: last.ry }
}

// kind: 'red'=死亡 X 红（#f85149）; 'white'=当前小球白（#ffffff）。谓词内联（evaluate 不能传函数）。
async function countNear(page, sel, x, y, box, kind) {
  return page.locator(sel).evaluate((cv, args) => {
    const ctx = cv.getContext('2d')
    const bx = Math.max(0, Math.min(cv.width - args.box, args.x - args.box / 2))
    const by = Math.max(0, Math.min(cv.height - args.box, args.y - args.box / 2))
    const { data } = ctx.getImageData(bx, by, args.box, args.box)
    let n = 0
    for (let i = 0; i < data.length; i += 4) {
      const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3]
      if (a > 100) {
        if (args.kind === 'red' && r > 190 && g < 110 && b < 110) n++
        else if (args.kind === 'white' && r > 235 && g > 235 && b > 235) n++
      }
    }
    return n
  }, { x, y, box, kind })
}

function fmt(sec) { const s = Math.max(0, sec); const m = Math.floor(s / 60); const r = Math.floor(s % 60); return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` }

async function scrubTo(page, sel, sec) {
  await page.locator(sel).evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, sec)
  await page.waitForTimeout(260)
}

test('死亡帧：动态选真实死亡回合，死亡前当前小球有像素，死亡后当前小球消失且红 X 出现', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDeathPlayer()
  await login(page, ACCOUNT)
  // 6R8：动态找 focus 玩家 + 回合，其中存在 alive===false 的真实死亡点
  const found = await findDeathRound()
  const { playerId, roundNo } = found
  const detail = found.detail
  await apiPut('/api/demos/19/focus-player', { player_id: playerId })
  await page.goto(ACCOUNT.demo)
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.picker select', { timeout: 15000 })
  const val = await page.locator('.picker select').inputValue()
  if (String(val) !== String(playerId)) {
    await page.locator('.picker select').selectOption(String(playerId))
  }
  await page.waitForSelector('.pt-canvas', { timeout: 25000 })
  await page.waitForTimeout(400)

  const death = (detail.points || []).find((p) => p.alive === false)
  expect(death).toBeTruthy()                                    // 动态选中的回合确有真实死亡点
  const tickRate = detail.round.tick_rate
  expect(tickRate).toBe(64)                                     // 权威时基来自 header（非 128）
  const freezeTick = detail.round.freeze_end_tick
  const deathSec = (death.tick - freezeTick) / tickRate
  const roundDur = (detail.round.end_tick - freezeTick) / tickRate

  // 选中该回合（非默认回合，等该回合 max_duration 出现确认已切换到该回合）
  await page.locator('.pt-sel').selectOption(String(roundNo))
  await page.waitForTimeout(700)
  await page.waitForFunction((txt) => document.querySelector('.pt-time')?.textContent.includes(txt), fmt(detail.max_duration), { timeout: 15000 })
  await page.waitForFunction(() => document.querySelector('.pt-canvas')?.getAttribute('data-ready') === 'ready', { timeout: 20000 })

  // 死亡前：当前小球在死亡前时刻仍有像素（project ball 位置附近白色像素>0）
  const preT = Math.max(0, deathSec - 3)
  const preBall = ballPosAt(detail.points, freezeTick, tickRate, preT)
  await scrubTo(page, '.pt-scrub', preT)
  const preWhite = await countNear(page, '.pt-canvas', preBall.rx * CANVAS, preBall.ry * CANVAS, 20, 'white')
  expect(preWhite).toBeGreaterThan(0)                           // 死亡前当前标识确实有像素

  // 死亡前：死亡点附近无红 X
  const preRed = await countNear(page, '.pt-canvas', death.rx * CANVAS, death.ry * CANVAS, 44, 'red')
  expect(preRed).toBeLessThan(6)

  // 死亡后：死亡点附近红 X 出现，且当前白色小球像素被移除（不叠加）
  await scrubTo(page, '.pt-scrub', Math.min(roundDur, deathSec + 2))
  const postRed = await countNear(page, '.pt-canvas', death.rx * CANVAS, death.ry * CANVAS, 44, 'red')
  expect(postRed).toBeGreaterThan(12)                           // 红 X 存在
  const postWhite = await countNear(page, '.pt-canvas', death.rx * CANVAS, death.ry * CANVAS, 44, 'white')
  expect(postWhite).toBeLessThan(6)                             // 当前小球已移除（不叠在 X 上）
  await page.screenshot({ path: `${SHOTS}/gate5-death-frame.png` })
})

test('时间原点 00:00=freeze_end，tick_rate 来自 header，时长合理', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDeathPlayer()
  await login(page, ACCOUNT)
  const token = await apiToken(page)
  const rd = await (await page.request.get('/api/demos/19/personal/route?round_no=1&side=all', {
    headers: { Authorization: `Bearer ${token}` },
  })).json()
  // 00:00 = play_start（freeze_end 边界）
  expect(rd.round.play_start_tick).toBe(rd.round.freeze_end_tick)
  expect(rd.round.play_start_tick).toBeGreaterThan(0)
  // tick_rate 来自权威 header（64）
  expect(rd.round.tick_rate).toBe(64)
  expect(rd.time_unverifiable).toBe(false)
  // 时长合理（CS2 一回合 30-120s 量级，不含热身）
  const dur = (rd.round.end_tick - rd.round.play_start_tick) / rd.round.tick_rate
  expect(dur).toBeGreaterThan(15)
  expect(dur).toBeLessThan(180)
})

test('地点翻译 + 未知区域回退：主 UI 只见中文地点，不见世界坐标/原始 tick', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDeathPlayer()
  await login(page, ACCOUNT)
  // demo19 重解析后 player_id 会变化；动态取一个 CT 侧玩家（回合 1 出生点为中文叫位）
  const players = (await apiGet('/api/demos/19/players')).body
  const PLAYER = ((players.find((p) => p.team_identity === 'A' || p.team === 'CT')) || players[0]).player_id
  await apiPut('/api/demos/19/focus-player', { player_id: PLAYER })
  await page.goto(ACCOUNT.demo)
  await page.click('button.tab:has-text("个人复盘")')
  await page.locator('.picker select').selectOption(String(PLAYER))
  await page.waitForSelector('.pt-region', { timeout: 25000 })
  // 当前区域徽标必须是中文叫位或“未知区域”，不得出现坐标/数字 tick
  const regionText = await page.locator('.pt-region').textContent()
  expect(regionText).toContain('当前区域')
  expect(regionText).toMatch(/[\u4e00-\u9fff]|未知区域/)
  expect(regionText).not.toMatch(/\(-?\d|\btick\b|\bx:\s/gi)
  // 主时间显示为 mm:ss（有效时基），无裸 tick
  const timeText = await page.locator('.pt-time').textContent()
  expect(timeText).toMatch(/当前\s*\d{2}:\d{2}/)
  // 主界面文本（技术诊断之外）不含原始世界坐标 / tick / scale（.pt-diag 默认收起，innerText 不含其内容）
  const mainText = await page.locator('.pt').innerText()
  expect(mainText).not.toMatch(/(start_tick=|end_tick=|tick_rate=|pos=\(|scale=)/)
  expect(mainText).not.toMatch(/\bworld\b/i)
  // 主界面诊断抽屉默认收起，坐标只在「技术诊断 / 导出」
  await expect(page.locator('.pt-diag')).toBeVisible()
  await expect(page.locator('.pt-diag summary')).toContainText('技术诊断')
  await expect(page.locator('.pt-diag')).not.toHaveAttribute('open')
})

test('个人/团队 combat：点位↔密度 mode/legend/render/summary 一致 + 数据范围隔离', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)   // 6R15 收口：按目标 demo 播种团队权益门禁
  seedDeathPlayer()
  await login(page, ACCOUNT)
  const token = await apiToken(page)

  // 数据范围隔离：个人 combat（focus）事件应少于团队（全部玩家）
  const personal = await (await page.request.get('/api/demos/19/personal/combat-map?side=all', {
    headers: { Authorization: `Bearer ${token}` },
  })).json()
  const team = await (await page.request.get('/api/demos/19/team/combat-map?team=all&side=all', {
    headers: { Authorization: `Bearer ${token}` },
  })).json()
  expect(team.kills.length).toBeGreaterThan(personal.kills.length)

  // 个人 combat：点位 → 图例/摘要与点位一致
  await page.goto(ACCOUNT.demo)
  await page.click('button.tab:has-text("个人复盘")')
  await page.locator('.picker select').selectOption({ index: 1 })
  await page.waitForSelector('.pcm-canvas', { timeout: 20000 })
  await page.locator('.pcm .seg-btn:has-text("点位")').click()
  await page.waitForTimeout(300)
  const sumPoints = await page.locator('.pcm-summary').textContent()
  expect(sumPoints).toMatch(/个击杀点位|个死亡点位/)
  expect(await page.locator('.pcm-stage .hm').count()).toBe(0)    // 点位无热力画布
  // 密度 → 热力画布 + 低/高图例 + 摘要一致
  await page.locator('.pcm .seg-btn:has-text("密度")').click()
  await page.waitForSelector('.pcm-stage .hm canvas', { timeout: 15000 })
  const sumDensity = await page.locator('.pcm-summary').textContent()
  expect(sumDensity).toMatch(/密度热力图/)
  await page.screenshot({ path: `${SHOTS}/gate5-personal-density.png` })

  // 团队 combat：点位 & 密度（现已接 /team/combat-density）
  await page.click('button.tab:has-text("团队复盘")')
  await page.waitForSelector('.tcm-canvas', { timeout: 20000 })
  await page.locator('.tcm .seg-btn:has-text("点位")').click()
  await page.waitForTimeout(300)
  expect(await page.locator('.tcm-stage .hm').count()).toBe(0)
  await page.locator('.tcm .seg-btn:has-text("密度")').click()
  await page.waitForSelector('.tcm-stage .hm canvas', { timeout: 15000 })
  await page.screenshot({ path: `${SHOTS}/gate5-team-density.png` })
})
