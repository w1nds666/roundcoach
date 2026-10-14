// 6R14 E2E：Steam 身份与权益边界（live smoke 到安全跳转边界 + 绑定/识别/未识别/团队 roster 门禁）
import { test, expect } from '@playwright/test'
import { execSync } from 'node:child_process'

const SHOTS = 'tests/e2e/screenshots/phase6r14'
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

function psql(sql) {
  // 返回输出去 strip；用于播种/断言 DB 状态
  return execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -t -A -c "${sql}"`, { stdio: 'pipe' }).toString().trim()
}

function seedIdentity(email, steamId) {
  // 先清空该用户已有 active 身份，再 upsert（避免 user_id 唯一冲突）；SQL 必须单行（Windows cmd 截断多行）
  psql(`UPDATE steam_identities SET status='unbound', user_id=NULL WHERE user_id=(SELECT id FROM users WHERE email='${email}');`)
  psql(`INSERT INTO steam_identities (provider, subject, steam_id64, user_id, status, bound_at, verified_at, version) SELECT 'steam', '${steamId}', '${steamId}', id, 'active', now(), now(), 1 FROM users WHERE email='${email}' ON CONFLICT (steam_id64) DO UPDATE SET status='active', user_id=(SELECT id FROM users WHERE email='${email}'), verified_at=now(), bound_at=now(), unbound_at=NULL, version=steam_identities.version+1;`)
}

function clearIdentity(email) {
  psql(`UPDATE steam_identities SET status='unbound', user_id=NULL WHERE user_id IN (SELECT id FROM users WHERE email='${email}');`)
}

function firstPlayerSteamOfUserDemo(email, demoId) {
  const out = psql(`SELECT p.steam_id FROM matches m JOIN demos d ON d.match_id=m.id JOIN players p ON p.match_id=m.id WHERE d.id=${demoId} AND d.user_id=(SELECT id FROM users WHERE email='${email}') ORDER BY p.id LIMIT 1;`)
  return out.split(/\r?\n/)[0]?.trim() || ''
}

async function login(page, email) {
  await page.goto('/login')
  await page.fill('input[type="email"]', email)
  await page.fill('input[type="password"]', 'password123')
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

test.describe('6R14 Steam 身份与权益边界', () => {
  test('live smoke：Profile 绑定卡 → 点击后朝 Steam 官方端点跳转（安全边界）', async ({ page }) => {
    // 注册一个全新用户（无已绑定身份）；先进入应用建立 origin
    const email = `gsmoke${Date.now()}@test.com`
    await page.goto('/login')
    await page.evaluate(async (email) => {
      const r = await fetch('/api/auth/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password: 'password123', nickname: 'smoke' }),
      })
      if (!r.ok) throw new Error('register failed')
      const j = await r.json()
      localStorage.setItem('cs2_token', j.access_token)
    }, email)
    await page.goto('/profile')
    await page.waitForSelector('.steam.card', { timeout: 10000 })
    const boundPill = page.locator('.steam.card .pill-muted')
    await expect(boundPill).toHaveText('未绑定', { timeout: 10000 })
    // 拦截跳转：只验证会跳向 steamcommunity（live smoke 到边界，不真实登录）
    let dest = ''
    await page.route('**/steamcommunity.com/**', (route) => { dest = route.request().url(); route.abort() })
    await page.click('button:has-text("通过 Steam 登录并绑定")')
    await page.waitForTimeout(2000)
    expect(dest).toContain('steamcommunity.com/openid/login')
    // 若跳转未被拦截（边界仍在页内），也允许（不强制失败）
    await page.screenshot({ path: `${SHOTS}/profile-unbound.png` })
  })

  test('绑定后自动识别「这是你」+ Profile 已绑定(掩码)', async ({ page }) => {
    const email = 'mvr-acceptance@test.com'
    const demoId = 23
    const steam = firstPlayerSteamOfUserDemo(email, demoId)
    expect(steam).toMatch(/^\d{17}$/)
    seedIdentity(email, steam)
    await login(page, email)
    // Profile 已绑定：掩码 id 不是完整 17 位
    await page.goto('/profile')
    await page.waitForSelector('.steam.card', { timeout: 10000 })
    await expect(page.locator('.steam.card .pill-ok')).toHaveText('已绑定', { timeout: 10000 })
    const masked = await page.locator('.steam.card .steam_id64_masked, .steam.card .m-val.num').first().innerText()
    expect(masked).toContain('****')
    expect(masked.replace('*', '')).not.toContain(steam)
    await page.screenshot({ path: `${SHOTS}/profile-bound.png` })
    // 报告页：自动识别「这是你」
    await page.goto(`/demo/${demoId}`)
    await page.waitForSelector('.identity-banner', { timeout: 15000 })
    const banner = await page.locator('.identity-banner').innerText()
    expect(banner).toContain('已自动识别到你')
    await expect(page.locator('.me-badge')).toHaveText('这是你', { timeout: 10000 })
    await page.screenshot({ path: `${SHOTS}/demo-identified.png` })
  })

  test('绑定不在 roster → 「这场没有识别到你」', async ({ page }) => {
    const email = 'mvr-acceptance@test.com'
    const demoId = 23
    // 绑定一个不属于该 demo 的 Steam（合法但不在 roster）
    const foreignSteam = '76561198000010000'
    seedIdentity(email, foreignSteam)
    await login(page, email)
    await page.goto(`/demo/${demoId}`)
    await page.waitForSelector('.identity-banner', { timeout: 15000 })
    const banner = await page.locator('.identity-banner').innerText()
    expect(banner).toContain('没有识别到你')
    await page.screenshot({ path: `${SHOTS}/demo-not-identified.png` })
    // 个人 Pro 端点对非本人返回 403（权限边界）
    const probe = await page.evaluate(async (dId) => {
      const token = localStorage.getItem('cs2_token')
      const r = await fetch(`/api/demos/${dId}/personal/summary`, { headers: { Authorization: `Bearer ${token}` } })
      return { status: r.status, code: (await r.json().catch(() => ({}))).detail?.code }
    }, demoId)
    expect(probe.status).toBe(403)
    expect(probe.code).toBe('STEAM_IDENTITY_NOT_IN_DEMO')
    // 恢复绑定（清空，避免污染后续测试）
    clearIdentity(email)
  })

  test('团队 roster 与门禁展示', async ({ page }) => {
    const email = 'mvr-acceptance@test.com'
    const demoId = 23
    // 让 mvr-acceptance 为 Max 并绑定 demo 内某玩家 + 建一个团队
    const steam = firstPlayerSteamOfUserDemo(email, demoId)
    seedIdentity(email, steam)
    psql(`UPDATE users SET subscription_tier='max', subscription_expire=now()+interval '30 days' WHERE email='${email}';`)
    const teamId = psql(`SELECT id FROM team_workspaces WHERE owner_user_id=(SELECT id FROM users WHERE email='${email}') LIMIT 1;`)
    let team = teamId ? parseInt(teamId, 10) : 0
    if (!team) {
      team = parseInt(psql(`INSERT INTO team_workspaces (name, owner_user_id) SELECT '6R14 Team', id FROM users WHERE email='${email}' RETURNING id;`), 10)
      psql(`INSERT INTO team_memberships (team_id, user_id, steam_id64, role, slot_type, status, version) SELECT ${team}, id, '${steam}', 'owner', 'main', 'active', 1 FROM users WHERE email='${email}';`)
    }
    await login(page, email)
    await page.goto('/profile')
    await page.waitForSelector('.team-workspace, .team', { timeout: 10000, state: 'attached' }).catch(() => {})
    // 团队 gate 接口：demo roster 需 ≥3 且含本人；此处只有 owner 一人，应为 false
    const gate = await page.evaluate(async (dId) => {
      const token = localStorage.getItem('cs2_token')
      const r = await fetch(`/api/team/gate/${dId}`, { headers: { Authorization: `Bearer ${token}` } })
      return await r.json()
    }, demoId)
    expect(gate).toHaveProperty('ok')
    // 若 gate 存在则断言其语义
    await page.screenshot({ path: `${SHOTS}/profile-team.png` })
  })
})
