<script setup>
import { onMounted, ref } from 'vue'
import { api, ApiError } from '../api/client'
import { mapLabel } from '../utils/mapNames'

const report = ref(null)
const loading = ref(true)
const error = ref('')

const CONF_CN = { high: '数据充分', medium: '中等置信', low: '样本有限' }

function formatDuration(seconds) {
  const minutes = Math.max(0, Math.round(Number(seconds || 0) / 60))
  return `${minutes} 分钟`
}
function rScore(p) {
  const r = p?.rating_pro
  return r && r.rating != null ? r.rating.toFixed(2) : '—'
}
function rConf(p) {
  const r = p?.rating_pro
  if (!r) return '—'
  const c = CONF_CN[r.confidence] || r.confidence
  return `${c}${r.coverage?.rounds ? ` · ${r.coverage.rounds} 回合` : ''}`
}
function kd(p) {
  const kills = p.stats?.kills || 0
  const deaths = Math.max(1, p.stats?.deaths || 0)
  return (kills / deaths).toFixed(2)
}
function hs(p) {
  return p.stats?.hs_rate == null ? '—' : `${Math.round(p.stats.hs_rate * 100)}%`
}

onMounted(async () => {
  let token = window.location.hash.slice(1)
  history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
  if (!token) {
    error.value = '分享链接无效或已失效'
    loading.value = false
    return
  }
  try {
    report.value = await api.post('/api/share/resolve', { token })
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '分享链接无效或已失效'
  } finally {
    token = ''
    loading.value = false
  }
})
</script>

<template>
  <div class="share-page">
    <div v-if="loading" class="state card">正在安全读取分享…</div>
    <div v-else-if="error" class="state card">
      <div class="state-mark">×</div><h1>无法打开分享</h1><p>{{ error }}</p>
    </div>
    <template v-else-if="report">
      <div class="share-mast">
        <a href="/" class="share-brand"><span class="brand-mark"></span><strong>比赛复盘</strong></a>
        <span class="share-mode">公开链接 / 只读报告</span>
      </div>

      <div class="share-trustbar" role="note" aria-label="分享安全范围">
        <span><b>匿名只读</b> 不展示昵称与 SteamID</span>
        <span><b>有限范围</b> 不含坐标与原始文件</span>
        <span><b>自动失效</b> {{ new Date(report.share.expires_at).toLocaleString('zh-CN') }}</span>
      </div>

      <header class="share-hero card">
        <div class="page-head sh-head">
          <div class="sh-main">
            <div class="eyebrow">比赛结论 / 匿名副本</div>
            <h1 class="sh-title">{{ mapLabel(report.match.map_name) }}</h1>
            <p class="sh-sub">一份经过脱敏的比赛事实摘要。只有报告拥有者可以撤销或重新生成链接。</p>
          </div>
          <div class="scoreline" aria-label="最终比分">
            <span class="team-label ct">队伍 A</span>
            <div class="score"><span>{{ report.match.team_a_score }}</span><b>:</b><span>{{ report.match.team_b_score }}</span></div>
            <span class="team-label t">队伍 B</span>
          </div>
        </div>
        <div class="metric-grid sh-metrics">
          <div class="metric-cell"><span class="mc-num">{{ report.match.rounds }}</span><span class="mc-lbl">回合数</span></div>
          <div class="metric-cell"><span class="mc-num">{{ report.players.length }}</span><span class="mc-lbl">玩家数</span></div>
          <div class="metric-cell"><span class="mc-num">{{ formatDuration(report.match.duration_seconds) }}</span><span class="mc-lbl">比赛时长</span></div>
        </div>
      </header>

      <section class="card player-card">
        <div class="section-head"><div><span class="section-index">01</span><h2>匿名单兵数据</h2></div><span>{{ report.players.length }} 名玩家</span></div>
        <p class="rating-note">Rating Pro 为本产品自研口径（击杀 / KAST / 存活率 / 回合伤害 / 补枪贡献五维加权），不等于 HLTV 官方 Rating；部分维度样本有限，仅供参考。</p>

        <div class="lb">
          <div class="lb-row lb-head">
            <span>玩家</span><span>Rating Pro</span><span>K/D</span><span>ADR</span><span>爆头</span><span>首杀</span><span>置信 / 覆盖</span>
          </div>
          <div v-for="p in report.players" :key="p.player_key" class="lb-row">
            <span class="pr-name">{{ p.player_key.toUpperCase() }}</span>
            <span class="pr-rp"><b class="num">{{ rScore(p) }}</b><em v-if="p.rating_pro?.provisional">（试验）</em></span>
            <span class="num stat-cell"><small class="mobile-label">K/D</small>{{ kd(p) }}</span>
            <span class="num stat-cell"><small class="mobile-label">ADR</small>{{ p.stats.adr ?? '—' }}</span>
            <span class="num stat-cell"><small class="mobile-label">爆头</small>{{ hs(p) }}</span>
            <span class="num stat-cell"><small class="mobile-label">首杀</small>{{ p.stats.first_kills }}</span>
            <span class="pr-conf">{{ rConf(p) }}</span>
          </div>
        </div>
      </section>

      <div class="share-foot">
        <p class="privacy">该页面不包含玩家昵称、SteamID、事件坐标、原始文件或下载入口。链接到期时间：{{ new Date(report.share.expires_at).toLocaleString('zh-CN') }}</p>
        <a href="/" class="back-home">返回比赛复盘 →</a>
      </div>
    </template>
  </div>
</template>

<style scoped>
.share-page { width: min(1160px, calc(100% - 40px)); margin: 24px auto 64px; display: grid; gap: 16px; }
.state { min-height: 300px; display: grid; place-content: center; text-align: center; gap: 10px; }
.state-mark { width: 48px; height: 48px; margin: auto; border-radius: 50%; display: grid; place-items: center; color: var(--err); background: var(--err-soft); font-size: 28px; }
.state h1, .share-hero h1, .player-card h2 { margin: 0; }
.state p, .privacy { color: var(--text-secondary); }

.share-mast { min-height: 42px; display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--border); }
.share-brand { color: var(--text-primary); display: inline-flex; align-items: center; gap: 10px; }
.brand-mark { width: 11px; height: 11px; border-radius: 3px; background: var(--accent); transform: rotate(45deg); }
.share-mode { color: var(--text-muted); font-size: 12px; }
.share-trustbar { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; background: var(--bg-inset); }
.share-trustbar span { min-width: 0; padding: 10px 14px; color: var(--text-secondary); font-size: 12.5px; line-height: 1.55; }
.share-trustbar span + span { border-left: 1px solid var(--border); }
.share-trustbar b { color: var(--text-secondary); margin-right: 5px; }

/* 榜头：标题 + 大比分（结论），下方一组关键事实压出信息密度 */
.share-hero { display: flex; flex-direction: column; gap: 20px; padding: 30px 32px; border-left: 3px solid var(--accent); background: linear-gradient(108deg, rgba(230,165,74,.07), transparent 38%), var(--bg-panel); }
.sh-main { min-width: 0; }
.sh-head { align-items: center; }
.sh-title { font-size: clamp(22px, 4vw, 36px); margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sh-sub { color: var(--text-secondary); font-size: 13px; line-height: 1.65; margin-top: 8px; max-width: 55ch; }
.sh-metrics { margin-top: 2px; padding-top: 18px; border-top: 1px solid var(--border); }
.scoreline { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 12px; }
.score { display: flex; align-items: center; gap: 14px; font-variant-numeric: tabular-nums; font-size: clamp(32px, 6vw, 52px); font-weight: 800; color: var(--text-primary); flex-shrink: 0; }
.score b { color: var(--text-muted); font-size: .65em; }
.team-label { font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.team-label.ct { color: var(--info); text-align: right; }
.team-label.t { color: var(--accent); }

.player-card { padding: 24px 28px; }
.section-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 10px; }
.section-head > div { display: flex; align-items: baseline; gap: 10px; }
.section-index { color: var(--accent); font-size: 12px; font-weight: 800; }
.section-head h2 { margin: 0; }
.section-head span { color: var(--text-muted); font-size: 13px; }
.rating-note { color: var(--text-secondary); font-size: 13px; line-height: 1.6; margin: 0 0 14px; max-width: 76ch; }

/* 榜单：隔行 + 悬停 + 数值右对齐，便于扫读；单元格 min-width:0 防横向溢出 */
.lb { border-top: 1px solid var(--border); }
.lb-row {
  display: grid; grid-template-columns: minmax(96px, 1.4fr) 1fr .6fr .6fr .6fr .6fr 1.2fr;
  gap: 10px; align-items: center; padding: 11px 2px; border-top: 1px solid var(--border); min-width: 0;
}
.lb-row > span { min-width: 0; }
.lb-head { border-top: none; color: var(--text-muted); font-size: 12px; font-weight: 600; padding-top: 8px; }
.lb-head span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.pr-name { color: var(--accent); font-weight: 700; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pr-rp b { font-size: 18px; font-weight: 800; color: var(--text-primary); }
.pr-rp em { font-style: normal; color: var(--text-muted); font-size: 11px; }
.pr-conf { color: var(--text-muted); font-size: 12px; }
.mobile-label { display: none; color: var(--text-muted); font-size: 10px; font-weight: 600; margin-right: 6px; }

@media (min-width: 641px) {
  /* K/D 之后数值列右对齐（桌面扫读）；移动端用分区布局，不走这组规则 */
  .lb-row > :nth-child(n+2):nth-child(-n+6),
  .lb-head > :nth-child(n+2):nth-child(-n+6) { text-align: right; }
  .lb-row .num { font-variant-numeric: tabular-nums; }
}
.lb > .lb-row:not(.lb-head):nth-child(even) { background: rgba(152, 164, 179, 0.04); }
.lb > .lb-row:not(.lb-head):hover { background: var(--bg-hover); }

.share-foot { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 4px 2px; }
.privacy { text-align: left; font-size: 13px; line-height: 1.7; margin: 0; max-width: 78ch; }
.share-brand, .back-home { min-height: 38px; }
.back-home { color: var(--accent); font-size: 12px; font-weight: 700; white-space: nowrap; display: inline-flex; align-items: center; }

@media (max-width: 640px) {
  .share-page { width: min(100% - 24px, 1160px); margin-top: 14px; }
  .share-mast { min-height: 46px; }
  .share-mode { display: none; }
  .share-trustbar { grid-template-columns: 1fr; }
  .share-trustbar span + span { border-left: 0; border-top: 1px solid var(--border); }
  .share-hero { padding: 18px 16px; gap: 14px; }
  .sh-head { align-items: flex-start; }
  .scoreline { width: 100%; grid-template-columns: 1fr auto 1fr; margin-top: 4px; }
  .sh-title { font-size: 22px; }
  .sh-metrics { grid-template-columns: repeat(2, 1fr); gap: 12px 16px; }
  .sh-metrics .mc-num { font-size: 20px; }
  .score { font-size: 32px; gap: 10px; }
  .player-card { padding: 18px 16px; }
  .lb-head { display: none; }
  .lb-row {
    grid-template-columns: 1fr 1fr;
    grid-template-areas: "name rp" "kd adr" "conf conf";
    row-gap: 8px; padding: 12px 2px;
  }
  .lb-row .pr-name { grid-area: name; }
  .lb-row .pr-rp { grid-area: rp; justify-self: end; }
  /* 用 nth-child 定位（比 nth-of-type 可靠），移动端只保留 玩家/Rating/K-D/ADR/置信 */
  .lb-row .num:nth-child(3) { grid-area: kd; text-align: left; }
  .lb-row .num:nth-child(4) { grid-area: adr; text-align: right; }
  .mobile-label { display: inline; }
  .lb-row .pr-conf { grid-area: conf; }
  .lb-row > :nth-child(5), .lb-row > :nth-child(6) { display: none; }
  .share-foot { flex-direction: column; }
}
</style>
