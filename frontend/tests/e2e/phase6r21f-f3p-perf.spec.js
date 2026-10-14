// 6R21F-F3P 门禁：TeamSyncReplay 播放性能。10s headed，五视口。
// 门槛：minFps>=50 / avg>=55 / p95,p99<=25 / jank<=2 / 无横滚（与 E6D/E6E/F3 一致）。
// 默认 demo21（Dust2）连续 F3P_RUNS 次；F3P_DEMOS 可指定其它 demo（如 19/22）各跑一次。
// 运行：$env:PERF_BASE='http://localhost:5173'; npx playwright test -c playwright.config.js phase6r21f-f3p-perf.spec.js
// 未显式设置 PERF_BASE 时继承所选 Playwright 配置的 baseURL；报告记录目标及来源。
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo, demoPlayerSteam, seedIdentity } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = resolve(__dirname, '../../../docs')
mkdirSync(OUT_DIR, { recursive: true })
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const MS = 10000
const WARM_MS = 1200
const RUN_TAG = process.env.F3P_RUN_TAG || 'run'
// 连续跑次数（demo21 门槛要求连续 3 次全部达标）
const RUNS = process.env.F3P_RUNS ? Number(process.env.F3P_RUNS) : 1
// 目标 demo：默认 demo21；F3P_DEMOS='19,22' 时跑 19/22 各一次
const DEMOS = process.env.F3P_DEMOS ? process.env.F3P_DEMOS.split(',').map(Number) : [21]
const VIEWS = [
  { label: 'desktop1280', width: 1280, height: 800 },
  { label: 'desktop1081', width: 1081, height: 800 },
  { label: 'desktop900', width: 900, height: 800 },
  { label: 'mobile390', width: 390, height: 844 },
  { label: 'mobile360', width: 360, height: 740 },
]

test.setTimeout(900000)

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page, BASE) {
  await page.goto(`${BASE}/login`); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 })
}

async function measure(page, ms) {
  return page.evaluate((dur) => new Promise((res) => {
    const ts = new Float64Array(16384); let n = 0
    const playback = { frames: [], stoppedSamples: 0 }
    window.__f3pPlayback = playback
    const loaf = []
    let loafObs = null
    try {
      if (window.PerformanceObserver && PerformanceObserver.supportedEntryTypes && PerformanceObserver.supportedEntryTypes.includes('long-animation-frame')) {
        loafObs = new PerformanceObserver((list) => { for (const e of list.getEntries()) { if (e.duration >= 20) loaf.push({ start: +e.startTime.toFixed(1), duration: +e.duration.toFixed(1), script: +e.scriptDuration.toFixed(1), layout: +e.layoutDuration.toFixed(1) }) } })
        loafObs.observe({ type: 'long-animation-frame' })
      }
    } catch (e) { loafObs = null }
    const start = performance.now()
    const mem0 = performance.memory ? performance.memory.usedJSHeapSize : null
    function loop(t) {
      ts[n++] = t
      playback.frames.push([t, Number(document.querySelector('.tm-canvas')?.dataset.time ?? NaN)])
      if (document.querySelector('.tm-play')?.getAttribute('aria-label') !== '暂停') playback.stoppedSamples++
      if (t - start < dur) requestAnimationFrame(loop)
      else {
        if (loafObs) { try { loafObs.disconnect() } catch (e) {} }
        const gaps = []; for (let i = 1; i < n; i++) gaps.push(ts[i] - ts[i - 1])
        const srt = [...gaps].sort((a, b) => a - b)
        const pct = (q) => srt.length ? srt[Math.min(srt.length - 1, Math.floor(q * srt.length))] : 0
        const d = (ts[n - 1] - start) / 1000
        const mem1 = performance.memory ? performance.memory.usedJSHeapSize : null
        res({ samples: n, duration: +d.toFixed(2), p50: +pct(0.5).toFixed(1), p95: +pct(0.95).toFixed(1), p99: +pct(0.99).toFixed(1), avgFps: +((n - 1) / d).toFixed(1), minFps: gaps.length ? +(1000 / Math.max(...gaps)).toFixed(1) : 0, jank: gaps.filter((g) => g > 33.3).length, maxFrameMs: +(Math.max(...gaps)).toFixed(1), heapBeforeMB: mem0 ? +(mem0 / 1048576).toFixed(1) : null, heapDuringMB: mem1 ? +(mem1 / 1048576).toFixed(1) : null, loaf })
      }
    }
    requestAnimationFrame(loop)
  }), ms)
}

for (const d of DEMOS) {
  test(`F3P demo${d} TeamSyncReplay 五视口性能硬门槛 ×${RUNS}`, async ({ page, baseURL, browser }) => {
    const BASE = process.env.PERF_BASE || baseURL
    if (!BASE) throw new Error('Performance target requires PERF_BASE or Playwright baseURL')
    setTier(ACCOUNT.email, 'max', 7)
    grantTeamEntitlementForDemo(ACCOUNT.email, d)
    const steam = demoPlayerSteam(ACCOUNT.email, d, 0)
    if (steam) seedIdentity(ACCOUNT.email, steam)
    await login(page, BASE)
    const report = { engine: 'team-sync-replay', demo: d, base: BASE, targetSource: process.env.PERF_BASE ? 'PERF_BASE' : 'playwright.baseURL', runs: RUN_TAG, results: {} }
    for (let run = 1; run <= RUNS; run++) {
      for (const v of VIEWS) {
        await page.setViewportSize({ width: v.width, height: v.height })
        await page.goto(`${BASE}/demo/${d}`, { waitUntil: 'networkidle' })
        await page.waitForSelector('.map-name', { timeout: 40000 })
        await page.click('.wb-tabs button:has-text("团队复盘")')
        await page.waitForSelector('.tm-play', { timeout: 30000 })
        await page.waitForSelector('.tm-canvas', { timeout: 30000 })
        // 进入播放（区域控制图层默认开启）
        if (process.env.F3P_TRACE === '1') await browser.startTracing(page, { path: resolve(OUT_DIR, `f3p-${RUN_TAG}-d${d}-r${run}-${v.label}.trace.json`), screenshots: false })
        await page.locator('.tm-play').first().click()
        await page.waitForTimeout(WARM_MS)
        const perf = await measure(page, MS)
        perf.playback = await page.evaluate(() => window.__f3pPlayback)
        perf.runtime = await page.evaluate(() => ({ userAgent: navigator.userAgent, dpr: devicePixelRatio, url: location.href, scripts: [...document.scripts].map(s => s.src) }))
        if (process.env.F3P_TRACE === '1') await browser.stopTracing()
        await page.locator('.tm-play').first().click()
        const noH = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
        console.log(`F3P run${run} demo${d} ${v.label} p95=${perf.p95} p99=${perf.p99} avg=${perf.avgFps} min=${perf.minFps} jank=${perf.jank} heap=${perf.heapDuringMB} noH=${noH}`)
        report.results[`run${run}`] = report.results[`run${run}`] || { views: {} }
        report.results[`run${run}`].views[v.label] = { viewport: `${v.width}x${v.height}`, viewportLabel: v.label, ...perf, noHScroll: noH }
        writeFileSync(resolve(OUT_DIR, `6r21f-f3p-perf-d${d}-${RUN_TAG}.json`), JSON.stringify(report, null, 2))
        expect(noH, `${v.label} 无横滚`).toBe(true)
        expect(perf.playback.stoppedSamples, `${v.label} 全程播放`).toBe(0)
        const frames = perf.playback.frames
        expect(frames.at(-1)[1] - frames[0][1], `${v.label} 时钟持续推进`).toBeGreaterThanOrEqual(9.5)
        expect(perf.minFps, `${v.label} min>=50`).toBeGreaterThanOrEqual(50)
        expect(perf.avgFps, `${v.label} avg>=55`).toBeGreaterThanOrEqual(55)
        expect(perf.p95, `${v.label} p95<=25`).toBeLessThanOrEqual(25)
        expect(perf.p99, `${v.label} p99<=25`).toBeLessThanOrEqual(25)
        expect(perf.jank, `${v.label} jank<=2`).toBeLessThanOrEqual(2)
      }
    }
    console.log(`F3P demo${d} DONE`)
  })
}
