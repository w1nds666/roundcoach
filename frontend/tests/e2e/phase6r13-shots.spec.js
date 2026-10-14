// 6R13：三地图实际回放截图（Mirage/Anubis/Dust2）——在击杀/道具时刻捕获，供 DeepSeek 原生 read_image 审阅。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const DEMOS = { mirage: 19, anubis: 20, dust2: 21 }
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
async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

test('6R13 三地图回放截图：在击杀时刻捕获（killfeed + 道具 + 图例）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const tok = await getToken()
  for (const [map, id] of Object.entries(DEMOS)) {
    grantTeamEntitlementForDemo(ACCOUNT.email, id)   // 6R15 收口：按目标 demo 播种团队权益门禁（避免静默 skip）
    let target = null
    for (const rnd of [1, 2, 3, 4, 5, 6]) {
      const d = await (await fetch(`http://localhost/api/demos/${id}/team/replay?round_no=${rnd}`, { headers: { Authorization: `Bearer ${tok}` } })).json()
      const k = (d.killfeed || []).find((x) => x.time_sec != null)
      if (k) {
        const nprop = (d.props || []).length
        const nblind = (d.blinds || []).length
        // 优先选有道具/致盲的回合，让截图能同时体现投掷归属与真实致盲
        if (!target || (nprop + nblind) > (target.props + target.blinds)) {
          target = { round: rnd, t: k.time_sec, props: nprop, blinds: nblind }
        }
      }
    }
    if (!target) continue
    await page.goto(`/demo/${id}`)
    await expect(page.locator('.map-name')).toHaveText(new RegExp(map), { timeout: 15000 })
    await page.click('button.tab:has-text("团队")')
    await page.waitForSelector('.tm-canvas', { timeout: 25000 })
    await page.locator('.tm-sel').selectOption(String(target.round))
    await page.waitForFunction((r) => document.querySelector('.tm-sel')?.value === String(r), target.round, { timeout: 20000 })
    await page.waitForTimeout(800)
    await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, target.t)
    await page.waitForTimeout(300)
    await page.locator('.tm-stage').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS}/6r13-replay-${map}.png` })
  }
})
