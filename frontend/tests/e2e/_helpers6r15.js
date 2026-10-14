// 6R15 长期教练共享 helper（最小、可复现、幂等）。
// 沿用 _helpers6r14.js 的 psql 播种模式：psql -> docker exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -t -A -c "<sql>"
// 用于 setTier / seedIdentity / demoPlayerSteam / DEMO19 等。coach 画像由后端按绑定身份生成，
// 因此 spec 进入前先 seedIdentity 绑定 demo 玩家，再 setTier 决定窗口。
import { execSync } from 'node:child_process'

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'

export function psql(sql) {
  return execSync(`"${DOCKER}" exec cs2demo-postgres-1 psql -U cs2 -d cs2demo -t -A -c "${sql}"`, { stdio: 'pipe' }).toString().trim()
}

export function setTier(email, tier, days) {
  const exp = days ? `, subscription_expire = now() + interval '${days} days'` : ', subscription_expire = NULL'
  psql(`UPDATE users SET subscription_tier='${tier}'${exp} WHERE email='${email}';`)
}

// 幂等：把 email 绑定到指定 steam_id64（先解绑该用户现有 active，再 upsert）
export function seedIdentity(email, steamId64) {
  psql(`UPDATE steam_identities SET status='unbound', user_id=NULL WHERE user_id=(SELECT id FROM users WHERE email='${email}');`)
  psql(`INSERT INTO steam_identities (provider, subject, steam_id64, user_id, status, bound_at, verified_at, version) SELECT 'steam', '${steamId64}', '${steamId64}', id, 'active', now(), now(), 1 FROM users WHERE email='${email}' ON CONFLICT (steam_id64) DO UPDATE SET status='active', user_id=(SELECT id FROM users WHERE email='${email}'), verified_at=now(), bound_at=now(), unbound_at=NULL, version=steam_identities.version+1;`)
}

export function clearIdentity(email) {
  psql(`UPDATE steam_identities SET status='unbound', user_id=NULL WHERE user_id IN (SELECT id FROM users WHERE email='${email}');`)
}

// 某用户、某一已解析 demo 里包含指定 steam 的 demo_id（用于证据跳转回退时选一个绑定玩家确实在 roster 的 demo）
export function demoWherePlayer(email, steamId) {
  const out = psql(`SELECT d.id FROM demos d JOIN matches m ON m.id=d.match_id JOIN players p ON p.match_id=m.id WHERE d.user_id=(SELECT id FROM users WHERE email='${email}') AND p.steam_id='${steamId}' AND d.status='parsed' ORDER BY d.id LIMIT 1;`)
  return out.split(/\r?\n/)[0]?.trim() || ''
}

// 某用户拥有的 demo 里，按 player.id 升序，第 offset 位的玩家 steam_id64（稳定标识）
export function demoPlayerSteam(email, demoId, offset = 0) {
  const out = psql(`SELECT p.steam_id FROM demos d JOIN matches m ON m.id=d.match_id JOIN players p ON p.match_id=m.id WHERE d.id=${demoId} AND d.user_id=(SELECT id FROM users WHERE email='${email}') ORDER BY p.id LIMIT 1 OFFSET ${offset};`)
  return out.split(/\r?\n/)[0]?.trim() || ''
}

// 某用户拥有的 demo 里全部玩家（id 升序），供身份/证据断言挑选目标
export function demoPlayers(email, demoId) {
  const rows = psql(`SELECT p.id, p.steam_id, p.name, p.team, p.team_identity FROM demos d JOIN matches m ON m.id=d.match_id JOIN players p ON p.match_id=m.id WHERE d.id=${demoId} AND d.user_id=(SELECT id FROM users WHERE email='${email}') ORDER BY p.id;`)
  return rows.split(/\r?\n/).filter(Boolean).map((line) => {
    const [player_id, steam_id, name, team, team_identity] = line.split('|')
    return { player_id: Number(player_id), steam_id, name, team, team_identity: (team_identity || '').trim() }
  })
}

export async function login(page, email, password = 'password123') {
  await page.goto('/login')
  await page.fill('input[type="email"]', email)
  await page.fill('input[type="password"]', password)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 15000 })
  await page.waitForSelector('.user', { timeout: 10000 })
}

// 带 Bearer 的 API GET（page 已登录）
export async function apiGet(page, path) {
  return page.evaluate(async (p) => {
    const token = localStorage.getItem('cs2_token')
    const r = await fetch(p, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    let body = null
    try { body = await r.json() } catch { /* 非 JSON */ }
    return { status: r.status, body }
  }, path)
}

// 预热 coach 画像：直接调用 coach 端点，确保后端已生成（结果被后续 UI 复用；若生成慢则重新加载页面即可）。
// 返回 {status, body}。调用方需自备登录态。
export async function coachWarm(page, window = 10) {
  return apiGet(page, `/api/coach/profile?window=${window}`)
}

// 稳定目标（steam_id64；player_id 随重解析变化但 steam 稳定）。与 _helpers6r14.js 对齐。
export const DEMO19 = {
  ROUND3_SMOKE: '76561198812054537',   // 531 风也有它的归宿. CT——回合3 有 smoke/flash/fire 道具；id 最小，=players[0]
  ROUND1_DEATH: '76561199364686842',   // 534 用户4686842 CT——回合1 alive===false 真实死亡点
}

export const DEMO23 = 23
export const ACCOUNT = { email: 'mvr-acceptance@test.com', password: 'password123' }
