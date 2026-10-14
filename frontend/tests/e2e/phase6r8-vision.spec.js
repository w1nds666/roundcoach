// 6R8 Native Vision 验收截图采集：3 张地图（mirage/anubis/dust2）live API 实景
// 每图进入「团队复盘」→ 选一含烟/火的道具回合 → scrub 到道具有效区间的中间时刻
// （此时应看到 10 人 + 真实朝向枪线 + 烟/火 + 原语列表）。
// 只截图不做断言；由外部 Native Vision（vision_glance live API）验收。
import { test } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const TARGETS = [
  { demo: 19, map: 'mirage', mapLabel: 'de_mirage' },
  { demo: 20, map: 'anubis', mapLabel: 'de_anubis' },
  { demo: 21, map: 'dust2', mapLabel: 'de_dust2' },
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
async function pickRound(demo, rounds) {
  for (let r = 1; r <= rounds; r++) {
    const d = await replayData(demo, r)
    const active = d.props.find((p) => (p.type === 'smoke' || p.type === 'fire') && p.active_start_sec != null && p.active_end_sec != null)
    if (active) return { round: r, t: (active.active_start_sec + active.active_end_sec) / 2, d }
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

test('采集 3 图团队同步回放 + 原语 实景截图（Native Vision 用）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  for (const t of TARGETS) {
    grantTeamEntitlementForDemo(ACCOUNT.email, t.demo)   // 6R15 收口：按目标 demo 播种团队权益门禁
    await page.goto(`/demo/${t.demo}`)
    await page.locator('.map-name').waitFor({ state: 'visible', timeout: 15000 })
    await page.click('button.tab:has-text("团队")')
    await page.waitForSelector('.tm-canvas', { timeout: 25000 })

    // 找一个含烟/火、且已回填 tactical 的回合
    const pick = await pickRound(t.demo, 40)
    if (!pick) continue
    await page.locator('.tm-sel').selectOption(String(pick.round))
    await page.waitForFunction((r) => document.querySelector('.tm-sel')?.value === String(r), pick.round, { timeout: 20000 })
    await page.waitForTimeout(800)

    // scrub 到烟/火有效区间中间
    await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, pick.t)
    await page.waitForTimeout(300)
    await page.waitForFunction(() => document.querySelector('.tm-canvas')?.getAttribute('data-ready') === 'ready', { timeout: 20000 })

    await page.locator('.tm-stage').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS}/6r8-vision-${t.map}-replay.png`, fullPage: true })

    // 原语列表 + 一个定位动作后的回放
    // 6R16 确定性加固：等高负载下 coordination 视图真正渲染出证据条目，再等定位按钮，避免渲染时序抖动
    await page.waitForSelector('.cp-item', { timeout: 20000 })
    await page.waitForSelector('.cp-locate:not([disabled])', { timeout: 20000 })
    await page.locator('.prim-card').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS}/6r8-vision-${t.map}-primitives.png` })
    await page.locator('.cp-locate:not([disabled])').first().click()
    await page.waitForTimeout(500)
    await page.locator('.tm-stage').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS}/6r8-vision-${t.map}-locate.png` })
    console.log(`captured ${t.map} round=${pick.round} t=${pick.t}`)
  }
})
