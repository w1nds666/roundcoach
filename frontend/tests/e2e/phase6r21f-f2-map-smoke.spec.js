// 6R21F-F2 三地图轻量 smoke：壳层改动后确保 /demo/19|21|22 的报告页仍正常加载、无横滚、无报错（不深入 Report 装修）。
// 运行：$env:PLAYWRIGHT_BASE_URL='http://localhost:5173'; npx playwright test -c playwright.headless.config.js phase6r21f-f2-map-smoke.spec.js
import { test, expect } from '@playwright/test'

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DEMOS = [{ id: 19, map: 'de_mirage' }, { id: 21, map: 'de_dust2' }, { id: 22, map: 'de_inferno' }]

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

test('三地图报告页 light smoke（加载/无横滚/无报错）', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await login(page)
  for (const d of DEMOS) {
    await page.goto(`${BASE}/demo/${d.id}`, { waitUntil: 'networkidle' })
    await page.waitForSelector('.map-name', { timeout: 40000 })
    await expect(page.locator('.map-name').first()).toContainText(d.map)
    // 无横向滚动
    const w = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
    expect(w, `demo ${d.id} 无横滚`).toBe(true)
    // 不应出现整页级错误（报告成功加载）
    const errBox = await page.locator('.form-error-box').count()
    expect(errBox, `demo ${d.id} 无整页错误`).toBe(0)
    console.log(`F2 map smoke demo ${d.id} ${d.map} ok`)
  }
})
