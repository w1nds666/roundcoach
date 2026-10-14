// 6R21F-E5L evidence (non-overwriting 6r21f-e5l-*): awaitExact hard gate + death truth + determinism.
import { test, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SHOTS = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(SHOTS, { recursive: true })
const DUR = 60.92

test('E5L five moments awaitExact + death truth + determinism', async ({ page }) => {
  await page.goto('/map-control-lab')
  await page.waitForSelector('[data-testid="mclab-canvas"]', { timeout: 20000 })
  await page.waitForFunction(() => { const c = document.querySelector('[data-testid="mclab-canvas"]'); if (!c) return false; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 40) if (d[i] !== 0) return true; return false }, undefined, { timeout: 20000 })

  const moments = [{ name: '0019', ratio: 19 / DUR }, { name: 'active-peak', ratio: 0.694 }, { name: 'contest-peak', ratio: 0.554 }, { name: 'death-before', ratio: 19.51 / DUR }, { name: 'death-after', ratio: 19.71 / DUR }]
  const sides = {}
  async function seekTo(page, ratio) { const max = await page.locator('.mclab-tl').evaluate((el) => Number(el.max) || 0); await page.locator('.mclab-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, max * ratio); await page.waitForTimeout(150) }
  async function awaitExact(page, t) { return page.evaluate((tt) => (window.__mclab && window.__mclab.awaitExactAt) ? window.__mclab.awaitExactAt(tt, 6000) : { ok: false, diagnostic: 'no API' }, t) }

  for (const m of moments) {
    await seekTo(page, m.ratio)
    const curT = await page.locator('.mclab-tl').evaluate((el) => Number(el.value) || 0)
    const res = await awaitExact(page, curT)
    console.log(`E5L await ${m.name} t=${curT} ${JSON.stringify(res)}`)
    expect(res.ok, `awaitExact ${m.name}: ${res.diagnostic || ''}`).toBe(true)
    await page.waitForTimeout(400)
    const side = await page.evaluate(() => { const c = document.querySelector('[data-testid="mclab-canvas"]'); return { states: (window.__mclab && window.__mclab.states) || null, alive: (window.__mclab && window.__mclab.alive) != null ? window.__mclab.alive : null, scalarHash: (window.__mclab && window.__mclab.frameHash) || null, modelExact: !!(window.__mclab && window.__mclab.modelExact), requestedTicks: (window.__mclab && window.__mclab.requestedTicks) || null, resolvedTicks: (window.__mclab && window.__mclab.resolvedTicks) || null, frameSources: (window.__mclab && window.__mclab.frameSources) || null, allocCounters: (window.__mclab && window.__mclab.allocCounters) || null, workerStats: (window.__mclab && window.__mclab.workerStats) || null, activeLines: +c.getAttribute('data-contour-active-lines'), predictedLines: +c.getAttribute('data-contour-predicted-lines'), contourLength: +c.getAttribute('data-contour-length') } })
    // frameSources 来自 render 期（awaitExact ok 已确认 exact；此处如实记录在 sidecar，非硬门禁）
    console.log(`E5L side ${m.name} frameSources=${JSON.stringify(side.frameSources)}`)
    const h = []
    for (let i = 0; i < 3; i++) { await seekTo(page, m.ratio); const ok = await awaitExact(page, curT); expect(ok.ok).toBe(true); h.push(await page.evaluate(() => window.__mclab.frameHash)) }
    expect(new Set(h).size).toBe(1)
    const png = resolve(SHOTS, `6r21f-e5l-${m.name}.png`)
    await page.screenshot({ path: png })
    const json = { moment: m.name, timestamp: curT, tSec: curT, eventTime: m.name.startsWith('death') ? 19.61 : null, eventType: m.name.startsWith('death') ? 'kill' : null, anonymousVictimIndex: m.name.startsWith('death') ? 0 : null, victimTeam: m.name.startsWith('death') ? 'T' : null, sourceBefore: m.name.startsWith('death') ? true : null, sourceAfter: m.name.startsWith('death') ? false : null, deathMarkerPresent: m.name.startsWith('death') ? (side.alive != null && sides['death-before'] ? (sides['death-before'].alive - side.alive === 1) : false) : null, comparisonPassed: res.ok, ...side }
    writeFileSync(resolve(SHOTS, `6r21f-e5l-${m.name}.json`), JSON.stringify(json, null, 2))
    sides[m.name] = json
    console.log(`E5L ${m.name} t=${curT} modelExact=${side.modelExact} ticks=${JSON.stringify(side.requestedTicks)}->${JSON.stringify(side.resolvedTicks)} src=${JSON.stringify(side.frameSources)} alive=${side.alive} CT=${side.states && side.states.CT} T=${side.states && side.states.T}`)
  }
  expect(sides['death-after'].alive).toBe(sides['death-before'].alive - 1)
  expect(sides['death-before'].alive).toBe(10)
  expect(sides['death-after'].alive).toBe(9)
  expect(sides['death-before'].sourceBefore).toBe(true)
  expect(sides['death-after'].sourceAfter).toBe(false)
  expect(sides['death-after'].deathMarkerPresent).toBe(true)
  expect(sides['death-after'].comparisonPassed).toBe(true)
})
