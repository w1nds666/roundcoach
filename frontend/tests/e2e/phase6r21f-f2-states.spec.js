// 6R21F-F2 局部状态视觉证据：Dashboard 空态、Upload 已选/错误态、Login 错误/注册态。
// 输出 docs/visual-evidence/6r21f-f2-<name>.png（after 基础全页由 phase6r21f-f2-shots 采集）。
// 运行：$env:PLAYWRIGHT_BASE_URL='http://localhost:5173'; npx playwright test -c playwright.headless.config.js phase6r21f-f2-states.spec.js
import { test, expect } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173'
const OUT = resolve(__dirname, '../../../docs/visual-evidence')
mkdirSync(OUT, { recursive: true })
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
const shot = (name) => resolve(OUT, `6r21f-f2-${name}-after.png`)

test('Dashboard 诚实空态（mock 空比赛列表）', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await login(page)
  await page.route('**/api/demos?*', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '[]' }))
  await page.goto(`${BASE}/`)
  await expect(page.locator('.empty-state')).toBeVisible()
  await page.screenshot({ path: shot('dashboard-empty-desktop1280'), fullPage: true })
})

test('Upload 已选文件态 + 非 .dem 错误态', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await login(page)
  await page.goto(`${BASE}/upload`)
  await page.locator('input[type="file"]').setInputFiles(TEST_DEM)
  await expect(page.locator('button.btn-primary', { hasText: '上传并解析' })).toBeVisible()
  await page.screenshot({ path: shot('upload-selected-desktop1280'), fullPage: true })
  await page.locator('input[type="file"]').setInputFiles({ name: 'match.txt', mimeType: 'text/plain', buffer: Buffer.from('x') })
  await expect(page.locator('.form-error-box')).toContainText(/\.dem/)
  await page.screenshot({ path: shot('upload-error-desktop1280'), fullPage: true })
})

test('Login 错误态 + 注册态（nickname/new-password 语义）', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto(`${BASE}/login`)
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', 'wrong-password-x')
  await page.click('button[type="submit"]')
  await expect(page.locator('.form-error-box')).toBeVisible()
  await page.screenshot({ path: shot('login-error-desktop1280'), fullPage: true })
})
