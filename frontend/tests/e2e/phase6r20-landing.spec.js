// 6R20：公开品牌首页。重点验证真实地图资产、诚实披露、播放交互、转化路径与移动端无横滚。
// 6R21：舞台已改为真实 demo19 回合样本驱动（匿名/版本化），此测试同步对齐新实现。
import { test, expect } from '@playwright/test'

test.describe('6R20 public landing', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/welcome')
    await expect(page.getByTestId('marketing-home')).toBeVisible()
  })

  test('真实 Mirage 雷达底图与诚实演示状态可见', async ({ page }) => {
    const radar = page.locator('.radar')
    await expect(radar).toHaveAttribute('src', '/maps/de_mirage.png')
    await expect(radar).toHaveAttribute('alt', /Mirage.*雷达/)
    await expect.poll(() => radar.evaluate((img) => ({ complete: img.complete, width: img.naturalWidth }))).toMatchObject({ complete: true })
    expect((await radar.evaluate((img) => img.naturalWidth))).toBeGreaterThan(0)
    await expect(page.getByText(/动效用于展示产品交互/)).toBeVisible()
    await expect(page.getByText(/实际结论以.*Demo 解析事件为准/)).toBeVisible()
    await expect(page.getByText(/真实回合片段/)).toBeVisible()
  })

  test('播放、暂停与时间轴推进确定可用', async ({ page }) => {
    await expect(page.locator('.player-time')).toBeVisible()
    const time = page.locator('.player-time')
    const before = await time.innerText()
    await page.getByRole('button', { name: '播放界面预览' }).click()
    await expect(page.getByRole('button', { name: '暂停界面预览' })).toBeVisible()
    await page.waitForTimeout(1250)
    expect(await time.innerText()).not.toBe(before)
    await page.getByRole('button', { name: '暂停界面预览' }).click()
    await expect(page.getByRole('button', { name: '播放界面预览' })).toBeVisible()
  })

  test('真实回合样本推送至舞台（10 名玩家渲染）', async ({ page }) => {
    await expect(page.locator('.actor')).toHaveCount(10)
    // 控制场来自官方 NavMesh mask 上的单 Canvas 连续场；不再按 area 画色块。
    await expect(page.getByTestId('landing-control-field')).toBeVisible()
    await expect(page.locator('polygon.area')).toHaveCount(0)
  })

  test('免费开始进入注册态', async ({ page }) => {
    await page.getByRole('link', { name: '免费开始' }).first().click()
    await expect(page).toHaveURL(/\/login\?mode=register$/)
    await expect(page.locator('.tab.on')).toHaveText('注册')
  })

  test('390px 无横滚且战术舞台仍可读', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.reload()
    await expect(page.getByTestId('landing-replay-stage')).toBeVisible()
    const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
    expect(width.scroll).toBeLessThanOrEqual(width.client + 1)
    await expect(page.locator('.actor')).toHaveCount(10)
    await expect(page.getByText(/关键证据/)).toBeVisible()
  })

  test('360px 无横滚', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 })
    await page.reload()
    const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
    expect(width.scroll).toBeLessThanOrEqual(width.client + 1)
    await expect(page.locator('.actor')).toHaveCount(10)
  })
})
