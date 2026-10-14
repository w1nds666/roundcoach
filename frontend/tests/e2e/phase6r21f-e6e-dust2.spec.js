// 6R21F-E6E：demo21 × de_dust2 产品接入 E2E（真实产品链路 nginx:80，顺序、单一账号）。
// 复用同一 MapControlReplay/mapControlEngine/mapCatalog（不复制第三套组件、不硬编码错图）；
// assert 接入/seek/parity/图层/视口/连续性。de_dust2 官方 NavMesh areaCount=2242。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/21' }
const DUST2_AREA_COUNT = 2242
const DUR = 57.33

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
async function enterMapControl(page) {
  await page.goto(ACCOUNT.demo)
  await page.waitForSelector('.map-name', { timeout: 20000 })
  await expect(page.locator('.map-name')).toContainText('Dust II')
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.e6a-view-switch', { timeout: 30000 })
  await page.click('button.e6a-vtab:has-text("区域控制")')
  await page.waitForSelector('[data-testid="mcr-canvas"]', { timeout: 30000 })
  await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt && window.__mcReplay.states), undefined, { timeout: 30000 })
}
async function seekSec(page, t) {
  await page.locator('.mcr-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, t)
  await page.waitForTimeout(200)
}

async function expectReducedMotionSnapshot(page) {
  const duration = Number(await page.locator('.mcr-tl').getAttribute('max'))
  const epoch = await page.evaluate(() => window.__mcReplay.renderEpoch)
  await page.locator('.mcr-play').click()
  await page.waitForFunction((before) => window.__mcReplay.renderEpoch > before, epoch)
  const state = await page.evaluate(() => new Promise((resolve) => {
    const t0 = window.__mcReplay.currentTime
    setTimeout(() => resolve({ t0, t1: window.__mcReplay.currentTime }), 400)
  }))
  expect(Math.min(Math.abs(state.t0), Math.abs(state.t0 - duration)), 'reduced-motion 点击播放应切换到静态端点快照').toBeLessThan(0.011)
  expect(state.t1, 'reduced-motion 静态快照不得连续推进').toBe(state.t0)
  // Exercise both directions; accepting either endpoint alone missed end -> end.
  let previous = state.t0
  for (let i = 0; i < 3; i++) {
    const target = previous === 0 ? duration : 0
    const before = await page.evaluate(() => window.__mcReplay.renderEpoch)
    await page.locator('.mcr-play').click()
    await page.waitForFunction((epoch) => window.__mcReplay.renderEpoch > epoch, before)
    await expect.poll(() => page.evaluate(() => window.__mcReplay.currentTime)).toBeCloseTo(target, 2)
    previous = target
  }
}

test('E6E demo21×Dust2 接入产品页：地图/控制场/seek/parity/图层', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 21)
  await login(page)
  await enterMapControl(page)
  const s = await page.evaluate(() => window.__mcReplay)
  expect(s.areas, 'de_dust2 areaCount 应为 2242').toBe(DUST2_AREA_COUNT)
  expect(s.map, '样本地图名必须是 de_dust2').toBe('de_dust2')

  const a = await page.evaluate((t) => window.__mcReplay.awaitExactAt(t, 12000), 19.5)
  expect(a.ok, 'exact frame 就绪').toBe(true)
  const h1 = await page.evaluate(() => window.__mcReplay.frameHash)
  await page.evaluate((t) => window.__mcReplay.awaitExactAt(t, 12000), 19.5)
  const h2 = await page.evaluate(() => window.__mcReplay.frameHash)
  expect(h1, '同 tick 同 hash（seek 确定性）').toBe(h2)

  const cap = await page.evaluate((t) => window.__mcReplay.captureExactSnapshot(t, 12000), 19.5)
  expect(cap.ok, 'capture snapshot ok').toBe(true)
  expect(cap.comparisonPassed, 'worker 与 sync ref parity 必须通过').toBe(true)

  const before = await page.evaluate(() => window.__mcReplay.frameHash)
  await page.evaluate(() => window.__mcReplay.toggle('scalar', false))
  await page.evaluate(() => window.__mcReplay.toggle('front', true))
  const after = await page.evaluate(() => window.__mcReplay.frameHash)
  expect(after, '图层切换不得改变底层真值').toBe(before)
})

test('E6E #2 视口：1280/1081/中窄/390/360 无横滚 + 触控>=44（demo21 Dust2）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 21)
  await login(page)
  await enterMapControl(page)
  for (const v of [{ w: 1280, h: 800 }, { w: 1081, h: 800 }, { w: 900, h: 800 }, { w: 390, h: 844 }, { w: 360, h: 740 }]) {
    await page.setViewportSize({ width: v.w, height: v.h })
    await page.waitForTimeout(200)
    const layout = await page.evaluate(() => {
      const togs = [...document.querySelectorAll('.mcr-layers label')]
      const tiny = togs.filter((t) => { const r = t.getBoundingClientRect(); return r.width < 44 || r.height < 44 }).length
      return { scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth, tiny }
    })
    expect(layout.scrollW, `无横滚 @${v.w}x${v.h}`).toBeLessThanOrEqual(layout.innerW)
    expect(layout.tiny, `触控目标>=44 @${v.w}x${v.h}`).toBe(0)
  }
})

test('E6E #3 reduced-motion / seek 连续性 / 暂停确定（demo21 Dust2）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 21)
  await login(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await enterMapControl(page)
  await page.evaluate(() => window.__mcReplay.awaitExactAt(19.51, 12000))
  const ts = [19.51, 19.54, 19.57, 19.59, 19.60]
  const cks = []
  for (const t of ts) { await seekSec(page, t); cks.push(await page.evaluate(() => window.__mcReplay.fieldChecksum)) }
  for (let i = 1; i < cks.length; i++) {
    expect(Math.abs(cks[i] - cks[i - 1]), `adjacent>0 @${i}`).toBeGreaterThan(0)
    expect(Math.abs(cks[i] - cks[i - 1]), `adjacent bounded @${i}`).toBeLessThanOrEqual(Math.abs(cks[4] - cks[0]) + 1)
  }
  await expectReducedMotionSnapshot(page)
  await seekSec(page, 30.0)
  let prev = null
  for (let k = 0; k < 3; k++) {
    await seekSec(page, 30.0)
    const cs = await page.evaluate(() => {
      const cv = document.querySelector('[data-testid="mcr-canvas"]')
      const c = document.createElement('canvas'); c.width = cv.width; c.height = cv.height
      c.getContext('2d').drawImage(cv, 0, 0)
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data
      let h = 0x811c9dc5
      for (let i = 0; i < d.length; i += 4) { h ^= d[i] & 0xff; h = (h * 0x01000193) >>> 0 }
      return h >>> 0
    })
    if (k > 0) expect(cs, `暂停 canvas 确定 @${k}`).toBe(prev)
    prev = cs
  }
})
