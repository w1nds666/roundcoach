// 6R21F-F1 性能门禁：Landing 首页真实 headed 应用渲染。
// 覆盖视口：desktop(1280)/1081/900/390/360。播放舞台 >=10s，硬门槛 minFps>=50、avg>=55、p95/p99<=25ms、jank<=2。
// 记录 LCP / CLS / 无横滚 / JS heap。默认继承所选 Playwright 配置的 baseURL；PERF_BASE 可显式覆盖。
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(__dirname, '../../../docs/6r21f-f1-perf.json')
const RUN_TAG = process.env.F1_RUN_TAG || 'latest'
mkdirSync(dirname(OUT), { recursive: true })
const MS = 10000
const WARM_MS = 1200
const VIEWS = [
  { label: 'desktop', width: 1280, height: 800 },
  { label: '1081', width: 1081, height: 800 },
  { label: '900', width: 900, height: 800 },
  { label: '390', width: 390, height: 844 },
  { label: '360', width: 360, height: 740 },
]

test.setTimeout(300000)

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

test('F1 Landing 五视口 headed 性能硬门槛 + LCP/CLS', async ({ page, baseURL }) => {
  const BASE = process.env.PERF_BASE || baseURL
  if (!BASE) throw new Error('Performance target requires PERF_BASE or Playwright baseURL')
  const report = { engine: 'landing-stage', base: BASE, targetSource: process.env.PERF_BASE ? 'PERF_BASE' : 'playwright.baseURL', views: {} }
  for (const v of VIEWS) {
    await page.setViewportSize({ width: v.width, height: v.height })
    // 生命周期的 LCP / CLS 采集
    await page.goto(`${BASE}/welcome`, { waitUntil: 'networkidle' })
    const lcp = await page.evaluate(() => new Promise((res) => {
      let gotLcp = 0, cls = 0
      try {
        new PerformanceObserver((l) => { for (const e of l.getEntries()) gotLcp = e.startTime }).observe({ type: 'largest-contentful-paint', buffered: true })
        try {
          new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) cls += e.value }).observe({ type: 'layout-shift', buffered: true })
        } catch (e) {}
      } catch (e) {}
      setTimeout(() => res({ lcpMs: +gotLcp.toFixed(0), cls: +cls.toFixed(4) }), 1200)
    }))
    await expect(page.getByTestId('landing-replay-stage')).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
    // 进入播放，确认时间轴确实推进后再预热采样，避免把暂停态误报为性能通过。
    const timeBeforePlay = await page.locator('.player-time').innerText()
    await page.locator('.play-btn').click()
    await expect(page.locator('.play-btn')).toHaveAttribute('aria-label', '暂停界面预览')
    await expect.poll(() => page.locator('.player-time').innerText()).not.toBe(timeBeforePlay)
    await page.locator('.timeline').scrollIntoViewIfNeeded()
    await page.waitForTimeout(WARM_MS)
    const perf = await measure(page, MS)
    await page.locator('.play-btn').click() // 暂停
    console.log(`F1 perf ${v.label} p95=${perf.p95} p99=${perf.p99} avg=${perf.avgFps} min=${perf.minFps} jank=${perf.jank} lcp=${lcp.lcpMs} cls=${lcp.cls}`)
    report.views[v.label] = { viewport: `${v.width}x${v.height}`, ...perf, lcpMs: lcp.lcpMs, cls: lcp.cls, noHScroll: overflow }
    const raw = resolve(__dirname, `../../../docs/6r21f-f1-perf-${RUN_TAG}.json`)
    writeFileSync(raw, JSON.stringify(report, null, 2))
    writeFileSync(OUT, JSON.stringify(report, null, 2))
    expect(perf.minFps, `min>=50 (${v.label})`).toBeGreaterThanOrEqual(50)
    expect(perf.avgFps, `avg>=55 (${v.label})`).toBeGreaterThanOrEqual(55)
    expect(perf.p95, `p95<=25 (${v.label})`).toBeLessThanOrEqual(25)
    expect(perf.p99, `p99<=25 (${v.label})`).toBeLessThanOrEqual(25)
    expect(perf.jank, `jank<=2 (${v.label})`).toBeLessThanOrEqual(2)
    expect(overflow, `no hscroll (${v.label})`).toBe(true)
  }
})
