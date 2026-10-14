// 6R21F-E6A 产品回放门禁（鉴权 /demo/19 团队视图，真实 Mirage E5N 控制场）。
// 证据唯一命名 6r21f-e6a-*，绝不覆盖 E4/E5J/E5K/E5L/E5N。
// 运行：PLAYWRIGHT_BASE_URL=http://localhost:5173
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo, seedIdentity, demoPlayerSteam } from './_helpers6r14.js'

const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const DUR = 60.92

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
async function enterTeam(page) {
  await page.goto(ACCOUNT.demo)
  await page.waitForSelector('.map-name', { timeout: 20000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.e6a-view-switch', { timeout: 30000 })
  await page.click('button.e6a-vtab:has-text("区域控制")')
  await page.waitForSelector('[data-testid="mcr-canvas"]', { timeout: 30000 })
  await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt && window.__mcReplay.captureExactSnapshot && window.__mcReplay.states), undefined, { timeout: 30000 })
}
async function seekSec(page, t) { await page.locator('.mcr-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, t); await page.waitForTimeout(150) }
async function canvasChecksum(page) { return page.locator('[data-testid="mcr-canvas"]').evaluate((c) => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let s = 0; for (let i = 0; i < d.length; i++) s = (s + d[i]) % 1000000007; return String(s) }) }

test.beforeEach(async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page)
})

test('E6A #1 五时刻 parity + 死亡真值 + 零扫描', async ({ page }) => {
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await enterTeam(page)
  const moments = ['0019', 'active-peak', 'contest-peak', 'death-before', 'death-after']
  const times = { '0019': 19, 'active-peak': 42.28, 'contest-peak': 33.75, 'death-before': 19.51, 'death-after': 19.71 }
  const sides = {}
  for (const m of moments) {
    await seekSec(page, times[m])
    await page.evaluate((tt) => window.__mcReplay.awaitExactAt(tt, 12000), times[m])
    const cap = await page.evaluate((tt) => window.__mcReplay.captureExactSnapshot(tt, 12000), times[m])
    console.log(`E6A ${m} cap=${JSON.stringify(cap)}`)
    expect(cap.ok, `${m} capture`).toBe(true)
    expect(cap.comparisonPassed).toBe(true)
    expect(cap.diffCount).toBe(0)
    const s = await page.evaluate(() => window.__mcReplay)
    sides[m] = { alive: s.alive, states: s.states, modelExact: s.modelExact, hotPathFullScans: s.hotPathFullScans }
    console.log(`E6A ${m} alive=${s.alive} modelExact=${s.modelExact} hotScan=${s.hotPathFullScans}`)
  }
  expect(sides['death-before'].alive).toBe(10)
  expect(sides['death-after'].alive).toBe(9)
  expect(sides['0019'].hotPathFullScans).toBe(0)
  for (const k of Object.keys(sides)) expect(sides[k].hotPathFullScans).toBe(0)
  expect(errors, 'no page errors').toEqual([])
})

test('E6A #2 确定性：播放/暂停/seek/倍速/结束 + 同 t 三次 canvas 稳定', async ({ page }) => {
  await enterTeam(page)
  await page.evaluate(() => window.__mcReplay.awaitExactAt(20.0, 12000))
  // 同 t 三次 seek 一致
  let prev = null
  for (let k = 0; k < 3; k++) { await seekSec(page, 20.0); const cs = await canvasChecksum(page); if (k > 0) expect(cs).toBe(prev); prev = cs }
  // 播放推进时间
  await seekSec(page, 5.0)
  const t0 = await page.locator('.mcr-tl').evaluate((el) => Number(el.value))
  await page.locator('.mcr-play').click()
  await page.waitForTimeout(900)
  const t1 = await page.locator('.mcr-tl').evaluate((el) => Number(el.value))
  expect(t1).toBeGreaterThan(t0)
  await page.locator('.mcr-play').click() // 暂停
  // 倍速：4x 推进更快
  await seekSec(page, 5.0)
  await page.locator('.mcr-seg button:has-text("4x")').click()
  await page.locator('.mcr-play').click(); await page.waitForTimeout(700); await page.locator('.mcr-play').click()
  const t4 = await page.locator('.mcr-tl').evaluate((el) => Number(el.value))
  expect(t4).toBeGreaterThan(5.0)
  await page.locator('.mcr-btn:has-text("结束")').click()
  const tend = await page.locator('.mcr-tl').evaluate((el) => Number(el.value))
  expect(tend).toBeGreaterThanOrEqual(DUR - 0.5)
})

test('E6A #3 视口：1280/1081/中窄/390/360 无横滚 + 触控>=44', async ({ page }) => {
  await enterTeam(page)
  for (const v of [{ w: 1280, h: 800 }, { w: 1081, h: 800 }, { w: 900, h: 800 }, { w: 390, h: 844 }, { w: 360, h: 740 }]) {
    await page.setViewportSize({ width: v.w, height: v.h })
    await page.waitForTimeout(200)
    const layout = await page.evaluate(() => {
      const togs = [...document.querySelectorAll('.mcr-layers label')]
      const tiny = togs.filter((t) => { const r = t.getBoundingClientRect(); return r.width < 44 || r.height < 44 }).length
      return { scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth, tiny }
    })
    console.log(`E6A layout ${v.w} scrollW=${layout.scrollW} innerW=${layout.innerW} tiny=${layout.tiny}`)
    expect(layout.scrollW).toBeLessThanOrEqual(layout.innerW)
    expect(layout.tiny).toBe(0)
  }
})

test('E6A #4 连续性：19.51-19.60 fieldChecksum + 暂停/seek/reduced-motion 确定', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await enterTeam(page)
  await page.evaluate(() => window.__mcReplay.awaitExactAt(19.51, 12000))
  const ts = [19.51, 19.54, 19.57, 19.59, 19.60]
  const cks = []
  for (const t of ts) { await seekSec(page, t); cks.push(await page.evaluate(() => window.__mcReplay.fieldChecksum)) }
  console.log('E6A continuity cks=' + JSON.stringify(cks))
  for (let i = 1; i < cks.length; i++) {
    expect(Math.abs(cks[i] - cks[i - 1]), `adjacent>0 @${i}`).toBeGreaterThan(0)
    expect(Math.abs(cks[i] - cks[i - 1]), `adjacent bounded @${i}`).toBeLessThanOrEqual(Math.abs(cks[4] - cks[0]) + 1)
  }
  // 暂停固定 t 三次一致
  await seekSec(page, 30.0)
  let prev = null
  for (let k = 0; k < 3; k++) { await seekSec(page, 30.0); const cs = await canvasChecksum(page); if (k > 0) expect(cs).toBe(prev); prev = cs }
})
