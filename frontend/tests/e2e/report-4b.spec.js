// Phase 4B 返工验收：UTC 时区契约 / Canvas ready 断言 / PNG fallback（demo 6）/ 移动端 ready 后截图
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'

const SHOTS = 'tests/e2e/screenshots'
const FREE = { email: 'phase4a@test.com', password: 'password123', demo: '/demo/6' }
const PRO = { email: 'acceptance@test.com', password: 'password123', demo: '/demo/1' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

// 本地 compose 栈内 psql（验收用临时改库，finally 恢复 free）
function setSubscription(email, tier, expireSql) {
  const expire = expireSql ? `, subscription_expire = ${expireSql}` : ', subscription_expire = NULL'
  execSync(
    `"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${expire} WHERE email='${email}';"`,
    { stdio: 'pipe' },
  )
}
function restoreFree(email) { setSubscription(email, 'free', null) }

async function login(page, account) {
  await page.goto('/login')
  await page.fill('input[type="email"]', account.email)
  await page.fill('input[type="password"]', account.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

// 等待热力图组件 ready（data-ready=ready），并断言 canvas 存在非背景像素
async function waitHeatmapReadyAndPainted(page) {
  await page.waitForSelector('.hm[data-ready="ready"]', { timeout: 20000 })
  // data-mode 标记在 canvas/img 子元素上（.hm 根只有 data-ready）
  const hasCanvas = (await page.locator('.hm canvas').count()) > 0
  const hasImg = (await page.locator('.hm img').count()) > 0
  const mode = hasCanvas ? 'canvas' : hasImg ? 'png' : 'error'
  if (mode === 'canvas') {
    const painted = await page.locator('.hm canvas').evaluate((cv) => {
      const ctx = cv.getContext('2d')
      const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
      // 非背景像素：非全透明或非纯背景色（背景 #0b0f14）
      let n = 0
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] > 10 || Math.abs(data[i] - 11) + Math.abs(data[i + 1] - 15) + Math.abs(data[i + 2] - 20) > 30) n++
      }
      return n
    })
    expect(painted).toBeGreaterThan(1000)   // 非空/非背景像素
  }
  return mode
}

test.describe('Phase 4B 返工验收', () => {
  // 无条件恢复：任何测试（含移动端等无 pro 标题的）不得残留订阅修改
  test.afterEach(() => { restoreFree(PRO.email) })

  test('pro：UTC now+1h 在 Asia/Shanghai 仍显示有效进阶版（P1-1）', async ({ browser }) => {
    // 先恢复 free，再设置 UTC now+1h 的有效 pro（复现 Codex 场景）
    restoreFree(PRO.email)
    setSubscription(PRO.email, 'pro', "now() + interval '1 hour'")
    const ctx = await browser.newContext({ timezoneId: 'Asia/Shanghai' })
    const page = await ctx.newPage()
    try {
      await login(page, PRO)
      await page.goto(PRO.demo)
      await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 15000 })
      // 报告头部徽标
      await expect(page.locator('.head .pill')).toContainText('进阶版')
      await page.screenshot({ path: `${SHOTS}/fix-pro-1h-utc.png` })
    } finally {
      await ctx.close()
    }
  })

  test('free：demo 6 热力图真实加载（canvas 或 PNG fallback，无 401）', async ({ page }) => {
    // demo 6 在 6R1 已重解析（有 density JSON）→ 走 canvas；fallback 路径由旧数据场景覆盖
    const statuses = []
    page.on('response', (resp) => { if (resp.url().includes('/api/demos/6/heatmap/')) statuses.push(resp.status()) })
    await login(page, FREE)
    await page.goto(FREE.demo)
    await page.click('details.diag-drawer summary')
    await page.waitForSelector('.hm[data-ready="ready"]', { timeout: 20000 })
    // canvas 或 PNG 任一渲染成功且无 401 裂图
    const hasCanvas = await page.locator('.hm canvas').count()
    const hasImg = await page.locator('.hm img').count()
    expect(hasCanvas + hasImg).toBeGreaterThan(0)
    if (hasImg > 0) {
      const naturalWidth = await page.locator('.hm img').evaluate((img) => img.naturalWidth)
      expect(naturalWidth).toBeGreaterThan(0)   // 不裂图
    }
    expect(statuses.every((s) => s === 200)).toBe(true)   // 无 401
    await page.screenshot({ path: `${SHOTS}/fix-demo6-fallback.png` })
  })

  test('pro：四模式 canvas ready + 非空像素断言（替换截图 buffer 假阳性）', async ({ page }) => {
    setSubscription(PRO.email, 'pro', "now() + interval '1 hour'")
    await login(page, PRO)
    await page.goto(PRO.demo)
    await page.click('details.diag-drawer summary')

    // 6R7B：路线热力图模式已移除（路线只允许折线/动画），仅保留击杀（全队）combat 密度
    const labels = { kills_all: '击杀（全队）' }
    const shots = {}
    for (const mode of ['kills_all']) {
      await page.click(`button.mode-btn:has-text("${labels[mode]}")`)
      const m = await waitHeatmapReadyAndPainted(page)
      expect(m).toBe('canvas')
      shots[mode] = await page.locator('canvas').screenshot()
      expect(shots[mode].length).toBeGreaterThan(1000)
    }

    // 控件真实重绘（ready 后断言 canvas 像素差异）
    const before = await page.locator('canvas').screenshot()
    await page.locator('.ctl input[type="range"]').first().fill('0.4')
    await page.waitForFunction(() => document.querySelector('.hm')?.getAttribute('data-ready') === 'ready')
    const after = await page.locator('canvas').screenshot()
    expect(Buffer.compare(before, after)).not.toBe(0)
    await page.screenshot({ path: `${SHOTS}/fix-pro-heatmap-final.png` })
  })

  test('移动视口 390x844：ready 后截图，无横向溢出', async ({ browser }) => {
    setSubscription(PRO.email, 'pro', "now() + interval '1 hour'")
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'Asia/Shanghai' })
    const page = await ctx.newPage()
    try {
      await login(page, PRO)
      await page.goto(PRO.demo)
      await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 15000 })
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow).toBeLessThanOrEqual(1)
      await page.screenshot({ path: `${SHOTS}/fix-mobile-overview.png`, fullPage: true })

      await page.click('details.diag-drawer summary')
      await waitHeatmapReadyAndPainted(page)
      const overflow2 = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow2).toBeLessThanOrEqual(1)
      await page.screenshot({ path: `${SHOTS}/fix-mobile-heatmap.png` })
    } finally {
      await ctx.close()
    }
  })

  test('free：锁定态受限接口零请求（保留回归）', async ({ page }) => {
    const restricted = []
    page.on('request', (req) => {
      if (/\/density\/route_|\/grenades|\/team-stats|\/heatmap\/route_/.test(req.url())) restricted.push(req.url())
    })
    await login(page, FREE)
    await page.goto(FREE.demo)
    await page.click('details.diag-drawer summary')
    await page.waitForSelector('.hm[data-ready="ready"]', { timeout: 20000 })
    // 6R7B：路线热力图模式已移除；点击已不存在的"路线（全队）"仅作遗留断言占位（无该按钮，仅验证不请求 route_ 资源）
    // 核心门禁：free 不请求受限资源（route_ / grenades / team-stats / heatmap/route_）
    expect(restricted).toEqual([])
    // 收回更多数据折叠，避免多 locked-box 干扰
    await page.click('details.diag-drawer summary')
    // 团队复盘 Max-only：free 锁定且零团队请求
    await page.click('button.tab:has-text("团队复盘")')
    await expect(page.locator('.locked-box').filter({ visible: true })).toBeVisible({ timeout: 8000 })
    expect(restricted).toEqual([])
  })
})
