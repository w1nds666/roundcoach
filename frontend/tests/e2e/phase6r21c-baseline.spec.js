// 6R21C evidence capture: 重做后的首页 first-screen / hero / stage（after）+ 同尺寸组合图素材。
import { test } from '@playwright/test'
import { mkdirSync } from 'fs'
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(OUT, { recursive: true })

// 只把时间轴拖到峰值时刻附近（默认 seek 已到叙事峰值；此处显式对时，保证与"before"同构图）
async function seekTo(page, ratio) {
  const tl = page.locator('.timeline')
  await tl.scrollIntoViewIfNeeded()
  const box = await tl.boundingBox()
  await page.mouse.click(box.x + box.width * ratio, box.y + box.height / 2)
  await page.waitForTimeout(300)
}

test('6R21C capture reworked first-screen + hero + stage (after)', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/welcome')
  await page.getByTestId('landing-replay-stage').waitFor({ state: 'visible' })
  await seekTo(page, 0.405)
  await page.screenshot({ path: `${OUT}/6r21c-after-viewport.png` })
  await page.locator('main#top').screenshot({ path: `${OUT}/6r21c-after-firstscreen.png` })
  await page.locator('.hero').screenshot({ path: `${OUT}/6r21c-after-hero.png` })
  await page.locator('[data-testid="landing-replay-stage"]').screenshot({ path: `${OUT}/6r21c-after-stage.png` })
})

test('6R21C capture mobile 390 hero + stage (after)', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/welcome')
  await page.getByTestId('landing-replay-stage').waitFor({ state: 'visible' })
  await seekTo(page, 0.405)
  await page.screenshot({ path: `${OUT}/6r21c-after-mobile-390.png`, fullPage: true })
})
