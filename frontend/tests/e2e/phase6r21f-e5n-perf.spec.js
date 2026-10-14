// 6R21F-E5N perf：desktop/390/360 各真实播放 >=10s（worker ready + endpoint prepared + 1s 暖机后）。
// 硬门槛：p95/p99<=20ms、avgFps>=55、minFps>=50、jank<=1；记录 maxFramePhase/fieldRenderHz/endpointBuildCount/
// req/unique/cacheHits / CPU-WS/heap / allocCounters(0/0/0) / hotPathFullScans(0)。
import { test, expect } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(__dirname, '../../../docs/6r21f-e5n-perf.json')

const VIEWS = [
  { label: 'desktop', width: 1280, height: 800 },
  { label: '390', width: 390, height: 844 },
  { label: '360', width: 360, height: 740 },
]
const MS = 10000
const WARM_MS = 1200

test('E5N 三视口 >=10s 性能硬门槛（p95/p99/minFps/jank）+ 相位/端点/零扫描记录', async ({ page, browser }) => {
  const report = { views: {} }
  for (const v of VIEWS) {
    await page.setViewportSize({ width: v.width, height: v.height })
    await page.goto('/map-control-lab')
    await page.waitForSelector('[data-testid="mclab-canvas"]', { timeout: 20000 })
    await page.waitForFunction(() => { const c = document.querySelector('[data-testid="mclab-canvas"]'); if (!c) return false; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 40) if (d[i] !== 0) return true; return false }, undefined, { timeout: 20000 })
    // worker ready + endpoint prepared（awaitExact 会强制 exact 端点 + 渲染绑定）
    await page.waitForFunction(() => !!(window.__mclab && window.__mclab.workerStats && window.__mclab.workerStats.ready), undefined, { timeout: 20000 })
    await page.evaluate(() => window.__mclab.awaitExactAt(30.0, 8000))
    const layout = await page.evaluate(() => {
      const togs = [...document.querySelectorAll('.mclab-tog')]
      const tiny = togs.filter((t) => { const r = t.getBoundingClientRect(); return r.width < 44 || r.height < 44 }).length
      return { scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth, tiny }
    })
    expect(layout.scrollW).toBeLessThanOrEqual(layout.innerW)
    expect(layout.tiny).toBe(0)

    await page.getByRole('button', { name: '播放' }).click()
    await page.waitForTimeout(WARM_MS) // 1.2s 暖机（预热 worker + 端点）
    const perf = await page.evaluate((ms) => new Promise((res) => {
      const ts = []; const start = performance.now()
      const mem = performance.memory ? { used: performance.memory.usedJSHeapSize, total: performance.memory.jsHeapSizeLimit } : null
      function loop(t) { ts.push(t); if (t - start < ms) requestAnimationFrame(loop); else { const gaps = []; for (let i = 1; i < ts.length; i++) gaps.push(ts[i] - ts[i - 1]); const srt = [...gaps].sort((a, b) => a - b); const pct = (q) => srt.length ? srt[Math.min(srt.length - 1, Math.floor(q * srt.length))] : 0; const dur = (ts[ts.length - 1] - start) / 1000; let maxGap = 0, maxIdx = 0; for (let i = 0; i < gaps.length; i++) if (gaps[i] > maxGap) { maxGap = gaps[i]; maxIdx = i + 1 } res({ samples: ts.length, duration: dur, p50: +pct(0.5).toFixed(1), p95: +pct(0.95).toFixed(1), p99: +pct(0.99).toFixed(1), avgFps: +((ts.length - 1) / dur).toFixed(1), minFps: gaps.length ? +(1000 / Math.max(...gaps)).toFixed(1) : 0, jank: gaps.filter((g) => g > 33.3).length, maxFrameMs: +maxGap.toFixed(1), maxFrameIndex: maxIdx, jsHeap: mem }) } }
      requestAnimationFrame(loop)
    }), MS)

    // 进程 working set + CPU（沿用 e4k 惯例，标注来源；不拿 JS heap 冒充进程）
    let proc = null
    try {
      const pid = typeof browser.process === 'function' ? browser.process()?.pid : null
      if (pid) { const out = execSync(`pwsh -Command "Get-Process -Id ${pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty WorkingSet64"`).toString().trim(); const cpu = execSync(`pwsh -Command "(Get-Process -Id ${pid} -ErrorAction SilentlyContinue).CPU"`).toString().trim(); proc = { pid, workingSet64: Number(out) || null, cpuTotalSec: Number(cpu) || null, note: 'browser main process' } }
      else if (typeof browser.newBrowserCDPSession === 'function') { const cdp = await browser.newBrowserCDPSession(); const info = await cdp.send('SystemInfo.getProcessInfo'); const render = info.processInfo.filter((p) => p.type === 'renderer'); const r2 = render.sort((a, b) => b.cpuTime - a.cpuTime)[0]; if (r2) { const ws = execSync(`powershell.exe -NoProfile -Command "(Get-Process -Id ${r2.id} -ErrorAction SilentlyContinue).WorkingSet64"`).toString().trim(); proc = { pid: r2.id, cpuTime: r2.cpuTime, workingSet64: Number(ws) || null, note: 'renderer pid via SystemInfo.getProcessInfo + Get-Process', rendererCount: render.length } } else proc = { pid: null, workingSet64: null, cpuTotalSec: null, note: 'no renderer from SystemInfo' } }
      else proc = { pid: null, workingSet64: null, cpuTotalSec: null, note: 'no browser.process nor CDP' }
    } catch (e) { proc = { pid: null, workingSet64: null, cpuTotalSec: null, note: 'sampling failed: ' + e.message } }

    await page.getByRole('button', { name: '暂停' }).click()
    const m = await page.evaluate(() => window.__mclab && {
      workerStats: window.__mclab.workerStats, fieldRenderHz: window.__mclab.fieldRenderHz, endpointBuildCount: window.__mclab.endpointBuildCount,
      hotPathFullScans: window.__mclab.hotPathFullScans, allocCounters: window.__mclab.allocCounters, req: window.__mclab.req, uniqueTicks: window.__mclab.uniqueTicks,
      cacheHits: window.__mclab.cacheHits, maxFramePhase: window.__mclab.maxFramePhase, renderPhase: window.__mclab.renderPhase,
    })
    console.log(`E5N perf ${v.label} samples=${perf.samples} p50=${perf.p50} p95=${perf.p95} p99=${perf.p99} avgFps=${perf.avgFps} minFps=${perf.minFps} jank=${perf.jank} mclab=${JSON.stringify(m)}`)
    expect(m.workerStats.ready).toBe(true)
    expect(m.workerStats.frames).toBeGreaterThan(50)
    expect(m.hotPathFullScans).toBe(0)
    expect(m.allocCounters.areaObjectBuilds).toBe(0)
    expect(m.allocCounters.fullAreaMapBuilds).toBe(0)
    expect(m.allocCounters.legacyFrontierCalls).toBe(0)
    expect(m.endpointBuildCount).toBeGreaterThan(50)
    expect(perf.p95, `p95<=20ms (${v.label})`).toBeLessThanOrEqual(20)
    expect(perf.p99, `p99<=20ms (${v.label})`).toBeLessThanOrEqual(20)
    expect(perf.avgFps, `avgFps>=55 (${v.label})`).toBeGreaterThanOrEqual(55)
    expect(perf.minFps, `minFps>=50 (${v.label})`).toBeGreaterThanOrEqual(50)
    expect(perf.jank, `jank<=1 (${v.label})`).toBeLessThanOrEqual(1)

    report.views[v.label] = { viewport: `${v.width}x${v.height}`, ...perf, ...m, processCpuWorkingSet: proc, noHScroll: layout.scrollW <= layout.innerW, touchTargetsGe44: layout.tiny === 0 }
  }
  writeFileSync(OUT, JSON.stringify(report, null, 2))
})
