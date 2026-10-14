// 6R17 视觉结构 strict E2E（新增门禁）：design system 2.0 的「反 AI 模板 + 结论→证据→回放动作 + 无障碍」硬指标。
// 运行：npx playwright test -c playwright.headless.config.js phase6r17.spec.js
// 覆盖：全局反模板(无玻璃/霓虹)、tokens 复用、概览结论→证据层级、说明墙折叠、主地图优先、
//       卡片/胶囊密度上限、移动 390/360 无横滚/长名不折行、focus/reduced-motion、Free/Pro/Max 锁定态、诚实态。
import { test, expect } from '@playwright/test'
import { login, ACCOUNT, DEMO19, setTier, seedIdentity, demoPlayerSteam } from './_helpers6r15.js'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const DEMO = '/demo/19'

function seedDemo19() { seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, 19)) }

function hScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
}
async function expectNoHorizScroll(page, ctx) {
  const over = await hScroll(page)
  expect(over, `${ctx} 横向溢出 ${over}px`).toBeLessThanOrEqual(1)
}
async function openTab(page, label) {
  await page.goto(DEMO)
  await page.waitForSelector('.tabs .tab', { timeout: 20000 })
  await page.click(`button.tab:has-text("${label}")`)
}

test('全局反模板：无玻璃拟态(backdrop-filter) 与霓虹渐变；主背景为语义 token', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDemo19()
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await page.goto(DEMO)
  await page.waitForSelector('.head', { timeout: 20000 })
  // 1) 全局无 backdrop-filter（禁玻璃拟态）
  const glass = await page.evaluate(() => {
    const bad = []
    const all = document.querySelectorAll('*')
    for (const el of all) {
      const s = getComputedStyle(el)
      const bf = s.backdropFilter || s.webkitBackdropFilter || ''
      if (bf && bf !== 'none') bad.push({ cls: el.className, bf })
      if (bad.length > 6) break
    }
    return bad
  })
  expect(glass, `检测到玻璃拟态: ${JSON.stringify(glass)}`).toEqual([])
  // 2) 主背景来自 --bg-base token（语义，非裸硬编码霓虹）
  const bg = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement)
    const body = getComputedStyle(document.body)
    const tmp = document.createElement('div'); tmp.style.background = root.getPropertyValue('--bg-base'); document.body.appendChild(tmp)
    const tokenRgb = getComputedStyle(tmp).backgroundColor; tmp.remove()
    return { tokenRgb: body ? tokenRgb : '', tokenRaw: root.getPropertyValue('--bg-base').trim(), body: body.backgroundColor }
  })
  expect(bg.tokenRaw).toBeTruthy()
  expect(bg.body, `背景非 token 解析值`).toBe(bg.tokenRgb)
})

test('tokens 复用：关键文本/主按钮颜色来自 --text-primary / --accent 变量', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDemo19()
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await page.goto(DEMO)
  await page.waitForSelector('.conclusion-strip', { timeout: 20000 })
  const tok = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement)
    const cs = document.querySelector('.conclusion-strip .cs-title')
    const btn = document.querySelector('.conclusion-strip .cs-actions .btn-primary')
    const tmp = document.createElement('div'); tmp.style.background = root.getPropertyValue('--accent'); document.body.appendChild(tmp)
    const accentRgb = getComputedStyle(tmp).backgroundColor; tmp.remove()
    return {
      csColor: cs ? getComputedStyle(cs).color : '',
      accentRgb,
      btnBg: btn ? getComputedStyle(btn).backgroundColor : '',
    }
  })
  // cs-title 用 --text-primary（继承 body）
  expect(tok.csColor).toBeTruthy()
  // 主按钮背景用 --accent（琥珀）
  expect(tok.btnBg).toBeTruthy()
  expect(tok.btnBg).toBe(tok.accentRgb)
})

test('概览「结论→证据」层级：conclusion-strip 位于记分表之前，且首屏突出关键观察', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDemo19()
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await openTab(page, '概览')
  await page.waitForSelector('.conclusion-strip', { timeout: 20000 })
  const order = await page.evaluate(() => {
    const sec = [...document.querySelectorAll('section')].find((s) => s.querySelector('.conclusion-strip'))
    if (!sec) return null
    const strip = sec.querySelector('.conclusion-strip')
    const table = sec.querySelector('.table-card')
    const obs = sec.querySelector('.key-observations')
    if (!strip || !table) return null
    const stripBeforeTable = !!(strip.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING)
    return { stripBeforeTable, obsCount: obs ? obs.querySelectorAll('.obs-line').length : 0 }
  })
  expect(order, '概览未形成「结论→证据」层级').toBeTruthy()
  expect(order.stripBeforeTable).toBe(true)
  expect(order.obsCount, '概览应至少 3 条关键观察').toBeGreaterThanOrEqual(3)
})

test('说明墙折叠：团队回放长口径收进 <details>，主界面仅 2 条短提示', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await openTab(page, '团队复盘')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  // 图例为可折叠 details（默认收起，不挤压地图）
  const legend = await page.locator('.tm-legend.fold').count()
  expect(legend, '长口径说明应折叠为 details').toBeGreaterThanOrEqual(1)
  const open = await page.locator('.tm-legend.fold[open]').count()
  expect(open, '图例默认应收起').toBe(0)
  // 直接可见的 .tm-hint（非 details 内）不多于 2 条
  const hints = await page.evaluate(() => {
    return [...document.querySelectorAll('.tm-hint')].filter((h) => !h.closest('details')).length
  })
  expect(hints).toBeLessThanOrEqual(2)
})

test('主地图优先：团队回放 canvas 为主要视觉（桌面尺寸大）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await page.setViewportSize({ width: 1440, height: 900 })
  await openTab(page, '团队复盘')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  const box = await page.locator('.tm-canvas').boundingBox()
  expect(box, '地图 canvas 未渲染').toBeTruthy()
  expect(box.width, '主地图宽度不足（被说明墙挤压）').toBeGreaterThanOrEqual(560)
  expect(box.height).toBeGreaterThanOrEqual(360)
})

test('卡片/胶囊密度上限：概览/团队复盘不用同尺寸卡片墙', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDemo19()
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await openTab(page, '概览')
  await page.waitForSelector('.conclusion-strip', { timeout: 20000 })
  const cardsOverview = await page.evaluate(() => [...document.querySelectorAll('.card')].filter((c) => {
    if (c.closest('details:not([open])')) return false
    const r = c.getBoundingClientRect()
    return r.width > 0 && r.height > 0
  }).length)
  expect(cardsOverview, '概览可见卡片密度超上限').toBeLessThanOrEqual(3)
  await openTab(page, '团队复盘')
  await page.waitForSelector('.team-summary, .tr-card', { timeout: 25000 })
  // 团队主要容器用 .tr-card/.prim-card/.tcm-card 而非全是 .card
  const nonCard = await page.evaluate(() => {
    return ['tr-card', 'prim-card', 'tcm-card'].filter((c) => document.querySelector('.' + c)).length
  })
  expect(nonCard, '团队复盘未按板块分隔（仍是卡片墙）').toBeGreaterThanOrEqual(1)
})

test('移动 390/360：概览/个人/团队无横向滚动，长玩家名不逐字换行', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDemo19()
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  for (const width of [390, 360]) {
    await page.setViewportSize({ width, height: 844 })
    await openTab(page, '概览')
    await page.waitForSelector('.conclusion-strip', { timeout: 20000 })
    await expectNoHorizScroll(page, `overview@${width}`)
    await openTab(page, '个人复盘')
    await page.waitForSelector('.picker select', { timeout: 20000 }).catch(() => {})
    await expectNoHorizScroll(page, `personal@${width}`)
    // 长玩家名保护
    const nameEl = page.locator('.sum-name')
    if (await nameEl.count()) {
      const st = await nameEl.first().evaluate((el) => {
        const s = getComputedStyle(el)
        return { ws: s.whiteSpace, overflow: s.overflow, lineClamp: s.webkitLineClamp }
      })
      const protectedName = st.overflow === 'hidden' || (st.lineClamp && st.lineClamp !== 'none')
      expect(protectedName, `长名字未做截断保护@${width}: ${JSON.stringify(st)}`).toBe(true)
    }
    await openTab(page, '团队复盘')
    await page.waitForSelector('.tm-canvas', { timeout: 25000 }).catch(() => {})
    await expectNoHorizScroll(page, `team@${width}`)
  }
})

test('focus/reduced-motion：Tab 聚焦出现可见 outline；减动效时无动画时长', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDemo19()
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await page.goto(DEMO)
  await page.waitForSelector('.tabs .tab', { timeout: 20000 })
  await page.locator('.tabs .tab').first().focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Tab')
  const focused = await page.evaluate(() => {
    const el = document.activeElement
    if (!el) return null
    const s = getComputedStyle(el)
    return { fv: el.matches(':focus-visible'), outline: s.outlineStyle, w: s.outlineWidth }
  })
  expect(focused).toBeTruthy()
  expect(focused.fv, '非 focus-visible').toBe(true)
  expect(focused.outline).not.toBe('none')
  expect(parseFloat(focused.w)).toBeGreaterThan(0)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const anim = await page.locator('.tabs .tab').first().evaluate((el) => {
    const s = getComputedStyle(el)
    return { t: s.transitionDuration, a: s.animationDuration }
  })
  expect(anim.t).toBe('0s')
  expect(anim.a).toBe('0s')
})

test('Free/Pro/Max 分层：锁定态清楚且不羞辱（不把 unavailable 伪装成 0）', async ({ page }) => {
  // Free：个人复盘锁定，展示「升级后解锁」而非 0 分
  setTier(ACCOUNT.email, 'free', 0)
  seedDemo19()
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await openTab(page, '个人复盘')
  await page.waitForSelector('.locked-box', { timeout: 20000 })
  const freeText = await page.locator('.locked-box').first().innerText()
  expect(freeText).toMatch(/进阶|升级|解锁/)
  expect(freeText).not.toMatch(/K\/D|评级|Rating 0|0\.0/)
  // Max：个人复盘可看（出现摘要/选择器），团队复盘出现（不锁定）
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await openTab(page, '个人复盘')
  await page.waitForSelector('.picker select, .summary', { timeout: 20000 })
  await openTab(page, '团队复盘')
  await page.waitForSelector('.tm-canvas, .team-summary', { timeout: 25000 })
  const teamLocked = await page.locator('.team-summary, .tm-canvas').count()
  expect(teamLocked, 'Max 团队复盘被错误锁定').toBeGreaterThan(0)
})

test('诚实态：loading/error/empty 元件存在且不伪造', async ({ page }) => {
  // Share 一个无效 token → 诚实 error 空态
  await page.context().clearCookies()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/share#invalid-token-xxx')
  await page.waitForSelector('.state:has-text("无法打开"), .state:has-text("失效"), .state:has-text("无效"), .error-state', { timeout: 15000 })
  const shareText = await page.locator('.state, .error-state').first().innerText()
  expect(shareText).toMatch(/无法打开|失效|无效/)
})

test('团队回放地图为首屏主角（桌面：高度占比达标 + 首屏可见）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await page.setViewportSize({ width: 1440, height: 900 })
  await openTab(page, '团队复盘')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  const box = await page.locator('.tm-canvas').boundingBox()
  expect(box, '回放地图未渲染').toBeTruthy()
  expect(box.height, '桌面回放地图高度占比不足').toBeGreaterThanOrEqual(0.5 * 900)
  expect(box.y, '回放地图未在首屏可见').toBeLessThan(900)
})

test('移动 390/360：团队复盘可达地图与播放入口', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  for (const w of [390, 360]) {
    await page.setViewportSize({ width: w, height: 844 })
    await openTab(page, '团队复盘')
    await page.waitForSelector('.tm-canvas', { timeout: 25000 }).catch(() => {})
    const play = await page.locator('.tm-play').boundingBox()
    expect(play, `移动@${w} 播放入口不可见`).toBeTruthy()
    expect(play.y, `移动@${w} 播放入口未在首屏`).toBeLessThan(844)
  }
})

test('Coach：默认可见卡片密度受限 + 重复问题默认前 3 行', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page, ACCOUNT.email, ACCOUNT.password)
  await page.goto('/coach')
  await page.waitForSelector('.top3-list, .issue-ledger, .sum-card', { timeout: 20000 })
  const cards = await page.evaluate(() => [...document.querySelectorAll('.card')].filter((c) => { const r = c.getBoundingClientRect(); return r.width > 0 && r.height > 0 }).length)
  expect(cards, 'Coach 默认可见卡片密度超限').toBeLessThanOrEqual(2)
  const rows = await page.locator('.issue-row').count()
  expect(rows, 'Coach 重复问题默认超过 3 行').toBeLessThanOrEqual(3)
})
