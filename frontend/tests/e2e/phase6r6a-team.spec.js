// 6R6A：Max 对齐 + 团队十人单回合同步路线 E2E 门禁
// 用 mvr-acceptance@test.com（demo 19 = de_mirage，已回填路线 artifact）
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const MIN_PAINTED = 0.10
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

function setTier(email, tier, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}

async function login(page, account) {
  await page.goto('/login')
  await page.fill('input[type="email"]', account.email)
  await page.fill('input[type="password"]', account.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

async function enterTeam(page) {
  await page.goto(ACCOUNT.demo)
  await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 15000 })
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.waitForFunction(() => {
    const cv = document.querySelector('.tm-canvas'); if (!cv) return false
    const ctx = cv.getContext('2d'); const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
    let p = 0; for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 10) p++
    return p / (cv.width * cv.height) > 0.01
  }, { timeout: 20000 })
}

async function scrubTeam(page, sec) {
  await page.locator('.tm-scrub').evaluate((el, v) => { el.value = String(v); el.dispatchEvent(new Event('input', { bubbles: true })) }, sec)
  await page.waitForTimeout(220)
}

async function canvasStats(page) {
  return page.locator('.tm-canvas').evaluate((cv) => {
    const ctx = cv.getContext('2d'); const { data } = ctx.getImageData(0, 0, cv.width, cv.height)
    let painted = 0, red = 0
    for (let i = 0; i < data.length; i += 4) {
      const a = data[i + 3]
      if (a > 10) painted++
      if (a > 100 && data[i] > 190 && data[i + 1] < 110 && data[i + 2] < 110) red++
    }
    return { painted, red }
  })
}

test('团队：Pro 锁定且零 team 请求（Max-only 门禁）', async ({ page }) => {
  setTier(ACCOUNT.email, 'pro', 7)
  const teamReqs = []
  page.on('request', (r) => { if (r.url().includes('/team/replay') || r.url().includes('/team-stats')) teamReqs.push(r.url()) })
  await login(page, ACCOUNT)
  await page.goto(ACCOUNT.demo)
  await page.click('button.tab:has-text("团队")')
  await expect(page.locator('.locked-box')).toBeVisible({ timeout: 15000 })
  expect(teamReqs).toEqual([])   // Pro 零团队请求（不泄露/不越权）
  await page.screenshot({ path: `${SHOTS}/team-pro-locked.png` })
})

test('Max：团队同步回放 一次聚合 + 首帧十球 + 播放像素变化 + death X + 突出 + 回合切换', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)   // 6R15 收口：按目标 demo 播种团队权益门禁
  const teamReqs = []
  page.on('request', (r) => { if (r.url().includes('/team/replay')) teamReqs.push(r.url()) })
  await login(page, ACCOUNT)
  await enterTeam(page)

  // 一次 team-route 聚合请求
  expect(teamReqs.filter((u) => u.includes('/team/replay')).length).toBe(1)

  // 摘要 + 玩家 chips（多人）；首帧绘制
  await expect(page.locator('.tm-summary')).toBeVisible()
  expect(await page.locator('.tm-chip').count()).toBeGreaterThan(5)   // 全部 + 多名玩家
  const first = await canvasStats(page)
  expect(first.painted / (720 * 720)).toBeGreaterThan(MIN_PAINTED)
  const redBefore = first.red
  await page.locator('.tm-stage').scrollIntoViewIfNeeded(); await page.waitForTimeout(200)
  await page.screenshot({ path: `${SHOTS}/team-first.png` })

  // 播放/推进：像素变化（路线增长）
  const maxDur = Number(await page.locator('.tm-scrub').getAttribute('max'))
  await scrubTeam(page, 0); const buf0 = await page.locator('.tm-canvas').screenshot()
  await scrubTeam(page, maxDur / 2); const bufMid = await page.locator('.tm-canvas').screenshot()
  expect(Buffer.compare(buf0, bufMid)).not.toBe(0)
  await page.locator('.tm-stage').scrollIntoViewIfNeeded(); await page.waitForTimeout(150)
  await page.screenshot({ path: `${SHOTS}/team-playing.png` })

  // death X 只在到达时出现：末段红像素显著增加
  await scrubTeam(page, maxDur)
  const redAfter = await canvasStats(page).then((s) => s.red)
  expect(redAfter).toBeGreaterThan(redBefore + 10)

  // 队伍筛选 A 改变渲染
  const allBuf = await page.locator('.tm-canvas').screenshot()
  await page.click('.tm-seg-btn:has-text("队伍A")')
  await page.waitForTimeout(300)
  const aBuf = await page.locator('.tm-canvas').screenshot()
  expect(Buffer.compare(allBuf, aBuf)).not.toBe(0)
  await page.click('.tm-seg-btn:has-text("全部")')

  // 玩家突出交互
  const chip = page.locator('.tm-chip').nth(1)
  await chip.click(); await expect(chip).toHaveClass(/on/, { timeout: 5000 })
  await page.locator('.tm-stage').screenshot({ path: `${SHOTS}/team-highlight.png` })
  const hiBuf = await page.locator('.tm-canvas').screenshot()
  await chip.click(); await expect(chip).not.toHaveClass(/on/, { timeout: 5000 })
  const unHiBuf = await page.locator('.tm-canvas').screenshot()
  expect(Buffer.compare(hiBuf, unHiBuf)).not.toBe(0)

  // 回合切换（选 R2 → 又一次 team-route 请求 + 数据更新）
  const beforeSwitch = teamReqs.filter((u) => u.includes('/team/replay')).length
  await page.locator('.tm-sel').selectOption('2')
  await page.waitForTimeout(600)
  expect(teamReqs.filter((u) => u.includes('/team/replay')).length).toBe(beforeSwitch + 1)
  await expect(page.locator('.tm-sel')).toHaveValue('2')
})

test('Max：团队同步回放 移动视口 390x844 有效像素 + 无横向溢出', async ({ browser }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)   // 6R15 收口：按目标 demo 播种团队权益门禁
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await ctx.newPage()
  try {
    await login(page, ACCOUNT)
    await enterTeam(page)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    const stats = await canvasStats(page)
    expect(stats.painted / (720 * 720)).toBeGreaterThan(MIN_PAINTED)
    await page.screenshot({ path: `${SHOTS}/team-mobile.png`, fullPage: true })
  } finally { await ctx.close() }
})

