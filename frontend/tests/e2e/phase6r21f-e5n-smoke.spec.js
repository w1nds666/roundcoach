// 6R21F-E5N smoke：确认实验室在 E5N 端点架构下运行，新 API/字段存在且热路径零全扫描、无控制台错误。
import { test, expect } from '@playwright/test'

test('E5N smoke: endpoint API + hot-path-zero-scan + no console errors', async ({ page }) => {
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto('/map-control-lab')
  await page.waitForSelector('[data-testid="mclab-canvas"]', { timeout: 25000 })
  await page.waitForFunction(() => { const c = document.querySelector('[data-testid="mclab-canvas"]'); if (!c) return false; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 40) if (d[i] !== 0) return true; return false }, undefined, { timeout: 25000 })
  await page.waitForFunction(() => !!(window.__mclab && (typeof window.__mclab.captureExactSnapshot === 'function') && (typeof window.__mclab.awaitExactAt === 'function')), undefined, { timeout: 25000 })
  const m = await page.evaluate(() => ({
    areas: window.__mclab.areas, gridN: window.__mclab.gridN, modelExact: window.__mclab.modelExact,
    renderedTicks: window.__mclab.renderedTicks, hashes: window.__mclab.hashes, frac: window.__mclab.frac,
    fieldChecksum: window.__mclab.fieldChecksum, fieldRenderHz: window.__mclab.fieldRenderHz,
    endpointBuildCount: window.__mclab.endpointBuildCount, hotPathFullScans: window.__mclab.hotPathFullScans,
    req: window.__mclab.req, uniqueTicks: window.__mclab.uniqueTicks, cacheHits: window.__mclab.cacheHits,
    states: window.__mclab.states, alive: window.__mclab.alive, workerReady: window.__mclab.workerStats.ready,
    hasCapture: typeof window.__mclab.captureExactSnapshot === 'function', hasAwait: typeof window.__mclab.awaitExactAt === 'function',
  }))
  console.log('E5N smoke mclab=' + JSON.stringify(m))
  expect(m.areas).toBe(2544)
  expect(m.gridN).toBe(128)
  expect(m.hasCapture).toBe(true)
  expect(m.hasAwait).toBe(true)
  expect(m.hotPathFullScans).toBe(0)
  expect(m.workerReady).toBe(true)
  expect(m.states).toBeTruthy()
  expect(typeof m.fieldChecksum).toBe('number')
  expect(m.frac).toBeGreaterThanOrEqual(0)
  expect(m.endpointBuildCount).toBeGreaterThan(0)
  expect(String(m.req)).toBeTruthy()
  expect(errors, 'no page errors').toEqual([])
})

test('E5N smoke: captureExactSnapshot parity all-true at 19.5', async ({ page }) => {
  await page.goto('/map-control-lab')
  await page.waitForSelector('[data-testid="mclab-canvas"]', { timeout: 25000 })
  await page.waitForFunction(() => { const c = document.querySelector('[data-testid="mclab-canvas"]'); if (!c) return false; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 40) if (d[i] !== 0) return true; return false }, undefined, { timeout: 25000 })
  await page.waitForFunction(() => !!(window.__mclab && window.__mclab.captureExactSnapshot), undefined, { timeout: 25000 })
  const max = await page.locator('.mclab-tl').evaluate((el) => Number(el.max) || 0)
  await page.locator('.mclab-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, max * 19.5 / 60.92)
  await page.waitForTimeout(150)
  const r = await page.evaluate(() => window.__mclab.captureExactSnapshot(19.5, 8000))
  console.log('E5N captureExactSnapshot(19.5)=' + JSON.stringify(r))
  expect(r.ok).toBe(true)
  expect(r.comparisonPassed).toBe(true)
  expect(r.diffCount).toBe(0)
  expect(r.hashMatch).toBe(true)
  expect(r.countsMatch).toBe(true)
  expect(r.controlsHashMatch).toBe(true)
})
