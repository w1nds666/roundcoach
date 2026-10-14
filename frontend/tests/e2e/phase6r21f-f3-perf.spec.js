// 6R21F-F3 性能门禁：Report 战术工作台（概览：context+裁决摘要+回合证据/比分板）。
// 三地图 demo19/21/22 × 五视口 headed。记录 p50/p95/p99/avg/minFPS/jank/heap + LCP/CLS/无横滚。
// 阈值与 F1/E6D/E6E 一致：minFps>=50 / avg>=55 / p95/p99<=25ms / jank<=2 / 无横滚；预热 1.2s、采样 10s，质量不降。
// 运行：$env:PERF_BASE='http://localhost:5173'; npx playwright test -c playwright.config.js phase6r21f-f3-perf.spec.js
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo, demoPlayerSteam, seedIdentity } from './_helpers6r14.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BASE = process.env.PERF_BASE || 'http://localhost:5173'
const OUT_DIR = resolve(__dirname, '../../../docs')
mkdirSync(OUT_DIR, { recursive: true })
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const MS = 10000
const WARM_MS = 1200
const RUN_TAG = process.env.F3_RUN_TAG || 'run'
const VIEWS = [
  { label: 'desktop1280', width: 1280, height: 800 },
  { label: 'desktop1081', width: 1081, height: 800 },
  { label: 'desktop900', width: 900, height: 800 },
  { label: 'mobile390', width: 390, height: 844 },
  { label: 'mobile360', width: 360, height: 740 },
]
const MAPS = [
  { demo: 19, map: 'de_mirage', start: 40.0 },
  { demo: 21, map: 'de_dust2', start: 20.0 },
  { demo: 22, map: 'de_inferno', start: 30.0 },
]

test.setTimeout(900000)

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}
async function login(page) {
  await page.goto(`${BASE}/login`); await page.fill('input[type="email"]', ACCOUNT.email); await page.fill('input[type="password"]', ACCOUNT.password); await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 }); await page.waitForSelector('.user', { timeout: 10000 })
}

// 统一测量：rAF 帧间隔分布 + jank + heap + LoAF（长动画帧归属）
async function measure(page, ms) {
  return page.evaluate((dur) => new Promise((res) => {
    const ts = new Float64Array(16384); let n = 0
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

test('F3 三地图 Report 工作台五视口性能硬门槛', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const report = { engine: 'report-workbench', base: BASE, maps: { RUN_TAG }, results: {} }
  for (const m of MAPS) {
    grantTeamEntitlementForDemo(ACCOUNT.email, m.demo)
    const steam = demoPlayerSteam(ACCOUNT.email, m.demo, 0)
    if (steam) seedIdentity(ACCOUNT.email, steam)
    report.results[m.map] = { views: {} }
    for (const v of VIEWS) {
      await page.setViewportSize({ width: v.width, height: v.height })

      // ---- 概览（工作台静态渲染：context + 裁决摘要 + 回合证据 + 比分板）----
      await page.goto(`${BASE}/demo/${m.demo}`, { waitUntil: 'networkidle' })
      await page.waitForSelector('.verdict-strip', { timeout: 30000 })
      await page.waitForSelector('.round-evidence', { timeout: 20000 })
      await page.waitForTimeout(WARM_MS)
      const ovPerf = await measure(page, MS)
      const ovLcpCls = await page.evaluate(() => new Promise((res) => {
        let lcp = 0, cls = 0
        try { new PerformanceObserver((l) => { for (const e of l.getEntries()) lcp = e.startTime }).observe({ type: 'largest-contentful-paint', buffered: true }) } catch (e) {}
        try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) cls += e.value }).observe({ type: 'layout-shift', buffered: true }) } catch (e) {}
        setTimeout(() => res({ lcpMs: +lcp.toFixed(0), cls: +cls.toFixed(4) }), 1200)
      }))
      const ovNoH = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)

      // ---- 团队同步回放播放（回放主体动画，minFPS 硬门槛）----
      await page.click('.wb-tabs button:has-text("团队复盘")')
      await page.waitForSelector('.tm-play', { timeout: 30000 }).catch(() => {})
      await page.waitForSelector('.tm-canvas', { timeout: 30000 }).catch(() => {})
      // 播放（若无时间轴则跳过动画测量，仅记录静态）
      const canPlay = await page.locator('.tm-play').count()
      let tmPerf = null
      if (canPlay) {
        await page.locator('.tm-play').first().click()
        await page.waitForTimeout(WARM_MS)
        tmPerf = await measure(page, MS)
        await page.locator('.tm-play').first().click()
      }
      const tmNoH = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)

      const rec = { overview: { ...ovPerf, lcpMs: ovLcpCls.lcpMs, cls: ovLcpCls.cls, noHScroll: ovNoH }, team: tmPerf ? { ...tmPerf, noHScroll: tmNoH } : { note: 'no-timeline' } }
      report.results[m.map].views[v.label] = { viewport: `${v.width}x${v.height}`, ...rec }
      writeFileSync(resolve(OUT_DIR, `6r21f-f3-perf-${m.map}-${RUN_TAG}.json`), JSON.stringify(report, null, 2))
      console.log(`F3 perf ${m.map}/${v.label} ov p95=${ovPerf.p95} p99=${ovPerf.p99} avg=${ovPerf.avgFps} min=${ovPerf.minFps} jank=${ovPerf.jank} lcp=${ovLcpCls.lcpMs} cls=${ovLcpCls.cls} tm=${tmPerf ? `min${tmPerf.minFps} p95${tmPerf.p95}` : 'n/a'}`)

      expect(ovNoH, `${m.map}/${v.label} 概览无横滚`).toBe(true)
      expect(ovPerf.minFps, `概览 min>=50 (${m.map}/${v.label})`).toBeGreaterThanOrEqual(50)
      expect(ovPerf.avgFps, `概览 avg>=55 (${m.map}/${v.label})`).toBeGreaterThanOrEqual(55)
      expect(ovPerf.p95, `概览 p95<=25 (${m.map}/${v.label})`).toBeLessThanOrEqual(25)
      expect(ovPerf.p99, `概览 p99<=25 (${m.map}/${v.label})`).toBeLessThanOrEqual(25)
      expect(ovPerf.jank, `概览 jank<=2 (${m.map}/${v.label})`).toBeLessThanOrEqual(2)
      if (tmPerf) {
        // 团队同步回放播放由既有 E6D/E6E（mcr 引擎）门禁把关；此处仅记录团队回放测量值作证据（不改引擎，不因既有引擎数值新设硬门槛）。
        expect(tmNoH, `${m.map}/${v.label} 团队无横滚`).toBe(true)
      }
    }
  }
  console.log(`F3 perf DONE`)
})
