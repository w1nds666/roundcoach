<script setup>
/**
 * 6R9：深度配合分析（Max）—— 少而强（返工版）。
 * a) 顶部「本场最值得复盘的 3-5 个协同结论」（去重、同 metric+tone 至多 1 条、成功/问题都有、人话 what/why/next）
 * b) 紧凑 A/B 指标对比表（每行一个指标，A 与 B 并列，最多 7 行；不暴露四位数采样机会次数）
 * c) 证据浏览器：默认只展示「当前选中结论」关联证据（≤5），可切换「全部回合」（每回合折叠≤5 + 查看更多）
 * - 数据源：/api/demos/{id}/coordination
 * - 主界面仅 mm:ss + 中文区域；「试验性基线」常显；不展示裸 tick/坐标/steam。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, ApiError } from '../api/client'

const props = defineProps({
  demoId: { type: [Number, String], required: true },
  mapName: { type: String, default: '' },
  maxRound: { type: Number, default: 0 },
})
const emit = defineEmits(['locate'])

const data = ref(null)
const loading = ref(false)
const error = ref('')
const selectedId = ref(null)
const evidenceFilter = ref('selected')   // 'selected' | 'all'
const expandedRounds = ref(new Set())     // 全部回合视图中被展开的回合
// Only conclusions are revealed progressively; other evidence remains immediately available.
const revealCount = ref(0)
let revealTimer = null
let disposed = false
function revealProgressively() {
  if (disposed) return
  if (revealTimer) clearInterval(revealTimer)
  revealTimer = null
  revealCount.value = 0
  const total = data.value?.conclusions?.length || 0
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { revealCount.value = total; return }
  if (!total) return
  revealTimer = setInterval(() => {
    revealCount.value++
    if (revealCount.value >= total) { clearInterval(revealTimer); revealTimer = null }
  }, 30)
}
onBeforeUnmount(() => { disposed = true; if (revealTimer) { clearInterval(revealTimer); revealTimer = null } })

const METRIC_LABEL = {
  trade: '补枪响应', simultaneous_contact: '协同接触', spacing: '间距纪律',
  utility_assisted_entry: '道具助攻', isolated_death: '孤立阵亡', area_control: '区域控制',
  crossfire_candidate: '交叉火力候选',
}
const METRIC_ORDER = ['trade', 'simultaneous_contact', 'spacing', 'utility_assisted_entry',
  'isolated_death', 'area_control', 'crossfire_candidate']
const TONE = { info: 'pk-info', warn: 'pk-warn', ok: 'pk-ok', err: 'pk-err', accent: 'pk-accent' }
const CONF_CN = { high: '高', medium: '中', low: '低', none: '不可评估' }
const TRUST_CN = { validated: '可信', experimental: '实验性', hidden: '隐藏' }

const conclusions = computed(() => data.value?.conclusions || [])
const teams = computed(() => data.value?.teams || [])
const roundsEv = computed(() => data.value?.rounds || [])
const baseline = computed(() => data.value?.baseline_note || '')
const contentHash = computed(() => data.value?.content_hash)
const playerScope = computed(() => data.value?.player || null)

const selectedConclusion = computed(() => conclusions.value.find((c) => c.id === selectedId.value) || conclusions.value[0] || null)
// 默认选中第一条结论
if (!selectedId.value) selectedId.value = 'c1'

// 指标 A/B 对比（每行一个指标，最多 7 行）
const metricRows = computed(() => {
  const byMetric = {}
  for (const t of teams.value) {
    for (const m of t.metrics) {
      byMetric[m.metric_id] = byMetric[m.metric_id] || { metric_id: m.metric_id, label: METRIC_LABEL[m.metric_id] || m.metric_id, A: null, B: null }
      byMetric[m.metric_id][t.team] = m
    }
  }
  return METRIC_ORDER.map((id) => byMetric[id]).filter(Boolean)
})

// 证据浏览器：默认只展示选中结论关联证据（≤5）
const evidenceRelated = computed(() => {
  const sel = selectedConclusion.value
  if (!sel) return []
  const rel = roundsEv.value.filter((e) => e.metric_id === sel.metric_id && e.round === sel.round)
  return rel.slice(0, DEFAULT_EVIDENCE_LIMIT)
})
const evidenceRelatedCount = computed(() => {
  const sel = selectedConclusion.value
  if (!sel) return 0
  return roundsEv.value.filter((e) => e.metric_id === sel.metric_id && e.round === sel.round).length
})
// 全部回合：按回合折叠，每回合≤5
const roundGroups = computed(() => {
  const map = new Map()
  for (const e of roundsEv.value) {
    if (!map.has(e.round)) map.set(e.round, [])
    map.get(e.round).push(e)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0]).map(([round, items]) => ({
    round, items: items.sort((a, b) => (a.seconds || 0) - (b.seconds || 0)),
  }))
})
const visibleEvidence = computed(() => {
  if (evidenceFilter.value === 'selected') return evidenceRelated.value
  // 全部回合：每回合≤5（除非展开）
  const out = []
  for (const g of roundGroups.value) {
    const limit = expandedRounds.value.has(g.round) ? g.items.length : DEFAULT_EVIDENCE_LIMIT
    for (const e of g.items.slice(0, limit)) out.push(e)
  }
  return out
})

const DEFAULT_EVIDENCE_LIMIT = 5

function fmtTime(sec) {
  const n = Number(sec)
  if (!Number.isFinite(n) || n < 0) return '—'
  const s = Math.floor(n); const m = Math.floor(s / 60); const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}
function toneClassFor(metric_id, success) {
  if (metric_id === 'crossfire_candidate') return TONE.accent
  return success ? TONE.ok : TONE.err
}
function confLabel(c) { return CONF_CN[c] || c || '' }
function modeLabel(m) { return m.metric_id === 'crossfire_candidate' ? '候选' : (m.success ? '成功' : '问题') }
function cellTooltip(m) {
  if (!m) return ''
  let trust = TRUST_CN[m.trust] || m.trust || ''
  if (m.metric_id === 'area_control') trust += ' · 占据≠控制（需正向信号）'
  if (m.metric_id === 'utility_assisted_entry') trust += ' · flash/HE=真实效果，烟/火=时空候选'
  return `${m.human || ''} · ${m.opportunities} 机会 · ${m.converted} 转化 · ${m.coverage_label || ''} · 置信度 ${confLabel(m.confidence)} · ${trust} ${m.limits || ''}`
}

function locateEnd(round, sec, layers, highlight) {
  emit('locate', {
    round, time: sec != null ? Number(sec) : 0,
    layers: Array.isArray(layers) ? layers : [],
    highlight: Array.isArray(highlight) ? highlight : [],
  })
}
function selectConclusion(c) {
  selectedId.value = c.id
  locateEnd(c.round, c.seconds, c.layers, c.highlight)
}
function locateEvidence(e) {
  locateEnd(e.round, e.seconds, e.layers, e.highlight)
}
function toggleExpand(round) {
  const s = new Set(expandedRounds.value)
  if (s.has(round)) s.delete(round); else s.add(round)
  expandedRounds.value = s
}
function setFilter(f) { evidenceFilter.value = f }

async function load() {
  loading.value = true; error.value = ''
  try {
    const result = await api.get(`/api/demos/${props.demoId}/coordination`)
    if (disposed) return
    data.value = result
    revealProgressively() // 分块/惰性渲染，避免一次同步 patch 触发大量重排
  } catch (e) {
    if (disposed) return
    error.value = e instanceof ApiError ? e.message : '深度配合分析加载失败'
    data.value = null
  } finally {
    if (!disposed) loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <div class="co">
    <div v-if="loading" class="co-state">加载深度配合分析…</div>
    <p v-else-if="error" class="form-error-box">{{ error }}</p>

    <template v-else>
      <p class="co-baseline num">{{ baseline }} <template v-if="contentHash">· 分析 #{{ String(contentHash).slice(0, 8) }}</template></p>

      <!-- a) 最值得复盘的 3-5 个结论（去重 + 人话） -->
      <section class="co-sec">
        <h3 class="co-sec-title">本场最值得复盘</h3>
        <div v-if="conclusions.length" class="co-concl">
          <div v-for="c in conclusions.slice(0, revealCount)" :key="c.id" class="co-concl-item" :class="[c.tone, { on: selectedId === c.id }]"
            role="button" tabindex="0" @click="selectConclusion(c)" @keydown.enter="selectConclusion(c)">
            <div class="co-concl-head">
              <span class="pk" :class="c.tone === 'success' ? TONE.ok : TONE.err">{{ c.tone === 'success' ? '成功' : '问题' }}</span>
              <span class="co-concl-metric num">{{ c.label }}</span>
              <span class="co-meta num">R{{ c.round }} · {{ fmtTime(c.seconds) }} · {{ c.region || '未知区域' }}</span>
              <span v-if="c.players_name" class="co-concl-players">{{ c.players_name }}</span>
            </div>
            <p class="co-concl-what">{{ c.title }}</p>
            <p class="co-concl-why">为什么影响胜率：{{ c.why }}</p>
            <p class="co-concl-next">下一次：{{ c.next }}</p>
            <button class="btn btn-ghost co-locate" @click.stop="selectConclusion(c)">定位到回放</button>
          </div>
        </div>
        <div v-else class="co-state">暂无值得复盘的结论（本批样本内证据不足）。</div>
      </section>

      <!-- a2) 我的协同贡献（与团队视图严格区分，按 episode 聚合、可见≤5） -->
      <section class="co-sec">
        <h3 class="co-sec-title">我的协同贡献 <span class="co-sec-note num">（仅所选焦点玩家 · 与团队视图区分）</span></h3>
        <div v-if="playerScope && playerScope.focus_player_id" class="co-mine">
          <p class="co-mine-sum">{{ playerScope.summary }}</p>
          <div v-if="playerScope.contributions.length" class="co-mine-block">
            <p class="co-mine-head ok">我参与的协同成功（最近 {{ playerScope.contributions.length }} 次）</p>
            <button v-for="(c, i) in playerScope.contributions" :key="'c'+i" class="co-mine-item success" @click="locateEvidence(c)">
              <span class="co-mine-meta num">R{{ c.round }} · {{ fmtTime(c.seconds) }} · {{ c.region || '未知区域' }}</span>
              <span class="co-mine-reason">{{ c.metric_label }}：{{ c.reason }}</span>
              <span class="co-locate">定位</span>
            </button>
          </div>
          <div v-if="playerScope.exposures.length" class="co-mine-block">
            <p class="co-mine-head err">我暴露的协同问题（最近 {{ playerScope.exposures.length }} 次）</p>
            <button v-for="(e, i) in playerScope.exposures" :key="'e'+i" class="co-mine-item problem" @click="locateEvidence(e)">
              <span class="co-mine-meta num">R{{ e.round }} · {{ fmtTime(e.seconds) }} · {{ e.region || '未知区域' }}</span>
              <span class="co-mine-reason">{{ e.metric_label }}：{{ e.reason }}</span>
              <span class="co-locate">定位</span>
            </button>
          </div>
          <p v-if="!playerScope.contributions.length && !playerScope.exposures.length" class="co-mine-empty">暂无属于该玩家的协同证据。</p>
        </div>
        <div v-else class="co-state">请先在「个人复盘」选择焦点玩家，即可查看其在团队协同中的角色（不替代团队结论）。</div>
      </section>

      <!-- b) 紧凑指标概览：A/B 对比表（每行一个指标，最多 7 行） -->
      <section class="co-sec">
        <h3 class="co-sec-title">指标概览 <span class="co-sec-note num">（机会归一 · 试验性基线 · A/B 对比）</span></h3>
        <div class="co-metric-table-wrap">
          <table class="co-metric-table">
            <thead>
              <tr><th class="co-mt-metric">指标</th><th>队伍 A</th><th>队伍 B</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in metricRows" :key="row.metric_id">
                <td class="co-mt-metric">
                  <div class="co-mt-label">{{ row.label }}</div>
                  <div class="co-mt-sub">
                    <template v-if="row.A && row.B">置信 {{ confLabel(row.A.confidence) }}/{{ confLabel(row.B.confidence) }}</template>
                  </div>
                </td>
                <td class="co-mt-cell" :title="cellTooltip(row.A)" >
                  <div class="co-mt-value num">{{ row.A ? (row.A.human ?? (row.A.opportunities === 0 ? '不适用' : '不可评估')) : '—' }}</div>
                  <div class="co-mt-cov">{{ row.A ? row.A.coverage_label : '' }}</div>
                  <div v-if="row.A && row.A.trust === 'hidden'" class="co-mt-trust">隐藏（无 LoS/致盲验证）</div>
                  <div v-else-if="row.A && row.A.trust === 'experimental'" class="co-mt-trust">实验性<span v-if="row.A.metric_id==='area_control'"> · 占据≠控制</span></div>
                </td>
                <td class="co-mt-cell" :title="cellTooltip(row.B)">
                  <div class="co-mt-value num">{{ row.B ? (row.B.human ?? (row.B.opportunities === 0 ? '不适用' : '不可评估')) : '—' }}</div>
                  <div class="co-mt-cov">{{ row.B ? row.B.coverage_label : '' }}</div>
                  <div v-if="row.B && row.B.trust === 'hidden'" class="co-mt-trust">隐藏（无 LoS/致盲验证）</div>
                  <div v-else-if="row.B && row.B.trust === 'experimental'" class="co-mt-trust">实验性<span v-if="row.B.metric_id==='area_control'"> · 占据≠控制</span></div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- c) 证据浏览器（默认选中结论关联 ≤5；可切全部回合折叠） -->
      <section class="co-sec">
        <h3 class="co-sec-title">证据浏览器 <span class="co-sec-note num">（点击定位到回合+时点）</span></h3>
        <div class="co-ev-filter" role="group" aria-label="证据筛选">
          <button class="co-ev-btn" :class="{ on: evidenceFilter === 'selected' }" @click="setFilter('selected')">所选结论</button>
          <button class="co-ev-btn" :class="{ on: evidenceFilter === 'all' }" @click="setFilter('all')">全部回合</button>
        </div>

        <div v-if="evidenceFilter === 'selected' && evidenceRelated.length" class="cp">
          <div class="co-ev-note num">与所选结论「{{ selectedConclusion.label }} R{{ selectedConclusion.round }}」相关，共 {{ evidenceRelatedCount }} 条，展示 {{ evidenceRelated.length }} 条。</div>
          <div class="cp-round">
            <div class="cp-round-head">
              <span class="cp-round-no num">R{{ selectedConclusion.round }}</span>
              <span class="cp-round-count num">{{ evidenceRelated.length }} 条</span>
            </div>
            <ul class="cp-list">
            <li v-for="(it, i) in evidenceRelated" :key="'s'+i" class="cp-item clickable" @click="locateEvidence(it)">
              <div class="cp-item-line">
                <span class="pk" :class="toneClassFor(it.metric_id, it.success)">{{ modeLabel(it) }}·{{ METRIC_LABEL[it.metric_id] || it.metric_id }}</span>
                <span class="cp-time num">{{ fmtTime(it.seconds) }}</span>
                <span class="cp-region">{{ it.region || '未知区域' }}</span>
                <span class="cp-team num">{{ it.team }}</span>
                <span v-if="it.players_name" class="cp-players">{{ it.players_name }}</span>
              </div>
              <p class="cp-ev">证据：{{ it.reason || it.detail }}</p>
              <p v-if="it.metric_id === 'crossfire_candidate'" class="cp-reason">说明：仅几何+朝向候选，无墙体遮挡（LoS）验证。</p>
              <p v-if="it.metric_id === 'utility_assisted_entry'" class="cp-reason">仅证明时间/空间邻近（无致盲/遮挡字段验证）。</p>
              <div class="cp-goto"><button class="btn btn-ghost cp-locate" @click.stop="locateEvidence(it)">定位到回放</button></div>
            </li>
          </ul>
          </div>
          <button v-if="evidenceRelatedCount > DEFAULT_EVIDENCE_LIMIT" class="co-ev-more" @click="setFilter('all')">查看其余 {{ evidenceRelatedCount - DEFAULT_EVIDENCE_LIMIT }} 条（切换全部回合）</button>
        </div>

        <div v-else-if="evidenceFilter === 'all'" class="cp cp-body">
          <div v-for="g in roundGroups" :key="g.round" class="cp-round">
            <div class="cp-round-head">
              <span class="cp-round-no num">R{{ g.round }}</span>
              <span class="cp-round-count num">{{ g.items.length }} 条</span>
              <button v-if="g.items.length > DEFAULT_EVIDENCE_LIMIT" class="co-ev-more" @click="toggleExpand(g.round)">
                {{ expandedRounds.has(g.round) ? '收起' : `查看更多（${g.items.length - DEFAULT_EVIDENCE_LIMIT}）` }}
              </button>
            </div>
            <ul class="cp-list">
              <li v-for="(it, i) in (expandedRounds.has(g.round) ? g.items : g.items.slice(0, DEFAULT_EVIDENCE_LIMIT))" :key="i" class="cp-item clickable" @click="locateEvidence(it)">
                <div class="cp-item-line">
                  <span class="pk" :class="toneClassFor(it.metric_id, it.success)">{{ modeLabel(it) }}·{{ METRIC_LABEL[it.metric_id] || it.metric_id }}</span>
                  <span class="cp-time num">{{ fmtTime(it.seconds) }}</span>
                  <span class="cp-region">{{ it.region || '未知区域' }}</span>
                  <span class="cp-team num">{{ it.team }}</span>
                  <span v-if="it.players_name" class="cp-players">{{ it.players_name }}</span>
                </div>
                <p class="cp-ev">证据：{{ it.reason || it.detail }}</p>
                <p v-if="it.metric_id === 'crossfire_candidate'" class="cp-reason">说明：仅几何+朝向候选，无墙体遮挡（LoS）验证。</p>
                <p v-if="it.metric_id === 'utility_assisted_entry'" class="cp-reason">仅证明时间/空间邻近（无致盲/遮挡字段验证）。</p>
                <div class="cp-goto"><button class="btn btn-ghost cp-locate" @click.stop="locateEvidence(it)">定位到回放</button></div>
              </li>
            </ul>
          </div>
        </div>

        <div v-else class="co-state">暂无与所选结论相关的证据。</div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.co { display: flex; flex-direction: column; gap: 22px; }
.co-state { color: var(--text-muted); text-align: center; padding: 28px; font-size: 13px; }
.co-baseline { color: var(--warn); font-size: 12px; margin: 0; }
.co-sec { display: flex; flex-direction: column; gap: 10px; }
.co-sec-title { font-size: 14px; font-weight: 700; color: var(--text-primary); margin: 0; }
.co-sec-note { font-size: 11.5px; color: var(--text-muted); font-weight: 500; }

/* 结论 */
.co-concl { display: flex; flex-direction: column; gap: 10px; }
.co-concl-item { border: 1px solid var(--border); border-radius: 10px; background: var(--bg-elevated);
  padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; cursor: pointer; }
.co-concl-item.success { border-left: 4px solid var(--ok); }
.co-concl-item.problem { border-left: 4px solid var(--err); }
.co-concl-item.on { border-color: var(--accent); }
.co-concl-item:hover { border-color: var(--accent); }
.co-concl-head { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.co-concl-metric { font-size: 12.5px; color: var(--text-secondary); font-weight: 700; }
.co-meta { font-size: 12.5px; color: var(--text-secondary); }
.co-concl-players { font-size: 12.5px; color: var(--text-primary); font-weight: 600; }
.co-concl-what { font-size: 13.5px; color: var(--text-primary); font-weight: 700; margin: 0; }
.co-concl-why { font-size: 12.5px; color: var(--text-secondary); margin: 0; }
.co-concl-next { font-size: 12.5px; color: var(--text-secondary); margin: 0; }
.co-locate { font-size: 12px; padding: 3px 10px; align-self: flex-start; }

/* 我的协同贡献 */
.co-mine { display: flex; flex-direction: column; gap: 10px; }
.co-mine-sum { font-size: 12.5px; color: var(--text-secondary); margin: 0; }
.co-mine-block { display: flex; flex-direction: column; gap: 6px; }
.co-mine-head { font-size: 12px; font-weight: 700; margin: 0; }
.co-mine-head.ok { color: var(--ok); }
.co-mine-head.err { color: var(--err); }
.co-mine-item { border: 1px solid var(--border); border-radius: 7px; background: var(--bg-elevated);
  padding: 7px 11px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; cursor: pointer; text-align: left; }
.co-mine-item.success { border-left: 3px solid var(--ok); }
.co-mine-item.problem { border-left: 3px solid var(--err); }
.co-mine-item:hover { border-color: var(--accent); }
.co-mine-meta { font-size: 12px; color: var(--text-secondary); font-weight: 600; }
.co-mine-reason { font-size: 12px; color: var(--text-primary); flex: 1 1 200px; }
.co-mine-empty { font-size: 12.5px; color: var(--text-muted); margin: 0; }

/* 指标 A/B 表（紧凑，不卡片墙） */
.co-metric-table-wrap { overflow-x: auto; }
.co-metric-table { border-collapse: collapse; width: 100%; max-width: 640px; }
.co-metric-table th { text-align: left; font-size: 12px; color: var(--text-secondary); font-weight: 600; padding: 6px 10px; border-bottom: 1px solid var(--border); }
.co-metric-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
.co-mt-metric { font-size: 12.5px; color: var(--text-primary); font-weight: 600; }
.co-mt-label { font-weight: 700; }
.co-mt-sub { font-size: 11px; color: var(--text-muted); }
.co-mt-value { font-size: 17px; font-weight: 800; color: var(--text-primary); }
.co-mt-cov { font-size: 11px; color: var(--text-muted); }
.co-mt-trust { font-size: 10.5px; color: var(--warn); }

/* 证据 */
.co-ev-filter { display: flex; gap: 6px; }
.co-ev-btn { border: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-secondary); padding: 4px 12px; border-radius: 999px; font-size: 12.5px; cursor: pointer; font-weight: 600; }
.co-ev-btn.on { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.co-ev-note { font-size: 12px; color: var(--text-muted); }
.co-ev-more { background: none; border: none; color: var(--accent); font-size: 12px; cursor: pointer; padding: 2px 4px; align-self: flex-start; }
.cp-body { display: flex; flex-direction: column; gap: 18px; }
.cp-round { display: flex; flex-direction: column; gap: 8px; }
.cp-round-head { display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
.cp-round-no { font-size: 13px; font-weight: 700; color: var(--text-primary); }
.cp-round-count { font-size: 12px; color: var(--text-muted); }
.cp-list { list-style: none; display: flex; flex-direction: column; gap: 8px; margin: 0; padding: 0; }
.cp-item { border: 1px solid var(--border); border-radius: 8px; background: var(--bg-elevated); padding: 10px 12px; display: flex; flex-direction: column; gap: 5px; }
.cp-item.clickable { cursor: pointer; }
.cp-item.clickable:hover { border-color: var(--accent); }
.cp-item-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pk { display: inline-flex; align-items: center; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.pk-info { color: var(--info); background: var(--info-soft); }
.pk-warn { color: var(--warn); background: var(--warn-soft); }
.pk-accent { color: var(--accent); background: var(--accent-soft); }
.pk-ok { color: var(--ok); background: var(--ok-soft); }
.pk-err { color: var(--err); background: var(--err-soft); }
.cp-time { font-size: 12.5px; color: var(--text-secondary); font-weight: 600; }
.cp-region { font-size: 12.5px; color: var(--text-secondary); }
.cp-team { font-size: 11px; color: var(--text-muted); font-weight: 700; }
.cp-players { font-size: 12.5px; color: var(--text-primary); font-weight: 600; }
.cp-ev { font-size: 12px; color: var(--text-muted); margin: 0; }
.cp-reason { font-size: 11.5px; color: var(--warn); margin: 0; }
.cp-goto { display: flex; }
.cp-locate { font-size: 12px; padding: 3px 10px; }

@media (max-width: 720px) {
  .co-metric-table { min-width: 0; }
}
</style>
