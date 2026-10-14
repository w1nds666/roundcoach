// 6R10B 视觉审阅截图源：mirage/anubis/dust2 的 Rating Pro 主卡（桌面 + 移动端）。
// 供 DSH Native Vision 对真实图审阅主卡/解释/移动端（证据见 gate-evidence）。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, demoPlayerSteam } from './_helpers6r14.js'

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const SHOTS = 'tests/e2e/screenshots'
const MAPS = [
  { demo: 19, map: 'mirage' },
  { demo: 20, map: 'anubis' },
  { demo: 21, map: 'dust2' },
]
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
  return r.json()
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

for (const m of MAPS) {
  test(`截取 ${m.map} Rating Pro 主卡（桌面 + 移动端）`, async ({ page }) => {
    setTier(ACCOUNT.email, 'max', 7)
    // 6R14 身份优先：先播种到该 demo 首位玩家，个人 Rating Pro 才命中「绑定本人」
    seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, m.demo))
    await login(page)
    const players = await apiGet(`/api/demos/${m.demo}/players`)
    const target = players.find((p) => p.is_you)?.player_id || (await apiGet(`/api/demos/${m.demo}/focus-player`)).player_id || players[0].player_id
    await apiPut(`/api/demos/${m.demo}/focus-player`, { player_id: target })
    await page.goto(`/demo/${m.demo}`)
    await expect(page.locator('.map-name')).toHaveText(/.+/, { timeout: 15000 })
    await page.click('button.tab:has-text("个人复盘")')
    await page.waitForSelector('.rating-card .rc-num', { timeout: 20000 })
    await page.locator('.picker select').selectOption(String(target))
    await page.waitForTimeout(700)
    await page.locator('.summary').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS}/6r10-vision-${m.map}-desktop.png` })

    await page.setViewportSize({ width: 390, height: 844 })
    await page.waitForTimeout(500)
    await page.locator('.summary').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS}/6r10-vision-${m.map}-mobile.png` })
  })
}
