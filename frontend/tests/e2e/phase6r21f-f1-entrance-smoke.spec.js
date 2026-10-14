// 6R21F-F1 入口回归 smoke：确认 base.css 增量改动未破坏地图引擎页与入口路由。
// 运行：$env:PLAYWRIGHT_BASE_URL='http://localhost:5173'; npx playwright test -c playwright.headless.config.js phase6r21f-f1-entrance-smoke.spec.js
import { test, expect } from '@playwright/test'

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173'

test('入口路由可挂载且无页面错误', async ({ page }) => {
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  for (const r of ['/login', '/share', '/coach', '/profile', '/upload']) {
    await page.goto(`${BASE}${r}`)
    await page.waitForTimeout(400)
    expect(errors, `${r} 无未捕获错误`).toHaveLength(0)
  }
})

test('实验室地图引擎画布可挂载（真实渲染未破坏）', async ({ page }) => {
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto(`${BASE}/map-control-lab`)
  await page.waitForTimeout(1800)
  const canvas = await page.locator('[data-testid="mclab-canvas"]').count()
  // 页面必须挂载画布且无未捕获错误（引擎真值逻辑未被破坏）
  expect(canvas).toBeGreaterThanOrEqual(1)
  expect(errors).toHaveLength(0)
})
