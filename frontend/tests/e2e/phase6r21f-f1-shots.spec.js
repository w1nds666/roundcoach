// 6R21F-F1 视觉证据截图采集（before/after）。
// 独立脚本：对 Vite dev（http://localhost:5173，即当前源代码）截图，不走 nginx 生产 build。
// 通过 TEST_TAG 区分 before/after：TEST_TAG=before|after，输出到 docs/visual-evidence/6r21f-f1-<tag>-<file>。
// 运行示例：
//   $env:TEST_TAG='before'; npx playwright test -c playwright.headless.config.js phase6r21f-f1-shots.spec.js
import { test, expect } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const TAG = process.env.TEST_TAG || 'before'
const BASE = process.env.SHOT_BASE || 'http://localhost:5173'
const OUT = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(OUT, { recursive: true })
const S = (name) => resolve(OUT, `6r21f-f1-${TAG}-${name}.png`)

test.setTimeout(180000)

const VIEWS = [
  { label: 'desktop1280', width: 1280, height: 800, full: true },
  { label: 'desktop1081', width: 1081, height: 800, full: true },
  { label: 'desktop900', width: 900, height: 800, full: true },
  { label: 'mobile390', width: 390, height: 844, full: true },
  { label: 'mobile360', width: 360, height: 740, full: true },
]

test(`F1 截图 ${TAG} 五视口`, async ({ page }) => {
  for (const v of VIEWS) {
    await page.setViewportSize({ width: v.width, height: v.height })
    await page.goto(`${BASE}/welcome`, { waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="landing-replay-stage"]', { timeout: 30000 })
    await page.waitForTimeout(900)
    // 首屏（视口）截图
    const hero = await page.locator('main').first()
    await page.screenshot({ path: S(`${v.label}-hero`) })
    if (v.full) {
      // 全长截图（分段滚动避免设备内存/超长图；先整页）
      await page.screenshot({ path: S(`${v.label}-full`), fullPage: true })
    }
    // 无横滚检查
    const w = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
    console.log(`F1 ${TAG} ${v.label} scrollW=${w.scroll} clientW=${w.client} ok=${w.scroll <= w.client + 1}`)
    expect(w.scroll, `${v.label} 无横滚`).toBeLessThanOrEqual(w.client + 1)
  }
})
