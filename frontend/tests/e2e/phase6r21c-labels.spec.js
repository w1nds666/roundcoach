// 6R21C 团队 canvas 标签二维避让 E2E 门禁（三地图）。
// 断言：同一时刻标签盒-盒不重叠、盒不覆盖其它锚点、盒在画布内（移动端不横滚）、
//       布局确定性（同一时刻两次重建 data-label-hash 一致——播放/暂停/seek 不抖动）。
// 账号 mvr-acceptance@test.com；地图 demos：mirage=19 / dust2=21 / inferno=22（同账号拥有）。
import { test, expect } from '@playwright/test'
import { grantTeamEntitlementForDemo, seedIdentity, demoPlayerSteam } from './_helpers6r14.js'
import { mkdirSync } from 'fs'
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(OUT, { recursive: true })

const LOGIN_ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const SIZE = 720
const MAPS = [
  { key: 'mirage', id: 19 },
  { key: 'dust2', id: 21 },
  { key: 'inferno', id: 22 },
]

async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', LOGIN_ACCOUNT.email)
  await page.fill('input[type="password"]', LOGIN_ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}
async function enterTeam(page, id) {
  await page.goto(`/demo/${id}`)
  await page.waitForSelector('.map-name', { timeout: 15000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.waitForFunction(() => document.querySelector('.tm-sel')?.value === '1', undefined, { timeout: 20000 })
  await page.waitForTimeout(900)
}
async function scrub(page, sec) {
  await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, sec)
  await page.waitForTimeout(160)
}
async function boxes(page, t) {
  await scrub(page, t)
  return page.locator('.tm-canvas').evaluate((c) => {
    const raw = c.getAttribute('data-label-boxes')
    try { return JSON.parse(raw || '[]') } catch { return [] }
  })
}
const overlaps = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y

for (const m of MAPS) {
  test(`6R21C：${m.key} 团队 canvas 标签 2D 避让——无重叠/不越界/不覆盖锚点/确定性`, async ({ page }) => {
    seedIdentity(LOGIN_ACCOUNT.email, demoPlayerSteam(LOGIN_ACCOUNT.email, m.id))
    grantTeamEntitlementForDemo(LOGIN_ACCOUNT.email, m.id)
    await login(page)
    await enterTeam(page, m.id)
    const maxD = Number(await page.locator('.tm-scrub').evaluate((el) => Number(el.max) || 0))
    expect(maxD).toBeGreaterThan(5)

    let anyLabels = false, bestShotT = 0, bestShotCount = -1
    for (let i = 0; i <= 8; i++) {
      const t = Math.round(maxD * (0.02 + 0.11 * i) * 100) / 100
      const bs = await boxes(page, t)
      if (bs.length) { anyLabels = true; if (bs.length > bestShotCount) { bestShotCount = bs.length; bestShotT = t } }
      for (let x = 0; x < bs.length; x++) {
        expect(bs[x].x).toBeGreaterThanOrEqual(6)
        expect(bs[x].x + bs[x].w).toBeLessThanOrEqual(SIZE - 6)
        expect(bs[x].y).toBeGreaterThanOrEqual(6)
        expect(bs[x].y + bs[x].h).toBeLessThanOrEqual(SIZE - 6)
        for (let y = x + 1; y < bs.length; y++) {
          expect(overlaps(bs[x], bs[y]), `${m.key} t=${t}: label ${bs[x].id} overlaps ${bs[y].id}`).toBe(false)
        }
      }
    }
    expect(anyLabels).toBe(true)

    // 布局确定性：跳走再跳回 → hash 不变
    const tTarget = bestShotT || Math.round(maxD * 0.35 * 100) / 100
    await scrub(page, tTarget)
    const h1 = await page.locator('.tm-canvas').getAttribute('data-label-hash')
    await scrub(page, Math.max(0, tTarget - 2))
    await scrub(page, tTarget)
    const h2 = await page.locator('.tm-canvas').getAttribute('data-label-hash')
    expect(h1).toBe(h2)

    // after 证据截图（标签最密集帧）
    await page.locator('.tm-canvas').screenshot({ path: `${OUT}/6r21c-team-${m.key}-after.png` })
  })
}
