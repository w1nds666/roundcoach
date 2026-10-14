// Phase 5A：AI 诊断报告 UI 垂直闭环（Fake provider，无外部请求）
// free 锁定 / pro 生成→渲染 / XSS 消毒 / 幂等
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'

const SHOTS = 'tests/e2e/screenshots'
const FREE = { email: 'phase4a@test.com', password: 'password123', demo: '/demo/6' }
// phase4a/demo6 is the dedicated parsed fixture: 73 kills + 142 grenades and
// no pre-seeded AI report. Each test sets its own tier and the hook restores it.
const PRO = { email: 'phase4a@test.com', password: 'password123', demo: '/demo/6' }
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

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

test.describe.serial('Phase 5A AI 诊断验收（隔离夹具）', () => {
  test.afterEach(() => { restoreFree(PRO.email) })

  test('free：AI 诊断锁定，且不发起创建请求', async ({ page }) => {
    const created = []
    page.on('request', (req) => { if (/\/ai-report$/.test(req.url()) && req.method() === 'POST') created.push(req.url()) })
    await login(page, FREE)
    await page.goto(FREE.demo)
    await page.click('button.tab:has-text("AI 教练")')
    await expect(page.locator('.locked-box').filter({ visible: true })).toBeVisible({ timeout: 10000 })
    expect(created).toEqual([])   // 锁定态不得请求创建
    await page.screenshot({ path: `${SHOTS}/fix-5a-ai-locked.png` })
  })

  test('pro：生成 AI 诊断（fake）→ 被真实性门禁拦截 → 诚实面板，不渲染正文/模板键', async ({ page }) => {
    page.on('pageerror', (e) => console.log('PAGEERROR:', e.message))
    page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE:', m.text()) })
    setSubscription(PRO.email, 'pro', "now() + interval '1 hour'")
    await login(page, PRO)
    await page.goto(PRO.demo)
    await expect(page.locator('.map-name')).toHaveText(/de_mirage/, { timeout: 20000 })
    await page.click('button.tab:has-text("AI 教练")')

    // 若已有报告则直接判定；否则生成（fake 离线生成）
    if (await page.locator('.ai-suspect, .md-body').count() === 0) {
      const genBtn = page.locator('button:has-text("开始生成"), button:has-text("重新生成")').first()
      if (await genBtn.count() > 0) await genBtn.click()
    }
    // 团队真实性门禁：fake provider → 不渲染正文，只显示诚实「测试/过期」面板
    await page.waitForSelector('.ai-suspect', { timeout: 25000 })
    expect(await page.locator('.ai-suspect .md-body').count()).toBe(0)
    expect(await page.locator('.md-body').count()).toBe(0)
    const text = await page.locator('.ai-suspect').innerText()
    expect(text).toMatch(/测试|过期|暂不可用|模板/)
    expect(text).toMatch(/重新生成/)
    // 不得泄漏裸模板键
    expect(text).not.toMatch(/\[[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+]/)
    await page.screenshot({ path: `${SHOTS}/fix-5a-ai-report.png`, fullPage: true })
  })

  test('pro：幂等——重复创建复用同一报告', async ({ page, request }) => {
    setSubscription(PRO.email, 'pro', "now() + interval '1 hour'")
    await login(page, PRO)
    const token = await page.evaluate(() => localStorage.getItem('cs2_token'))
    const h = { Authorization: `Bearer ${token}` }
    const r1 = await request.post('/api/demos/6/ai-report', { headers: h })
    const r2 = await request.post('/api/demos/6/ai-report', { headers: h })
    const b1 = await r1.json()
    const b2 = await r2.json()
    // 幂等：第二次必须复用第一次的报告（同一 report_id），无论第一次是否新建
    expect(r1.status()).toBe(200)
    expect(b2.reused).toBe(true)
    expect(b2.report.id).toBe(b1.report.id)
    expect(r2.status()).toBe(200)
    expect(b1.report.id).toBe(b2.report.id)
  })
})
