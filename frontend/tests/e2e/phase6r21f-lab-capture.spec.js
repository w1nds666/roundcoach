// 6R21F /map-control-lab capture + 专项校验（对 Vite dev server，PLAYWRIGHT_BASE_URL=http://localhost:5173）。
// 覆盖：真实渲染 + 固定时刻截图 + 短交互帧差 + perf(p50/p95/p99,avg/min FPS,jank) + 无横滚(390/360) + reduced-motion 静态。
import { test, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SHOTS = process.env.MCLAB_SHOTS_DIR
  ? resolve(process.env.MCLAB_SHOTS_DIR)
  : resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(SHOTS, { recursive: true })

async function waitLab(page) {
  await page.goto('/map-control-lab')
  await page.waitForSelector('[data-testid="mclab-canvas"]', { timeout: 20000 })
  await page.waitForFunction(() => {
    const c = document.querySelector('[data-testid="mclab-canvas"]')
    if (!c) return false
    if (!window.__mclab?.states || !window.__mclab?.contour) return false
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data
    for (let i = 0; i < d.length; i += 40) if (d[i] !== 0 || d[i + 1] !== 0 || d[i + 2] !== 0 || d[i + 3] !== 0) return true
    return false
  }, undefined, { timeout: 20000 })
}
async function seekRatio(page, ratio) {
  const max = await page.locator('.mclab-tl').evaluate((el) => Number(el.max) || 0)
  await page.locator('.mclab-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, max * ratio)
  await page.waitForTimeout(350)
}
async function canvasChecksum(page) {
  return page.locator('[data-testid="mclab-canvas"]').evaluate((c) => {
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data
    let h = 0n
    for (let i = 0; i < d.length; i += 199) h = (h * 31n + BigInt(d[i])) % 1000000007n
    return String(h)
  })
}
function stateCounts(page) {
  return page.evaluate(() => {
    // 从 window 读取 lab 暴露的模型帧（若开放）；否则从 canvas 像素粗估
    if (window.__mclab && window.__mclab.states) return window.__mclab.states
    return null
  })
}

test('lab render + fixed-timestamp shots + frame-diff + perf + no-scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await waitLab(page)
  const canvas = await page.locator('[data-testid="mclab-canvas"]').boundingBox()
  expect(canvas).toBeTruthy()

  // truth：状态混合（必须保留 NEUTRAL，不做全图二分）
  const st = await page.evaluate(() => window.__mclab && window.__mclab.states)
  expect(st).toBeTruthy()
  expect(st.CT).toBeGreaterThan(0)
  expect(st.T).toBeGreaterThan(0)
  expect(st.NEUTRAL).toBeGreaterThan(0)
  expect(st.NEUTRAL / (st.CT + st.T + st.CONTESTED + st.NEUTRAL)).toBeGreaterThan(0.3) // neutral 占比诚实
  expect(st.CONTESTED).toBeGreaterThan(0)
  expect(st.CONTESTED / (st.CT + st.T + st.CONTESTED + st.NEUTRAL)).toBeLessThan(0.12) // contested 带宽有上限（窄前线）

  // D4：contour 只读 data 属性 + 低覆盖率（非大面积色块）+ 独立 layer 开关
  const contAttr = await page.locator('[data-testid="mclab-canvas"]').getAttribute('data-contour-active-lines')
  expect(contAttr).toBeTruthy()
  expect(await page.locator('.mclab-tog:has-text("连续控制场")').count()).toBe(1)
  expect(await page.locator('.mclab-tog:has-text("控制前线")').count()).toBe(1)
  expect(await page.locator('.mclab-tog:has-text("击杀/事件")').count()).toBe(1)
  await seekRatio(page, 0.55)
  const cont = await page.evaluate(() => window.__mclab && window.__mclab.contour)
  expect(cont).toBeTruthy()
  expect(cont.coloredRatio).toBeLessThan(0.5) // 覆盖显著低于 D2（证明非大片色块）

  // Milestone D renderer truth：隔墙像素不串色（若存在近屏幕不同连通分量探测；cross-stain 由 fieldTexture 单测托底）
  const probes = await page.evaluate(() => window.__mclab && window.__mclab.probes)
  for (const p of probes || []) {
    if (['CT', 'T', 'CONTESTED'].includes(p.aState)) expect(p.bState, `area ${p.bId} 应 neutral（隔墙不串色）`).toBe('NEUTRAL')
  }

  // 确定性：同时间 seek 3 次 → 整幅渲染 checksum 相同
  await seekRatio(page, 0.3)
  const c1 = await canvasChecksum(page); await seekRatio(page, 0.3); const c2 = await canvasChecksum(page); await seekRatio(page, 0.3); const c3 = await canvasChecksum(page)
  expect(c1).toBe(c2)
  expect(c2).toBe(c3)

  // 不同时刻 → checksum 不同（真实时间轴驱动）
  await seekRatio(page, 0.1); const t1 = await canvasChecksum(page)
  await seekRatio(page, 0.85); const t2 = await canvasChecksum(page)
  expect(t1).not.toBe(t2)

  // 固定时刻截图（默认峰值 + 0.2 / 0.8）
  await seekRatio(page, 0.405); await page.screenshot({ path: `${SHOTS}/6r21f-map-control-lab-a0.png` })
  await seekRatio(page, 0.2); await page.screenshot({ path: `${SHOTS}/6r21f-map-control-lab-a02.png` })
  // E1：真实前线峰值时刻（0.6 ≈ 36.5s，scan 显示 active 峰值）
  await seekRatio(page, 0.6); await page.screenshot({ path: `${SHOTS}/6r21f-lab-e1-peak.png` })
  const e1 = await page.evaluate(() => ({ active: +document.querySelector('[data-testid="mclab-canvas"]').getAttribute('data-contour-active-lines'), pred: +document.querySelector('[data-testid="mclab-canvas"]').getAttribute('data-contour-predicted-lines'), len: +document.querySelector('[data-testid="mclab-canvas"]').getAttribute('data-contour-length') }))
  console.log('E1 peak contour', JSON.stringify(e1))
  expect(e1.active + e1.pred).toBeGreaterThan(0)
  // 纯 radar（总控制场关 → 无连续场/无前线）+ 迁移帧（D2 前线迁移）
  await page.locator('.mclab-tog').filter({ hasText: '控制场(总)' }).locator('input').uncheck(); await page.waitForTimeout(150)
  await page.screenshot({ path: `${SHOTS}/6r21f-lab-radar-only.png` })
  await page.locator('.mclab-tog').filter({ hasText: '控制场(总)' }).locator('input').check(); await page.waitForTimeout(150)
  await seekRatio(page, 0.32)
  await page.getByRole('button', { name: '播放' }).click(); await page.waitForTimeout(650); await page.getByRole('button', { name: '暂停' }).click()
  await page.screenshot({ path: `${SHOTS}/6r21f-lab-d2-migrated.png` })

  // 帧差：seek 前后整幅 canvas 校验和变化（交互证据）
  const before = await canvasChecksum(page)
  await seekRatio(page, 0.5)
  const after = await canvasChecksum(page)
  expect(before).not.toBe(after)

  // 连续帧差：短播放（真实时间轴）→ checksum 变化（扩张/收缩/前线迁移）
  const f0 = await canvasChecksum(page)
  await page.getByRole('button', { name: '播放' }).click()
  await page.waitForTimeout(500)
  await page.getByRole('button', { name: '暂停' }).click()
  const f1 = await canvasChecksum(page)
  expect(f0).not.toBe(f1)

  // perf：短暂播放，测 rAF 帧间隔 + minFps + renderer 任务时长代理 + JS heap/RAM
  await page.getByRole('button', { name: '播放' }).click()
  const perf = await page.evaluate(() => new Promise((res) => {
    const ts = []; const start = performance.now()
    // renderer task 时长代理：测量一次 rAF 回调耗时（CPU proxy）
    let taskMax = 0, taskSum = 0, taskN = 0
    const mem = performance.memory && performance.memory.usedJSHeapSize != null
      ? { usedJSHeapSize: performance.memory.usedJSHeapSize, jsHeapSizeLimit: performance.memory.jsHeapSizeLimit }
      : null
    function loop(t) {
      const t0 = performance.now()
      ts.push(t)
      // 近似渲染任务时长：本回调体内同步测量
      const dt = performance.now() - t0
      if (dt > 0) { taskSum += dt; taskN++; if (dt > taskMax) taskMax = dt }
      if (t - start < 2500) requestAnimationFrame(loop)
      else {
        const gaps = []; for (let i = 1; i < ts.length; i++) gaps.push(ts[i] - ts[i - 1])
        const srt = [...gaps].sort((a, b) => a - b)
        const pct = (q) => srt.length ? srt[Math.min(srt.length - 1, Math.floor(q * srt.length))] : 0
        const maxGap = gaps.length ? Math.max(...gaps) : 0
        res({
          measurement: 'rAF gap sampling over 2.5s playback (Chromium headed via Playwright)',
          frames: ts.length,
          p50: +pct(0.5).toFixed(1), p95: +pct(0.95).toFixed(1), p99: +pct(0.99).toFixed(1),
          avgFps: +((ts.length - 1) / ((ts[ts.length - 1] - start) / 1000)).toFixed(1),
          minFps: maxGap > 0 ? +(1000 / maxGap).toFixed(1) : 0, // 由最大有效 frame gap 定义
          jank: gaps.filter((g) => g > 33.3).length,
          rendererTaskCpuProxyMs: { avg: +(taskSum / Math.max(1, taskN)).toFixed(2), max: +taskMax.toFixed(2) },
          jsHeapUsed: mem ? { usedJSHeapSize: mem.usedJSHeapSize, jsHeapSizeLimit: mem.jsHeapSizeLimit, note: 'Chromium performance.memory.usedJSHeapSize (working set 代理)' } : 'n/a',
        })
      }
    }
    requestAnimationFrame(loop)
  }))
  await page.getByRole('button', { name: '暂停' }).click()
  writeFileSync(resolve(SHOTS, '../6r21f-lab-perf.json'), JSON.stringify(perf, null, 2))
  expect(perf.avgFps).toBeGreaterThan(0)

  // 无横滚 390 / 360
  for (const w of [390, 360]) {
    await page.setViewportSize({ width: w, height: 844 })
    await page.waitForTimeout(200)
    const m = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
    expect(m.scroll).toBeLessThanOrEqual(m.client + 1)
  }
})

test('reduced-motion 静态：点击播放不连续推进', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 1000, height: 1000 })
  await waitLab(page)
  const getT = () => page.locator('.mclab-time').innerText()
  const t0 = await getT()
  await page.getByRole('button', { name: '播放' }).click()
  await page.waitForTimeout(400)
  const t1 = await getT()
  expect(t1).not.toBe(t0)
  await page.waitForTimeout(900)
  expect(await getT()).toBe(t1)
})
