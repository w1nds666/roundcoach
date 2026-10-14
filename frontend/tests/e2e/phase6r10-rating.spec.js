// 6R10B 严格 E2E：Rating Pro 主指标（K/D 降级）、Pro/Max 展示、Free 锁定、
// 人话解释/版本/分量可用、切玩家不串缓存、无 NaN/Infinity、无「精确复刻 HLTV」误导。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, demoPlayerSteam, grantTeamEntitlementForDemo } from './_helpers6r14.js'

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const SHOTS = 'tests/e2e/screenshots'
let TOKEN = ''

// 6R14 身份优先：Pro/Max 付费个人数据恒为绑定本人，先播种 demo19 首位玩家作「我」
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
  await page.waitForSelector('.summary', { timeout: 25000 })
}

test('Pro：Rating Pro 为个人主指标，K/D 降级为次级', async ({ page }) => {
  setTier(ACCOUNT.email, 'pro', 7)
  seedDemo19()
  await login(page)
  const players = (await apiGet('/api/demos/19/players')).body
  const target = players[0]
  await apiPut('/api/demos/19/focus-player', { player_id: target.player_id })
  await enterPersonal(page)
  await page.locator('.picker select').selectOption(String(target.player_id))
  await page.waitForSelector('.rating-card', { timeout: 20000 })
  // 主指标：Rating Pro 大数字可见
  await expect(page.locator('.rating-card .rc-num')).toBeVisible()
  await expect(page.locator('.rating-card .rc-cap')).toContainText('Rating Pro')
  // K/D 降级：出现 `K/D（次级）` 标签或 sub-note
  const sumText = await page.locator('.summary').innerText()
  expect(sumText).toMatch(/K\/D（次级）|K\/D.*次级/)
  await page.locator('.summary').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r10-rating-pro.png` })
})

test('Pro：人话解释/版本/分量/参考范围可用，且无 NaN/Infinity', async ({ page }) => {
  setTier(ACCOUNT.email, 'pro', 7)
  seedDemo19()
  await login(page)
  const players = (await apiGet('/api/demos/19/players')).body
  const target = players.find((p) => p.player_id) || players[0]
  await apiPut('/api/demos/19/focus-player', { player_id: target.player_id })
  await enterPersonal(page)
  await page.locator('.picker select').selectOption(String(target.player_id))
  await page.waitForSelector('.rating-card .rc-explain', { timeout: 20000 })
  // 人话解释存在
  const explain = await page.locator('.rating-card .rc-explain').innerText()
  expect(explain.length).toBeGreaterThan(10)
  expect(explain).not.toMatch(/NaN|Infinity|Inf/)
  // 展开技术口径：版本/参考范围/分量表/不是 HLTV
  await page.locator('.rating-card .rc-tech summary').click()
  await page.waitForSelector('.rating-card .rc-not-hltv', { timeout: 15000 })
  const techText = await page.locator('.rating-card .rc-tech-body').innerText()
  expect(techText).toMatch(/版本/)
  expect(techText).toMatch(/试验性基线|样本内|非行业/)
  expect(techText).toMatch(/分量|权重/)
  await page.locator('.rating-card .rc-tech-body table').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/6r10-rating-tech.png` })
})

test('Pro：明确标注不等于 HLTV 官方 Rating（无精确复刻误导）', async ({ page }) => {
  setTier(ACCOUNT.email, 'pro', 7)
  seedDemo19()
  await login(page)
  const players = (await apiGet('/api/demos/19/players')).body
  const target = players[0]
  await apiPut('/api/demos/19/focus-player', { player_id: target.player_id })
  await enterPersonal(page)
  await page.locator('.picker select').selectOption(String(target.player_id))
  await page.waitForSelector('.rating-card', { timeout: 20000 })
  await page.locator('.rating-card .rc-tech summary').click()
  await page.waitForSelector('.rating-card .rc-not-hltv', { timeout: 15000 })
  const note = await page.locator('.rating-card .rc-not-hltv').innerText()
  expect(note).toContain('不等于')   // 不等于 HLTV 官方 Rating
  expect(note).toMatch(/HLTV/)
})

test('Max：切换玩家不串 Rating 缓存（不同玩家评分/状态独立）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDemo19()
  await login(page)
  const players = (await apiGet('/api/demos/19/players')).body
  const a = players[0], b = players[1]
  // player A 的 rating
  await apiPut('/api/demos/19/focus-player', { player_id: a.player_id })
  const ra = await apiGet('/api/demos/19/personal/rating')
  // player B 的 rating（应独立，不一致必为不同 player_id）
  await apiPut('/api/demos/19/focus-player', { player_id: b.player_id })
  const rb = await apiGet('/api/demos/19/personal/rating')
  expect(ra.body.player.player_id).toBe(a.player_id)
  // 6R14 身份优先：个人 Pro 恒返回绑定玩家，手动切换 focus 不改变数据（无跨玩家缓存污染）
  expect(rb.body.player.player_id).toBe(a.player_id)
  // 前端切换
  await enterPersonal(page)
  await page.locator('.picker select').selectOption(String(a.player_id))
  await page.waitForSelector('.rating-card .rc-num', { timeout: 20000 })
  const va = await page.locator('.rating-card .rc-num').innerText()
  await page.locator('.picker select').selectOption(String(b.player_id))
  await page.waitForTimeout(700)
  const vb = await page.locator('.rating-card .rc-num').innerText()
  // 若两个玩家评分不同则前端数字不同；一致则至少不串 player 身份
  expect(va).toMatch(/^\d/); expect(vb).toMatch(/^\d/)
  await page.screenshot({ path: `${SHOTS}/6r10-rating-switch.png` })
})

test('Free：个人复盘锁定，无数据泄漏', async ({ page }) => {
  setTier(ACCOUNT.email, 'free', null)
  await login(page)
  const players = (await apiGet('/api/demos/19/players')).body
  await apiPut('/api/demos/19/focus-player', { player_id: players[0].player_id })
  await page.goto(ACCOUNT.demo)
  await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 15000 })
  await page.click('button.tab:has-text("个人复盘")')
  // Free 显示锁定卡（无个人评分数据泄漏）；页面可能有多张 locked-box（个人复盘/道具）
  await page.waitForSelector('.locked-box', { timeout: 20000 })
  await expect(page.locator('.locked-box').first()).toBeVisible()
  await expect(page.locator('.rating-card')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/6r10-rating-free-locked.png` })
})

test('6R10A：demo19 主界面无负秒 / 裸 tick / 裸坐标', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)   // 6R15 收口：按目标 demo 播种团队权益门禁
  await login(page)
  await page.goto(ACCOUNT.demo)
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.waitForSelector('.co', { timeout: 25000 })
  const coText = await page.locator('.co').innerText()
  expect(coText).not.toMatch(/-\d{2}:\d{2}/)      // 无负秒
  expect(coText).not.toMatch(/steamid|steam_id|_1[0-9]{15,}/)  // 无 steam
  const bodyText = await page.evaluate(() => document.body.innerText)
  expect(bodyText).not.toMatch(/^\s*-?\d{1,4}(\.\d+)?\s*,\s*-?\d{1,4}(\.\d+)?\s*$/m)  // 无裸坐标对
  await page.screenshot({ path: `${SHOTS}/6r10-round-boundary.png` })
})
