/**
 * 订阅状态共享 helper（P1-1 修复）
 * - 唯一判定逻辑：App / Profile / Report 不再各自复制
 * - 后端统一输出 UTC RFC3339（带 Z）；旧的无偏移字符串按 UTC 安全兼容解析
 */

export function parseUtcDate(value) {
  if (!value) return null
  // 无时区偏移（历史 naive 数据）→ 按 UTC 解释
  const s = /(Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? null : d
}

/** 有效 pro：tier=pro 且（无到期时间 或 到期时间在未来）。Max 包含 Pro 全部能力，故视为 pro。 */
export function isPro(user) {
  if (!user || !['pro', 'max'].includes(user.subscription_tier)) return false
  const expire = parseUtcDate(user.subscription_expire)
  return !(expire && expire.getTime() < Date.now())
}

/** 有效 max：tier=max 且未过期（团队工作区仅对有效 Max 开放）。 */
export function isMax(user) {
  if (!user || user.subscription_tier !== 'max') return false
  const expire = parseUtcDate(user.subscription_expire)
  return !(expire && expire.getTime() < Date.now())
}

/** 展示信息：{label, cls, desc}（cls: pill-ok/err/muted） */
export function tierInfo(user) {
  if (!user) return { label: '—', cls: 'pill-muted', desc: '' }
  if (user.subscription_tier === 'max') {
    const expire = parseUtcDate(user.subscription_expire)
    if (expire && expire.getTime() < Date.now()) {
      return { label: '高阶版（已过期）', cls: 'pill-err', desc: '订阅已过期，团队功能已锁定；续费后可继续使用。' }
    }
    return { label: '高阶版', cls: 'pill-ok', desc: '包含进阶版全部能力 + 团队汇总、团队同步回放等 Max 功能。' }
  }
  if (user.subscription_tier === 'pro') {
    const expire = parseUtcDate(user.subscription_expire)
    if (expire && expire.getTime() < Date.now()) {
      return {
        label: '进阶版（已过期）',
        cls: 'pill-err',
        desc: '订阅已过期，进阶功能已锁定；续费后可继续使用。',
      }
    }
    return {
      label: '进阶版',
      cls: 'pill-ok',
      desc: `有效期至 ${expire ? expire.toLocaleDateString('zh-CN') : '永久（测试）'}。路线分析、死亡复盘、团队配合与 AI 报告已解锁。`,
    }
  }
  return { label: '免费版', cls: 'pill-muted', desc: '免费版包含基础统计与击杀热力图，每天可上传 1 个 demo。' }
}
