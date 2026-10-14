// 6R21F-F4：Coach / Profile / Share 全站装修专项门禁。
// 目标：证据优先、身份边界清楚、分享可信，并在 1280/900/390/360 无横滚。
import { test, expect } from '@playwright/test'
import { login, ACCOUNT, setTier, seedIdentity, demoPlayerSteam, coachWarm } from './_helpers6r15.js'

async function expectNoHorizontalScroll(page, label) {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement
    return root.scrollWidth - root.clientWidth
  })
  expect(overflow, `${label} 横向溢出 ${overflow}px`).toBeLessThanOrEqual(1)
}

async function prepareMax(page) {
  setTier(ACCOUNT.email, 'max', 7)
  seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, 19))
  await login(page, ACCOUNT.email, ACCOUNT.password)
}

test('Coach：首屏形成「结论 → 证据 → 趋势」工作台，且诚实披露样本', async ({ page }) => {
  await prepareMax(page)
  await coachWarm(page, 10)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/coach')
  await page.waitForSelector('.coach-command', { timeout: 20000 })

  await expect(page.locator('.coach-command .summary-text')).toBeVisible()
  await expect(page.locator('.coach-command .cov-cell')).toHaveCount(3)
  await expect(page.locator('.coach-primary .coach-top3')).toBeVisible()
  await expect(page.locator('.coach-analysis-rail .issue-ledger')).toBeVisible()
  await expect(page.locator('.coach-analysis-rail .trend-list')).toBeVisible()
  await expect(page.locator('.coach-disclosure')).toContainText(/样本|证据|置信/)
  await expectNoHorizontalScroll(page, 'coach@1280')
})

test('Profile：身份、权益、Steam、团队 roster 与隐私边界同屏可审计', async ({ page }) => {
  await prepareMax(page)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/profile')
  await page.waitForSelector('.identity-command', { timeout: 20000 })

  const identity = page.locator('.identity-command')
  await expect(identity).toContainText(ACCOUNT.email)
  await expect(identity).toContainText(/高阶版|团队版/)
  await expect(page.locator('.profile-workspace')).toBeVisible()
  await expect(page.locator('.profile-workspace')).toContainText('Steam 绑定')
  await expect(page.locator('.profile-workspace')).toContainText('团队工作区')
  await expect(page.locator('.privacy')).toContainText(/不公开真实 SteamID|分享页不包含任何 SteamID/)
  await expectNoHorizontalScroll(page, 'profile@1280')
})

test('Share：匿名只读、到期时间、评分口径和隐私范围均可见', async ({ page }) => {
  await prepareMax(page)
  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const response = await page.request.post('/api/demos/19/share', {
    headers: { Authorization: `Bearer ${token}` },
    data: { expires_in_hours: 24 },
  })
  expect(response.ok()).toBeTruthy()
  const shareToken = (await response.json()).token

  await page.context().clearCookies()
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/share#${shareToken}`)
  await page.waitForSelector('.share-trustbar', { timeout: 20000 })
  await expect(page.locator('.share-trustbar')).toContainText('匿名只读')
  await expect(page.locator('.share-trustbar')).toContainText('自动失效')
  await expect(page.locator('.rating-note')).toContainText('不等于 HLTV')
  await expect(page.locator('.privacy')).toContainText(/SteamID|原始文件|下载入口/)
  await expectNoHorizontalScroll(page, 'share@1280')
})

test('F4 响应式：Coach / Profile / Share 在 900/390/360 无横滚且操作可触达', async ({ page }) => {
  await prepareMax(page)
  await coachWarm(page, 10)
  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const response = await page.request.post('/api/demos/19/share', {
    headers: { Authorization: `Bearer ${token}` },
    data: { expires_in_hours: 24 },
  })
  const shareToken = (await response.json()).token

  for (const width of [900, 390, 360]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/coach')
    await page.waitForSelector('.coach-command', { timeout: 20000 })
    await expectNoHorizontalScroll(page, `coach@${width}`)

    await page.goto('/profile')
    await page.waitForSelector('.identity-command', { timeout: 20000 })
    await expectNoHorizontalScroll(page, `profile@${width}`)

    await page.goto(`/share#${shareToken}`)
    await page.waitForSelector('.share-trustbar', { timeout: 20000 })
    await expectNoHorizontalScroll(page, `share@${width}`)

    if (width <= 390) {
      // Share 的只读操作是链接而不是按钮；两者都必须具备移动端可触达高度。
      const targets = await page.locator('button, a[href]').evaluateAll((nodes) => nodes
        .filter((node) => {
          const box = node.getBoundingClientRect()
          return box.width > 0 && box.height > 0
        })
        .map((node) => Math.round(node.getBoundingClientRect().height)))
      expect(targets.length).toBeGreaterThan(0)
      expect(Math.min(...targets)).toBeGreaterThanOrEqual(38)
    }
  }
})
