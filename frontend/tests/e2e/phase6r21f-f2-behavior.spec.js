// 6R21F-F2 行为/结构门禁（红绿）：统一应用壳层 + Dashboard/Upload/Login 的可测行为。
// 红绿纪律：以下断言在实现前失败（red），实现后转绿（green）。
// 运行：$env:PLAYWRIGHT_BASE_URL='http://localhost:5173'; npx playwright test -c playwright.headless.config.js phase6r21f-f2-behavior.spec.js
import { test, expect } from '@playwright/test'

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }

async function login(page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

test.describe('6R21F-F2 统一应用壳层', () => {
  test('桌面壳层：侧栏/面包屑/顶栏身份档位齐备', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await login(page)
    // 统一壳层根 + 侧栏（桌面可见）
    await expect(page.locator('.app-shell')).toBeVisible()
    await expect(page.locator('.app-side')).toBeVisible()
    await expect(page.locator('.app-crumb')).toBeVisible()
    // 侧栏主导航（四项主路径 + 活动态）
    for (const label of ['我的复盘', '上传', '长期教练', '我的档案']) {
      await expect(page.locator('.app-nav a.app-nav-link', { hasText: label }).first()).toBeVisible()
    }
    // 顶栏身份：头像/昵称/档位徽标
    await expect(page.locator('.app-topbar .app-user')).toBeVisible()
    await expect(page.locator('.app-topbar .app-avatar')).toBeVisible()
    await expect(page.locator('.app-topbar .tier-badge')).toBeVisible()
  })

  test('移动端导航不遮挡正文、无横滚', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await login(page)
    await expect(page.locator('.app-nav-strip')).toBeVisible()
    await expect(page.locator('.app-side')).toBeHidden()
    const strip = await page.locator('.app-nav-strip').boundingBox()
    const title = await page.locator('.page-title').first().boundingBox()
    expect(strip.y + strip.height, '导航条不应遮挡正文标题').toBeLessThanOrEqual(title.y + 1)
    const w = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
    expect(w, '无横向滚动').toBe(true)
  })
})

test.describe('6R21F-F2 Dashboard 作战记录台', () => {
  test('身份/权益状态条第一眼可读 + 主路径突出', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await login(page)
    await page.goto(`${BASE}/`)
    await expect(page.locator('.page-title')).toBeVisible()
    // 权益与身份状态条（Free/Pro/Max + Steam 未绑定/已绑定 语义不得弱化）
    await expect(page.locator('.status-strip')).toBeVisible()
    await expect(page.locator('.status-strip .tier-badge')).toHaveText(/免费版|进阶版|团队版/)
    await expect(page.locator('.status-strip .identity-pill')).toContainText(/已绑定|未绑定/)
    // 主路径：「上传 Demo」按钮 + 「继续复盘」入口指向最近已解析比赛
    await expect(page.locator('a.btn, button.btn', { hasText: /上传 Demo|上传比赛/ }).first()).toBeVisible()
    await expect(page.locator('a.continue-review', { hasText: /继续复盘/ }).first()).toBeVisible()
    const href = await page.locator('a.continue-review').first().getAttribute('href')
    expect(href, '继续复盘应指向 /demo/{id}').toMatch(/^\/demo\/\d+$/)
  })

  test('诚实空态：无比赛时显示可理解的空态 + 引导上传', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await login(page)
    // 只 mock 列表为空，服务端身份/权益保持真实
    await page.route('**/api/demos?*', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '[]' }))
    await page.goto(`${BASE}/`)
    await expect(page.locator('.empty-state')).toBeVisible()
    await expect(page.locator('.empty-state')).toContainText(/还没有|没有匹配/)
    await expect(page.locator('.empty-state a.btn', { hasText: /去上传|上传/ }).first()).toBeVisible()
  })
})

test.describe('6R21F-F2 Upload 可靠数据入口', () => {
  test('步骤化流程 + .dem 限制 + 键盘可操作 + 移动端非大空白拖放框', async ({ page }) => {
    await login(page)
    // 桌面：三步流程 + 拖放框可聚焦
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto(`${BASE}/upload`)
    await expect(page.locator('.flow')).toBeVisible()
    await expect(page.locator('.flow-step')).toHaveCount(3)
    // 拖放框必须可键盘聚焦（focusable）、且绑定 .dem 限制
    await expect(page.locator('.dropzone')).toHaveAttribute('tabindex', '0')
    await expect(page.locator('.dropzone')).toHaveAttribute('role', 'button')
    await expect(page.locator('input[type="file"]')).toHaveAttribute('accept', /\.dem|\.dem,/)
    await expect(page.locator('.upload-note')).toContainText(/.dem/)
    await expect(page.locator('.upload-note')).toContainText(/300MiB|300 MB/)

    // 移动端：拖放框不应是"巨大空白"
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${BASE}/upload`)
    const dz = await page.locator('.dropzone').boundingBox()
    expect(dz.height, '移动端拖放框高度应克制').toBeLessThanOrEqual(240)
  })
})

test.describe('6R21F-F2 Login 可信身份门', () => {
  test('邮箱/密码语义 + autocomplete + 诚实 Steam 入口 + 错误态', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto(`${BASE}/login`)
    await expect(page.locator('.auth-card')).toBeVisible()
    await expect(page.locator('input[type="email"]')).toHaveAttribute('autocomplete', 'email')
    await expect(page.locator('input[type="password"]')).toHaveAttribute('autocomplete', /current-password|new-password/)
    // 诚实 Steam 入口：说明绑定发生在登录后（我的档案），不制造"已绑定"
    await expect(page.locator('.steam-entry')).toBeVisible()
    await expect(page.locator('.steam-entry')).toContainText(/Steam/)
    await expect(page.locator('.steam-entry')).toContainText(/绑定/)
    // 开发提示只属于 Vite dev；生产构建不应误称用户正使用测试身份。
    const viteDev = await page.locator('script[src="/@vite/client"]').count() > 0
    if (viteDev) {
      await expect(page.locator('.dev-boundary')).toContainText(/开发 \/ 测试环境/)
    } else {
      await expect(page.locator('.dev-boundary')).toHaveCount(0)
    }
    // 错误恢复：空密码提交 → 字段错误可见且可读取
    await page.fill('input[type="email"]', ACCOUNT.email)
    await page.fill('input[type="password"]', '')
    await page.click('button[type="submit"]')
    await expect(page.locator('.field-error')).toBeVisible()
    await expect(page.locator('[role="alert"],.field-error').first()).toContainText(/密码/)
  })
})
