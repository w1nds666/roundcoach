// 6R1 E2E：「我的」Player-first 闭环（free 锁定 / pro 可见 / 击杀死亡切换 / CT-T 过滤 / 无 steam_id）
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'
import { seedIdentity, demoPlayerSteam } from './_helpers6r14.js'

const SHOTS = 'tests/e2e/screenshots/phase6r1'
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

function setTier(email, tierName, expireDays) {
  const exp = expireDays ? `, subscription_expire = now() + interval '${expireDays} days'` : ', subscription_expire = NULL'
  execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -c "UPDATE users SET subscription_tier='${tierName}'${exp} WHERE email='${email}';"`, { stdio: 'pipe' })
}

async function login(page, email) {
  await page.goto('/login')
  await page.fill('input[type="email"]', email)
  await page.fill('input[type="password"]', 'password123')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

test.describe('6R1 我的 Tab', () => {
  test('free：锁定态 + 选择器可用 + 无 steam_id', async ({ page }) => {
    await login(page, 'phase4a@test.com')   // free
    await page.goto('/demo/6')               // phase4a 的 demo
    await page.click('button.tab:has-text("个人复盘")')

    // 选择器存在（等待玩家列表填充：至少出现 1 个真实玩家 option，避免加载竞态只读到占位项）
    await expect(page.locator('.picker select')).toBeVisible({ timeout: 10000 })
    await page.waitForFunction(() => document.querySelectorAll('.picker select option').length > 1, { timeout: 15000 })
    const opts = await page.locator('.picker select option').allTextContents()
    expect(opts.some((o) => o.includes('（'))).toBe(true)
    // 锁定态（服务端 entitlement）
    await expect(page.locator('.my .locked-box')).toBeVisible()
    // 选择玩家后仍锁定（不请求坐标 API）
    const first = await page.locator('.picker select option').nth(1).getAttribute('value')
    await page.locator('.picker select').selectOption(first)
    await expect(page.locator('.my .locked-box')).toBeVisible()
    // 响应无 steam_id
    const resp = await page.evaluate(async () => {
      const r = await fetch('/api/demos/6/players')
      return await r.json()
    })
    expect(JSON.stringify(resp)).not.toContain('steam_id')
    expect(JSON.stringify(resp)).not.toContain('7656')
    await page.screenshot({ path: `${SHOTS}/free-locked.png` })
  })

  test('pro：选择我 → 摘要 + 击杀/死亡切换 + CT/T 过滤', async ({ page }) => {
    setTier('mvr-acceptance@test.com', 'pro', 7)
    // 6R14 身份优先：个人摘要恒为绑定本人；先播种 demo23 首位玩家作「我」
    seedIdentity('mvr-acceptance@test.com', demoPlayerSteam('mvr-acceptance@test.com', 23))
    await login(page, 'mvr-acceptance@test.com')
    await page.goto('/demo/23')
    await page.click('button.tab:has-text("个人复盘")')

    await expect(page.locator('.picker select')).toBeVisible({ timeout: 10000 })
    await page.locator('.picker select').selectOption({ index: 1 })
    await page.waitForTimeout(1500)
    const probe = await page.evaluate(async () => {
      const token = localStorage.getItem('cs2_token')
      const r = await fetch('/api/demos/23/players', { headers: { Authorization: `Bearer ${token}` } })
      const j = await r.json().catch(() => null)
      return { status: r.status, len: Array.isArray(j) ? j.length : 'n/a', head: JSON.stringify(j).slice(0, 150) }
    })
    console.log('PROBE players:', JSON.stringify(probe))
    const probe2 = await page.evaluate(async () => {
      const token = localStorage.getItem('cs2_token')
      const h = { Authorization: `Bearer ${token}` }
      const f = await fetch('/api/demos/23/focus-player', { headers: h })
      const fj = await f.json().catch(() => null)
      const s = await fetch('/api/demos/23/personal/summary', { headers: h })
      const sj = await s.json().catch(() => null)
      return { focus: JSON.stringify(fj), summaryStatus: s.status, summary: JSON.stringify(sj).slice(0, 120) }
    })
    console.log('PROBE2:', JSON.stringify(probe2))
    const myText = await page.locator('.my').innerText().catch(() => '(no .my)')
    console.log('MY PANEL:', myText.slice(0, 400))
    // 摘要出现
    await expect(page.locator('.summary .sum-name')).toHaveText(/.+/, { timeout: 15000 })
    const sumText = await page.locator('.summary').innerText()
    expect(sumText).toContain('交战转化率')
    await page.screenshot({ path: `${SHOTS}/pro-summary.png` })

    // 个人击杀/死亡图（PersonalCombatMap）：点位画布渲染 + 击杀/死亡、CT/T 切换控件
    await expect(page.locator('.pcm-canvas')).toBeVisible({ timeout: 10000 })
    await page.screenshot({ path: `${SHOTS}/pro-kills.png` })

    // 死亡视图
    await page.click('button.seg-btn:has-text("我的死亡")')
    await page.waitForTimeout(500)
    await expect(page.locator('.pcm-canvas')).toBeVisible()
    await page.screenshot({ path: `${SHOTS}/pro-deaths.png` })

    // CT/T 过滤不报错（点位画布或空态）
    await page.click('button.seg-btn:has-text("CT")')
    await page.waitForTimeout(600)
    await expect(page.locator('.pcm-canvas')).toBeVisible()
    await page.screenshot({ path: `${SHOTS}/pro-ct-filter.png` })
  })
})
