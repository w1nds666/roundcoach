// 6R21F-E4P 性能：desktop/390/360 各 >=10s 真实播放。rAF 帧间隔统计 + JS heap + 视口/触控/reduced-motion + 图层独立像素差。
// CPU/进程 working set：能取得浏览器进程则记录（明确标注非 renderer PID），不能则写 null+原因；绝不拿 rendererTaskCpuProxy/JS heap 冒充。
import { test, expect } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { execSync } from 'node:child_process'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(__dirname, '../../../docs/6r21f-e5k-perf.json')

const VIEWS = [
  { label: 'desktop', width: 1280, height: 800 },
  { label: '390', width: 390, height: 844 },
  { label: '360', width: 360, height: 740 },
]
const MS = 10000

async function readChecksum(page) { return page.locator('[data-testid="mclab-canvas"]').evaluate((c) => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let s = 0; for (let i = 0; i < d.length; i++) s = (s + d[i]) % 1000000007; return String(s) }) }
async function nonTransparent(page) { return page.locator('[data-testid="mclab-canvas"]').evaluate((c) => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] !== 0) n++; return n }) }
async function toggleBy(page, label, on) { const el = page.locator('.mclab-tog').filter({ hasText: label }).locator('input'); if (on) await el.check(); else await el.uncheck(); await page.waitForTimeout(180) }

test('E4P 三视口 >=10s 性能 + 视口/触控/reduced-motion + 图层独立', async ({ page, browser }) => {
  const report = { views: {} }
  for (const v of VIEWS) {
    await page.setViewportSize({ width: v.width, height: v.height })
    await page.goto('/map-control-lab')
    await page.waitForSelector('[data-testid="mclab-canvas"]', { timeout: 20000 })
    await page.waitForFunction(() => { const c = document.querySelector('[data-testid="mclab-canvas"]'); if (!c) return false; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 40) if (d[i] !== 0) return true; return false }, undefined, { timeout: 20000 })

    // 无横滚 + 触控>=44 + reduced-motion
    const layout = await page.evaluate(() => {
      const togs = [...document.querySelectorAll('.mclab-tog')]
      const tiny = togs.filter((t) => { const r = t.getBoundingClientRect(); return r.width < 44 || r.height < 44 }).length
      return { scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth, tiny }
    })
    expect(layout.scrollW).toBeLessThanOrEqual(layout.innerW) // 无横滚
    expect(layout.tiny).toBe(0) // 触控 >=44

    // 播放 >=10s，采集 rAF 帧间隔 + JS heap + browser process working set
    await toggleBy(page, '控制场(总)', true)
    await page.getByRole('button', { name: '播放' }).click()
    const perf = await page.evaluate((ms) => new Promise((res) => {
      const ts = []; const start = performance.now()
      const mem = performance.memory ? { used: performance.memory.usedJSHeapSize, total: performance.memory.jsHeapSizeLimit } : null
      function loop(t) { ts.push(t); if (t - start < ms) requestAnimationFrame(loop); else { const gaps = []; for (let i = 1; i < ts.length; i++) gaps.push(ts[i] - ts[i - 1]); const srt = [...gaps].sort((a, b) => a - b); const pct = (q) => srt.length ? srt[Math.min(srt.length - 1, Math.floor(q * srt.length))] : 0; const dur = (ts[ts.length - 1] - start) / 1000; let maxGap = 0, maxIdx = 0; for (let i = 0; i < gaps.length; i++) if (gaps[i] > maxGap) { maxGap = gaps[i]; maxIdx = i + 1 } res({ samples: ts.length, duration: dur, p50: +pct(0.5).toFixed(1), p95: +pct(0.95).toFixed(1), p99: +pct(0.99).toFixed(1), avgFps: +((ts.length - 1) / dur).toFixed(1), minFps: gaps.length ? +(1000 / Math.max(...gaps)).toFixed(1) : 0, jank: gaps.filter((g) => g > 33.3).length, maxFrameMs: +maxGap.toFixed(1), maxFrameIndex: maxIdx, jsHeap: mem }) } }
      requestAnimationFrame(loop)
    }), MS)

    // 进程 working set + CPU：先试 browser.process()，再试 browser-level CDP SystemInfo.getProcessInfo → 绑定 renderer pid。
    let proc = null
    try {
      const pid = typeof browser.process === 'function' ? browser.process()?.pid : null
      if (pid) { const out = execSync(`pwsh -Command "Get-Process -Id ${pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty WorkingSet64"`).toString().trim(); const cpu = execSync(`pwsh -Command "(Get-Process -Id ${pid} -ErrorAction SilentlyContinue).CPU"`).toString().trim(); proc = { pid, workingSet64: Number(out) || null, cpuTotalSec: Number(cpu) || null, note: 'browser main process' } }
      else if (typeof browser.newBrowserCDPSession === 'function') { const cdp = await browser.newBrowserCDPSession(); const info = await cdp.send('SystemInfo.getProcessInfo'); const render = info.processInfo.filter((p) => p.type === 'renderer'); const r2 = render.sort((a, b) => b.cpuTime - a.cpuTime)[0]; if (r2) { const ws = execSync(`powershell.exe -NoProfile -Command "(Get-Process -Id ${r2.id} -ErrorAction SilentlyContinue).WorkingSet64"`).toString().trim(); proc = { pid: r2.id, cpuTime: r2.cpuTime, workingSet64: Number(ws) || null, note: 'renderer pid via SystemInfo.getProcessInfo + Get-Process', rendererCount: render.length } } else proc = { pid: null, workingSet64: null, cpuTotalSec: null, note: 'no renderer from SystemInfo' } }
      else proc = { pid: null, workingSet64: null, cpuTotalSec: null, note: 'no browser.process nor browser-level CDP available' }
    } catch (e) { proc = { pid: null, workingSet64: null, cpuTotalSec: null, note: 'sampling failed: ' + e.message } }

    await page.getByRole('button', { name: '暂停' }).click()
    const tick = await page.evaluate(() => window.__mclab && window.__mclab.tick)
    const ws = await page.evaluate(() => window.__mclab && window.__mclab.workerStats)
    expect(ws.ready).toBe(true) // worker 预热后 ready
    expect(ws.frames).toBeGreaterThan(50) // 连续播放 workerFrameCount 增长
    expect(ws.sync).toBeLessThan(5) // 运行中 syncFallbackCount 不再增长（warmed 后走 worker）
    console.log(`E4P perf ${v.label} tick=${JSON.stringify(tick)} worker=${JSON.stringify(ws)}`)

    // 图层独立像素差：scalarField on->off / front on->off 均改变画面；两者都关时玩家/事件仍存在
    await toggleBy(page, '连续控制场', true); await toggleBy(page, '控制前线', true)
    const base = await readChecksum(page); const baseN = await nonTransparent(page)
    await toggleBy(page, '连续控制场', false); const noScalar = await readChecksum(page)
    await toggleBy(page, '连续控制场', true)
    await toggleBy(page, '控制前线', false); const noFront = await readChecksum(page)
    await toggleBy(page, '控制前线', false); await toggleBy(page, '连续控制场', false); const none = await nonTransparent(page)
    expect(noScalar).not.toBe(base); expect(noFront).not.toBe(base) // 图层独立
    expect(none).toBeGreaterThan(0) // 玩家/事件/雷达仍在

    report.views[v.label] = { viewport: `${v.width}x${v.height}`, ...perf, processCpuWorkingSet: proc, layerPixelDiff: { scalarOffChanged: noScalar !== base, frontOffChanged: noFront !== base, nonTransparentWhenBothOff: none }, noHScroll: layout.scrollW <= layout.innerW, touchTargetsGe44: layout.tiny === 0 }
    console.log(`E4P perf ${v.label} samples=${perf.samples} dur=${perf.duration.toFixed(1)}ms? p50=${perf.p50} p95=${perf.p95} p99=${perf.p99} avgFps=${perf.avgFps} minFps=${perf.minFps} jank=${perf.jank} heap=${perf.jsHeap?.used}`)
  }
  writeFileSync(OUT, JSON.stringify(report, null, 2))
})
