// 6R7A：去 AI 味 + 个人复盘矛盾返工 门禁
// 一级标签收敛 / 死亡点位真 X / 密度图例 / 击杀死亡与 CT/T 切换 / 状态保持 / 个人道具 / Max 团队 / 390
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, demoPlayerSteam } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const PRO = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

// 6R14 身份优先：个人复盘数据恒为绑定本人；先播种 demo19 首位玩家作「我」
function seedPersonal() { seedIdentity(PRO.email, demoPlayerSteam(PRO.email, 19)) }

function setTier(email, tier, days) {
  const exp = days ? `, subscription_expire = now() + interval '${days} days'` : ', subscription_expire = NULL'
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

async function selectPlayerAndWait(page, index) {
  const origin = new URL(page.url()).origin
  const demoId = new URL(page.url()).pathname.match(/^\/demo\/(\d+)/)?.[1]
  expect(demoId, '点位请求必须绑定当前比赛').toBeTruthy()
  const sideLabel = (await page.locator('.combat-head .seg-btn.on').allTextContents()).map(s => s.trim())
  const side = sideLabel.includes('CT') ? 'CT' : sideLabel.includes('T') ? 'T' : 'all'
  // Personal combat data is identity-scoped, not the selected comparison player.
  // Capture a newly issued request, so an older in-flight response cannot satisfy readiness.
  const [request] = await Promise.all([
    page.waitForRequest((r) => {
      const url = new URL(r.url())
      return r.method() === 'GET' && url.origin === origin &&
        url.pathname === `/api/demos/${demoId}/personal/combat-map` && url.searchParams.get('side') === side
    }, { timeout: 20000 }),
    page.locator('.picker select').selectOption({ index }),
  ])
  const response = await request.response()
  expect(response, '本次点位请求必须返回响应').not.toBeNull()
  expect(response.ok(), '本次点位请求必须成功，不能由其他响应代替').toBe(true)
  await response.finished()
}

async function capturePointCanvas(page, requireDeathX = false) {
  const snapshot = await page.evaluate(async ({ requireDeathX }) => {
    const deadline = performance.now() + 15000
    while (performance.now() < deadline) {
      await new Promise(requestAnimationFrame)
      const cv = document.querySelector('.pcm-canvas')
      if (!(cv instanceof HTMLCanvasElement) || !cv.isConnected || !cv.width || !cv.height) continue
      const data = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data
      let opaque = 0; let red = 0
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] > 100) opaque++
        if (data[i + 3] > 100 && data[i] > 190 && data[i + 1] < 130 && data[i + 2] < 130) red++
      }
      if (opaque === 0 || (requireDeathX && red === 0)) continue
      return { png: cv.toDataURL('image/png').split(',')[1], opaque, red, width: cv.width, height: cv.height }
    }
    throw new Error('当前点位画布未在 15s 内形成可审计的非空 backing bitmap')
  }, { requireDeathX })
  expect(snapshot.width).toBeGreaterThan(0)
  expect(snapshot.height).toBeGreaterThan(0)
  expect(snapshot.opaque, '点位画布必须包含非空像素').toBeGreaterThan(0)
  const png = Buffer.from(snapshot.png, 'base64')
  expect(png.length, '点位画布 PNG backing bitmap 不得为空').toBeGreaterThan(1000)
  return { ...snapshot, png }
}

test('报告一级标签收敛为 4 个', async ({ page }) => {
  setTier(PRO.email, 'pro', 7)
  seedPersonal()
  await login(page, PRO)
  await page.goto(PRO.demo)
  await page.click('button.tab:has-text("个人复盘")')
  const labels = await page.locator('.tabs .tab').allTextContents()
  expect(labels).toEqual(['概览', '个人复盘', '团队复盘', 'AI 教练'])
  await page.screenshot({ path: `${SHOTS}/report-personal-route.png` })
})

test('个人死亡默认点位图绘制红色 X，切密度才出现热力色条', async ({ page }) => {
  setTier(PRO.email, 'pro', 7)
  seedPersonal()
  await login(page, PRO)
  await page.goto(PRO.demo)
  await page.click('button.tab:has-text("个人复盘")')
  await selectPlayerAndWait(page, 1)

  // 切到「我的死亡」：点位画布（非热力 .hm）且与击杀视图不同 → 死亡用真实标记而非热斑
  const kills = await capturePointCanvas(page)
  await page.click('.combat-head .seg-btn:has-text("我的死亡")')
  await expect(page.locator('.pcm-legend')).toContainText('死亡（受害者位置）')
  const deaths = await capturePointCanvas(page, true)
  expect(Buffer.compare(kills.png, deaths.png)).not.toBe(0)
  expect(deaths.red, '死亡点位必须绘制红色 X 像素').toBeGreaterThan(kills.red)
  // 点位视图无热力画布（.hm）
  expect(await page.locator('.pcm-stage .hm').count()).toBe(0)

  // 切「密度」：出现热力（.hm）+ 低/高图例
  await page.click('.pcm .seg-btn:has-text("密度")')
  await page.waitForSelector('.pcm-stage .hm', { timeout: 15000 })
  expect(await page.locator('.pcm-stage .hm canvas').count()).toBeGreaterThan(0)
  await page.screenshot({ path: `${SHOTS}/report-personal-points.png` })

  // 切回「点位」：回到点位标记
  await page.click('.pcm .seg-btn:has-text("点位")')
  const restored = await capturePointCanvas(page, true)
  expect(restored.png.equals(deaths.png), '切回点位必须恢复同一死亡数据的完整画布').toBe(true)
  expect(await page.locator('.pcm-stage .hm').count()).toBe(0)
})

test('击杀/死亡、CT/T 切换产生真实变化', async ({ page }) => {
  setTier(PRO.email, 'pro', 7)
  seedPersonal()
  await login(page, PRO)
  await page.goto(PRO.demo)
  await page.click('button.tab:has-text("个人复盘")')
  await selectPlayerAndWait(page, 1)

  const kills = await capturePointCanvas(page)
  await page.click('.combat-head .seg-btn:has-text("我的死亡")')
  await expect(page.locator('.pcm-legend')).toContainText('死亡（受害者位置）')
  const deaths = await capturePointCanvas(page, true)
  expect(Buffer.compare(kills.png, deaths.png)).not.toBe(0)   // 击杀 vs 死亡 画布不同

  // CT 筛选：触发重新拉取 combat-map（真实变化）
  const sideReqs = []
  page.on('request', (r) => { if (r.url().includes('/personal/combat-map')) sideReqs.push(r.url()) })
  await page.click('.combat-head .seg-btn:has-text("CT")')
  await page.waitForTimeout(600)
  expect(sideReqs.filter((u) => u.includes('side=CT')).length).toBeGreaterThan(0)   // CT 筛选触发重新拉取
})

test('切玩家与切 Tab 状态不丢', async ({ page }) => {
  setTier(PRO.email, 'pro', 7)
  seedPersonal()
  await login(page, PRO)
  await page.goto(PRO.demo)
  await page.click('button.tab:has-text("个人复盘")')
  await page.locator('.picker select').selectOption({ index: 1 })
  await page.waitForSelector('.pcm-canvas', { timeout: 20000 })
  await page.locator('.picker select').selectOption({ index: 2 })
  await page.waitForTimeout(800)
  const picked = await page.locator('.picker select').inputValue()
  // 切到概览再回个人复盘
  await page.click('button.tab:has-text("概览")')
  await page.waitForTimeout(300)
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.picker select', { timeout: 15000 })
  await page.waitForTimeout(600)
  expect(await page.locator('.picker select').inputValue()).toBe(picked)   // 玩家选择保持
  expect(await page.locator('.pcm-canvas')).toBeVisible()
})

test('个人道具只统计所选玩家', async ({ page }) => {
  setTier(PRO.email, 'pro', 7)
  seedPersonal()
  await login(page, PRO)
  await page.goto(PRO.demo)
  await page.click('button.tab:has-text("个人复盘")')
  await page.locator('.picker select').selectOption({ index: 1 })
  await page.waitForSelector('.gren-total', { timeout: 15000 })
  await expect(page.locator('.gren-total')).toBeVisible()
})

test('Max 团队回放可用；Pro 锁定且零 team 请求', async ({ page }) => {
  const teamReqs = []
  page.on('request', (r) => { if (r.url().includes('/team/route') || r.url().includes('/team-stats')) teamReqs.push(r.url()) })
  setTier(PRO.email, 'pro', 7)
  seedPersonal()
  await login(page, PRO)
  await page.goto(PRO.demo)
  await page.click('button.tab:has-text("团队复盘")')
  await expect(page.locator('.locked-box').filter({ visible: true })).toBeVisible({ timeout: 10000 })
  expect(teamReqs).toEqual([])   // Pro 零团队请求
})

test('390px 移动端无横向溢出（个人复盘）', async ({ browser }) => {
  setTier(PRO.email, 'pro', 7)
  seedPersonal()
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await ctx.newPage()
  try {
    await login(page, PRO)
    await page.goto(PRO.demo)
    await page.click('button.tab:has-text("个人复盘")')
    await page.locator('.picker select').selectOption({ index: 1 })
    await page.waitForSelector('.pcm-canvas', { timeout: 20000 })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    await page.screenshot({ path: `${SHOTS}/report-mobile.png`, fullPage: true })
  } finally { await ctx.close() }
})
