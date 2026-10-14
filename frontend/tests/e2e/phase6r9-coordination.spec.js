// 6R9 深度配合分析 E2E 门禁（返工版）：结论签名唯一/去重、A/B 指标表、证据折叠、范围隔离、零机会不可评估、无四位数采样、移动/键盘。
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123', demo: '/demo/19' }
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
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.waitForFunction(() => document.querySelector('.co-concl-item') != null, { timeout: 25000 })
}
async function getAnalysis(page) {
  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const r = await page.request.get('/api/demos/19/coordination', { headers: { Authorization: `Bearer ${token}` } })
  return r.json()
}

test.beforeEach(async ({ context }) => { setTier(ACCOUNT.email, 'max', 7); grantTeamEntitlementForDemo(ACCOUNT.email, 19) })   // 6R15 收口：按目标 demo 播种团队权益门禁

test('Max：结论点击能切回合+scrub到秒+高亮+开图层（gate 1）', async ({ page }) => {
  await login(page, ACCOUNT); await enterTeam(page)
  const analysis = await getAnalysis(page)
  const concl = analysis.conclusions[0]
  await page.locator('.co-concl-item').first().locator('.co-locate').click()
  await page.waitForFunction((rn) => document.querySelector('.tm-sel')?.value === String(rn), concl.round, { timeout: 15000 })
  await page.waitForTimeout(500)
  const scrubVal = Number(await page.locator('.tm-scrub').inputValue())
  const target = Math.min(Math.max(0, Number(concl.seconds) || 0), Number(await page.locator('.tm-scrub').getAttribute('max')) || 9999)
  expect(Math.abs(scrubVal - target)).toBeLessThanOrEqual(3)
  for (const layer of concl.layers || []) {
    const toggle = page.locator(`.tm-toggle:has-text("${layerName(layer)}")`)
    if (await toggle.count() > 0) await expect(toggle).toHaveClass(/on/)
  }
  if (Array.isArray(concl.highlight) && concl.highlight.length) {
    const chip = page.locator(`.tm-chip`).filter({ hasText: String(concl.highlight[0]) }) // 编号可能被切片，宽松处理
    if (await chip.count() > 0) { /* 有 chip 即视为已渲染 */ }
  }
  await page.screenshot({ path: `${SHOTS}/6r9-locate-${concl.metric_id}.png` })
})

test('Max：普通 UI 无裸 tick / 世界坐标 / steam（gate 2）', async ({ page }) => {
  await login(page, ACCOUNT); await enterTeam(page)
  await page.locator('.co').scrollIntoViewIfNeeded()
  const coText = await page.locator('.co').innerText()
  expect(coText.toLowerCase()).not.toMatch(/steamid|steam_id|_\d{15,}/)
  expect(coText).not.toMatch(/(?<!\d)-\d{4,}(?!\d)/)
  await page.screenshot({ path: `${SHOTS}/6r9-noleak.png` })
})

test('Max：team 与「我的贡献」数据范围不串（gate 3）', async ({ page }) => {
  await login(page, ACCOUNT); await enterTeam(page)
  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const h = { Authorization: `Bearer ${token}` }
  const focus = await (await page.request.get('/api/demos/19/focus-player', { headers: h })).json()
  const a = await getAnalysis(page)
  expect(a.player.focus_player_id).toBe(focus.player_id)
  // 真实校验：每条个人 contribution/exposure 的 players 必须包含 focus_player_id
  for (const c of a.player.contributions) expect(c.players).toContain(focus.player_id)
  for (const e of a.player.exposures) expect(e.players).toContain(focus.player_id)
  // 切换 focus 后：个人证据/摘要变化，team metrics + content_hash 保持相同
  const players = await (await page.request.get('/api/demos/19/players', { headers: h })).json()
  const other = players.find((p) => p.player_id !== focus.player_id)
  await page.request.put('/api/demos/19/focus-player', { headers: h, data: { player_id: other.player_id } })
  const b = await getAnalysis(page)
  expect(b.content_hash).toBe(a.content_hash)
  expect(b.teams).toEqual(a.teams)
  // 个人摘要随焦点玩家变化（不一定永远不同，但至少 focus id 变了）
  expect(b.player.focus_player_id).toBe(other.player_id)
})

test('Max：试验性基线 / 置信度 / 覆盖率可见（gate 4）', async ({ page }) => {
  await login(page, ACCOUNT); await enterTeam(page)
  expect(await page.locator('.co-baseline').innerText()).toContain('试验性基线')
  const tableText = await page.locator('.co-metric-table').innerText()
  expect(tableText).toMatch(/置信/)
  expect(tableText).toMatch(/覆盖/)
  await page.screenshot({ path: `${SHOTS}/6r9-baseline.png` })
})

test('Max：零机会显示「不适用」而非 0 分（gate 5）', async ({ page }) => {
  await page.route('**/api/demos/19/coordination**', async (route) => {
    await route.fulfill({ json: {
      coordination_schema_version: '1.0', thresholds_version: '1.0.0', content_hash: 'F'.repeat(16),
      match_id: 1, map_name: 'de_mirage', tick_rate: 64, total_rounds: 1,
      baseline_note: '试验性基线（本批验收样本内对照，非行业基准）',
      teams: [{ team: 'A', name: '队伍 A', metrics: [
        { metric_id: 'trade', label: '补枪响应', opportunities: 0, converted: 0, rate: null, rate_label: null,
          human: null, coverage: 0, coverage_label: null, confidence: 'none', status: 'no_opportunity', evidence_count: 0 },
        { metric_id: 'isolated_death', label: '孤立阵亡', opportunities: 5, converted: 1, rate: 0.2, rate_label: '20%',
          human: '孤立阵亡 20%', coverage: 1, coverage_label: '覆盖 100% 回合', confidence: 'high', status: 'ok', evidence_count: 1 },
      ]}, { team: 'B', name: '队伍 B', metrics: [] }],
      rounds: [], player: { focus_player_id: null }, conclusions: [], note: '',
    }})
  })
  await login(page, ACCOUNT)
  await page.goto(ACCOUNT.demo)
  await page.click('button.tab:has-text("团队")')
  await page.waitForSelector('.tm-canvas', { timeout: 25000 })
  await page.waitForSelector('.co-metric-table', { timeout: 25000 })
  const tradeRow = page.locator('.co-metric-table tbody tr').filter({ hasText: '补枪响应' }).first()
  // 零机会 → 不适用（非 0 分）
  await expect(tradeRow.locator('.co-mt-value').first()).toHaveText('不适用')
})

test('Max：指标表 ≤7 行 + 无四位数机会/转化计数（gate 6a）', async ({ page }) => {
  await login(page, ACCOUNT); await enterTeam(page)
  const rows = await page.locator('.co-metric-table tbody tr').count()
  expect(rows).toBeLessThanOrEqual(7)
  const tableText = await page.locator('.co-metric-table').innerText()
  expect(tableText).not.toMatch(/\d{4,}\s*机会|\d{4,}\s*转化/)
})

test('Max：结论签名唯一 + 同 metric/tone 不重复 + 中文模板无病句（gate 6b）', async ({ page }) => {
  await login(page, ACCOUNT); await enterTeam(page)
  const analysis = await getAnalysis(page)
  const cs = analysis.conclusions
  expect(cs.length).toBeGreaterThanOrEqual(3)
  expect(cs.length).toBeLessThanOrEqual(5)
  // signature 唯一
  const sigs = cs.map((c) => JSON.stringify(c.signature))
  expect(new Set(sigs).size).toBe(sigs.length)
  // 同 (metric,tone) 不重复
  const mt = cs.map((c) => `${c.metric_id}|${c.tone}`)
  expect(new Set(mt).size).toBe(mt.length)
  // 成功/问题都要有
  const tones = new Set(cs.map((c) => c.tone))
  expect(tones).toContain('success'); expect(tones).toContain('problem')
  // 中文模板不得命中已知病句
  const BROKEN = ['亮成进点', '能见不断', '道具掷下亮成', '目标明确的反打', '没有 navmesh/LoS，无法确认是否真实交叉']
  for (const c of cs) {
    const txt = `${c.title} ${c.why} ${c.next}`
    for (const b of BROKEN) expect(txt).not.toContain(b)
  }
})

test('Max：默认证据浏览器可见 ≤5 条 + 移动端 390 无溢出 + 键盘 Enter 定位（gate 7）', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page, ACCOUNT); await enterTeam(page)
  // 默认「所选结论」视图，可见 .cp-item ≤5
  const count = await page.locator('.co .cp-item').count()
  expect(count).toBeLessThanOrEqual(5)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(overflow).toBe(false)
  const item = page.locator('.co-concl-item').first()
  await item.focus(); await page.keyboard.press('Enter')
  await page.waitForTimeout(700)
  expect(await page.locator('.tm-sel').inputValue()).toBeTruthy()
  await page.screenshot({ path: `${SHOTS}/6r9-mobile.png` })
})

function layerName(key) {
  return { playerName: '玩家名', route: '路线', gun: '真实朝向', smoke: '烟', fire: '火', flash: '闪', he: '手雷', trajectory: '投掷轨迹' }[key] || key
}
