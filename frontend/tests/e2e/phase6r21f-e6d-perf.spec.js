// 6R21F-E6D：demo22 × de_inferno 产品页真实应用渲染性能门禁 + 关键帧证据。
// 覆盖视口：desktop(1280)/1081/中窄(900)/390/360，各播放 >=10s；硬门槛 p95/p99<=20ms、avg>=55、min>=50、jank<=1。
// 记录 JS heap（before/during）、fieldRenderHz/endpointBuildCount/hotPathFullScans。写入 docs/6r21f-e6d-perf.json。
// 关键帧证据（帧差 + 人工真值）：几个代表性时刻截图到 docs/visual-evidence/6r21f-e6d-*，并落盘 fieldChecksum/frameHash/状态。
// 本 spec 面向真实应用渲染，使用默认 headed 配置运行（baseURL=http://localhost，nginx:80 产品链路）。
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
// 每次运行保留独立原始 JSON（不取最好覆盖失败证据）；E6D_RUN_TAG 由外层重复运行注入（如 run1/run2/run3）。
const RUN_TAG = process.env.E6D_RUN_TAG || 'latest'
const OUT = resolve(__dirname, `../../../docs/6r21f-e6d-perf-${RUN_TAG}.json`)
const OUT_CANON = resolve(__dirname, '../../../docs/6r21f-e6d-perf.json')
const SHOTS = resolve(__dirname, '../../../docs/visual-evidence')
const SHOT_SUFFIX = RUN_TAG === 'latest' ? '' : `-${RUN_TAG}`
mkdirSync(SHOTS, { recursive: true })
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/22' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const MS = 10000
const WARM_MS = 1200
const VIEWS = [
  { label: 'desktop', width: 1280, height: 800 },
  { label: '1081', width: 1081, height: 800 },
  { label: 'mid', width: 900, height: 800 },
  { label: '390', width: 390, height: 844 },
  { label: '360', width: 360, height: 740 },
]
// 关键帧证据时刻（回放秒）：回合初期 / 击杀活动峰 / 回合末（胜方控制收尾）。
const MOMENTS = [
  { name: 'start', t: 5.0 },
  { name: 'active', t: 25.19 },
  { name: 'win', t: 85.0 },
]

test.setTimeout(300000)

function writeReport(report) {
  writeFileSync(OUT, JSON.stringify(report, null, 2))
  // 带标签的审计/失败运行必须只写独立原始文件，不能覆盖 canonical 历史证据。
  if (RUN_TAG === 'latest') writeFileSync(OUT_CANON, JSON.stringify(report, null, 2))
}

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page) {
  await page.goto('/login'); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterMapControl(page) {
  await page.goto(ACCOUNT.demo)
  await page.waitForSelector('.map-name', { timeout: 20000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.e6a-view-switch', { timeout: 30000 })
  await page.click('button.e6a-vtab:has-text("区域控制")')
  await page.waitForSelector('[data-testid="mcr-canvas"]', { timeout: 30000 })
  await page.waitForFunction(() => !!(window.__mcReplay && window.__mcReplay.awaitExactAt && window.__mcReplay.states), undefined, { timeout: 30000 })
}
async function measure(page, ms) {
  return page.evaluate((dur) => new Promise((res) => {
    const ts = new Float64Array(16384); let n = 0
    const slow = [] // 帧间隔 > 20ms 的慢帧（逐帧归属：build/composite/fieldDraw/overlay + 是否发生端点构建）
    const loaf = [] // Long Animation Frame 归属（script/layout/render/compositor），区分主线程JS/布局/绘制/compositor
    let loafObs = null
    try {
      if (window.PerformanceObserver && PerformanceObserver.supportedEntryTypes && PerformanceObserver.supportedEntryTypes.includes('long-animation-frame')) {
        loafObs = new PerformanceObserver((list) => { for (const e of list.getEntries()) { if (e.duration >= 20) loaf.push({ start: +e.startTime.toFixed(1), duration: +e.duration.toFixed(1), script: +e.scriptDuration.toFixed(1), layout: +e.layoutDuration.toFixed(1), render: +((e.renderDuration || 0)).toFixed(1), compositor: +((e.compositorDuration || 0)).toFixed(1), scripts: e.scripts ? e.scripts.length : 0 }) } })
        loafObs.observe({ type: 'long-animation-frame' })
      }
    } catch (e) { loafObs = null }
    const start = performance.now()
    const mem0 = performance.memory ? performance.memory.usedJSHeapSize : null
    function loop(t) {
      ts[n++] = t
      if (n > 1) {
        const g = t - ts[n - 2]
        if (g > 20 && slow.length < 512) {
          const mr = window.__mcReplay || {}
          const lf = mr.lastFrame || {}
          slow.push({ i: n, gap: +g.toFixed(1), t: +((mr.currentTime || 0)).toFixed(2), build: (lf.buildMs != null ? lf.buildMs : null), composite: (lf.compositeMs != null ? lf.compositeMs : null), fieldDraw: (lf.fieldDrawMs != null ? lf.fieldDrawMs : null), overlay: (lf.overlayMs != null ? lf.overlayMs : null), renderTotal: (lf.totalMs != null ? lf.totalMs : null), note: lf.note || null, ep: mr.endpointBuildCount, at: mr.maxFramePhase, buildMaxTotal: mr.buildPhaseMax ? mr.buildPhaseMax.total : null, heap: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null })
        }
      }
      if (t - start < dur) requestAnimationFrame(loop)
      else {
        if (loafObs) { try { loafObs.disconnect() } catch (e) {} }
        const gaps = []; for (let i = 1; i < n; i++) gaps.push(ts[i] - ts[i - 1])
        const srt = [...gaps].sort((a, b) => a - b)
        const pct = (q) => srt.length ? srt[Math.min(srt.length - 1, Math.floor(q * srt.length))] : 0
        const d = (ts[n - 1] - start) / 1000
        const mem1 = performance.memory ? performance.memory.usedJSHeapSize : null
        res({ samples: n, duration: +d.toFixed(2), p50: +pct(0.5).toFixed(1), p95: +pct(0.95).toFixed(1), p99: +pct(0.99).toFixed(1), avgFps: +((n - 1) / d).toFixed(1), minFps: gaps.length ? +(1000 / Math.max(...gaps)).toFixed(1) : 0, jank: gaps.filter((g) => g > 33.3).length, maxFrameMs: +(Math.max(...gaps)).toFixed(1), heapBeforeMB: mem0 ? +(mem0 / 1048576).toFixed(1) : null, heapDuringMB: mem1 ? +(mem1 / 1048576).toFixed(1) : null, loaf, slow })
      }
    }
    requestAnimationFrame(loop)
  }), ms)
}

test('E6D demo22×Inferno 五视口性能硬门槛 + 关键帧证据', async ({ page, browser }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 22)
  await login(page)
  const report = { engine: 'single-truth-map-control-engine', map: 'de_inferno', demo: 22, views: {}, keyframes: [] }
  for (const v of VIEWS) {
    await page.setViewportSize({ width: v.width, height: v.height })
    await enterMapControl(page)
    await page.evaluate(() => window.__mcReplay.awaitExactAt(30.0, 12000))
    await page.locator('.mcr-play').click()
    await page.waitForTimeout(WARM_MS)
    const perf = await measure(page, MS)
    await page.locator('.mcr-play').click() // 暂停
    const m = await page.evaluate(() => window.__mcReplay && { fieldRenderHz: window.__mcReplay.fieldRenderHz, endpointBuildCount: window.__mcReplay.endpointBuildCount, hotPathFullScans: window.__mcReplay.hotPathFullScans, req: window.__mcReplay.req, uniqueTicks: window.__mcReplay.uniqueTicks, cacheHits: window.__mcReplay.cacheHits, maxFramePhase: window.__mcReplay.maxFramePhase, areas: window.__mcReplay.areas, map: window.__mcReplay.map })
    console.log(`E6D perf ${v.label} p95=${perf.p95} p99=${perf.p99} avg=${perf.avgFps} min=${perf.minFps} jank=${perf.jank} heap=${perf.heapDuringMB} areas=${m.areas}`)
    report.views[v.label] = { viewport: `${v.width}x${v.height}`, ...perf, ...m }
    writeReport(report) // 先落盘证据（含慢帧诊断），再断言
    expect(m.hotPathFullScans, `hotPathFullScans==0 (${v.label})`).toBe(0)
    expect(m.map, `map 必须是 de_inferno (${v.label})`).toBe('de_inferno')
    expect(m.areas, `areaCount==2738 (${v.label})`).toBe(2738)
    expect(perf.minFps, `min>=50 (${v.label})`).toBeGreaterThanOrEqual(50)
    expect(perf.p95, `p95<=20 (${v.label})`).toBeLessThanOrEqual(20)
    expect(perf.p99, `p99<=20 (${v.label})`).toBeLessThanOrEqual(20)
    expect(perf.avgFps, `avg>=55 (${v.label})`).toBeGreaterThanOrEqual(55)
    expect(perf.jank, `jank<=1 (${v.label})`).toBeLessThanOrEqual(1)
  }
  // 关键帧证据（以 desktop 视口）：三个代表性时刻截图 + 帧差/校验和真值
  await page.setViewportSize({ width: 1280, height: 800 })
  await enterMapControl(page)
  let prev = null
  for (const mm of MOMENTS) {
    await page.locator('.mcr-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, mm.t)
    await page.evaluate((t) => window.__mcReplay.awaitExactAt(t, 12000), mm.t)
    const frame = await page.evaluate(() => ({ fieldChecksum: window.__mcReplay.fieldChecksum, frameHash: window.__mcReplay.frameHash, alive: window.__mcReplay.alive, states: window.__mcReplay.states, map: window.__mcReplay.map, areaCount: window.__mcReplay.areas }))
    const shot = resolve(SHOTS, `6r21f-e6d-${mm.name}${SHOT_SUFFIX}.png`)
    await page.locator('[data-testid="mcr-canvas"]').screenshot({ path: shot })
    const rec = { name: mm.name, t: mm.t, ...frame, frameDiffFromPrev: prev ? Math.abs(frame.fieldChecksum - prev) : null, screenshot: shot }
    report.keyframes.push(rec)
    console.log(`E6D keyframe ${mm.name} t=${mm.t} ck=${frame.fieldChecksum} alive=${frame.alive} diff=${rec.frameDiffFromPrev}`)
    prev = frame.fieldChecksum
  }
  writeReport(report)
  console.log(`E6D perf WROTE ${OUT}`)
})
