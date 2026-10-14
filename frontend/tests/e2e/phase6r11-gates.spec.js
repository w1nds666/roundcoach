// 6R11 视觉/结构门禁（返工版）：针对 Login/Upload/Team/Share/AI Coach 的结构断言 + after 截图矩阵。
// before 不在此生成（core before 引用既有 6R10-vision committed 截图，见 docs）。
// 运行：npx playwright test -c playwright.headless.config.js phase6r11-gates
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, demoPlayerSteam, grantTeamEntitlementForDemo } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots/6r11'
const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
const DEMO = '/demo/19'
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
const DEMOS = { mirage: 19, anubis: 20, dust2: 21 }

// 6R14 身份优先：个人复盘需先绑定 demo19 首位玩家作「我」
function seedDemo19() { seedIdentity(ACCOUNT.email, demoPlayerSteam(ACCOUNT.email, 19)) }

function setTier(email, tier, days) {
  const exp = days ? `, subscription_expire = now() + interval '${days} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}

async function login(page) {
  await page.goto('/login')
  await page.fill('input[type="email"]', ACCOUNT.email)
  await page.fill('input[type="password"]', ACCOUNT.password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

function hScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
}
async function expectNoHorizScroll(page, ctx) {
  const over = await hScroll(page)
  expect(over, `${ctx} 横向溢出 ${over}px`).toBeLessThanOrEqual(1)
}

async function openPersonal(page, width) {
  await page.goto(DEMO)
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.picker select', { timeout: 20000 })
  await page.waitForSelector('.summary', { timeout: 20000 })
  await expectNoHorizScroll(page, `personal@${width}`)
}

test('360px 移动端：个人复盘不横向溢出，长玩家名 ellipsis/两行受控', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDemo19()
  await login(page)
  await page.setViewportSize({ width: 360, height: 780 })
  await openPersonal(page, 360)
  const nameEl = page.locator('.sum-name')
  const nameStyle = await nameEl.evaluate((el) => {
    const s = getComputedStyle(el)
    return { ws: s.whiteSpace, overflow: s.overflow, textOverflow: s.textOverflow, lineClamp: s.webkitLineClamp }
  })
  const clamped = nameStyle.lineClamp && nameStyle.lineClamp !== 'none'
  const ellipsed = nameStyle.overflow === 'hidden' && nameStyle.textOverflow === 'ellipsis' && nameStyle.ws === 'nowrap'
  expect(clamped || ellipsed, `名字未做截断保护: ${JSON.stringify(nameStyle)}`).toBe(true)
  await nameEl.evaluate((el) => { el.textContent = '这个玩家名字长得非常非常离谱想看看会不会把布局撑爆超出屏幕边界'.repeat(3) })
  await page.waitForTimeout(120)
  await expectNoHorizScroll(page, 'long-name@360')
})

test('360px 移动端：Rating 置信/覆盖 pill 不逐字换行（单行 nowrap）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  seedDemo19()
  await login(page)
  await page.setViewportSize({ width: 360, height: 780 })
  await openPersonal(page, 360)
  const pills = page.locator('.rating-card .rc-meta .pill')
  const n = await pills.count()
  expect(n).toBeGreaterThanOrEqual(2)
  for (let i = 0; i < n; i++) {
    const el = pills.nth(i)
    const st = await el.evaluate((e) => {
      const s = getComputedStyle(e)
      return { ws: s.whiteSpace, h: e.offsetHeight, w: e.offsetWidth }
    })
    expect(st.ws, `pill#${i} 未 nowrap: ${JSON.stringify(st)}`).toBe('nowrap')
    expect(st.h, `pill#${i} 高度异常（折行）`).toBeLessThan(40)
    expect(st.w).toBeLessThanOrEqual(360)
  }
  await expectNoHorizScroll(page, 'rating-pill@360')
})

test('360px 移动端：主操作按钮触控区 >= 44px 且不横向溢出', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  await page.setViewportSize({ width: 360, height: 780 })
  await page.goto('/')
  await page.waitForSelector('.head-row .btn.btn-primary', { timeout: 15000 })
  await expectNoHorizScroll(page, 'home@360')
  const bbox = await page.locator('.head-row .btn.btn-primary').first().boundingBox()
  expect(Math.max(bbox.width, bbox.height), '主按钮触控区 < 44px').toBeGreaterThanOrEqual(44)
})

test('键盘可用：Tab 聚焦按钮出现可见 :focus-visible 轮廓', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  await page.goto(DEMO)
  await page.waitForSelector('.tabs .tab', { timeout: 20000 })
  await page.locator('.tabs .tab').first().focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Tab')
  const focused = await page.evaluate(() => {
    const el = document.activeElement
    if (!el) return null
    const s = getComputedStyle(el)
    return { fv: el.matches(':focus-visible'), outline: s.outlineStyle, outlineW: s.outlineWidth }
  })
  expect(focused).toBeTruthy()
  expect(focused.fv, `非 focus-visible: ${JSON.stringify(focused)}`).toBe(true)
  expect(focused.outline).not.toBe('none')
  expect(parseFloat(focused.outlineW)).toBeGreaterThan(0)
})

test('reduced-motion：控件无自动动画（transition/animation 时长为 0）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(DEMO)
  await page.waitForSelector('.tabs .tab', { timeout: 20000 })
  const anim = await page.locator('.tabs .tab').first().evaluate((el) => {
    const s = getComputedStyle(el)
    return { t: s.transitionDuration, a: s.animationDuration }
  })
  expect(anim.t, `transition 未禁: ${JSON.stringify(anim)}`).toBe('0s')
  expect(anim.a, `animation 未禁: ${JSON.stringify(anim)}`).toBe('0s')
})

test('桌面 1440：报告头为紧凑记分牌分组，无横向溢出', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(DEMO)
  await page.waitForSelector('.head', { timeout: 20000 })
  await expectNoHorizScroll(page, 'report-head@1440')
  const headText = await page.locator('.head').innerText()
  expect(headText).toMatch(/Mirage|mirage/)
  expect(headText).toMatch(/分钟/)
  expect(headText).toMatch(/回合/)
})

test('Login：页面级「档案入口」结构（品牌+表单+隐私说明+错误态），无横向溢出', async ({ page }) => {
  await page.context().clearCookies()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/login')
  await page.waitForSelector('.auth-card', { timeout: 15000 })
  await expect(page.locator('.auth-card .brand')).toBeVisible()   // 品牌块
  await expect(page.locator('.auth-card .form .input')).toHaveCount(2)  // 邮箱+密码
  await expect(page.locator('.auth-note')).toBeVisible()       // 隐私/说明
  await expect(page.locator('.form-error-box, .field-error')).toHaveCount(0)  // 初始无错误态
  await expectNoHorizScroll(page, 'login@1440')
  await page.setViewportSize({ width: 360, height: 780 })
  await expectNoHorizScroll(page, 'login@360')
})

test('Upload：文件选择/拖拽 + 解析流程 + 隐私说明 + 返回档案柜 结构', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/upload')
  await page.waitForSelector('.dropzone', { timeout: 15000 })
  await expect(page.locator('.dropzone input[type="file"]')).toHaveCount(1)
  await expect(page.locator('.flow')).toBeVisible()           // 3 步流程
  await expect(page.locator('.flow-step')).toHaveCount(3)
  await expect(page.locator('.upload-privacy')).toBeVisible()  // 隐私说明
  await expect(page.locator('.page-head a[href="/"]')).toBeVisible()  // 返回档案柜
  await expectNoHorizScroll(page, 'upload@1440')
  await page.setViewportSize({ width: 360, height: 780 })
  await expectNoHorizScroll(page, 'upload@360')
})

test('Team：紧凑两队摘要条 → 同步回放首个主体 → 七项 A/B 配合表', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)   // 6R15 收口：按目标 demo 播种团队权益门禁
  await login(page)
  await page.goto(DEMO)
  await page.click('button.tab:has-text("团队复盘")')
  await page.waitForSelector('.team-summary', { timeout: 20000 })
  // 同步回放在深度配合（A/B 表）之前
  const replayIdx = await page.evaluate(() => {
    const labels = [...document.querySelectorAll('.row-title')].map((el) => el.textContent)
    return labels.findIndex((t) => t.includes('团队同步回放'))
  })
  const coordIdx = await page.evaluate(() => {
    const labels = [...document.querySelectorAll('.row-title')].map((el) => el.textContent)
    return labels.findIndex((t) => t.includes('深度配合指标'))
  })
  expect(replayIdx).toBeGreaterThanOrEqual(0)
  expect(coordIdx).toBeGreaterThan(replayIdx)
  await page.waitForSelector('.co-metric-table', { timeout: 20000 })  // A/B 表
  const rows = await page.locator('.co-metric-table tbody tr').count()
  expect(rows).toBeGreaterThan(0)
  await expectNoHorizScroll(page, 'team@1440')
})

test('Share：可扫读榜单 + Rating 说明仅一次 + 无重复解释墙', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const resp = await page.request.post('/api/demos/19/share', {
    headers: { Authorization: `Bearer ${token}` }, data: { expires_in_hours: 24 },
  })
  const shareToken = (await resp.json()).token
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/share#${shareToken}`)
  // Await the resolved Share view, not a generic leaderboard during route startup.
  await expect(page.locator('.share-page .lb')).toBeVisible({ timeout: 15000 })
  // 说明只出现一次：仅 `.rating-note` 一处；每行不再重复长解释
  await expect(page.locator('.share-page .rating-note')).toHaveCount(1)
  await expect(page.locator('.share-page .rating-note')).toBeVisible()
  const rows = await page.locator('.lb-row').count()
  expect(rows).toBeGreaterThan(1)     // 含表头 + 至少一名玩家
  // 不在榜单行里出现整段解释（评分口径细节）
  const firstRow = await page.locator('.lb-row').nth(1).innerText()
  expect(firstRow).not.toMatch(/权重|公式|五维加权/)
  await expectNoHorizScroll(page, 'share@1440')
})

test('AI Coach：诚实 preview/空态 + fake/过期报告被拦 + 不是通用聊天框', async ({ page }) => {
  setTier(ACCOUNT.email, 'pro', 7)
  await login(page)
  await page.goto(DEMO)
  await page.click('button.tab:has-text("AI 教练")')
  await page.waitForSelector('.ai-preview, .locked-box, .md-body, .ai-suspect', { timeout: 20000 })
  // 不出现聊天输入框
  expect(await page.locator('textarea, input[type="text"]').count()).toBe(0)
  // 页面主文本不得出现裸 [a.b] 模板键（fake/stale 报告不渲染正文）
  const allText = await page.locator('.ai-preview, .ai-suspect, .md-body, .locked-box').first().innerText().catch(() => '')
  expect(allText).not.toMatch(/\[[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+]/)
  // demo19 存在 fake/过期报告 → 显示诚实「不可用」面板 + 中文提示 + 操作
  if (await page.locator('.ai-suspect').count() > 0) {
    const suspect = await page.locator('.ai-suspect').innerText()
    expect(suspect).toMatch(/测试|过期|暂不可用|模板/)
    expect(suspect).toMatch(/重新生成/)
    expect(suspect).toMatch(/个人复盘|团队复盘/)
  }
  await expectNoHorizScroll(page, 'ai@1440')
})

test('AI Coach 真实性门禁：fake/过期报告被拦（头部 6:13 不显示旧正文 9:10），且不渲染正文/模板键', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  await page.goto(DEMO)
  await page.click('button.tab:has-text("AI 教练")')
  await page.waitForSelector('.ai-suspect', { timeout: 20000 })
  const pageText = await page.locator('.ai-suspect').innerText()
  // 不出现旧正文的矛盾比分 9:10（头部是 6:13）
  expect(pageText).not.toMatch(/9\s*:\s*10/)
  // 不出现裸模板键
  expect(pageText).not.toMatch(/\[[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+]/)
  // 正文主体未被渲染
  expect(await page.locator('.ai-suspect .md-body').count()).toBe(0)
  // 中文提示与操作
  expect(pageText).toMatch(/测试|过期|暂不可用/)
  expect(pageText).toMatch(/重新生成/)
})

test('AI Coach 真实性门禁：真实且匹配的 trusted 报告仍可正常展示（fixture）', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  await login(page)
  // demo20（anubis）专用夹具：创建报告后改为「真实 provider + 干净正文 + 匹配当前数据」
  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const demos = await (await page.request.get('/api/demos?limit=100', { headers: { Authorization: `Bearer ${token}` } })).json()
  const d = demos.find((x) => x.map_name === 'de_anubis') || demos.find((x) => x.id == 20)
  const demoId = d ? d.id : 20
  const doc = await (await page.request.get(`/api/demos/${demoId}/report/basic`, { headers: { Authorization: `Bearer ${token}` } })).json()
  const m = doc.match
  const created = await (await page.request.post(`/api/demos/${demoId}/ai-report`, { headers: { Authorization: `Bearer ${token}` } })).json()
  const rid = created.report.id
  expect(rid).toBeTruthy()
  // 等 Celery worker 完成生成（终态）再覆写，避免竞态
  for (let i = 0; i < 30; i++) {
    const st = await (await page.request.get(`/api/demos/${demoId}/ai-report/${rid}`, { headers: { Authorization: `Bearer ${token}` } })).json()
    if (st.status === 'succeeded' || st.status === 'failed') break
    await page.waitForTimeout(500)
  }
  const cleanMd = `## 摘要\n\n本场 ${m.map_name} 共 ${m.rounds} 回合，比分 ${m.team_a_score}:${m.team_b_score}。这是真实且匹配的复盘结论示例（fixture）。\n\n## 优势（1）\n\n1. 数据一致。\n\n## 问题（1）\n\n1. 无。\n\n## 可执行建议\n\n1. 继续。\n\n## 证据\n\n- match.*\n`
  // psql 单行命令：用 E'...' 让 \n 存成真换行；转义单引号与反斜杠
  const mdEsc = cleanMd.replace(/\\/g, '\\\\').replace(/'/g, "''").replace(/\n/g, '\\n')
  execSync(
    `"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE ai_reports SET provider='test-llm', model='summary-v2', markdown=E'${mdEsc}', status='succeeded' WHERE id=${rid};"`,
    { stdio: 'pipe' },
  )
  await page.goto(`/demo/${demoId}`)
  await page.click('button.tab:has-text("AI 教练")')
  await page.waitForSelector('.md-body', { timeout: 20000 })
  const body = await page.locator('.md-body').innerText()
  expect(body).toContain('真实且匹配')
  expect(body).toContain(m.map_name)
  expect(body).not.toMatch(/\[[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+]/)
  await page.screenshot({ path: `${SHOTS}/after-ai-coach-trusted-fixture.png` })
})

test('截图矩阵（after）：Login/Upload/Personal/Team/Share/AI Coach 桌面+移动 + 三地图 Personal/Team', async ({ page }) => {
  setTier(ACCOUNT.email, 'max', 7)
  // --- Login (未登录) ---
  await page.context().clearCookies()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/login')
  await page.waitForSelector('.auth-card')
  await page.screenshot({ path: `${SHOTS}/after-login-desktop.png` })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: `${SHOTS}/after-login-mobile.png` })

  await login(page)

  // --- Dashboard ---
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.waitForSelector('.deck .deck-row, .empty-state')
  await page.screenshot({ path: `${SHOTS}/after-home-desktop.png` })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.waitForSelector('.deck .deck-row, .empty-state')
  await page.screenshot({ path: `${SHOTS}/after-home-mobile.png` })

  // --- Upload ---
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/upload')
  await page.waitForSelector('.dropzone')
  await page.screenshot({ path: `${SHOTS}/after-upload-desktop.png` })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/upload')
  await page.waitForSelector('.dropzone')
  await page.screenshot({ path: `${SHOTS}/after-upload-mobile.png` })

  // --- Personal/Team 三地图（桌面）---
  for (const [map, id] of Object.entries(DEMOS)) {
    grantTeamEntitlementForDemo(ACCOUNT.email, id)   // 6R15 收口：按目标 demo 播种团队权益门禁
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`/demo/${id}`)
    await page.waitForSelector('.head')
    // 个人：设个焦点玩家以渲染完整摘要
    const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
    const playersResp = await page.request.get(`/api/demos/${id}/players`, { headers: { Authorization: `Bearer ${token}` } })
    const players = await playersResp.json()
    const fp = (players[0] || {}).player_id
    if (fp) await page.request.put(`/api/demos/${id}/focus-player`, { headers: { Authorization: `Bearer ${token}` }, data: { player_id: fp } })
    await page.click('button.tab:has-text("个人复盘")')
    await page.waitForSelector('.picker select', { timeout: 20000 })
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${SHOTS}/after-personal-${map}-desktop.png` })
    await page.click('button.tab:has-text("团队复盘")')
    await page.waitForSelector('.team-summary', { timeout: 20000 })
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${SHOTS}/after-team-${map}-desktop.png` })
  }

  // --- Personal/Team 移动（mirage）---
  grantTeamEntitlementForDemo(ACCOUNT.email, 19)   // 6R15 收口：移动段回到 demo19，重播种团队权益门禁
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(DEMO)
  await page.click('button.tab:has-text("个人复盘")')
  await page.waitForSelector('.picker select', { timeout: 20000 })
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/after-personal-mobile.png` })
  await page.click('button.tab:has-text("团队复盘")')
  await page.waitForSelector('.team-summary', { timeout: 20000 })
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/after-team-mobile.png` })

  // --- AI Coach（mirage 桌面+移动）---
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(DEMO)
  await page.click('button.tab:has-text("AI 教练")')
  await page.waitForSelector('.ai-preview, .locked-box, .md-body, .ai-suspect', { timeout: 20000 })
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/after-ai-coach-desktop.png` })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: `${SHOTS}/after-ai-coach-mobile.png` })

  // --- Share（匿名）---
  const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
  const shareResp = await page.request.post('/api/demos/19/share', {
    headers: { Authorization: `Bearer ${token}` }, data: { expires_in_hours: 24 },
  })
  const shareToken = (await shareResp.json()).token
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/share#${shareToken}`)
  await page.waitForSelector('.lb', { timeout: 15000 })
  await page.screenshot({ path: `${SHOTS}/after-share-desktop.png` })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/share#${shareToken}`)
  await page.waitForSelector('.lb', { timeout: 15000 })
  await page.screenshot({ path: `${SHOTS}/after-share-mobile.png` })
})
