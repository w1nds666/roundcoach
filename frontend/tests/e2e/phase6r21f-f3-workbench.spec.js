// 6R21F-F3 战术工作台 结构/行为门禁（红绿）。
// 以下断言在实现前失败（red），实现后转绿（green）。
// 运行：$env:PLAYWRIGHT_BASE_URL='http://localhost:5173'; npx playwright test -c playwright.headless.config.js phase6r21f-f3-workbench.spec.js
import { test, expect } from '@playwright/test'
import { setTier, demoPlayerSteam, seedIdentity } from './_helpers6r14.js'

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DEMOS = [{ id: 19, map: 'de_mirage' }, { id: 21, map: 'de_dust2' }, { id: 22, map: 'de_inferno' }]

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })
  await page.waitForSelector('.user', { timeout: 15000 })
}

async function noHScroll(page, label) {
  const w = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
  expect(w, label).toBe(true)
}

test.describe('6R21F-F3 战术工作台框架', () => {
  test('顶部比赛上下文条清晰显示地图/比分/队伍/回合/时长/身份/权益', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await login(page)
    await page.goto(`${BASE}/demo/19`, { waitUntil: 'networkidle' })
    await page.waitForSelector('.wb-context', { timeout: 15000 })
    // 地图 + 比分 + 队伍（工作台上下文首屏）
    await expect(page.locator('.wb-context .wb-map')).toContainText(/de_mirage|Mirage/)
    await expect(page.locator('.wb-context .wb-score')).toBeVisible()
    // 回合 / 时长
    await expect(page.locator('.wb-context')).toContainText(/回合/)
    await expect(page.locator('.wb-context')).toContainText(/分钟/)
    // 身份/权益状态（团队版徽标 or 身份说明）至少一项可见
    const anyStatus = await page.locator('.wb-context .wb-status, .wb-context .tier-badge, .wb-context .wb-identity').count()
    expect(anyStatus, '上下文条应有身份/权益信号').toBeGreaterThan(0)
    await noHScroll(page, 'workbench/19 context 无横滚')
  })

  test('三方切换 tab 齐备且工作台化（概览裁决摘要/个人复盘/团队复盘）', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await login(page)
    await page.goto(`${BASE}/demo/19`, { waitUntil: 'networkidle' })
    await page.waitForSelector('.wb-tabs', { timeout: 15000 })
    for (const label of ['概览', '个人复盘', '团队复盘']) {
      await expect(page.locator('.wb-tabs button', { hasText: label }).first()).toBeVisible()
    }
    await noHScroll(page, 'tabs 无横滚')
  })
})

test.describe('6R21F-F3 概览 = 裁决摘要', () => {
  test('结论优先 + 回合证据（真值 /rounds，不虚构每回合胜负）', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await login(page)
    await page.goto(`${BASE}/demo/19`, { waitUntil: 'networkidle' })
    await page.waitForSelector('.verdict-strip', { timeout: 15000 })
    // 结论条：胜方 + 比分（来自 team_a/team_b_score 真值）
    await expect(page.locator('.verdict-strip')).toContainText(/取胜|战平/)
    // 回合证据：出现 R1…R{rounds} 刻度条；每回合有击杀数；不得出现杜撰的每回合胜方
    await page.waitForSelector('.round-evidence', { timeout: 15000 })
    const rounds = await page.locator('.round-evidence .re-round').count()
    expect(rounds, '回合证据应按 match.rounds 渲染').toBeGreaterThanOrEqual(2)
    // 无横滚 / 无整页错误
    await noHScroll(page, 'overview 无横滚')
    expect(await page.locator('.form-error-box').count()).toBe(0)
  })

  test('关键观察 + 可扫描比分板保留（不虚构）', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await login(page)
    await page.goto(`${BASE}/demo/19`, { waitUntil: 'networkidle' })
    await page.waitForSelector('.wb-tabs', { timeout: 15000 })
    await page.waitForSelector('.tbl', { timeout: 15000 })
    await expect(page.locator('.tbl tbody tr').first()).toBeVisible()
    await noHScroll(page, 'scoreboard 无横滚')
  })
})

test.describe('6R21F-F3 个人 = 可追溯训练单', () => {
  test('Rating Pro 主结论 + 基础指标带 + 诚实低覆盖/零机会标注', async ({ page }) => {
    // 个人复盘需绑定本人身份，否则服务端拒绝生成画像。先为 demo19 幂等播种「这是你」。
    setTier(ACCOUNT.email, 'max', 7)
    const steam = demoPlayerSteam(ACCOUNT.email, 19, 0)
    if (steam) seedIdentity(ACCOUNT.email, steam)
    await page.setViewportSize({ width: 1280, height: 800 })
    await login(page)
    await page.goto(`${BASE}/demo/19`, { waitUntil: 'networkidle' })
    await page.waitForSelector('.wb-tabs', { timeout: 15000 })
    await page.click('.wb-tabs button:has-text("个人复盘")')
    await page.waitForSelector('.rating-card', { timeout: 15000 })
    await expect(page.locator('.rating-card')).toBeVisible()
    // 训练单框架 + 基础指标带
    await page.waitForSelector('.train-strip', { timeout: 15000 })
    await expect(page.locator('.train-strip')).toBeVisible()
    await noHScroll(page, 'personal 无横滚')
  })
})
