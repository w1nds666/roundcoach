// 6R8 Native Vision：canvas 特写截图（3 图）——供 live vision_glance 验收。
// 只截 .tm-stage（回放画布+控件区域），避免整页超长导致 vision 像素上限。
import { test } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const TARGETS = [
  { demo: 19, map: 'mirage' },
  { demo: 20, map: 'anubis' },
  { demo: 21, map: 'dust2' },
]
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
async function replayData(demo, round) {
  const t = await getToken()
  return (await fetch(`http://localhost/api/demos/${demo}/team/replay?round_no=${round}`, { headers: { Authorization: `Bearer ${t}` } })).json()
}
async function pickRound(demo) {
  for (let r = 1; r <= 40; r++) {
    const d = await replayData(demo, r)
    const active = d.props.find((p) => (p.type === 'smoke' || p.type === 'fire') && p.active_start_sec != null && p.active_end_sec != null)
    if (active) return { round: r, t: (active.active_start_sec + active.active_end_sec) / 2 }
  }
  return null
}
async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

test('canvas 特写截图（3 图，Native Vision 用）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  for (const t of TARGETS) {
    grantTeamEntitlementForDemo(ACCOUNT.email, t.demo)   // 6R15 收口：按目标 demo 播种团队权益门禁
    await page.goto(`/demo/${t.demo}`)
    await page.locator('.map-name').waitFor({ state: 'visible', timeout: 15000 })
    await page.click('button.tab:has-text("团队")')
    await page.waitForSelector('.tm-canvas', { timeout: 25000 })
    const pick = await pickRound(t.demo)
    if (!pick) { console.log(`skip ${t.map} no active prop round`); continue }
    await page.locator('.tm-sel').selectOption(String(pick.round))
    await page.waitForFunction((r) => document.querySelector('.tm-sel')?.value === String(r), pick.round, { timeout: 20000 })
    await page.waitForTimeout(800)
    await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, pick.t)
    await page.waitForTimeout(300)
    await page.waitForFunction(() => document.querySelector('.tm-canvas')?.getAttribute('data-ready') === 'ready', { timeout: 20000 })
    await page.locator('.tm-stage').scrollIntoViewIfNeeded()
    await page.locator('.tm-stage').screenshot({ path: `${SHOTS}/6r8-canvas-${t.map}.png` })
    console.log(`captured canvas ${t.map} round=${pick.round} t=${pick.t}`)
  }
})
