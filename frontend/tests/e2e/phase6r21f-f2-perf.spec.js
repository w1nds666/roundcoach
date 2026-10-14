// 6R21F-F2 性能门禁：Dashboard(/)/Upload(/upload)/Login(/login) 三页五视口，headed 真实渲染。
// 记录 idle-rAF p50/p95/p99、avg/minFPS、jank、JS heap、LCP/CLS、无横滚。
// 硬门槛与 F1 相同：minFps>=50、avg>=55、p95/p99<=25ms、jank<=2、无横滚；阈值/预热/采样窗不降。
// 运行：$env:PERF_BASE='http://localhost:5173'; npx playwright test -c playwright.config.js phase6r21f-f2-perf.spec.js
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BASE = process.env.PERF_BASE || 'http://localhost:5173'
const OUT = resolve(__dirname, '../../../docs/6r21f-f2-perf.json')
const RUN_TAG = process.env.F2_RUN_TAG || 'latest'
mkdirSync(dirname(OUT), { recursive: true })
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const MS = 6000
const WARM_MS = 800
const VIEWS = [
  { label: 'desktop', width: 1280, height: 800 },
  { label: '1081', width: 1081, height: 800 },
  { label: '900', width: 900, height: 800 },
  { label: '390', width: 390, height: 844 },
  { label: '360', width: 360, height: 740 },
]
const PAGES = [
  { key: 'dashboard', path: '/', auth: true },
  { key: 'upload', path: '/upload', auth: true },
  { key: 'login', path: '/login', auth: false },
]
test.setTimeout(600000)

async function measure(page, ms) {
  return page.evaluate((dur) => new Promise((res) => {
    const ts = new Float64Array(16384); let n = 0
    const start = performance.now()
    const mem0 = performance.memory ? performance.memory.usedJSHeapSize : null
    function loop(t) {
      ts[n++] = t
      if (t - start < dur) requestAnimationFrame(loop)
      else {
        const gaps = []; for (let i = 1; i < n; i++) gaps.push(ts[i] - ts[i - 1])
        const srt = [...gaps].sort((a, b) => a - b)
        const pct = (q) => srt.length ? srt[Math.min(srt.length - 1, Math.floor(q * srt.length))] : 0
        const d = (ts[n - 1] - start) / 1000
        const mem1 = performance.memory ? performance.memory.usedJSHeapSize : null
        res({ samples: n, duration: +d.toFixed(2), p50: +pct(0.5).toFixed(1), p95: +pct(0.95).toFixed(1), p99: +pct(0.99).toFixed(1), avgFps: +((n - 1) / d).toFixed(1), minFps: gaps.length ? +(1000 / Math.max(...gaps)).toFixed(1) : 0, jank: gaps.filter((g) => g > 33.3).length, maxFrameMs: +(Math.max(...gaps)).toFixed(1), heapBeforeMB: mem0 ? +(mem0 / 1048576).toFixed(1) : null, heapDuringMB: mem1 ? +(mem1 / 1048576).toFixed(1) : null })
      }
    }
    requestAnimationFrame(loop)
  }), ms)
}

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

async function lifecycle(page) {
  return page.evaluate(() => new Promise((res) => {
    let gotLcp = 0, cls = 0
    try { new PerformanceObserver((l) => { for (const e of l.getEntries()) gotLcp = e.startTime }).observe({ type: 'largest-contentful-paint', buffered: true }) } catch {}
    try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) cls += e.value }).observe({ type: 'layout-shift', buffered: true }) } catch {}
    setTimeout(() => res({ lcpMs: +gotLcp.toFixed(0), cls: +cls.toFixed(4) }), 1200)
  }))
}

test('F2 三页五视口 headed 性能硬门槛 + LCP/CLS 无横滚', async ({ browser }) => {
  const report = { engine: 'app-shell-static-pages', base: BASE, pages: {} }
  for (const p of PAGES) {
    report.pages[p.key] = { viewports: {} }
    for (const v of VIEWS) {
      const ctx = await browser.newContext({ viewport: { width: v.width, height: v.height } })
      const page = await ctx.newPage()
      if (p.auth) await login(page)
      await page.goto(`${BASE}${p.path}`, { waitUntil: 'networkidle' })
      if (p.key === 'dashboard' || p.key === 'upload') await page.waitForSelector(p.key === 'dashboard' ? '.page-title' : '.dropzone', { timeout: 30000 })
      else await page.waitForSelector('.auth-card', { timeout: 30000 })
      const lcp = await lifecycle(page)
      await page.waitForTimeout(WARM_MS)
      const perf = await measure(page, MS)
      const noHScroll = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
      console.log(`F2 perf ${p.key} ${v.label} p95=${perf.p95} p99=${perf.p99} avg=${perf.avgFps} min=${perf.minFps} jank=${perf.jank} lcp=${lcp.lcpMs} cls=${lcp.cls} hscroll=${!noHScroll}`)
      report.pages[p.key].viewports[v.label] = { viewport: `${v.width}x${v.height}`, ...perf, lcpMs: lcp.lcpMs, cls: lcp.cls, noHScroll }
      await ctx.close()
    }
    // 每页写独立原始报告（不改门禁，独立保留失败 JSON）
    writeFileSync(resolve(__dirname, `../../../docs/6r21f-f2-perf-${p.key}-${RUN_TAG}.json`), JSON.stringify(report.pages[p.key], null, 2))
  }
  writeFileSync(OUT, JSON.stringify(report, null, 2))

  for (const p of PAGES) {
    for (const v of VIEWS) {
      const r = report.pages[p.key].viewports[v.label]
      expect(r.minFps, `${p.key}/${v.label} min>=50`).toBeGreaterThanOrEqual(50)
      expect(r.avgFps, `${p.key}/${v.label} avg>=55`).toBeGreaterThanOrEqual(55)
      expect(r.p95, `${p.key}/${v.label} p95<=25`).toBeLessThanOrEqual(25)
      expect(r.p99, `${p.key}/${v.label} p99<=25`).toBeLessThanOrEqual(25)
      expect(r.jank, `${p.key}/${v.label} jank<=2`).toBeLessThanOrEqual(2)
      expect(r.noHScroll, `${p.key}/${v.label} 无横滚`).toBe(true)
    }
  }
})
