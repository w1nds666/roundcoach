import { test, expect } from '@playwright/test'

for (const width of [1280, 390, 360]) {
  test(`login pending and recovery at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    let release, entered, count = 0
    const received = new Promise(resolve => { entered = resolve })
    const gate = new Promise(resolve => { release = resolve })
    await page.route('**/api/auth/login', async route => {
      count++
      if (count === 1) { entered(); await gate }
      await route.fulfill({ status: 401, contentType: 'application/json',
        body: JSON.stringify({ detail: { code: 'INVALID_CREDENTIALS', message: '邮箱或密码错误（测试响应）' } }) })
    })
    await page.goto('/login')
    await page.getByLabel('邮箱', { exact: true }).fill('pending@example.com')
    const password = page.getByLabel('密码', { exact: true })
    await password.fill('test-password-only')
    await password.press('Enter')
    await received
    const login = page.getByRole('tab', { name: '登录', exact: true })
    const register = page.getByRole('tab', { name: '注册', exact: true })
    await expect(login).toBeDisabled()
    await expect(register).toBeDisabled()
    await expect(page.getByRole('button', { name: '请稍候…' })).toBeDisabled()
    await password.press('Enter')
    expect(count).toBe(1)
    release()
    await expect(page.getByRole('alert')).toHaveText('邮箱或密码错误（测试响应）')
    await expect(register).toBeEnabled()
    await expect(page.getByLabel('邮箱', { exact: true })).toHaveValue('pending@example.com')
    await password.press('Enter')
    await expect.poll(() => count).toBe(2)
    await expect(register).toBeEnabled()
    await register.focus()
    await register.press('Enter')
    await expect(register).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByLabel('昵称', { exact: true })).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
