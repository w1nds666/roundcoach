import { test, expect } from '@playwright/test'

async function prepare(page) {
  // Synthetic UI-only session; all API requests are intercepted, never authorized server-side.
  await page.addInitScript(() => localStorage.setItem('cs2_user', JSON.stringify({ id: -1, nickname: 'UI fixture', subscription_tier: 'free' })))
  await page.route('**/api/**', route => route.fulfill({ contentType: 'application/json', body: '{}' }))
}
for (const width of [1280, 390, 360]) {
  test(`upload reaches parsed at ${width}`, async ({ page }) => {
    await prepare(page)
    await page.setViewportSize({ width, height: 844 })
    await page.route('**/api/demos/upload', route => route.fulfill({ contentType: 'application/json', body: '{"demo_id":19}' }))
    let polls = 0
    let status = 'uploaded'
    await page.route('**/api/demos/19/status', route => { polls++; return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ status }) }) })
    await page.goto('/upload')
    await page.locator('input[type=file]').setInputFiles({ name: 'synthetic.dem', mimeType: 'application/octet-stream', buffer: Buffer.from('PBDEMS2\0') })
    await page.getByRole('button', { name: '上传并解析', exact: true }).click()
    await expect.poll(() => polls).toBeGreaterThan(0)
    await expect(page.getByText('已进入解析队列…', { exact: true })).toBeVisible()
    await expect(page.getByText('解析中…', { exact: true })).toHaveCount(0)
    status = 'parsing'
    await expect(page.getByText('解析中…', { exact: true })).toBeVisible()
    status = 'parsed'
    await expect(page.getByText('解析完成', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '查看报告 →' })).toBeVisible()
    expect(polls).toBeGreaterThanOrEqual(3)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
test('late upload after navigation does not restart polling', async ({ page }) => {
  await prepare(page)
  let release, received, polls = 0
  const gate = new Promise(r => { release = r }), entered = new Promise(r => { received = r })
  await page.route('**/api/demos/upload', async route => { received(); await gate; await route.fulfill({ contentType: 'application/json', body: '{"demo_id":19}' }) })
  await page.route('**/api/demos/19/status', route => { polls++; return route.fulfill({ contentType: 'application/json', body: '{"status":"parsed"}' }) })
  await page.goto('/upload')
  await page.locator('input[type=file]').setInputFiles({ name: 'synthetic.dem', mimeType: 'application/octet-stream', buffer: Buffer.from('PBDEMS2\0') })
  await page.getByRole('button', { name: '上传并解析', exact: true }).click()
  await entered
  await expect(page.locator('.dropzone')).toHaveAttribute('aria-disabled', 'true')
  await expect(page.locator('.dropzone')).toHaveAttribute('tabindex', '-1')
  await expect(page.locator('input[type=file]')).toBeDisabled()
  await page.getByRole('link', { name: '返回我的复盘' }).click()
  await expect(page).not.toHaveURL(/\/upload$/)
  const completed = page.waitForResponse(r => new URL(r.url()).pathname === '/api/demos/upload')
  release()
  await (await completed).finished()
  // Negative observation window spans two original 2s polling periods (not a readiness wait).
  await page.waitForTimeout(4500)
  expect(polls).toBe(0)
})
