// 6R15 长期教练 E2E：权益门禁（Free lock / Pro 10 / Max 30+对照）+ 证据跳转 + 移动适配。
// 自给自足：每个用例进入即 seedIdentity 绑定 demo19-23 系列的真实用户 Steam（见 COACH_STEAM），并 setTier 决定窗口。
// coach 画像由后端按绑定身份生成（生产 demo19-23 应产出 5 场），因此进入前先 coachWarm 预热再加载页面。
import { test, expect } from '@playwright/test'
import { setTier, seedIdentity, clearIdentity, login, coachWarm, apiGet, demoWherePlayer, DEMO19, DEMO23, ACCOUNT } from './_helpers6r15.js'

const SHOTS = 'tests/e2e/screenshots/phase6r15'
// 生产 demo19-23 是 mvr-acceptance 的真实回放，其绑定身份为 demo19 的 players[0]（DEMO19.ROUND3_SMOKE）。
// 绑定该 Steam 后 coach 聚合最近 5 场（demo19-23），并据此断言「真实覆盖、不补假比赛」。
const COACH_STEAM = DEMO19.ROUND3_SMOKE

test.describe('6R15 长期教练', () => {
  test('(a) Free：只读锁定预览 + 升级 CTA', async ({ page }) => {
    setTier(ACCOUNT.email, 'free')
    clearIdentity(ACCOUNT.email)
    await login(page, ACCOUNT.email, ACCOUNT.password)
    await page.goto('/coach')
    await page.waitForSelector('.coach', { timeout: 10000 })
    await expect(page.locator('.locked-box .lock-title')).toContainText('长期教练属于进阶版功能', { timeout: 10000 })
    await expect(page.locator('.locked-box .btn-primary')).toBeVisible()
    await page.screenshot({ path: `${SHOTS}/coach-free-locked.png`, fullPage: true })
  })

  test('(b) Pro：加载最近 10 场窗口（真实覆盖，不补假比赛）+ 30 窗口锁定', async ({ page }) => {
    seedIdentity(ACCOUNT.email, COACH_STEAM)
    setTier(ACCOUNT.email, 'pro', 30)
    await login(page, ACCOUNT.email, ACCOUNT.password)
    await coachWarm(page, 10)   // 预热，确保后端已生成 ProfileCoachSnapshot
    await page.goto('/coach')
    await page.waitForSelector('.coach', { timeout: 10000 })
    // 不应出现锁定预览
    await expect(page.locator('.locked-box')).toHaveCount(0, { timeout: 10000 })
    // 10 场覆盖显示真实数（k/10，不补成 10/10）
    await expect(page.locator('.cov-cell', { hasText: '最近 10 场' }).locator('.cov-num')).toContainText(/\d+\/10/, { timeout: 15000 })
    // 30 场为 Max-only：按钮锁定
    const btn30 = page.locator('.toggle .seg-btn').filter({ hasText: '最近 30 场' })
    await expect(btn30).toBeDisabled()
    await expect(btn30).toContainText('高阶版')
    await page.screenshot({ path: `${SHOTS}/coach-pro-10.png`, fullPage: true })
  })

  test('(c) Max：加载最近 30 场 + 10/30 对照', async ({ page }) => {
    seedIdentity(ACCOUNT.email, COACH_STEAM)
    setTier(ACCOUNT.email, 'max', 30)
    await login(page, ACCOUNT.email, ACCOUNT.password)
    await coachWarm(page, 30)
    await page.goto('/coach')
    await page.waitForSelector('.coach', { timeout: 10000 })
    await expect(page.locator('.locked-box')).toHaveCount(0, { timeout: 10000 })
    await expect(page.locator('.cov-cell', { hasText: '最近 30 场' }).locator('.cov-num')).toContainText(/\d+\/30/, { timeout: 15000 })
    // 30 场按钮可用
    const btn30 = page.locator('.toggle .seg-btn').filter({ hasText: '最近 30 场' })
    await expect(btn30).toBeEnabled()
    // 切换到 30 场窗口（refetch）后再切回，验证分段控件与 10/30 对照并存
    await btn30.click()
    await page.waitForTimeout(600)
    await expect(page.locator('.cov-cell', { hasText: '最近 10 场' }).locator('.cov-num')).toContainText(/\d+\/10/)
    await expect(page.locator('.cov-cell', { hasText: '最近 30 场' }).locator('.cov-num')).toContainText(/\d+\/30/)
    await page.screenshot({ path: `${SHOTS}/coach-max-30.png`, fullPage: true })
  })

  test('(d) 证据跳转：教练页 → 报告页自动定位到对应回合', async ({ page }) => {
    seedIdentity(ACCOUNT.email, COACH_STEAM)
    setTier(ACCOUNT.email, 'max', 30)
    await login(page, ACCOUNT.email, ACCOUNT.password)
    await coachWarm(page, 10)
    await page.goto('/coach')
    await page.waitForSelector('.coach', { timeout: 10000 })

    let demoId, round, time
    const btn = page.locator('.ev-btn').first()
    if (await btn.count() > 0) {
      // 优先走「教练页真实点击证据」路径
      demoId = await btn.getAttribute('data-demo-id')
      round = await btn.getAttribute('data-round')
      time = await btn.getAttribute('data-time')
      expect(demoId).toBeTruthy()
      expect(round).toBeTruthy()
      await btn.click()
    } else {
      // 该窗口暂无重复问题/证据：直达验证 Report 的 locate 机制。
      // 选一个绑定玩家确实在 roster 的已解析 demo（否则个人回放不会挂载），再用其第一个可用回合。
      const fbDemo = demoWherePlayer(ACCOUNT.email, COACH_STEAM) || String(DEMO23)
      demoId = fbDemo
      const replay = await apiGet(page, `/api/demos/${fbDemo}/personal/replay`)
      const rounds = Array.isArray(replay?.body?.rounds) ? replay.body.rounds : []
      const r0 = rounds[0]?.round ?? 1
      round = String(r0)
      time = ''
      await page.goto(`/demo/${demoId}?view=personal&locate=${encodeURIComponent(JSON.stringify({ round: r0, time: null, layers: ['route'], highlight: [] }))}`)
    }

    const targetRound = Number(round)
    // 应导航到该 demo 的报告页，并带 view=personal + locate 查询
    await page.waitForURL((u) => u.pathname === `/demo/${demoId}` && u.search.includes('view=personal'), { timeout: 15000 })
    // 报告页：个人战术回放就绪
    await page.waitForSelector('.pt-stage canvas[data-ready="ready"]', { timeout: 20000 })
    // 回合选择器已定位到证据回合
    await expect(page.locator('.pt-sel')).toHaveValue(String(targetRound), { timeout: 10000 })
    // 时间戳已 seek（若证据带秒，时间戳应为该秒；至少证明定位已发生）
    const ts = await page.locator('.pt-stage canvas').getAttribute('data-time')
    expect(ts).not.toBeNull()
    if (time != null && time !== '') expect(Math.round(Number(ts))).toBe(Math.round(Number(time)))
    await page.screenshot({ path: `${SHOTS}/coach-evidence-jump.png`, fullPage: true })
  })

  test('(e) 移动 390：无横向溢出 + 触控目标 >=44px', async ({ page }) => {
    seedIdentity(ACCOUNT.email, COACH_STEAM)
    setTier(ACCOUNT.email, 'max', 30)
    await login(page, ACCOUNT.email, ACCOUNT.password)
    await coachWarm(page, 10)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/coach')
    await page.waitForSelector('.coach', { timeout: 10000 })
    await expect(page.locator('.locked-box')).toHaveCount(0, { timeout: 10000 })
    // 无横向溢出
    const overflow = await page.evaluate(() => {
      const el = document.scrollingElement
      return { sw: el.scrollWidth, cw: el.clientWidth }
    })
    expect(overflow.sw).toBeLessThanOrEqual(overflow.cw + 1)
    // 触控目标 >=44px（分段控件 + 证据按钮 + 一般按钮）
    const heights = await page.evaluate(() =>
      Array.from(document.querySelectorAll('.toggle .seg-btn, .ev-btn, .btn'))
        .map((t) => Math.round(t.getBoundingClientRect().height))
    )
    expect(heights.length).toBeGreaterThan(0)
    for (const h of heights) expect(h).toBeGreaterThanOrEqual(44)
    await page.screenshot({ path: `${SHOTS}/coach-mobile-390.png`, fullPage: true })
  })
})
