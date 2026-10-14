// 6R21 首页视觉证据截图（不进入默认 headless E2E 断言收集；仅显式运行时生成证据图）。
// 运行：npx playwright test -c playwright.headless.config.js phase6r21-shots.spec.js
import { test } from '@playwright/test'
import { mkdirSync } from 'fs'
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(OUT, { recursive: true })

async function seekTo(page, ratio) {
  const tl = page.locator('.timeline')
  await tl.scrollIntoViewIfNeeded()
  const box = await tl.boundingBox()
  await page.mouse.click(box.x + box.width * ratio, box.y + box.height / 2)
  await page.waitForTimeout(260)
}

test('6R21 desktop stage + full page evidence', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/welcome')
  await page.getByTestId('landing-replay-stage').waitFor({ state: 'visible' })
  await seekTo(page, 0.4)
  await page.locator('#top').screenshot({ path: `${OUT}/6r21-landing-desktop.png` })
  await page.locator('.hero').screenshot({ path: `${OUT}/6r21-landing-hero.png` })
  await page.locator('[data-testid="landing-replay-stage"]').screenshot({ path: `${OUT}/6r21-landing-stage.png` })
  // 晚期残局帧
  await seekTo(page, 0.9)
  await page.locator('[data-testid="landing-replay-stage"]').screenshot({ path: `${OUT}/6r21-landing-stage-late.png` })
})

test('6R21 mobile 390 evidence', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/welcome')
  await page.getByTestId('landing-replay-stage').waitFor({ state: 'visible' })
  await seekTo(page, 0.4)
  await page.screenshot({ path: `${OUT}/6r21-landing-mobile.png`, fullPage: true })
})

test('6R21 360 no overflow evidence', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  await page.goto('/welcome')
  await page.getByTestId('landing-replay-stage').waitFor({ state: 'visible' })
  await seekTo(page, 0.4)
  await page.screenshot({ path: `${OUT}/6r21-landing-360.png`, fullPage: true })
})
