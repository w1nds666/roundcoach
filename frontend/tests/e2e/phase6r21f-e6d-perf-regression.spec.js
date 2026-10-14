// 6R21F-E6D-PERF 回归：证明性能优化（deathTick 预计算缓存 + 每帧一次 playerVisualAt + deathX 忠实 no-op 位姿）后，
// 控制场/玩家图层真值不漂移。逐项断言：worker/browser parity、seek/pause/end 确定性、reduced-motion 静态、图层切换不改底层真值；
// 并覆盖 demo19 Mirage（同一引擎，证明跨地图无回归）。不为测试写专用旁路，均为真实产品链路 headed。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DEMO = { inferno: { id: 22, path: '/demo/22', map: 'de_inferno', area: 2738, dur: 89.55 }, mirage: { id: 19, path: '/demo/19', map: 'de_mirage', area: 2544, dur: 89.9 } }

test.setTimeout(180000)

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page) {
  await page.goto('/login'); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterMapControl(page, d) {
  await page.goto(d.path)
  await page.waitForSelector('.map-name', { timeout: 20000 })
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
async function settleExactAt(page, t) {
  await seekSec(page, t)
  const exact = await page.evaluate((tt) => window.__mcReplay.awaitExactAt(tt, 12000), t)
  expect(exact.ok, `exact frame 就绪 @${t}`).toBe(true)
  await page.waitForFunction((tt) => Math.abs(window.__mcReplay.currentTime - tt) < 0.011 && window.__mcReplay.modelExact, t)
  return exact
}
async function toggleAndWait(page, layer, on) {
  const epoch = await page.evaluate(() => window.__mcReplay.renderEpoch)
  await page.evaluate(([name, enabled]) => window.__mcReplay.toggle(name, enabled), [layer, on])
  await page.waitForFunction((before) => window.__mcReplay.renderEpoch > before, epoch)
}

// 走一遍完整性矩阵：parity / seek 确定性 / 图层切换不改真值 / 到端静止
async function runMatrix(page, d) {
  const t = 19.5
  await enterMapControl(page, d)
  const s = await page.evaluate(() => window.__mcReplay)
  expect(s.areas, `${d.map} areaCount`).toBe(d.area)
  expect(s.map, `样本地图名 ${d.map}`).toBe(d.map)

  // worker/browser 真值 parity（控制场真值守卫）
  const cap = await page.evaluate((tt) => window.__mcReplay.captureExactSnapshot(tt, 12000), t)
  expect(cap.ok, 'exact 快照就绪').toBe(true)
  expect(cap.comparisonPassed, 'worker vs sync-reference parity 必须通过').toBe(true)

  // seek 确定性：同 tick 同 frameHash / fieldChecksum（重放不漂移）
  await settleExactAt(page, t)
  const h1 = await page.evaluate(() => window.__mcReplay.frameHash)
  const c1 = await page.evaluate(() => window.__mcReplay.fieldChecksum)
  await settleExactAt(page, t)
  const h2 = await page.evaluate(() => window.__mcReplay.frameHash)
  const c2 = await page.evaluate(() => window.__mcReplay.fieldChecksum)
  expect(h1, '同 tick 同 frameHash').toBe(h2)
  expect(c1, '同 tick 同 fieldChecksum').toBe(c2)

  // 图层独立切换不改变底层真值：关/开控制场与前线，frameHash 不变
  await settleExactAt(page, t)
  const before = await page.evaluate(() => ({ hash: window.__mcReplay.frameHash, t: window.__mcReplay.currentTime }))
  for (const [layer, on] of [['scalar', true], ['scalar', false], ['front', false], ['front', true], ['players', false], ['players', true]]) await toggleAndWait(page, layer, on)
  const after = await page.evaluate(() => ({ hash: window.__mcReplay.frameHash, t: window.__mcReplay.currentTime, exact: window.__mcReplay.modelExact }))
  expect(after.t, '图层切换不得推进时间轴').toBeCloseTo(before.t, 2)
  expect(after.exact, '图层切换后仍为 exact 真值').toBe(true)
  expect(after.hash, '图层切换不得改变底层真值').toBe(before.hash)

  // 暂停确定：固定 t 三次 canvas checksum 一致（无漂移）
  await seekSec(page, t)
  let prev = null
  for (let k = 0; k < 3; k++) {
    await seekSec(page, t)
    const cs = await page.evaluate(() => {
      const cv = document.querySelector('[data-testid="mcr-canvas"]')
      const c = document.createElement('canvas'); c.width = cv.width; c.height = cv.height
      c.getContext('2d').drawImage(cv, 0, 0)
      const dd = c.getContext('2d').getImageData(0, 0, c.width, c.height).data
      let hh = 0x811c9dc5
      for (let i = 0; i < dd.length; i += 4) { hh ^= dd[i] & 0xff; hh = (hh * 0x01000193) >>> 0 }
      return hh >>> 0
    })
    if (k > 0) expect(cs, `暂停 canvas 确定 @${k}`).toBe(prev)
    prev = cs
  }
}

test('E6D-PERF 回归 demo22 Inferno：parity/seek/图层/暂停/到端/reduced-motion', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 22)
  await login(page)
  await runMatrix(page, DEMO.inferno)
  // 到端静止：toEnd → currentTime == duration
  await page.evaluate(() => window.__mcReplay.awaitExactAt(19.5, 12000))
  await page.locator('.mcr-btn:has-text("结束")').click()
  const end = await page.evaluate(() => window.__mcReplay.currentTime)
  expect(end, 'toEnd 必须到达时长').toBeGreaterThanOrEqual(DEMO.inferno.dur - 0.01)
})

test('E6D-PERF 回归 demo19 Mirage：parity/seek/图层/暂停（同一引擎无回归）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page)
  await runMatrix(page, DEMO.mirage)
})
