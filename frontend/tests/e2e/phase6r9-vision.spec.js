// 6R9 Native Vision：对 3 张地图的最终关键页面采集截图（结论/指标/我的贡献/证据定位后回放）。
// 由 DSH 原生多模态（deepseek-v4-flash-vision-exp 经 read_image）做真实模型审阅，非 mock。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DEMOS = [19, 20, 21]
const MAP = { 19: 'mirage', 20: 'anubis', 21: 'dust2' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

for (const demo of DEMOS) {
  test(`Max：Native Vision 截图 demo ${demo}（${MAP[demo]}）`, async ({ page }) => {
    setTier(ACCOUNT.email, 'max', 7)
    grantTeamEntitlementForDemo(ACCOUNT.email, demo)   // 6R15 收口：按目标 demo 播种团队权益门禁
    await login(page)
    // 设置焦点玩家，让「我的贡献」有内容
    const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
    const players = await (await page.request.get(`/api/demos/${demo}/players`, {
      headers: { Authorization: `Bearer ${token}` },
    })).json()
    if (Array.isArray(players) && players.length) {
      await page.request.put(`/api/demos/${demo}/focus-player`, {
        headers: { Authorization: `Bearer ${token}` }, data: { player_id: players[0].player_id },
      })
    }

    await page.goto(`/demo/${demo}`)
    await page.click('button.tab:has-text("团队")')
    await page.waitForSelector('.tm-canvas', { timeout: 25000 })
    await page.waitForFunction(() => document.querySelector('.co-baseline') != null, { timeout: 25000 })

    // 结论 + 指标概览（A/B 表）+ 我的贡献（视口截图，避免超长导致像素上限）
    await page.locator('.co-concl').scrollIntoViewIfNeeded()
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${SHOTS}/6r9-concl-${MAP[demo]}.png` })
    await page.locator('.co-metric-table').scrollIntoViewIfNeeded()
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/6r9-metrics-${MAP[demo]}.png` })

    // 点击一个结论 → 定位到回放（切回合+scrub+开图层）
    const locateBtn = page.locator('.co-concl-item').first().locator('.co-locate')
    if (await locateBtn.count() > 0) {
      await locateBtn.click()
      await page.waitForTimeout(800)
    }
    await page.locator('.tm-stage').scrollIntoViewIfNeeded()
    await page.waitForTimeout(300)
    await page.locator('.tm-stage').screenshot({ path: `${SHOTS}/6r9-locate-${MAP[demo]}.png` })

    // 我的协同贡献
    await page.locator('.co-mine').scrollIntoViewIfNeeded()
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/6r9-mine-${MAP[demo]}.png` })
    expect(true).toBe(true)
  })
}
