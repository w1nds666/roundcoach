import { test, expect } from '@playwright/test'

for (const width of [1280, 390, 360]) {
  test(`profile distinguishes unavailable records from empty at ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 })
    await page.addInitScript(() => localStorage.setItem('cs2_user', JSON.stringify({ id: -1, nickname: 'UI fixture', subscription_tier: 'free' })))
    await page.route('**/api/**', route => route.fulfill({ contentType: 'application/json', body: '{}' }))
    let calls = 0, release
    const gate = new Promise(resolve => { release = resolve })
    await page.route('**/api/demos?limit=100', async route => {
      calls++
      if (calls === 1) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"unavailable"}' })
      await gate
      return route.fulfill({ contentType: 'application/json', body: '[]' })
    })
    await page.goto('/profile')
    await expect(page.getByText('比赛记录暂时无法加载，请重试。')).toBeVisible()
    await expect(page.getByText('暂无未删除的比赛记录')).toHaveCount(0)
    await expect(page.locator('.stats .num')).toHaveText(['—', '—', '—', '—'])
    await page.screenshot({ path: testInfo.outputPath('records-unavailable.png'), fullPage: true })
    await page.getByRole('button', { name: '重试加载比赛记录' }).click()
    await expect(page.locator('.match-section')).toContainText('加载中…')
    await expect(page.getByRole('button', { name: '重试加载比赛记录' })).toHaveCount(0)
    release()
    await expect(page.getByText('暂无未删除的比赛记录')).toBeVisible()
    await expect(page.locator('.stats .num')).toHaveText(['0', '0', '0', '0'])
    expect(calls).toBe(2)
    await page.screenshot({ path: testInfo.outputPath('records-empty.png'), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
  test(`profile caps statistics explicitly at ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 })
    await page.addInitScript(() => localStorage.setItem('cs2_user', JSON.stringify({ id: -1, nickname: 'UI fixture', subscription_tier: 'free' })))
    await page.route('**/api/**', route => route.fulfill({ contentType: 'application/json', body: '{}' }))
    const records = Array.from({ length: 100 }, (_, i) => ({ id: i + 1, file_name: `fixture-${i}.dem`, status: ['parsed', 'uploaded', 'parsing', 'failed'][i % 4] }))
    await page.route('**/api/demos?limit=100', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(records) }))
    await page.goto('/profile')
    await expect(page.locator('.stats .num')).toHaveText(['100', '25', '50', '25'])
    await expect(page.locator('.stats .lbl').first()).toHaveText('已加载')
    await expect(page.locator('.records-scope')).toContainText('最近最多 100 场未删除的比赛；不是账户历史总量')
    await expect(page.locator('.match-section .list .row')).toHaveCount(20)
    await expect(page.getByRole('link', { name: '查看全部记录 →' })).toHaveAttribute('href', '/')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('records-capped.png'), fullPage: true })
  })
}
