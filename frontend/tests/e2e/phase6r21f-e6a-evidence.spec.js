// 6R21F-E6A 证据采集（产品页 /demo/19 鉴权团队视图；唯一命名 6r21f-e6a-*，绝不覆盖历史）。
import { test, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SHOTS = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(SHOTS, { recursive: true })
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const DUR = 60.92

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page) {
  await page.goto('/login'); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterTeam(page) {
  await page.goto(ACCOUNT.demo); await page.waitForSelector('.map-name', { timeout: 20000 }); await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.e6a-view-switch', { timeout: 30000 }); await page.click('button.e6a-vtab:has-text("区域控制")')
  await page.waitForSelector('[data-testid="mcr-canvas"]', { timeout: 30000 })
  await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt && window.__mcReplay.states), undefined, { timeout: 30000 })
}
async function seekSec(page, t) { await page.locator('.mcr-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, t); await page.waitForTimeout(200) }

test('E6A evidence capture: 5 moments on /demo/19', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page)
  await enterTeam(page)
  const moments = [
    { name: '0019', t: 19, eventTime: null },
    { name: 'active-peak', t: 42.28, eventTime: null },
    { name: 'contest-peak', t: 33.75, eventTime: null },
    { name: 'death-before', t: 19.51, eventTime: 19.61 },
    { name: 'death-after', t: 19.71, eventTime: 19.61 },
  ]
  for (const m of moments) {
    await seekSec(page, m.t)
    await page.evaluate((tt) => window.__mcReplay.awaitExactAt(tt, 12000), m.t)
    const cap = await page.evaluate((tt) => window.__mcReplay.captureExactSnapshot(tt, 12000), m.t)
    const s = await page.evaluate(() => window.__mcReplay)
    const canvas = page.locator('[data-testid="mcr-canvas"]')
    const png = resolve(SHOTS, `6r21f-e6a-${m.name}.png`)
    await canvas.scrollIntoViewIfNeeded()
    await canvas.screenshot({ path: png })
    const json = { moment: m.name, timestamp: m.t, tSec: m.t, eventTime: m.eventTime, eventType: m.eventTime ? 'kill' : null, stateCounts: s.states, alive: s.alive, frameHash: s.frameHash, modelExact: s.modelExact, requestedTicks: s.requestedTicks, resolvedTicks: s.resolvedTicks, frameSources: s.frameSources, renderedTicks: s.renderedTicks, frac: s.frac, fieldChecksum: s.fieldChecksum, hotPathFullScans: s.hotPathFullScans, endpointBuildCount: s.endpointBuildCount, areas: s.areas, capture: cap, comparisonPassed: cap.comparisonPassed }
    writeFileSync(resolve(SHOTS, `6r21f-e6a-${m.name}.json`), JSON.stringify(json, null, 2))
    console.log(`E6A evidence ${m.name} alive=${s.alive} modelExact=${s.modelExact} cap=${cap.comparisonPassed} hotScan=${s.hotPathFullScans}`)
    expect(cap.ok, `${m.name} capture`).toBe(true)
    expect(cap.comparisonPassed).toBe(true)
  }
})
