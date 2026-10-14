// Phase 6A 证据截图：五场报告页热力图（Canvas 成品化渲染）+ 回合 Tab（独立 Chromium）
import { test, expect } from '@playwright/test'

const SHOTS = 'tests/e2e/screenshots/phase6a'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }

async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

for (const [demoId, map] of [[19, 'mirage'], [20, 'anubis'], [21, 'dust2'], [22, 'inferno'], [23, 'ancient']]) {
  test(`截图 demo ${demoId} ${map} 热力图+回合`, async ({ page }) => {
    await login(page)
    await page.goto(`/demo/${demoId}`)
    await expect(page.locator('.map-name')).toHaveText(/de_/, { timeout: 20000 })

    // 概览 Tab（6R7B：原始 tick-range/事件表已从主流程移除；仅保留战斗密度热力图于「技术诊断 / 导出」）
    await page.click('button.tab:has-text("概览")')
    await page.click('details.diag-drawer summary')
    await page.waitForSelector('.diag-drawer .hm[data-ready="ready"]', { timeout: 20000 })
    const painted = await page.locator('.hm canvas').evaluate((cv) => {
      const ctx = cv.getContext('2d')
      const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
      let n = 0
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] > 10) n++
      }
      return n
    })
    expect(painted).toBeGreaterThan(1000)
    await page.screenshot({ path: `${SHOTS}/${map}-heatmap.png` })
  })
}
