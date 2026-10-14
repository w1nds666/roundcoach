// 6R14 共享身份播种 helper（最小、可复现、幂等）。
// 每个 personal-Pro/Max E2E spec 自给自足：进入即 seedIdentity 绑定其 demo 玩家，
// 使服务端 require_pro_focus 命中「绑定本人」身份，并据此做身份优先断言。
//
// 关键事实（与 backend/app/services/identity.py 对齐）：
// - 个人 Pro/Max 端点（personal/summary|rating|combat-map|route|replay）由绑定 SteamID 决定「我」，
//   DEMO 里 Player.steam_id == 绑定 steam_id64 的那位玩家。手动 focus-player 不改数据主体。
// - steam_id64 在多次重解析间稳定；player_id 会变（个人端点按 steam_id 现查 Player）。
// - 播种直接 psql 写入 steam_identities（绕过业务冷却），幂等 upsert。
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

// 某用户拥有的 demo 里，按 player.id 升序，第 offset 位的玩家 steam_id64（稳定标识）
export function demoPlayerSteam(email, demoId, offset = 0) {
  const out = psql(`SELECT p.steam_id FROM demos d JOIN matches m ON m.id=d.match_id JOIN players p ON p.match_id=m.id WHERE d.id=${demoId} AND d.user_id=(SELECT id FROM users WHERE email='${email}') ORDER BY p.id LIMIT 1 OFFSET ${offset};`)
  return out.split(/\r?\n/)[0]?.trim() || ''
}

// 某用户拥有的 demo 里全部玩家（id 升序），供身份优先挑选目标
export function demoPlayers(email, demoId) {
  const rows = psql(`SELECT p.id, p.steam_id, p.name, p.team, p.team_identity FROM demos d JOIN matches m ON m.id=d.match_id JOIN players p ON p.match_id=m.id WHERE d.id=${demoId} AND d.user_id=(SELECT id FROM users WHERE email='${email}') ORDER BY p.id;`)
  return rows.split(/\r?\n/).filter(Boolean).map((line) => {
    const [player_id, steam_id, name, team, team_identity] = line.split('|')
    return { player_id: Number(player_id), steam_id, name, team, team_identity: (team_identity || '').trim() }
  })
}

// 稳定目标（steam_id64；player_id 随重解析变化但 steam 稳定）。
// 契约：demo19 的 players[0]（id 最小）＝ ROUND3_SMOKE 对应的玩家（531 风也有它的归宿.），
// 这样以 players[0] 做「绑定本人」的 spec 断言自洽。
export const DEMO19 = {
  ROUND3_SMOKE: '76561198812054537',   // 531 风也有它的归宿. CT——回合3 有 smoke/flash/fire 道具；id 最小，=players[0]
  ROUND1_DEATH: '76561199364686842',   // 534 用户4686842 CT——回合1 alive===false 真实死亡点
}

// 从 API 的 players 列表取出绑定本人（is_you）的玩家。需要 spec 传入其 apiGet(path)->{status,body}。
export function boundPlayerOf(players) {
  return players.find((p) => p.is_you) || null
}

// 把 caller 传入的 apiGet 结果体交给 boundPlayerId，返回绑定本人 player_id（或 null）
export async function boundPlayerId(apiGet, demoId) {
  const res = await apiGet(`/api/demos/${demoId}/players`)
  const list = Array.isArray(res?.body) ? res.body : (res?.body ?? [])
  return boundPlayerOf(list)?.player_id || null
}

// ---------------------------------------------------------------------------
// 6R15 收口：为「团队 Max 报告」门禁按**目标 demo** 可复现地播种 workspace / roster。
// 对应 backend/app/services/team.py 的 require_team_demo_entitlement，硬性条件：
//   1) 调用者在唯一 active team workspace；2) active roster 5-7 人；
//   3) 调用者本人 active SteamIdentity 在该 demo 阵容；4) demo 与 roster 重合 >=3。
//
// 实现要点（只改隔离测试数据库状态，不改产品规则）：
//   - 直接 psql 播种（绕过业务冷却），串行/幂等、自给自足；
//   - 把测试 Max 用户绑定到该 demo 的 players[0]（真实 SteamID）；
//   - 将调用者唯一 active 团队 roster 重建为「该 demo 的 5 名真实玩家」：owner 座 = players[0]，
//     另 4 座 = players[1..4]，每个成员都绑定到真实玩家 SteamID（合法座位；避免跨团队重复占座）；
//   - 清掉上一测试残留的其它 active 成员身份/占座（解绑 + removed），避免冲突；
//   - 若调用者还没有 active 团队则新建一个（owner = 调用者）。
//
// 注意：每个 psql 语句必须是**单行**（Windows shell 会按换行拆分 -c 字符串导致语法错）。
// 契约：调用方在导航到 /demo/{d} 并点击「团队/团队复盘」前，用自己测试的 demo_id 调用本函数。
// demo21 等 roster 特殊时，本函数按该 demo 的**真实 players** 建队，绝不 skip。
// ---------------------------------------------------------------------------
// 标量读：（`psql` 对 INSERT..RETURNING 会附 "INSERT 0 1" 标签行）取首个纯数字行。
function psqlScalar(sql) {
  const line = psql(sql).split(/\r?\n/).map((s) => s.trim()).find((s) => /^\d+$/.test(s))
  return line || ''
}
function ensureUser(email) {
  let uid = psqlScalar(`SELECT id FROM users WHERE email='${email}'`)
  if (!uid) uid = psqlScalar(`INSERT INTO users (email, password_hash, nickname, subscription_tier, subscription_expire) VALUES ('${email}', 'x', 'GV', 'max', now() + interval '30 days') RETURNING id`)
  return uid
}

// 幂等：把 email 绑定到 steam_id64（清其旧 active 身份；冲突 steam 用 ON CONFLICT 借位重绑）。
function bindIdentity(email, steamId64) {
  const uid = ensureUser(email)
  psql(`UPDATE steam_identities SET status='unbound', user_id=NULL WHERE user_id=${uid}`)
  psql(`INSERT INTO steam_identities (provider, subject, steam_id64, user_id, status, bound_at, verified_at, version) VALUES ('steam', '${steamId64}', '${steamId64}', ${uid}, 'active', now(), now(), 1) ON CONFLICT (steam_id64) DO UPDATE SET status='active', user_id=${uid}, last_verified_at=now(), unbound_at=NULL, version=steam_identities.version+1`)
  return uid
}

// 幂等：在某团队内 upsert 一个 active seat（按 (team_id, steam_id64) 去重；先清其它团队对该 steam 占座）。
function upsertSeat(teamId, userId, steamId64, role, slotType) {
  psql(`UPDATE team_memberships SET status='removed', removed_at=now(), version=version+1 WHERE steam_id64='${steamId64}' AND status='active' AND team_id<>${teamId}`)
  const seatId = psqlScalar(`SELECT id FROM team_memberships WHERE team_id=${teamId} AND steam_id64='${steamId64}'`)
  if (seatId) {
    psql(`UPDATE team_memberships SET user_id=${userId}, status='active', removed_at=NULL, role='${role}', slot_type='${slotType}', version=version+1 WHERE id=${seatId}`)
  } else {
    psql(`INSERT INTO team_memberships (team_id, user_id, steam_id64, role, slot_type, status, joined_at, version) VALUES (${teamId}, ${userId}, '${steamId64}', '${role}', '${slotType}', 'active', now(), 1)`)
  }
}

export function grantTeamEntitlementForDemo(email, demoId) {
  const players = demoPlayers(email, demoId)   // ORDER BY p.id，含真实 steam_id
  if (!players || players.length < 3) throw new Error(`grantTeamEntitlementForDemo: demo ${demoId} 玩家不足 3`)
  const uid = Number(psqlScalar(`SELECT id FROM users WHERE email='${email}'`))

  // 1) 绑定调用者到该 demo players[0]（真实身份）
  const steam0 = players[0].steam_id
  seedIdentity(email, steam0)

  // 2) 定位调用者唯一 active 团队；无则新建（owner = 调用者）
  let wsId = psqlScalar(`SELECT tm.team_id FROM team_memberships tm WHERE tm.user_id=${uid} AND tm.status='active' ORDER BY tm.team_id LIMIT 1`)
  if (!wsId) wsId = psqlScalar(`INSERT INTO team_workspaces (name, owner_user_id, created_at) VALUES ('Team6R15-${demoId}', ${uid}, now()) RETURNING id`)

  // 3) 只保留调用者这一个 active 团队（清其它团队里调用者的 active 占座 + 本团队内调用者重复 owner 座）
  psql(`UPDATE team_memberships SET status='removed', removed_at=now(), version=version+1 WHERE user_id=${uid} AND status='active' AND (team_id<>${wsId} OR (team_id=${wsId} AND role='owner'))`)
  // 4) 清掉本团队其它 active 成员（上一测试残留）
  psql(`UPDATE team_memberships SET status='removed', removed_at=now(), version=version+1 WHERE team_id=${wsId} AND status='active' AND user_id<>${uid}`)
  // 5) 建调用者唯一 owner 座（steam = 该 demo players[0]）
  upsertSeat(wsId, uid, steam0, 'owner', 'main')

  // 6) 再补 4 个成员座（= 该 demo 真实玩家 players[1..4]），凑 5 人 roster（全部来自该 demo → 重合=5）
  const seats = players.slice(1, 5)
  seats.forEach((p, i) => {
    const mu = bindIdentity(`grantteam${demoId}_${i}@test.com`, p.steam_id)
    upsertSeat(wsId, mu, p.steam_id, 'member', 'main')
  })
  return { workspace_id: Number(wsId), roster_size: 1 + seats.length, matched: 1 + seats.length, caller_included: true }
}
