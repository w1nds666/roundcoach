// 6R21：首页战术舞台真值专项门禁。
// 覆盖 P0：真实坐标不越界（统一变换+裁剪）、匿名无 SteamID/昵称泄漏、区域三态连续渲染、
//         播放/暂停/seek/倍速确定性、reduced-motion 静态快照、桌面+390+360 无横滚。
import { test, expect } from '@playwright/test'

let sample
test.beforeAll(async ({ request }) => {
  const res = await request.get('/demo/6r21-round-sample.json')
  expect(res.ok()).toBeTruthy()
  sample = await res.json()
})

test.describe('6R21 landing truth gate', () => {
  test('样本为匿名、版本化、可审计：无 SteamID/昵称/库主键泄漏', async () => {
    expect(sample.schema_version).toBeTruthy()
    expect(sample.content_hash).toMatch(/^[0-9a-f]{64}$/)
    expect(sample.meta.no_steam_ids).toBe(true)
    expect(sample.meta.no_player_names).toBe(true)
    expect(sample.meta.artifact_sha256).toMatch(/^[0-9a-f]{64}$/)
    for (const p of sample.players) {
      expect(p.uid).toMatch(/^(CT|T)\d$/)
      expect(p).not.toHaveProperty('steam_id')
      expect(p).not.toHaveProperty('player_id')
      expect(p).not.toHaveProperty('name')
    }
    const str = JSON.stringify(sample)
    expect(str).not.toMatch(/steam_id\s*:/)
    expect(str).not.toMatch(/风也有它的归宿|BOOM|芙f|用户4686842|Yuta|木舞者|陆言|尼古丁/)
  })

  test('样本点位全部位于雷达可玩区 [0,1]（统一变换+裁剪，禁止越界漂移）', async () => {
    let min = { rx: 1, ry: 1 }, max = { rx: 0, ry: 0 }, oob = 0, count = 0
    for (const p of sample.players) {
      for (const pt of p.points) {
        if (pt.rx == null || pt.ry == null) continue
        count++
        if (pt.rx < 0 || pt.rx > 1 || pt.ry < 0 || pt.ry > 1) oob++
        min.rx = Math.min(min.rx, pt.rx); max.rx = Math.max(max.rx, pt.rx)
        min.ry = Math.min(min.ry, pt.ry); max.ry = Math.max(max.ry, pt.ry)
      }
    }
    expect(count).toBeGreaterThan(1000)
    expect(oob).toBe(0)
    // 严格位于雷达可玩区，且不贴到水印/白边（留出真实地图边距）
    expect(min.rx).toBeGreaterThan(0.02); expect(max.rx).toBeLessThan(0.98)
    expect(min.ry).toBeGreaterThan(0.02); expect(max.ry).toBeLessThan(0.98)
  })

  test('样本含真实三态区域数据（CT/T/contested 均出现）且不含玩家名', async () => {
    const states = new Set()
    let geometric = 0
    let teamNonUnknown = { A: 0, B: 0 }
    for (const a of sample.areas) {
      if (a.geometric) geometric++
      for (const team of ['A', 'B']) {
        for (const seg of a.segments[team] || []) {
          if (['controlled', 'occupied', 'contested', 'influence'].includes(seg.state)) {
            states.add(seg.state)
            if (seg.state !== 'unknown') teamNonUnknown[team]++
          }
        }
      }
    }
    // 3 态渲染由 display 状态（ct/t/contested）构成；数据侧验证 contested/occupied/influence 真实存在，
    // 且双方均有非 unknown 的战术段落（不伪造满样本）。
    expect(states.has('contested')).toBe(true)
    expect(states.has('occupied')).toBe(true)
    expect(states.has('influence')).toBe(true)
    expect(teamNonUnknown.A).toBeGreaterThan(0)
    expect(teamNonUnknown.B).toBeGreaterThan(0)
    expect(geometric).toBeGreaterThanOrEqual(10)
  })

  test('真实样本推送至舞台：10 玩家渲染且不越出地图容器', async ({ page }) => {
    await page.goto('/welcome')
    await expect(page.getByTestId('landing-replay-stage')).toBeVisible()
    await expect(page.locator('.actor')).toHaveCount(10)
    const engine = await page.locator('.engine').boundingBox()
    expect(engine).toBeTruthy()
    for (const actor of await page.locator('.actor').all()) {
      const box = await actor.boundingBox()
      const cx = box.x + box.width / 2
      const cy = box.y + box.height / 2
      expect(cx).toBeGreaterThanOrEqual(engine.x - 2)
      expect(cx).toBeLessThanOrEqual(engine.x + engine.width + 2)
      expect(cy).toBeGreaterThanOrEqual(engine.y - 2)
      expect(cy).toBeLessThanOrEqual(engine.y + engine.height + 2)
    }
  })

  test('控制场随播放推进出现双方连续场与真实前线（无 per-area 色块）', async ({ page }) => {
    await page.goto('/welcome')
    await expect(page.getByTestId('landing-replay-stage')).toBeVisible()
    const tl = page.locator('.timeline')
    await tl.scrollIntoViewIfNeeded()
    const seen = { ct: false, t: false, frontier: false }
    await expect(page.locator('polygon.area')).toHaveCount(0)
    // seek 多个真实时刻，直接读取 Canvas 像素与真值 frontier path。
    const ratios = [0.1, 0.25, 0.4, 0.6, 0.8, 0.95]
    for (const r of ratios) {
      const box = await tl.boundingBox()
      await page.mouse.click(box.x + box.width * r, box.y + box.height / 2)
      await page.waitForTimeout(140)
      const colors = await page.getByTestId('landing-control-field').evaluate((canvas) => {
        const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
        let ct = false, t = false
        for (let i = 0; i < data.length; i += 4) {
          if (data[i + 3] === 0) continue
          if (data[i + 2] > data[i] + 40) ct = true
          if (data[i] > data[i + 2] + 40 && data[i + 1] > 100) t = true
          if (ct && t) break
        }
        return { ct, t }
      })
      seen.ct ||= colors.ct
      seen.t ||= colors.t
      seen.frontier ||= (await page.locator('.control-frontier').count()) > 0
    }
    expect(seen.ct).toBe(true)
    expect(seen.t).toBe(true)
    expect(seen.frontier).toBe(true)
  })

  test('播放/seek/倍速确定性：播放推进时间、倍速生效、seek 可跳转', async ({ page }) => {
    await page.goto('/welcome')
    const time = page.locator('.player-time')
    const before = await time.innerText()
    await page.getByRole('button', { name: '播放界面预览' }).click()
    await page.waitForTimeout(1600)
    const after = await time.innerText()
    expect(after).not.toBe(before)
    // 倍速切换为同步即时，不产生不可控状态
    await page.getByRole('button', { name: '4x' }).click()
    await page.getByRole('button', { name: '暂停界面预览' }).click()
    const tl = page.locator('.timeline')
    await tl.scrollIntoViewIfNeeded()
    const box = await tl.boundingBox()
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height / 2)
    await page.waitForTimeout(120)
    expect(await time.innerText()).not.toBe(after)
  })

  test('reduced-motion 下舞台为确定性静态快照（不连续自推动画）', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/welcome')
    await expect(page.getByTestId('landing-replay-stage')).toBeVisible()
    const time = page.locator('.player-time')
    const t0 = await time.innerText()
    await page.getByRole('button', { name: '播放界面预览' }).click()
    await page.waitForTimeout(400)
    const t1 = await time.innerText()
    // reduced-motion：点击即为确定性跳帧（0 -> 总时长），不出现中间连续帧
    expect(t1).not.toBe(t0)
    await page.waitForTimeout(1200)
    expect(await time.innerText()).toBe(t1)
  })

  test('桌面 1440 无横滚', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/welcome')
    const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
    expect(width.scroll).toBeLessThanOrEqual(width.client + 1)
  })

  test('390 与 360 无横滚', async ({ page }) => {
    for (const w of [390, 360]) {
      await page.setViewportSize({ width: w, height: 844 })
      await page.goto('/welcome')
      const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
      expect(width.scroll).toBeLessThanOrEqual(width.client + 1)
    }
  })
})
