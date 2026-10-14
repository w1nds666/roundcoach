import { test, expect } from '@playwright/test'

for (const width of [1280, 390, 360]) {
  test(`login tabs keyboard at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/login')
    const login = page.getByRole('tab', { name: '登录', exact: true })
    const register = page.getByRole('tab', { name: '注册', exact: true })
    await login.focus()
    await login.press('ArrowRight')
    await expect(register).toBeFocused()
    await expect(register).toHaveAttribute('aria-selected', 'true')
    await expect(login).toHaveAttribute('tabindex', '-1')
    await expect(page.getByRole('tabpanel', { name: '注册', exact: true })).toBeVisible()
    await register.press('Tab')
    await expect(page.getByLabel('昵称', { exact: true })).toBeFocused()
    await register.focus()
    await register.press('Home')
    await expect(login).toBeFocused()
    await expect(page.getByRole('tabpanel', { name: '登录', exact: true })).toBeVisible()
    await login.press('End')
    await expect(register).toBeFocused()
    await register.press('ArrowLeft')
    await expect(login).toBeFocused()
    await login.press('Tab')
    await expect(page.getByLabel('邮箱', { exact: true })).toBeFocused()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
