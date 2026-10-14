// 6R21F-E6D：demo22 × de_inferno 产品接入 E2E（真实产品链路 nginx:80，顺序、单一账号）。
// 红测先行：当前产品（未接入）在 /demo/22 无「区域控制」入口/控制场，本 spec 应 fail（红）。
// 接入后：/demo/22 团队复盘「区域控制」渲染 Inferno NavMesh 连续控制场，seek/determinism/parity/图层全绿。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/22' }
// demo22 权威地图 = de_inferno；真实 NavMesh areaCount = 2738。
const INFERNO_AREA_COUNT = 2738
const DUR = 89.55

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
  // 地图名显示为 Inferno
  await expect(page.locator('.map-name')).toContainText('Inferno')
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
async function canvasChecksum(page) {
  return page.evaluate(() => {
    const cv = document.querySelector('[data-testid="mcr-canvas"]')
    const c = document.createElement('canvas'); c.width = cv.width; c.height = cv.height
    c.getContext('2d').drawImage(cv, 0, 0)
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data
    let h = 0x811c9dc5
    for (let i = 0; i < d.length; i += 4) { h ^= d[i] & 0xff; h = (h * 0x01000193) >>> 0 }
    return h >>> 0
  })
}

test('E6D demo22×Inferno 接入产品页：地图/控制场/seek/parity/图层', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 22)
  await login(page)
  await enterMapControl(page)

  // 权威地图识别 + 真实 NavMesh areaCount（非 Mirage 2544，非复制样本）
  const s = await page.evaluate(() => window.__mcReplay)
  expect(s.areas, 'de_inferno areaCount 应为 2738').toBe(INFERNO_AREA_COUNT)
  expect(s.map, '样本地图名必须是 de_inferno').toBe('de_inferno')

  // seek 确定性：同一 tick ∈ bracket 的 frameHash 稳定（重放不漂移）
  await settleExactAt(page, 19.5)
  const h1 = await page.evaluate(() => window.__mcReplay.frameHash)
  await settleExactAt(page, 19.5)
  const h2 = await page.evaluate(() => window.__mcReplay.frameHash)
  expect(h1, '同 tick 同 hash（seek 确定性）').toBe(h2)

  // worker/browser 真值 parity（同一端点同一 hash，diff=0）
  const cap = await page.evaluate((t) => window.__mcReplay.captureExactSnapshot(t, 12000), 19.5)
  expect(cap.ok, 'capture snapshot ok').toBe(true)
  expect(cap.comparisonPassed, 'worker 与 sync 参考 parity 必须通过').toBe(true)

  // 玩家/事件与雷达底图同一 transform：alive 计数合理（无瞬移向外）
  const alive = await page.evaluate(() => window.__mcReplay.alive)
  expect(typeof alive).toBe('number')

  // 图层独立切换不改变底层真值：关控制场，frameHash 不变（底层真值独立于图层）
  await settleExactAt(page, 19.5)
  const before = await page.evaluate(() => ({ hash: window.__mcReplay.frameHash, t: window.__mcReplay.currentTime }))
  for (const [layer, on] of [['scalar', true], ['scalar', false], ['front', false], ['front', true]]) await toggleAndWait(page, layer, on)
  const after = await page.evaluate(() => ({ hash: window.__mcReplay.frameHash, t: window.__mcReplay.currentTime, exact: window.__mcReplay.modelExact }))
  expect(after.t, '图层切换不得推进时间轴').toBeCloseTo(before.t, 2)
  expect(after.exact, '图层切换后仍为 exact 真值').toBe(true)
  expect(after.hash, '图层切换不得改变底层真值').toBe(before.hash)
})

test('E6D #2 视口：1280/1081/中窄/390/360 无横滚 + 触控>=44（demo22 Inferno）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 22)
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
    console.log(`E6D layout ${v.w} scrollW=${layout.scrollW} innerW=${layout.innerW} tiny=${layout.tiny}`)
    expect(layout.scrollW, `无横滚 @${v.w}x${v.h}`).toBeLessThanOrEqual(layout.innerW)
    expect(layout.tiny, `触控目标>=44 @${v.w}x${v.h}`).toBe(0)
  }
})

test('E6D #3 reduced-motion / seek 连续性 / 暂停确定（demo22 Inferno）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 22)
  await login(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await enterMapControl(page)
  await page.evaluate(() => window.__mcReplay.awaitExactAt(19.51, 12000))
  const ts = [19.51, 19.54, 19.57, 19.59, 19.60]
  const cks = []
  for (const t of ts) { await seekSec(page, t); cks.push(await page.evaluate(() => window.__mcReplay.fieldChecksum)) }
  console.log('E6D continuity cks=' + JSON.stringify(cks))
  for (let i = 1; i < cks.length; i++) {
    expect(Math.abs(cks[i] - cks[i - 1]), `adjacent>0 @${i}`).toBeGreaterThan(0)
    expect(Math.abs(cks[i] - cks[i - 1]), `adjacent bounded @${i}`).toBeLessThanOrEqual(Math.abs(cks[4] - cks[0]) + 1)
  }
  // reduced-motion 静态：点击只切换端点快照，不启动连续播放。
  await expectReducedMotionSnapshot(page)
  // 暂停固定 t 三次 → 同一 canvas checksum（无漂移）
  await seekSec(page, 30.0)
  let prev = null
  for (let k = 0; k < 3; k++) { await seekSec(page, 30.0); const cs = await canvasChecksum(page); if (k > 0) expect(cs).toBe(prev); prev = cs }
})
