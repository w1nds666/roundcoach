// 6R14 身份/权益边界截图矩阵（补充）：绑定/未绑定/冲突/自动「这是你」/未识别/团队 roster-gate，
// 覆盖桌面 1440x900 与移动端 390px、360px。仅供 DeepSeek 原生 Native Vision 审阅（不改门禁）。
import { test, expect } from '@playwright/test'
import { seedIdentity, clearIdentity, demoPlayerSteam, setTier } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots/phase6r14'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DEMO = 23
const FOREIGN = '76561198000010000'   // 合法格式但不在 demo23 roster

async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

async function shoot(page, name, { fullPage = false } = {}) {
  await page.screenshot({ path: `${SHOTS}/${name}`, fullPage })
}

// 在同一复用 page 上按三档视口截图
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

test('未绑定：Profile Steam 绑定卡（未绑定态）', async ({ page }) => {
  clearIdentity(ACCOUNT.email)
  await login(page)
  await page.goto('/profile')
  await page.waitForSelector('.steam.card', { timeout: 10000 })
  await expect(page.locator('.steam.card .pill-muted')).toHaveText('未绑定', { timeout: 10000 })
  await capture(page, 'profile-unbound', { fullPage: true })
})

test('已绑定：Profile Steam 绑定卡（掩码 id）', async ({ page }) => {
  seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, DEMO))
  await login(page)
  await page.goto('/profile')
  await page.waitForSelector('.steam.card', { timeout: 10000 })
  await expect(page.locator('.steam.card .pill-ok')).toHaveText('已绑定', { timeout: 10000 })
  await expect(page.locator('.steam.card .steam_id64_masked, .steam.card .m-val.num').first()).toContainText('****')
  await capture(page, 'profile-bound', { fullPage: true })
})

test('身份冲突：Profile Steam 绑定卡（Steam 已被其他账号绑定）', async ({ page }) => {
  clearIdentity(ACCOUNT.email)
  await login(page)
  // 唤醒 Steam 回调落地的冲突提示（?steam=error&reason=STEAM_IDENTITY_CONFLICT），与真实 OpenID 冲突一致
  await page.goto('/profile?steam=error&reason=STEAM_IDENTITY_CONFLICT')
  await page.waitForSelector('.steam.card', { timeout: 10000 })
  await expect(page.locator('.steam.card .notice-err')).toContainText('该 Steam 账号已绑定其他账号，无法重复绑定')
  await capture(page, 'profile-conflict', { fullPage: true })
})

test('自动「这是你」：demo 已识别（banner 绿 + me-badge）', async ({ page }) => {
  seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, DEMO))
  await login(page)
  await page.goto(`/demo/${DEMO}`)
  await page.waitForSelector('.identity-banner', { timeout: 15000 })
  const banner = await page.locator('.identity-banner').innerText()
  expect(banner).toContain('已自动识别到你')
  await page.waitForSelector('.me-badge', { timeout: 10000 })
  await capture(page, 'demo-identified')
})

test('未识别：绑定不在 roster → 「这场没有识别到你」', async ({ page }) => {
  seedIdentity(ACCOUNT.email, FOREIGN)
  await login(page)
  await page.goto(`/demo/${DEMO}`)
  await page.waitForSelector('.identity-banner', { timeout: 15000 })
  const banner = await page.locator('.identity-banner').innerText()
  expect(banner).toContain('没有识别到你')
  await capture(page, 'demo-not-identified')
})

test('团队 roster 与门禁展示（Profile 团队工作区 + demo 团队 gate）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, DEMO))
  await login(page)
  await page.goto('/profile')
  await page.waitForSelector('.team-workspace, .team', { timeout: 10000, state: 'attached' }).catch(() => {})
  await capture(page, 'profile-team', { fullPage: true })
  // demo 团队 gate（只读展示）
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/demo/${DEMO}`)
  await page.click('button.tab:has-text("团队复盘")').catch(() => {})
  await page.waitForTimeout(800)
  await shoot(page, 'team-gate-desktop.png')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(250)
  await shoot(page, 'team-gate-390.png')
  await page.setViewportSize({ width: 360, height: 780 })
  await page.waitForTimeout(250)
  await shoot(page, 'team-gate-360.png')
})
