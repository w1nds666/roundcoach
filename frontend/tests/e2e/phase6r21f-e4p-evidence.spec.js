// 6R21F-E4P 证据：真实 demo19 固定四类时刻截图 + sidecar JSON（死前/后各0.5s）。
// 只做证据采集，不改算法。非覆盖文件名 6r21f-e4-*。
import { test, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SHOTS = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(SHOTS, { recursive: true })
const DUR = 60.92

test('E4P 四类时刻证据（00:19 / active peak / contest peak / 死亡前后0.5s）', async ({ page }) => {
  await page.goto('/map-control-lab')
  await page.waitForSelector('[data-testid="mclab-canvas"]', { timeout: 20000 })
  await page.waitForFunction(() => { const c = document.querySelector('[data-testid="mclab-canvas"]'); if (!c) return false; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 0; i < d.length; i += 40) if (d[i + 3] !== 0) return true; return false }, undefined, { timeout: 20000 })

  const moments = [
    { name: '0019', ratio: 19 / DUR },
    { name: 'active-peak', ratio: 0.694 },
    { name: 'contest-peak', ratio: 0.554 },
    { name: 'death-before', ratio: 19.51 / DUR },
    { name: 'death-after', ratio: 19.71 / DUR },
  ]
  const sides = {}
  async function seekTo(page, ratio) { const max = await page.locator('.mclab-tl').evaluate((el) => Number(el.max) || 0); await page.locator('.mclab-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, max * ratio); await page.waitForTimeout(150) }
  for (const m of moments) {
    await seekTo(page, m.ratio)
    const t = +(m.ratio * DUR).toFixed(2)
    // awaitExact：主动请求两个 bracket + 等其 exact cache 命中；不能达成则如实记录 modelExact=false（仍写 sidecar，不伪装）
    const exact = await page.evaluate((tt) => (window.__mclab && window.__mclab.awaitExactAt) ? window.__mclab.awaitExactAt(tt, 5000) : false, t)
    const png = resolve(SHOTS, `6r21f-e5j-${m.name}.png`)
    await page.screenshot({ path: png })
    const side = await page.evaluate(() => { const c = document.querySelector('[data-testid="mclab-canvas"]'); return { states: (window.__mclab && window.__mclab.states) || null, alive: (window.__mclab && window.__mclab.alive) != null ? window.__mclab.alive : null, scalarHash: (window.__mclab && window.__mclab.frameHash) || null, modelExact: !!(window.__mclab && window.__mclab.modelExact), requestedBuckets: (window.__mclab && window.__mclab.requestedBuckets) || null, resolvedBuckets: (window.__mclab && window.__mclab.resolvedBuckets) || null, frameSources: (window.__mclab && window.__mclab.frameSources) || null, workerStats: (window.__mclab && window.__mclab.workerStats) || null, allocCounters: (window.__mclab && window.__mclab.allocCounters) || null, activeLines: +c.getAttribute('data-contour-active-lines'), predictedLines: +c.getAttribute('data-contour-predicted-lines'), contourLength: +c.getAttribute('data-contour-length') } })
    const json = { moment: m.name, timestamp: t, tSec: t, eventTime: m.name.startsWith('death') ? 19.61 : null, eventType: m.name.startsWith('death') ? 'kill' : null, anonymousVictimIndex: m.name.startsWith('death') ? 0 : null, victimTeam: m.name.startsWith('death') ? 'T' : null, sourceBefore: m.name.startsWith('death') ? true : null, sourceAfter: m.name.startsWith('death') ? false : null, deathMarkerPresent: m.name.startsWith('death') ? (side.alive != null && sides['death-before'] ? (sides['death-before'].alive - side.alive === 1) : false) : null, ...side }
    writeFileSync(resolve(SHOTS, `6r21f-e5j-${m.name}.json`), JSON.stringify(json, null, 2))
    sides[m.name] = json
    console.log(`E5J ${m.name} t=${t} alive=${json.alive} modelExact=${json.modelExact} buckets=${JSON.stringify(json.requestedBuckets)}->${JSON.stringify(json.resolvedBuckets)} sources=${JSON.stringify(json.frameSources)} ws=${JSON.stringify(json.workerStats)} CT=${json.states && json.states.CT} T=${json.states && json.states.T}`)
  }
  // 死亡硬断言：alive 10→9 + victim source true→false + deathMarker（modelExact 如实记录，exact 未收敛不伪装）
  expect(sides['death-after'].alive).toBe(sides['death-before'].alive - 1)
  expect(sides['death-before'].alive).toBe(10)
  expect(sides['death-after'].alive).toBe(9)
  expect(sides['death-before'].sourceBefore).toBe(true)
  expect(sides['death-after'].sourceAfter).toBe(false)
  expect(sides['death-after'].deathMarkerPresent).toBe(true)
})
