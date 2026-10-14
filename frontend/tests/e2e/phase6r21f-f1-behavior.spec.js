// 6R21F-F1 行为门禁（红绿）：首页重构后的关键可测行为。
// 红绿纪律：以下断言先在未改实现时失败（red），实现后再转绿（green）。
// 运行：$env:PLAYWRIGHT_BASE_URL='http://localhost:5173'; npx playwright test -c playwright.headless.config.js phase6r21f-f1-behavior.spec.js
import { test, expect } from '@playwright/test'

const BASE = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173'

test.describe('6R21F F1 landing behavior', () => {
  test('首屏文案保留分段入场并结束在完整可读态', async ({ page }) => {
    await page.goto(`${BASE}/welcome`)
    const home = page.getByTestId('marketing-home')
    const heroItems = page.locator('.hero-copy [data-reveal]')
    await expect(home).not.toHaveClass(/hero-intro-pending/)
    await expect(heroItems).toHaveCount(6)
    const readStates = () => heroItems.evaluateAll((elements) => elements.map((element) => {
      const style = getComputedStyle(element)
      return { opacity: style.opacity, delay: style.transitionDelay, duration: style.transitionDuration }
    }))
    await expect.poll(async () => (await readStates()).every((state) => state.opacity === '1')).toBe(true)
    const states = await readStates()
    expect(new Set(states.map((state) => state.delay)).size).toBeGreaterThan(1)
    expect(states.every((state) => state.duration.includes('0.4s'))).toBeTruthy()
  })

  test('首屏舞台 seek 到真实叙事峰值（非 00:00 空场帧）', async ({ page }) => {
    await page.goto(`${BASE}/welcome`)
    await expect(page.getByTestId('landing-replay-stage')).toBeVisible()
    // 舞台应打开在"叙事峰值"帧（同时可见区域数 × 击杀窗口最大化的真实时刻），而非 00:00 空场
    const clock = await page.locator('.round-clock strong').innerText()
    expect(clock, '首屏应打开在叙事峰值时刻').not.toBe('00:00')
    // 且此时地图上是官方 NavMesh 裁剪的连续 Canvas 场，而非 per-area 多边形色块。
    await expect(page.getByTestId('landing-control-field')).toBeVisible()
    await expect(page.locator('polygon.area')).toHaveCount(0)
    await expect.poll(async () => page.getByTestId('landing-control-field').evaluate((canvas) => {
      const ctx = canvas.getContext('2d')
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data
      let visible = 0
      for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 0) visible++
      return visible
    })).toBeGreaterThan(100)
  })

  test('击杀后保留与死亡玩家数一致的红色死亡 X', async ({ page }) => {
    await page.goto(`${BASE}/welcome`)
    await expect(page.getByTestId('landing-replay-stage')).toBeVisible()
    const stage = page.getByTestId('landing-replay-stage')
    const timeline = stage.locator('.timeline')
    const box = await timeline.boundingBox()
    expect(box).toBeTruthy()
    await timeline.click({ position: { x: box.width * 0.42, y: box.height / 2 } })
    await expect.poll(() => stage.getByTestId('landing-death-x').count()).toBeGreaterThan(0)
    const deadActors = await stage.locator('.actor.dead').count()
    const deathXs = await stage.getByTestId('landing-death-x').count()
    expect(deathXs).toBe(deadActors)
  })

  test('长期教练区显示诚实披露：样本不足/低置信状态可见', async ({ page }) => {
    await page.goto(`${BASE}/welcome`)
    await expect(page.getByTestId('marketing-home')).toBeVisible()
    const coach = page.locator('#coach')
    await coach.scrollIntoViewIfNeeded()
    // 必须有一个机器可读的诚实状态元素：明确标出当前样本不足/低置信（绝不伪装成 10/30 满样本）
    const status = page.getByTestId('coach-sample-status')
    await expect(status).toBeVisible()
    await expect(status).toContainText(/样本不足|低置信|1 场|不足/)
    await expect(coach).toContainText('带回放证据的训练问题触发次数')
    await expect(coach).toContainText('达到最低解析覆盖率的有效比赛场数')
    const readiness = coach.getByTestId('coach-readiness')
    await expect(readiness).toHaveAttribute('aria-label', /当前真实样本仍为 0 场有效比赛/)
    await expect(readiness.locator('progress')).toHaveAttribute('max', '10')
    await expect(readiness).toContainText('解锁演示 · 当前真实样本 0 场')
    await expect(readiness).toContainText('动画仅演示解锁流程；当前单回合样本不计入有效比赛')
    await expect(readiness).toContainText('Top 3 可点击结论')
    await expect.poll(async () => Number(await readiness.locator('progress').getAttribute('value'))).toBe(10)
    await expect(coach.getByTestId('coach-examples')).toHaveAttribute('aria-hidden', 'false')
    await expect(coach.locator('.cv-chart')).toHaveCount(0)
  })

  test('复盘三步使用真实事件动画与同源 NavMesh 地图快照', async ({ page }) => {
    await page.goto(`${BASE}/welcome`)
    const evidence = page.locator('#evidence')
    await evidence.scrollIntoViewIfNeeded()
    await expect(evidence).toContainText('复盘三步')
    await expect(evidence).not.toContainText('01—03')
    await expect(evidence.locator('.proof-event .tl-dot')).toHaveCount(8)
    const preview = page.getByTestId('proof-control-preview')
    await expect(preview).toBeVisible()
    await expect(preview).toHaveAttribute('aria-label', /真实|第 2 回合|00:21|B 区/)
    await expect(page.locator('.region-samples')).toHaveCount(0)
    await expect.poll(async () => page.getByTestId('proof-control-field').evaluate((canvas) => {
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
      let visible = 0
      for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 0) visible++
      return visible
    })).toBeGreaterThan(100)
  })
})
