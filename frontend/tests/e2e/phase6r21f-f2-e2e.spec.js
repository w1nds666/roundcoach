// 6R21F-F2 功能流向 E2E（绿门）：登录/登出、Dashboard 主路径 CTA、上传入口与错误恢复不回归。
// 运行：$env:PLAYWRIGHT_BASE_URL='http://localhost:5173'; npx playwright test -c playwright.headless.config.js phase6r21f-f2-e2e.spec.js
import { test, expect } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const TEST_DEM = resolve(__dirname, '../../../backend/tests/demos/test_demo.dem')

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

test('登录 → Dashboard 可见真实比赛 → 「继续复盘」进入报告（不回归）', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await login(page)
  await page.goto(`${BASE}/`)
  await expect(page.locator('.page-title')).toHaveText('我的复盘')
  // 真实比赛已显示（mvr-acceptance 拥有已解析 demo），状态徽标为"已解析"
  await expect(page.locator('.deck-row').first()).toBeVisible()
  await expect(page.locator('.deck-row .pill').first()).toContainText(/已解析|解析中|已上传|失败/)
  // 主路径：继续复盘 → 报告页加载
  await page.locator('a.continue-review').first().click()
  await page.waitForSelector('.map-name', { timeout: 30000 })
  await expect(page.locator('.map-name').first()).toBeVisible()
})

test('Dashboard「上传 Demo」CTA → Upload 页（入口不回归）', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await login(page)
  await page.goto(`${BASE}/`)
  await page.locator('.page-head a.btn-primary', { hasText: /上传 Demo/ }).first().click()
  await expect(page).toHaveURL(/\/upload/)
  await expect(page.locator('.dropzone')).toBeVisible()
})

test('Upload 校验与错误恢复：非 .dem 提示错误；合法 .dem 出现上传按钮', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await login(page)
  await page.goto(`${BASE}/upload`)
  // 非法扩展名 → 可见错误
  await page.locator('input[type="file"]').setInputFiles({ name: 'match.txt', mimeType: 'text/plain', buffer: Buffer.from('nope') })
  await expect(page.locator('.form-error-box')).toContainText(/\.dem/)
  // 合法 .dem（前端仅校验扩展名/大小，后端为最终权威）→ 出现上传按钮
  await page.locator('input[type="file"]').setInputFiles(TEST_DEM)
  await expect(page.locator('button.btn-primary', { hasText: '上传并解析' })).toBeVisible()
  // 取消 → 回到闲置态
  await page.locator('button.btn', { hasText: '取消' }).first().click()
  await expect(page.locator('.dropzone')).toContainText(/点击选择/)
})

test('Login 错误恢复：错误密码提示；登出回到登录页', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto(`${BASE}/login`)
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', 'wrong-password-xxx')
  await page.click('button[type="submit"]')
  await expect(page.locator('.form-error-box')).toBeVisible()
  await expect(page.locator('.form-error-box')).toContainText(/邮箱或密码错误/)
  // 正确登录 → 登出 → 回到登录页
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })
  await page.locator('button.logout, .logout').first().click()
  await expect(page).toHaveURL(/\/login/)
})
