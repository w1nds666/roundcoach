// 6R8 返工：个人战术回放严格 E2E（scope 仅所选玩家 / 切换玩家不串缓存 / 我的道具·我的朝向开关真实影响 Canvas）
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, DEMO19, boundPlayerId } from './_helpers6r14.js'

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const SHOTS = process.env.PERSONAL_SHOTS_DIR || 'tests/e2e/screenshots'
let TOKEN = ''

// 6R14 身份优先：个人回放恒为绑定本人；先播种到 demo19 回合3 有 smoke/fire 道具的稳定玩家
function seedSmokePlayer() { seedIdentity(ACCOUNT.email, DEMO19.ROUND3_SMOKE) }

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
async function apiPut(path, body) {
  const t = await getToken()
  const r = await fetch(`http://localhost${path}`, { method: 'PUT', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  return r.json()
}
async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterPersonal(page) {
  await page.goto(ACCOUNT.demo)
  await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 15000 })
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.pt-canvas', { timeout: 25000 })
  await page.waitForFunction(() => document.querySelector('.pt-canvas')?.getAttribute('data-ready') === 'ready', { timeout: 20000 })
}
async function selectPlayer(page, playerId) {
  await page.locator('.picker select').selectOption(String(playerId))
  await page.waitForFunction((pid) => document.querySelector('.pt')?.getAttribute('data-player') === String(pid), playerId, { timeout: 20000 })
  await page.waitForTimeout(700)
}
async function scrub(page, sec) {
  await page.locator('.pt-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, sec)
  await page.waitForTimeout(180)
}
async function canvAttr(page, attr) { return page.locator('.pt-canvas').getAttribute(attr) }

// 挑一个在 demo19 回合3 有投掷道具（烟/火）的玩家；否则用第一个
async function pickPropPlayer(players) {
  for (const p of players) {
    const r = await apiGet(`/api/demos/19/personal/replay?round_no=3`)
    // focus 需先设置到该 player 才能拿到其数据；简化：改用 players 列表 + 试 focus
    await apiPut('/api/demos/19/focus-player', { player_id: p.player_id })
    const d = await apiGet(`/api/demos/19/personal/replay?round_no=3`)
    const hasInterval = (d.body.props || []).some((pr) => pr.type === 'smoke' || pr.type === 'fire')
    if (hasInterval) return p
  }
  return players[0]
}

test('个人战术回放：scope 仅含所选玩家（非十人视图）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedSmokePlayer()
  await login(page)
  const players = (await apiGet('/api/demos/19/players')).body
  const target = await pickPropPlayer(players)
  await apiPut('/api/demos/19/focus-player', { player_id: target.player_id })
  await enterPersonal(page)
  await selectPlayer(page, target.player_id)

  // 后端 scope：personal/replay 只返回一个玩家 + 只含其投掷道具
  const d = await apiGet(`/api/demos/19/personal/replay?round_no=3`)
  expect(d.body.player.player_id).toBe(target.player_id)
  expect(d.body.props.every((pr) => pr.thrower_player_id === target.player_id)).toBe(true)
  expect(d.body.props.length).toBeGreaterThan(0)
  expect(JSON.stringify(d.body).includes('steam')).toBe(false)

  // 前端 scope：data-player = 所选玩家；不是团队十人视图（无 .tm-chip 十人 chip）
  expect(await page.locator('.pt').getAttribute('data-player')).toBe(String(target.player_id))
  expect(await page.locator('.pt-canvas').getAttribute('data-ready')).toBe('ready')
  expect(await page.locator('.tm-chip').count()).toBe(0)   // 个人视图不出现十人团队 chips
  await page.locator('.pt-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r8-personal-scope.png` })
})

test('个人战术回放：我的朝向 / 我的道具 开关真实影响 Canvas', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedSmokePlayer()
  await login(page)
  const players = (await apiGet('/api/demos/19/players')).body
  const target = await pickPropPlayer(players)
  await apiPut('/api/demos/19/focus-player', { player_id: target.player_id })
  await enterPersonal(page)
  await selectPlayer(page, target.player_id)
  // 选回合 3（该玩家有 烟/火）
  await page.locator('.pt-sel').selectOption('3')
  await page.waitForTimeout(700)
  // 找一个烟/火同时处于有效区间的时刻（用该回合 API 窗口）
  const d = await apiGet(`/api/demos/19/personal/replay?round_no=3`)
  const prop = (d.body.props || []).find((pr) => pr.type === 'smoke' && pr.active_start_sec != null && pr.active_end_sec != null)
  const t = prop ? (prop.active_start_sec + prop.active_end_sec) / 2 : 15
  await scrub(page, t)

  // 我的道具开（默认）→ 有烟；关 → 无烟
  expect(await canvAttr(page, 'data-smoke')).toBe('1')
  await page.locator('.pt-toggle:has-text("我的道具")').click()
  await page.waitForTimeout(180)
  expect(await canvAttr(page, 'data-smoke')).toBe('')
  await page.locator('.pt-toggle:has-text("我的道具")').click()
  await page.waitForTimeout(180)

  // 我的朝向开 → data-gun=1；关 → data-gun=''
  expect(await canvAttr(page, 'data-gun')).toBe('1')
  await page.locator('.pt-toggle:has-text("视角朝向")').click()
  await page.waitForTimeout(180)
  expect(await canvAttr(page, 'data-gun')).toBe('')
  // 画布应改变（枪线被移除）
  const bufOn = await page.locator('.pt-canvas').screenshot()
  await page.locator('.pt-toggle:has-text("视角朝向")').click()
  await page.waitForTimeout(180)
  const bufOff = await page.locator('.pt-canvas').screenshot()
  expect(Buffer.compare(bufOn, bufOff)).not.toBe(0)
  await page.locator('.pt-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r8-personal-toggles.png` })
})

test('个人战术回放：切换玩家不串缓存（reload 重置，无跨玩家状态）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedSmokePlayer()
  await login(page)
  const players = (await apiGet('/api/demos/19/players')).body
  const propPlayer = await pickPropPlayer(players)
  // 找一个在回合3 无烟/火的玩家（作为对照）
  let noProp = players.find((p) => p.player_id !== propPlayer.player_id)
  for (const p of players) {
    if (p.player_id === propPlayer.player_id) continue
    await apiPut('/api/demos/19/focus-player', { player_id: p.player_id })
    const d = await apiGet(`/api/demos/19/personal/replay?round_no=3`)
    const hasInterval = (d.body.props || []).some((pr) => pr.type === 'smoke' || pr.type === 'fire')
    if (!hasInterval) { noProp = p; break }
  }

  await apiPut('/api/demos/19/focus-player', { player_id: propPlayer.player_id })
  await enterPersonal(page)
  await selectPlayer(page, propPlayer.player_id)
  await page.locator('.pt-sel').selectOption('3')
  await page.waitForTimeout(700)
  const d = await apiGet(`/api/demos/19/personal/replay?round_no=3`)
  const prop = (d.body.props || []).find((pr) => pr.type === 'smoke' && pr.active_start_sec != null && pr.active_end_sec != null)
  const t = prop ? (prop.active_start_sec + prop.active_end_sec) / 2 : 15
  await scrub(page, t)
  // 玩家A：有烟
  expect(await canvAttr(page, 'data-smoke')).toBe('1')

  // 6R14 身份优先：切换 focus 不改变付费个人主体——personal/replay 恒返回绑定本人（propPlayer）。
  // 证明：前端把 focus 切到 noProp 后，渲染数据与 data-player 仍是绑定本人 531 及其烟（无跨玩家泄漏 / 无手改主体）。
  await page.locator('.picker select').selectOption(String(noProp.player_id))
  await page.waitForTimeout(900)
  await page.locator('.pt-sel').selectOption('3')
  await page.waitForTimeout(700)
  await scrub(page, t)
  expect(await page.locator('.pt').getAttribute('data-player')).toBe(String(propPlayer.player_id))  // 恒为绑定本人
  expect(await canvAttr(page, 'data-smoke')).toBe('1')   // 付费个人数据恒为绑定本人，切换玩家不改主体、不漏他人数据
  // 服务端权威：focus 在 noProp 时 personal/replay 仍返回绑定本人
  await apiPut('/api/demos/19/focus-player', { player_id: noProp.player_id })
  const d2 = await apiGet(`/api/demos/19/personal/replay?round_no=3`)
  expect(d2.body.player.player_id).toBe(propPlayer.player_id)
  expect((d2.body.props || []).some((pr) => pr.type === 'smoke' || pr.type === 'fire')).toBe(true)
  await page.locator('.pt-stage').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r8-personal-switch.png` })
})
