import { test, expect } from '@playwright/test'

for (const width of [1280, 390, 360]) {
  test(`unbind backdrop respects pending verification at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    // UI fixtures only. Never authenticate against Steam or change a real binding.
    await page.addInitScript(() => localStorage.setItem('cs2_user', JSON.stringify({ id: -1, nickname: 'UI fixture', subscription_tier: 'free' })))
    await page.route('**/api/**', route => route.fulfill({ contentType: 'application/json', body: '{}' }))
    await page.route('**/api/demos?limit=100', route => route.fulfill({ contentType: 'application/json', body: '[]' }))
    await page.route('**/api/auth/steam/status', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ bound: true, steam_id64_masked: '***fixture', provider: 'steam' }) }))
    let release, entered, calls = 0, unbindCalls = 0
    const gate = new Promise(r => { release = r }), received = new Promise(r => { entered = r })
    await page.route('**/api/auth/verify-password', async route => {
      calls++; entered(); await gate
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"message":"temporary failure"}' })
    })
    await page.route('**/api/auth/steam/unbind?*', route => { unbindCalls++; return route.fulfill({ status: 500, body: '{}' }) })
    await page.goto('/profile')
    await page.getByRole('button', { name: '解绑', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '确认解绑 Steam' })
    await dialog.getByLabel('账号密码').fill('synthetic-only')
    await dialog.getByRole('button', { name: '确认解绑', exact: true }).click()
    await received
    await expect(dialog.getByLabel('账号密码')).toBeDisabled()
    await expect(dialog.getByRole('button', { name: '取消', exact: true })).toBeDisabled()
    await page.locator('.backdrop').click({ position: { x: 2, y: 2 } })
    await expect(dialog).toBeVisible()
    release()
    await expect(dialog.locator('.form-error-box')).toBeVisible()
    await expect(dialog.getByLabel('账号密码')).toBeEnabled()
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await page.getByRole('button', { name: '解绑', exact: true }).click()
    await expect(dialog.getByLabel('账号密码')).toHaveValue('')
    expect(calls).toBe(1)
    expect(unbindCalls).toBe(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
