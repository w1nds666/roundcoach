// 6R21F-F4 性能门禁：Coach / Profile / Share 三页五视口真实应用渲染。
// 记录 p50/p95/p99、avg/min FPS、jank、JS heap、renderer 工作集/CPU、无横滚。
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BASE = process.env.PERF_BASE || 'http://localhost:5173'
const OUT = resolve(__dirname, '../../../docs/6r21f-f4-perf.json')
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const MS = 6000
const WARM_MS = 900
const VIEWS = [
  { label: '1280', width: 1280, height: 800 },
  { label: '1081', width: 1081, height: 800 },
  { label: '900', width: 900, height: 800 },
  { label: '390', width: 390, height: 844 },
  { label: '360', width: 360, height: 740 },
]
mkdirSync(dirname(OUT), { recursive: true })
test.setTimeout(600000)

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 20000 })
}

async function measure(page, ms) {
  return page.evaluate((duration) => new Promise((resolveMeasure) => {
    const frames = new Float64Array(16384)
    let count = 0
    const start = performance.now()
    const heapBefore = performance.memory?.usedJSHeapSize ?? null
    function frame(now) {
      frames[count++] = now
      if (now - start < duration) requestAnimationFrame(frame)
      else {
        const gaps = []
        for (let i = 1; i < count; i++) gaps.push(frames[i] - frames[i - 1])
        const sorted = [...gaps].sort((a, b) => a - b)
        const percentile = (q) => sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] : 0
        const seconds = (frames[count - 1] - start) / 1000
        const heapAfter = performance.memory?.usedJSHeapSize ?? null
        resolveMeasure({
          samples: count,
          duration: +seconds.toFixed(2),
          p50: +percentile(.5).toFixed(1),
          p95: +percentile(.95).toFixed(1),
          p99: +percentile(.99).toFixed(1),
          avgFps: +((count - 1) / seconds).toFixed(1),
          minFps: gaps.length ? +(1000 / Math.max(...gaps)).toFixed(1) : 0,
          jank: gaps.filter((gap) => gap > 33.3).length,
          maxFrameMs: +(Math.max(...gaps)).toFixed(1),
          heapBeforeMB: heapBefore ? +(heapBefore / 1048576).toFixed(1) : null,
          heapAfterMB: heapAfter ? +(heapAfter / 1048576).toFixed(1) : null,
        })
      }
    }
    requestAnimationFrame(frame)
  }), ms)
}

async function rendererProcess(browser) {
  try {
    if (typeof browser.newBrowserCDPSession !== 'function') return { note: 'CDP process info unavailable' }
    const cdp = await browser.newBrowserCDPSession()
    const info = await cdp.send('SystemInfo.getProcessInfo')
    const renderer = info.processInfo.filter((process) => process.type === 'renderer').sort((a, b) => b.cpuTime - a.cpuTime)[0]
    if (!renderer) return { note: 'no renderer process found' }
    const ws = execSync(`powershell.exe -NoProfile -Command "(Get-Process -Id ${renderer.id} -ErrorAction SilentlyContinue).WorkingSet64"`, { encoding: 'utf8' }).trim()
    return { pid: renderer.id, cpuTimeSec: renderer.cpuTime, workingSetMB: ws ? +(Number(ws) / 1048576).toFixed(1) : null }
  } catch (error) {
    return { note: `process probe failed: ${String(error.message || error)}` }
  }
}

test('F4 三页五视口性能硬门槛', async ({ browser }) => {
  const bootstrap = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const bootstrapPage = await bootstrap.newPage()
  await login(bootstrapPage)
  const token = await bootstrapPage.evaluate(() => localStorage.getItem('cs2_token'))
  const response = await bootstrapPage.request.post('/api/demos/19/share', {
    headers: { Authorization: `Bearer ${token}` },
    data: { expires_in_hours: 24 },
  })
  const shareToken = (await response.json()).token
  await bootstrap.close()

  const pages = [
    { key: 'coach', path: '/coach', selector: '.coach-command', auth: true },
    { key: 'profile', path: '/profile', selector: '.identity-command', auth: true },
    { key: 'share', path: `/share#${shareToken}`, selector: '.share-trustbar', auth: false },
  ]
  const report = { engine: 'F4 real-app-rAF', base: BASE, measuredAt: new Date().toISOString(), pages: {} }

  for (const target of pages) {
    report.pages[target.key] = { viewports: {} }
    for (const view of VIEWS) {
      const context = await browser.newContext({ viewport: { width: view.width, height: view.height } })
      const page = await context.newPage()
      if (target.auth) await login(page)
      await page.goto(`${BASE}${target.path}`, { waitUntil: 'networkidle' })
      await page.waitForSelector(target.selector, { timeout: 30000 })
      await page.waitForTimeout(WARM_MS)
      const perf = await measure(page, MS)
      const noHScroll = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
      const process = await rendererProcess(browser)
      report.pages[target.key].viewports[view.label] = { viewport: `${view.width}x${view.height}`, ...perf, noHScroll, process }
      console.log(`F4 perf ${target.key}/${view.label} p95=${perf.p95} p99=${perf.p99} avg=${perf.avgFps} min=${perf.minFps} jank=${perf.jank} heap=${perf.heapAfterMB}MB ws=${process.workingSetMB ?? 'n/a'}MB`)
      await context.close()
    }
  }
  writeFileSync(OUT, JSON.stringify(report, null, 2))

  for (const target of pages) for (const view of VIEWS) {
    const result = report.pages[target.key].viewports[view.label]
    expect(result.minFps, `${target.key}/${view.label} minFPS`).toBeGreaterThanOrEqual(50)
    expect(result.avgFps, `${target.key}/${view.label} avgFPS`).toBeGreaterThanOrEqual(55)
    expect(result.p95, `${target.key}/${view.label} p95`).toBeLessThanOrEqual(25)
    expect(result.p99, `${target.key}/${view.label} p99`).toBeLessThanOrEqual(25)
    expect(result.jank, `${target.key}/${view.label} jank`).toBeLessThanOrEqual(2)
    expect(result.noHScroll, `${target.key}/${view.label} horizontal scroll`).toBe(true)
  }
})
