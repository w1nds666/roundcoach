// 6R15 长期教练截图矩阵（补充）：Free 锁定 / Pro 10 场 / Max 30 场 + 对照，桌面 1440x900 与 390px、360px。
// 仅供 DeepSeek 原生 Native Vision 审阅（不改门禁）。
import { test, expect } from '@playwright/test'
import { setTier, seedIdentity, clearIdentity, login, coachWarm, DEMO19, ACCOUNT } from './_helpers6r15.js'

const SHOTS = 'tests/e2e/screenshots/phase6r15'
const COACH_STEAM = DEMO19.ROUND3_SMOKE

async function shoot(page, name, { fullPage = false } = {}) {
  await page.screenshot({ path: `${SHOTS}/${name}`, fullPage })
}

async function capture(page, prefix, opts) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.waitForTimeout(250)
  await shoot(page, `${prefix}-desktop.png`, opts)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(250)
  await shoot(page, `${prefix}-390.png`, opts)
  await page.setViewportSize({ width: 360, height: 780 })
  await page.waitForTimeout(250)
  await shoot(page, `${prefix}-360.png`, opts)
}

test('Free：长期教练锁定预览', async ({ page }) => {
  setTier(ACCOUNT.email, 'free')
  clearIdentity(ACCOUNT.email)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await page.goto('/coach')
  await page.waitForSelector('.coach', { timeout: 10000 })
  await expect(page.locator('.locked-box .lock-title')).toContainText('长期教练属于进阶版功能', { timeout: 10000 })
  await capture(page, 'coach-free-locked', { fullPage: true })
})

test('Pro：长期教练最近 10 场', async ({ page }) => {
  seedIdentity(ACCOUNT.email, COACH_STEAM)
  setTier(ACCOUNT.email, 'pro', 30)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await coachWarm(page, 10)
  await page.goto('/coach')
  await page.waitForSelector('.coach', { timeout: 10000 })
  await expect(page.locator('.cov-cell', { hasText: '最近 10 场' }).locator('.cov-num')).toContainText(/\d+\/10/, { timeout: 15000 })
  await capture(page, 'coach-pro-10', { fullPage: true })
})

test('Max：长期教练最近 30 场 + 10/30 对照', async ({ page }) => {
  seedIdentity(ACCOUNT.email, COACH_STEAM)
  setTier(ACCOUNT.email, 'max', 30)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await coachWarm(page, 30)
  await page.goto('/coach')
  await page.waitForSelector('.coach', { timeout: 10000 })
  await expect(page.locator('.cov-cell', { hasText: '最近 30 场' }).locator('.cov-num')).toContainText(/\d+\/30/, { timeout: 15000 })
  await capture(page, 'coach-max-30', { fullPage: true })
})
