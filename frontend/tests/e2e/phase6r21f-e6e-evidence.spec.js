// 6R21F-E6E：三地图人工真值抽查证据（截图 + 真值记录）。逐图关键时刻截图（desktop + 390 + 360），
// 记录 fieldChecksum/frameHash/alive/parity/图层；供原生 Vision 逐张审图 + 人工核对（控制场/frontier/contested/玩家/击杀/道具/死亡X/坐标一致性）。
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SHOTS = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(SHOTS, { recursive: true })
const OUT = resolve(__dirname, '../../../docs/6r21f-e6e-evidence.json')
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const MAPS = [
  { demo: 19, path: '/demo/19', map: 'de_mirage', area: 2544, moments: [ { n: 'start', t: 19.0 }, { n: 'active', t: 42.28 }, { n: 'contest', t: 33.75 }, { n: 'death-before', t: 19.51 }, { n: 'death-after', t: 19.71 } ] },
  { demo: 21, path: '/demo/21', map: 'de_dust2', area: 2242, moments: [ { n: 'start', t: 5.0 }, { n: 'control', t: 13.5 }, { n: 'kill', t: 15.0 }, { n: 'contested', t: 30.5 }, { n: 'end', t: 56.0 } ] },
  { demo: 22, path: '/demo/22', map: 'de_inferno', area: 2738, moments: [ { n: 'start', t: 5.0 }, { n: 'active', t: 25.19 }, { n: 'win', t: 85.0 } ] },
]
test.setTimeout(300000)

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page) {
  await page.goto('/login'); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterMapControl(page, path) {
  await page.goto(path); await page.waitForSelector('.map-name', { timeout: 20000 })
  await page.click('button.tab:has-text("团队")'); await page.waitForSelector('.e6a-view-switch', { timeout: 30000 })
  await page.click('button.e6a-vtab:has-text("区域控制")'); await page.waitForSelector('[data-testid="mcr-canvas"]', { timeout: 30000 })
  await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt && window.__mcReplay.states), undefined, { timeout: 30000 })
}
async function seekSec(page, t) {
  await page.locator('.mcr-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, t)
  await page.evaluate((tt) => window.__mcReplay.awaitExactAt(tt, 12000), t)
  await page.waitForTimeout(150)
}
async function settleExactAt(page, t) {
  await seekSec(page, t)
  const exact = await page.evaluate((tt) => window.__mcReplay.awaitExactAt(tt, 12000), t)
  expect(exact.ok, `exact frame 就绪 @${t}`).toBe(true)
  await page.waitForFunction((tt) => Math.abs(window.__mcReplay.currentTime - tt) < 0.011 && window.__mcReplay.modelExact, t)
}
async function toggleAndWait(page, layer, on) {
  const epoch = await page.evaluate(() => window.__mcReplay.renderEpoch)
  await page.evaluate(([name, enabled]) => window.__mcReplay.toggle(name, enabled), [layer, on])
  await page.waitForFunction((before) => window.__mcReplay.renderEpoch > before, epoch)
}

test('E6E 三地图人工真值抽查证据（截图+真值+parity+图层）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const report = { engine: 'single-truth-map-control-engine', maps: {} }
  for (const m of MAPS) {
    grantTeamEntitlementForDemo(ACCOUNT.email, m.demo)
    await page.setViewportSize({ width: 1280, height: 800 })
    await enterMapControl(page, m.path)
    const s = await page.evaluate(() => window.__mcReplay)
    expect(s.areas, `${m.map} areaCount`).toBe(m.area)
    expect(s.map, `${m.map} map`).toBe(m.map)
    const rec = { map: m.map, areaCount: s.areas, moments: [] }
    for (const mm of m.moments) {
      await seekSec(page, mm.t)
      const frame = await page.evaluate(() => ({ fieldChecksum: window.__mcReplay.fieldChecksum, frameHash: window.__mcReplay.frameHash, alive: window.__mcReplay.alive, states: window.__mcReplay.states, modelExact: window.__mcReplay.modelExact }))
      const shot = resolve(SHOTS, `6r21f-e6e-${m.map}-${mm.n}-desktop.png`)
      await page.locator('[data-testid="mcr-canvas"]').screenshot({ path: shot })
      rec.moments.push({ name: mm.n, t: mm.t, ...frame, screenshot: shot })
      console.log(`E6E ev ${m.map}/${mm.n} t=${mm.t} ck=${frame.fieldChecksum} alive=${frame.alive}`)
    }
    // parity（worker vs sync ref）真值守卫
    await seekSec(page, m.moments[1].t)
    const cap = await page.evaluate((t) => window.__mcReplay.captureExactSnapshot(t, 12000), m.moments[1].t)
    expect(cap.ok, `${m.map} capture`).toBe(true)
    expect(cap.comparisonPassed, `${m.map} parity`).toBe(true)
    rec.parity = { t: m.moments[1].t, diffCount: cap.diffCount, maxAbsDiff: cap.maxAbsDiff, comparisonPassed: cap.comparisonPassed }
    // 图层独立切换不改变底层真值（每步等渲染落定，避免竞争读取 frameHash）
    await settleExactAt(page, m.moments[1].t)
    const before = await page.evaluate(() => ({ hash: window.__mcReplay.frameHash, t: window.__mcReplay.currentTime }))
    for (const [layer, on] of [['scalar', true], ['scalar', false], ['front', false], ['front', true], ['players', false], ['players', true]]) {
      await toggleAndWait(page, layer, on)
    }
    const after = await page.evaluate(() => ({ hash: window.__mcReplay.frameHash, t: window.__mcReplay.currentTime, exact: window.__mcReplay.modelExact }))
    expect(after.t, `${m.map} 图层切换不得推进时间轴`).toBeCloseTo(before.t, 2)
    expect(after.exact, `${m.map} 图层切换后仍为 exact 真值`).toBe(true)
    expect(after.hash, `${m.map} 图层切换不得改变底层真值`).toBe(before.hash)
    rec.layerStable = before.hash === after.hash
    // 390 + 360 视口截图（视觉证据要求）
    for (const vp of [{ w: 390, h: 844, tag: '390' }, { w: 360, h: 740, tag: '360' }]) {
      await page.setViewportSize({ width: vp.w, height: vp.h })
      await page.waitForTimeout(200)
      await seekSec(page, m.moments[1].t)
      const shot = resolve(SHOTS, `6r21f-e6e-${m.map}-${m.moments[1].n}-${vp.tag}.png`)
      await page.locator('[data-testid="mcr-canvas"]').screenshot({ path: shot })
      rec[`shot_${vp.tag}`] = shot
    }
    await page.setViewportSize({ width: 1280, height: 800 })
    report.maps[m.map] = rec
    writeFileSync(OUT, JSON.stringify(report, null, 2))
  }
  console.log(`E6E evidence WROTE ${OUT}`)
})
