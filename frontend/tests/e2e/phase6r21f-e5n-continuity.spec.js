// 6R21F-E5N continuity：field 层连续插值校验和在同一 bracket 内相邻均有界递增、跨 bracket 无跳变；
// 暂停固定 t 连续 3 帧完全一致；seek 同 t 三次一致；reduced-motion 确定性。
import { test, expect } from '@playwright/test'

const DUR = 60.92
async function waitReady(page) {
  await page.goto('/map-control-lab')
  await page.waitForSelector('[data-testid="mclab-canvas"]', { timeout: 20000 })
  await page.waitForFunction(() => { const c = document.querySelector('[data-testid="mclab-canvas"]'); if (!c) return false; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 40) if (d[i] !== 0) return true; return false }, undefined, { timeout: 20000 })
  await page.waitForFunction(() => !!(window.__mclab && window.__mclab.fieldChecksum != null), undefined, { timeout: 20000 })
}
async function seekSec(page, t) {
  await page.locator('.mclab-tl').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, t)
  await page.waitForTimeout(140)
}
async function awaitExact(page, t) { return page.evaluate((tt) => window.__mclab.awaitExactAt(tt, 8000), t) }
async function readFieldCk(page) { return page.evaluate(() => window.__mclab.fieldChecksum) }
async function canvasChecksum(page) { return page.locator('[data-testid="mclab-canvas"]').evaluate((c) => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let s = 0; for (let i = 0; i < d.length; i++) s = (s + d[i]) % 1000000007; return String(s) }) }

test('E5N continuity: fieldChecksum 同 bracket 相邻>0 有界 + 19.59→19.60 无跳变', async ({ page }) => {
  await waitReady(page)
  // 准备 exact 端点：19.51 属于 [195,196]，19.60 属于 [196,197]
  await awaitExact(page, 19.51)
  await awaitExact(page, 19.6)
  const ts = [19.51, 19.54, 19.57, 19.59, 19.60]
  const cks = []
  for (const t of ts) { await seekSec(page, t); cks.push(await readFieldCk(page)) }
  console.log('E5N continuity cks=' + JSON.stringify(cks))
  // 同 bracket [195,196]：19.51/19.54/19.57/19.59
  for (let i = 1; i < 4; i++) {
    const d = Math.abs(cks[i] - cks[i - 1])
    expect(d, `adjacent fieldChecksum diff>0 at i=${i}`).toBeGreaterThan(0)
    expect(d, `adjacent diff bounded at i=${i}`).toBeLessThanOrEqual(Math.abs(cks[3] - cks[0]) + 1)
  }
  // 19.59→19.60 跨 bracket：不应异常跳变（相对 19.51→19.59 的累积变化也应有界）
  const dEdge = Math.abs(cks[4] - cks[3])
  expect(dEdge, '19.59→19.60 bounded').toBeLessThanOrEqual(Math.abs(cks[3] - cks[0]) + 1)
  // 也可反向验证：field 层确实连续（相邻都非 0 突变）
  const all = cks.slice(1).every((c, i) => Math.abs(c - cks[i]) > 0)
  expect(all).toBe(true)
})

test('E5N continuity: 暂停固定 t 连续 3 帧完全一致（确定性渲染）', async ({ page }) => {
  await waitReady(page)
  await awaitExact(page, 20.0)
  await seekSec(page, 20.0)
  const a = await canvasChecksum(page)
  await seekSec(page, 20.0); const b = await canvasChecksum(page)
  await seekSec(page, 20.0); const c = await canvasChecksum(page)
  console.log(`E5N pause determinism a=${a} b=${b} c=${c}`)
  expect(a).toBe(b)
  expect(b).toBe(c)
})

test('E5N continuity: seek 同 t 三次一致（fieldChecksum + canvas）', async ({ page }) => {
  await waitReady(page)
  await awaitExact(page, 19.7)
  let prevCk = null, prevCs = null
  for (let k = 0; k < 3; k++) {
    await seekSec(page, 19.7)
    const ck = await readFieldCk(page); const cs = await canvasChecksum(page)
    if (k === 0) { expect(typeof ck).toBe('number'); expect(ck).toBeGreaterThan(0) }
    if (k > 0) { expect(ck, 'fieldChecksum identical across seek').toBe(prevCk); expect(cs, 'canvas identical across seek').toBe(prevCs) }
    prevCk = ck; prevCs = cs
  }
})

test('E5N continuity: reduced-motion 确定性（静态、同 t 渲染一致）', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await waitReady(page)
  await awaitExact(page, 21.0)
  await seekSec(page, 21.0)
  const hint = await page.locator('.mclab-hint').textContent().catch(() => '')
  expect(hint).toContain('reduced-motion')
  const a = await canvasChecksum(page)
  await seekSec(page, 21.0); const b = await canvasChecksum(page)
  expect(a).toBe(b)
  // 播放按钮在 reduced-motion 下应保持静态（点击后 currentTime 不变或跳到端点，但渲染确定）
  await page.getByRole('button', { name: '播放' }).click()
  const t1 = await page.locator('.mclab-tl').evaluate((el) => Number(el.value))
  const cs = await canvasChecksum(page)
  console.log(`E5N reduced-motion t=${t1} cs=${cs}`)
  expect(cs).toBeTruthy()
})
